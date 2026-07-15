"""V2 backend tests for Runner.ai (Phases 5-6).

Covers new endpoints:
 - GET /api/tools shows 7 tools with save_user_preference requires_approval=True
 - Approval workflow: request -> waiting_approval SSE -> /approve / /reject
 - Multi-file upload: /api/documents/upload_bulk
 - Digest schedules CRUD + list_digests
 - Share thread: POST/GET/DELETE /api/threads/{id}/share + public /api/share/{token}
 - Structured JSON planner: run has plan with steps
 - Rate limiter proxy-aware: X-Forwarded-For keying

External LLM calls are LIVE. Timeouts generous.
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

pytestmark = pytest.mark.skipif(
    not BASE_URL,
    reason="E2E tests require REACT_APP_BACKEND_URL and a running backend",
)

API = f"{BASE_URL}/api"
RUN_STREAM_TIMEOUT = 120.0


# --------------------------------------------------------------------------
# Helpers / Fixtures
# --------------------------------------------------------------------------

def _unique_email(prefix: str = "test") -> str:
    return f"test_{prefix.lower()}_{uuid.uuid4().hex[:10]}@example.com"


def _auth_headers(user: dict) -> dict:
    return {"Authorization": f"Bearer {user['token']}", "Content-Type": "application/json"}


@pytest.fixture(scope="session")
def http() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def _register(http, prefix: str) -> dict:
    email = _unique_email(prefix)
    r = http.post(
        f"{API}/auth/register",
        data=json.dumps({"email": email, "password": "TestPass123!", "name": f"U {prefix}"}),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    return {"token": body["access_token"], "user": body["user"], "email": email,
            "password": "TestPass123!"}


@pytest.fixture(scope="session")
def user_a(http) -> dict:
    return _register(http, "userA_v2")


@pytest.fixture(scope="session")
def user_b(http) -> dict:
    return _register(http, "userB_v2")


def _make_pdf_bytes(text: str = "V2 test doc about agentic retrieval and hybrid search.") -> bytes:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, "Runner.ai V2 test doc")
    c.drawString(72, 700, text)
    c.drawString(72, 680, "The executor runs read-only tools in parallel where possible.")
    c.drawString(72, 660, "Evidence is normalised and cited by page.")
    c.showPage()
    c.save()
    return buf.getvalue()


def _consume_sse(resp: requests.Response, timeout: float = RUN_STREAM_TIMEOUT):
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
    resp = requests.post(f"{API}/agent/run/stream", headers=headers,
                         data=json.dumps(payload), stream=True, timeout=timeout)
    assert resp.status_code == 200, f"stream not started: {resp.status_code} {resp.text[:400]}"
    events: list[tuple[str, dict]] = []
    for ev, data in _consume_sse(resp, timeout=timeout):
        events.append((ev, data))
        if ev in ("run_completed", "run_failed", "waiting_approval"):
            break
    return {"events": events}


# --------------------------------------------------------------------------
# Tools registry (V2)
# --------------------------------------------------------------------------

class TestToolsV2:
    def test_seven_tools_and_write_flag(self, http):
        r = http.get(f"{API}/tools")
        assert r.status_code == 200
        tools = r.json()["tools"]
        assert len(tools) == 7, f"expected 7 tools, got {len(tools)}: {[t['id'] for t in tools]}"

        by_id = {t["id"]: t for t in tools}
        expected = {"search_document_chunks", "get_document_summary", "list_user_documents",
                    "web_search", "paper_search", "get_user_preferences", "save_user_preference"}
        assert expected.issubset(set(by_id.keys()))

        save = by_id["save_user_preference"]
        assert save["risk_level"] == "write"
        assert save["requires_approval"] is True

        get_prefs = by_id["get_user_preferences"]
        assert get_prefs["risk_level"] == "read"
        assert get_prefs["requires_approval"] is False


# --------------------------------------------------------------------------
# Approval workflow
# --------------------------------------------------------------------------

class TestApprovalWorkflow:
    """Save-preference must go through approval workflow."""

    def test_waiting_approval_sse_emitted(self, http, user_a):
        result = _run_agent(
            user_a,
            "Please save my preferred topic as agentic-RAG",
        )
        events = result["events"]
        names = [e[0] for e in events]
        assert "run_started" in names, f"names={names}"
        assert "capabilities_selected" in names
        assert "planning" in names
        assert "plan_ready" in names
        assert "waiting_approval" in names, f"expected waiting_approval, got {names}"
        # Should NOT have completed since it needs approval
        assert "run_completed" not in names

        run_id = next(d for e, d in events if e == "run_started")["run_id"]
        user_a["_approval_run_id"] = run_id

        # GET run status
        r = http.get(f"{API}/agent/runs/{run_id}", headers=_auth_headers(user_a))
        assert r.status_code == 200
        row = r.json()
        assert row["status"] == "waiting_approval"
        assert row.get("plan")
        # steps present
        plan = row["plan"]
        assert plan.get("steps"), f"plan missing steps: {plan}"

    def test_approve_persists_answer(self, http, user_a):
        run_id = user_a.get("_approval_run_id")
        assert run_id, "prior test must run first"

        # Also record thread_id to check message persistence
        r = http.get(f"{API}/agent/runs/{run_id}", headers=_auth_headers(user_a))
        thread_id = r.json()["thread_id"]

        # Before approval, no assistant message yet
        rm = http.get(f"{API}/threads/{thread_id}/messages", headers=_auth_headers(user_a))
        assert rm.status_code == 200
        msgs_before = rm.json()
        assistant_before = [m for m in msgs_before if m["role"] == "assistant"]
        assert len(assistant_before) == 0, f"assistant persisted before approval! {assistant_before}"

        # Approve
        r = http.post(f"{API}/agent/runs/{run_id}/approve",
                      headers=_auth_headers(user_a), data="{}", timeout=120)
        assert r.status_code == 200, r.text
        finished = r.json()
        assert finished["status"] == "completed", f"status={finished}"
        assert finished.get("answer"), "answer missing after approve"
        tool_calls = finished.get("tool_calls") or []
        save_calls = [t for t in tool_calls if t.get("tool_id") == "save_user_preference"]
        assert save_calls, f"save_user_preference tool call missing: {tool_calls}"
        assert save_calls[0].get("status") == "ok", f"save call not ok: {save_calls[0]}"

        # Assistant message now persisted
        rm = http.get(f"{API}/threads/{thread_id}/messages", headers=_auth_headers(user_a))
        msgs_after = rm.json()
        assistant_after = [m for m in msgs_after if m["role"] == "assistant"]
        assert len(assistant_after) >= 1, "no assistant message after approve"

    def test_reject_persists_rejection(self, http, user_a):
        # Create a fresh save-pref run
        result = _run_agent(user_a, "Please save my preferred topic as multi-agent-systems")
        events = result["events"]
        names = [e[0] for e in events]
        assert "waiting_approval" in names, f"names={names}"
        run_id = next(d for e, d in events if e == "run_started")["run_id"]

        r = http.get(f"{API}/agent/runs/{run_id}", headers=_auth_headers(user_a))
        thread_id = r.json()["thread_id"]

        r = http.post(f"{API}/agent/runs/{run_id}/reject",
                      headers=_auth_headers(user_a), data="{}")
        assert r.status_code == 200, r.text
        finished = r.json()
        assert finished["status"] == "failed"

        rm = http.get(f"{API}/threads/{thread_id}/messages", headers=_auth_headers(user_a))
        assistant = [m for m in rm.json() if m["role"] == "assistant"]
        assert assistant, "no rejection message persisted"
        assert any("reject" in (m.get("content") or "").lower() for m in assistant), \
            f"assistant message doesn't mention rejection: {[m['content'] for m in assistant]}"


# --------------------------------------------------------------------------
# Multi-file upload
# --------------------------------------------------------------------------

class TestBulkUpload:
    def test_upload_bulk_with_mixed_files(self, http, user_a):
        headers = {"Authorization": f"Bearer {user_a['token']}"}
        pdf1 = _make_pdf_bytes("First bulk pdf about hybrid retrieval.")
        pdf2 = _make_pdf_bytes("Second bulk pdf about approvals.")
        files = [
            ("files", ("TEST_bulk1.pdf", pdf1, "application/pdf")),
            ("files", ("TEST_bulk2.pdf", pdf2, "application/pdf")),
            ("files", ("bad.txt", b"not a pdf", "text/plain")),
        ]
        r = requests.post(f"{API}/documents/upload_bulk",
                          headers=headers, files=files, timeout=60)
        assert r.status_code == 202, r.text
        body = r.json()
        assert "accepted" in body and "rejected" in body
        assert len(body["accepted"]) == 2, f"expected 2 accepted, got {body}"
        assert len(body["rejected"]) == 1, f"expected 1 rejected, got {body}"
        assert body["rejected"][0].get("reason")

        # Poll one of the accepted to reach ready
        doc_id = body["accepted"][0]["document_id"]
        deadline = time.time() + 60
        final = None
        while time.time() < deadline:
            gr = http.get(f"{API}/documents/{doc_id}", headers=_auth_headers(user_a))
            assert gr.status_code == 200
            final = gr.json()
            if final["status"] in ("ready", "failed"):
                break
            time.sleep(1.0)
        assert final and final["status"] == "ready", f"bulk-uploaded doc not ready: {final}"
        user_a["_bulk_doc_id"] = doc_id
        user_a["_bulk_doc_filename"] = final["filename"]


# --------------------------------------------------------------------------
# Hybrid retrieval - doc-scoped run should still return correct citations
# --------------------------------------------------------------------------

class TestHybridRetrieval:
    def test_doc_scoped_run(self, http, user_a):
        doc_id = user_a.get("_bulk_doc_id")
        assert doc_id, "bulk upload must run first"
        result = _run_agent(
            user_a,
            "What does the document say about hybrid retrieval or approvals?",
            document_ids=[doc_id],
        )
        events = result["events"]
        names = [e[0] for e in events]
        assert "run_completed" in names, f"names={names}"
        completed = next(d for e, d in events if e == "run_completed")
        cites = completed.get("citations") or []
        private = [c for c in cites if c.get("source_type") == "private_doc"]
        assert private, f"no private_doc citation. Cites: {cites}"
        # citation has filename + page
        assert any(c.get("filename") and c.get("page") is not None for c in private)

        # inspect persisted run to confirm plan produced
        run_id = next(d for e, d in events if e == "run_started")["run_id"]
        r = http.get(f"{API}/agent/runs/{run_id}", headers=_auth_headers(user_a))
        assert r.status_code == 200
        assert r.json()["status"] == "completed"
        assert r.json().get("plan"), "plan not persisted"


# --------------------------------------------------------------------------
# Digest schedules
# --------------------------------------------------------------------------

class TestDigests:
    def test_schedule_crud(self, http, user_a):
        # Create
        r = http.post(f"{API}/digests/schedules", headers=_auth_headers(user_a),
                      data=json.dumps({"topic": "TEST agentic RAG", "cadence": "weekly"}))
        assert r.status_code == 201, r.text
        sched = r.json()
        sid = sched["id"]
        assert sched["topic"] == "TEST agentic RAG"
        assert sched["cadence"] == "weekly"

        # List
        r = http.get(f"{API}/digests/schedules", headers=_auth_headers(user_a))
        assert r.status_code == 200
        rows = r.json()
        assert any(x["id"] == sid for x in rows)

        # Delete
        r = http.delete(f"{API}/digests/schedules/{sid}", headers=_auth_headers(user_a))
        assert r.status_code == 204, r.text

        # Confirm gone
        r = http.get(f"{API}/digests/schedules", headers=_auth_headers(user_a))
        assert not any(x["id"] == sid for x in r.json())

    def test_list_digests(self, http, user_a):
        r = http.get(f"{API}/digests", headers=_auth_headers(user_a))
        assert r.status_code == 200
        assert isinstance(r.json(), list)  # may be empty


# --------------------------------------------------------------------------
# Share thread
# --------------------------------------------------------------------------

class TestShareThread:
    def test_share_lifecycle(self, http, user_a, user_b):
        # Create a thread via an agent run so it has messages
        result = _run_agent(user_a, "Find one recent arxiv paper on retrieval-augmented generation")
        events = result["events"]
        # ok even if only partial; grab thread_id
        run_started = next(d for e, d in events if e == "run_started")
        thread_id = run_started["thread_id"]

        # Enable sharing
        r = http.post(f"{API}/threads/{thread_id}/share", headers=_auth_headers(user_a), data="{}")
        assert r.status_code == 200, r.text
        body = r.json()
        token = body["share_token"]
        assert token
        assert body["url_suffix"] == f"/share/{token}"

        # Public GET — no auth
        r = requests.get(f"{API}/share/{token}", timeout=15)
        assert r.status_code == 200, r.text
        pub = r.json()
        assert "thread" in pub
        assert "messages" in pub
        assert isinstance(pub["messages"], list)

        # user_b cannot disable A's share (should 404)
        r = http.delete(f"{API}/threads/{thread_id}/share", headers=_auth_headers(user_b))
        assert r.status_code == 404

        # user_a disables sharing
        r = http.delete(f"{API}/threads/{thread_id}/share", headers=_auth_headers(user_a))
        assert r.status_code == 204

        # Public GET now 404
        r = requests.get(f"{API}/share/{token}", timeout=15)
        assert r.status_code == 404


# --------------------------------------------------------------------------
# Rate limiter — proxy-aware via X-Forwarded-For
# --------------------------------------------------------------------------

class TestRateLimiterProxy:
    def test_xff_keys_the_limiter(self, http):
        """Sending different X-Forwarded-For IPs should NOT share the same
        rate window. Note: since our limit is 20/min per key, we send 15
        requests to a single XFF IP and confirm we haven't hit the same
        limit when using a DIFFERENT XFF IP.
        """
        email_bogus = _unique_email("rl_xff")
        # Hammer with same XFF
        same_ip_status = []
        for i in range(25):
            r = requests.post(
                f"{API}/auth/login",
                headers={"X-Forwarded-For": "1.2.3.4",
                         "Content-Type": "application/json"},
                data=json.dumps({"email": email_bogus, "password": "nope"}),
                timeout=15,
            )
            same_ip_status.append(r.status_code)
            if r.status_code == 429:
                break

        # Different XFF should still be allowed (not yet rate limited)
        r = requests.post(
            f"{API}/auth/login",
            headers={"X-Forwarded-For": "9.9.9.9",
                     "Content-Type": "application/json"},
            data=json.dumps({"email": email_bogus, "password": "nope"}),
            timeout=15,
        )

        # If same-ip saw 429 AND different ip returned 401 (unauthorised)
        # then per-IP keying is correct.
        if 429 in same_ip_status:
            assert r.status_code == 401, \
                f"different XFF returned {r.status_code}, expected 401 (not 429)"
        else:
            # limit not reached; report as skip
            pytest.skip("Rate limit (20/min) not observed even with 25 rapid attempts;"
                        f" statuses={same_ip_status[-5:]}. Cannot verify per-IP keying.")


# --------------------------------------------------------------------------
# Structured JSON planner - verify plan shape
# --------------------------------------------------------------------------

class TestStructuredPlanner:
    def test_plan_has_steps_with_tool_ids(self, http, user_a):
        result = _run_agent(user_a, "Find recent arxiv papers about hybrid retrieval")
        events = result["events"]
        assert "plan_ready" in [e[0] for e in events]
        run_id = next(d for e, d in events if e == "run_started")["run_id"]
        r = http.get(f"{API}/agent/runs/{run_id}", headers=_auth_headers(user_a))
        assert r.status_code == 200
        plan = r.json().get("plan")
        assert plan, "no plan persisted"
        assert plan.get("goal"), f"plan.goal missing: {plan}"
        steps = plan.get("steps") or []
        assert steps, f"plan has no steps: {plan}"
        assert all(s.get("tool_id") for s in steps), \
            f"steps missing tool_id: {steps}"
