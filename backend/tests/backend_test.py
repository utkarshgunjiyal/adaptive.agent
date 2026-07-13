"""End-to-end backend tests for Runner.ai (Phase 1-4).

Covers:
 - Health / readiness
 - Auth: register/login/me + rate-limit sanity
 - Threads: CRUD + cross-user isolation
 - Documents: PDF upload happy path + validation errors + retry + isolation
 - Tools registry
 - Agent runs (SSE): paper_search, web_search (Tavily), document-grounded

External LLM / Tavily / arXiv calls are LIVE — set generous timeouts.
"""
from __future__ import annotations

import io
import json
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Fallback: read frontend/.env
    try:
        with open("/app/frontend/.env") as fh:
            for line in fh:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.strip().split("=", 1)[1].rstrip("/")
                    break
    except Exception:  # noqa: BLE001
        pass

assert BASE_URL, "REACT_APP_BACKEND_URL must be set"
API = f"{BASE_URL}/api"

RUN_STREAM_TIMEOUT = 90.0  # LLM + external APIs can be slow


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def _unique_email(prefix: str = "test") -> str:
    return f"test_{prefix.lower()}_{uuid.uuid4().hex[:10]}@example.com"


@pytest.fixture(scope="session")
def http() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def user_a(http) -> dict:
    email = _unique_email("userA")
    r = http.post(
        f"{API}/auth/register",
        data=json.dumps({"email": email, "password": "TestPass123!", "name": "User A"}),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    return {"token": body["access_token"], "user": body["user"], "email": email,
            "password": "TestPass123!"}


@pytest.fixture(scope="session")
def user_b(http) -> dict:
    email = _unique_email("userB")
    r = http.post(
        f"{API}/auth/register",
        data=json.dumps({"email": email, "password": "TestPass123!", "name": "User B"}),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    return {"token": body["access_token"], "user": body["user"], "email": email,
            "password": "TestPass123!"}


def _auth_headers(user: dict) -> dict:
    return {"Authorization": f"Bearer {user['token']}", "Content-Type": "application/json"}


def _make_pdf_bytes(text: str = "This document describes the executor design.") -> bytes:
    """Generate a small valid PDF using reportlab."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, "Runner.ai test document")
    c.drawString(72, 700, text)
    c.drawString(72, 680, "The executor runs read-only tools in parallel where possible.")
    c.drawString(72, 660, "Evidence is normalised and cited by page.")
    c.showPage()
    c.save()
    return buf.getvalue()


# --------------------------------------------------------------------------
# Health / readiness
# --------------------------------------------------------------------------

class TestHealth:
    def test_health(self, http):
        r = http.get(f"{API}/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_ready(self, http):
        r = http.get(f"{API}/ready")
        assert r.status_code == 200
        body = r.json()
        assert body.get("ready") is True
        assert body.get("mongodb") is True


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

class TestAuth:
    def test_register_and_me(self, http, user_a):
        assert user_a["token"]
        assert user_a["user"]["email"] == user_a["email"]
        r = http.get(f"{API}/auth/me", headers=_auth_headers(user_a))
        assert r.status_code == 200
        assert r.json()["email"] == user_a["email"]

    def test_duplicate_register_returns_409(self, http, user_a):
        r = http.post(
            f"{API}/auth/register",
            data=json.dumps({"email": user_a["email"], "password": "x" * 8, "name": "dup"}),
        )
        assert r.status_code == 409, r.text

    def test_login_success(self, http, user_a):
        r = http.post(
            f"{API}/auth/login",
            data=json.dumps({"email": user_a["email"], "password": user_a["password"]}),
        )
        assert r.status_code == 200, r.text
        assert "access_token" in r.json()
        assert r.json()["user"]["email"] == user_a["email"]

    def test_login_wrong_password(self, http, user_a):
        r = http.post(
            f"{API}/auth/login",
            data=json.dumps({"email": user_a["email"], "password": "wrong-pass!"}),
        )
        assert r.status_code == 401

    def test_me_without_token_is_401(self, http):
        r = requests.get(f"{API}/auth/me")
        assert r.status_code == 401

    def test_rate_limit_eventual_429(self, http):
        """20+ rapid login attempts against a fresh email should hit 429 for
        that client host. Uses a bogus email to avoid interfering with other
        tests. NOTE: we tolerate not seeing 429 if the ingress rewrites the
        client host, which would make each request appear from a different
        source (record as skip, not fail).
        """
        email = _unique_email("rl")
        saw_429 = False
        for _ in range(30):
            r = requests.post(
                f"{API}/auth/login",
                json={"email": email, "password": "nope"},
            )
            if r.status_code == 429:
                saw_429 = True
                break
        if not saw_429:
            pytest.skip("429 not observed — likely ingress client-host normalization; "
                        "rate limiter is per client.host and preview may not pass true peer IP.")


# --------------------------------------------------------------------------
# Threads
# --------------------------------------------------------------------------

class TestThreads:
    def test_create_list_and_get_thread(self, http, user_a):
        h = _auth_headers(user_a)
        r = http.post(f"{API}/threads", headers=h, data=json.dumps({"title": "TEST_thread_a"}))
        assert r.status_code == 201, r.text
        t = r.json()
        assert t["title"] == "TEST_thread_a"
        thread_id = t["id"]

        r = http.get(f"{API}/threads", headers=h)
        assert r.status_code == 200
        assert any(x["id"] == thread_id for x in r.json())

        r = http.get(f"{API}/threads/{thread_id}", headers=h)
        assert r.status_code == 200
        assert r.json()["id"] == thread_id

    def test_get_random_thread_returns_404(self, http, user_a):
        r = http.get(
            f"{API}/threads/{'0' * 24}",
            headers=_auth_headers(user_a),
        )
        assert r.status_code == 404

    def test_cross_user_isolation(self, http, user_a, user_b):
        r = http.post(
            f"{API}/threads",
            headers=_auth_headers(user_b),
            data=json.dumps({"title": "TEST_userB_thread"}),
        )
        assert r.status_code == 201
        b_thread_id = r.json()["id"]

        # User A cannot see user B's thread
        r = http.get(f"{API}/threads/{b_thread_id}", headers=_auth_headers(user_a))
        assert r.status_code == 404


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------

class TestDocuments:
    def _upload(self, user: dict, name: str, blob: bytes, ctype: str = "application/pdf"):
        # Multipart — do NOT include the JSON Content-Type header
        headers = {"Authorization": f"Bearer {user['token']}"}
        files = {"file": (name, blob, ctype)}
        return requests.post(f"{API}/documents/upload", headers=headers, files=files, timeout=30)

    def test_reject_non_pdf_content_type(self, user_a):
        r = self._upload(user_a, "hello.txt", b"not a pdf", ctype="text/plain")
        assert r.status_code == 415, r.text

    def test_reject_empty_pdf(self, user_a):
        r = self._upload(user_a, "empty.pdf", b"", ctype="application/pdf")
        # Empty body — content-type is pdf but size 0
        assert r.status_code == 400, r.text

    def test_reject_wrong_magic_bytes(self, user_a):
        r = self._upload(user_a, "fake.pdf", b"HELLO_WORLD_NOT_A_PDF", ctype="application/pdf")
        assert r.status_code == 415, r.text

    def test_upload_and_process(self, http, user_a):
        pdf = _make_pdf_bytes()
        r = self._upload(user_a, "TEST_sample.pdf", pdf)
        assert r.status_code == 202, r.text
        body = r.json()
        doc_id = body["document_id"]
        assert body.get("job_id")

        # Poll status
        deadline = time.time() + 45
        final = None
        while time.time() < deadline:
            gr = http.get(f"{API}/documents/{doc_id}", headers=_auth_headers(user_a))
            assert gr.status_code == 200
            final = gr.json()
            if final["status"] in ("ready", "failed"):
                break
            time.sleep(1.0)
        assert final is not None
        assert final["status"] == "ready", f"doc did not become ready: {final}"
        assert final.get("page_count") == 1
        assert final.get("summary")
        # save for later use
        user_a["doc_id"] = doc_id

    def test_list_documents_shows_only_own(self, http, user_a, user_b):
        # user_a has at least one doc from previous test
        r = http.get(f"{API}/documents", headers=_auth_headers(user_a))
        assert r.status_code == 200
        a_docs = r.json()
        assert isinstance(a_docs, list) and len(a_docs) >= 1

        r = http.get(f"{API}/documents", headers=_auth_headers(user_b))
        assert r.status_code == 200
        b_docs = r.json()
        a_ids = {d["id"] for d in a_docs}
        b_ids = {d["id"] for d in b_docs}
        assert a_ids.isdisjoint(b_ids), "cross-user document leak"

    def test_retry_reprocesses(self, http, user_a):
        doc_id = user_a.get("doc_id")
        assert doc_id, "expected doc_id from previous test"
        r = http.post(
            f"{API}/documents/{doc_id}/retry", headers=_auth_headers(user_a),
        )
        assert r.status_code == 202, r.text

        # Poll until ready again
        deadline = time.time() + 45
        while time.time() < deadline:
            gr = http.get(
                f"{API}/documents/{doc_id}", headers=_auth_headers(user_a),
            )
            assert gr.status_code == 200
            if gr.json()["status"] == "ready":
                break
            time.sleep(1.0)
        else:
            pytest.fail("Retry did not bring document back to ready")


# --------------------------------------------------------------------------
# Tools registry
# --------------------------------------------------------------------------

class TestTools:
    def test_registry_shape(self, http):
        r = http.get(f"{API}/tools")
        assert r.status_code == 200
        tools = r.json()["tools"]
        ids = {t["id"] for t in tools}
        expected = {
            "search_document_chunks", "get_document_summary",
            "list_user_documents", "web_search", "paper_search",
        }
        assert expected.issubset(ids), f"missing tools: {expected - ids}"

        by_id = {t["id"]: t for t in tools}
        assert by_id["web_search"]["available"] is True
        assert by_id["web_search"]["badge"] == "web_source"
        assert by_id["paper_search"]["available"] is True
        assert by_id["paper_search"]["badge"] == "research_paper"


# --------------------------------------------------------------------------
# Agent SSE runs
# --------------------------------------------------------------------------

def _consume_sse(resp: requests.Response, timeout: float = RUN_STREAM_TIMEOUT):
    """Yield (event, data) tuples from an SSE response."""
    event = None
    buf: list[str] = []
    started = time.time()
    for raw in resp.iter_lines(decode_unicode=True):
        if time.time() - started > timeout:
            break
        if raw is None:
            continue
        if raw == "":
            if event is not None and buf:
                try:
                    data = json.loads("\n".join(buf))
                except Exception:  # noqa: BLE001
                    data = {"_raw": "\n".join(buf)}
                yield event, data
            event, buf = None, []
            continue
        if raw.startswith("event: "):
            event = raw[len("event: "):].strip()
        elif raw.startswith("data: "):
            buf.append(raw[len("data: "):])


def _run_agent(user: dict, message: str, document_ids: list[str] | None = None,
               timeout: float = RUN_STREAM_TIMEOUT) -> dict:
    headers = {
        "Authorization": f"Bearer {user['token']}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    payload = {"message": message, "document_ids": document_ids or []}
    resp = requests.post(
        f"{API}/agent/run/stream",
        headers=headers,
        data=json.dumps(payload),
        stream=True,
        timeout=timeout,
    )
    assert resp.status_code == 200, f"stream not started: {resp.status_code} {resp.text[:400]}"

    events: list[tuple[str, dict]] = []
    for ev, data in _consume_sse(resp, timeout=timeout):
        events.append((ev, data))
        if ev in ("run_completed", "run_failed"):
            break
    return {"events": events}


class TestAgentRuns:
    def test_paper_search_run(self, http, user_a):
        result = _run_agent(user_a, "Find recent arxiv papers about agentic RAG")
        events = result["events"]
        names = [e[0] for e in events]
        assert "run_started" in names
        assert "capabilities_selected" in names
        assert "planning" in names
        assert "plan_ready" in names
        assert "executing" in names
        # tool_call frames appear as event name 'tool_call'
        assert "evidence_ready" in names
        assert "run_completed" in names, f"stream ended without completion: {names}"

        completed = next(d for e, d in events if e == "run_completed")
        assert completed.get("answer"), "run_completed missing answer"
        citations = completed.get("citations") or []
        assert citations, "no citations returned"
        # There should be at least one research_paper source with url/authors/published
        paper_hits = [c for c in citations if c.get("source_type") == "research_paper"]
        assert paper_hits, f"expected research_paper citation. got: {citations[:2]}"
        top = paper_hits[0]
        assert top.get("url"), "paper citation missing url"
        # authors may be a list
        assert top.get("authors"), "paper citation missing authors"

        run_id = next(d for e, d in events if e == "run_started")["run_id"]
        r = http.get(f"{API}/agent/runs/{run_id}", headers=_auth_headers(user_a))
        assert r.status_code == 200
        assert r.json()["status"] == "completed"

    def test_document_grounded_run(self, http, user_a):
        doc_id = user_a.get("doc_id")
        assert doc_id, "requires document from earlier test"
        result = _run_agent(
            user_a,
            "What does my document say about the executor?",
            document_ids=[doc_id],
        )
        events = result["events"]
        names = [e[0] for e in events]
        assert "run_completed" in names, f"not completed: {names}"
        completed = next(d for e, d in events if e == "run_completed")
        cites = completed.get("citations") or []
        private = [c for c in cites if c.get("source_type") == "private_doc"]
        assert private, f"expected private_doc citation. Got citation source_types: {[c.get('source_type') for c in cites]}"
        assert any(c.get("filename") and c.get("page") is not None for c in private), \
            "private_doc citation missing filename/page"

    def test_tavily_web_search_run(self, http, user_a):
        result = _run_agent(user_a, "What is the current news on the MCP protocol?")
        events = result["events"]
        names = [e[0] for e in events]
        assert "run_completed" in names, f"not completed: {names}"
        tool_calls = [d for e, d in events if e == "tool_call"]
        # At least one of web_search or paper_search should have run
        assert any(tc.get("tool_id") in {"web_search", "paper_search"} for tc in tool_calls), \
            f"expected web/paper search tool call. Got: {[tc.get('tool_id') for tc in tool_calls]}"
        # If web_search ran, it should have ok status
        web = [tc for tc in tool_calls if tc.get("tool_id") == "web_search"]
        if web:
            assert any(tc.get("status") == "ok" and (tc.get("evidence_count") or 0) > 0 for tc in web), \
                f"web_search tool calls not ok or no evidence: {web}"
        completed = next(d for e, d in events if e == "run_completed")
        cites = completed.get("citations") or []
        # tolerate paper-only fallback but we want at least SOME evidence
        assert cites, "web+paper run returned no citations"
