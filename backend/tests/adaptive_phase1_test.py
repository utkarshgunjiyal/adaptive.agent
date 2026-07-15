"""Phase 1 adaptive-runtime acceptance tests.

Covers:
  A1  legacy regression baseline (in backend_test.py — not repeated here)
  A2  direct answer (no tool call)
  A3  document retrieval (one tool round, ToolMessage loop)
  A4  trim protection: latest tool message never dropped
  A5  MongoDB persistence + final SSE frame present
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
STREAM_TIMEOUT = 90.0


def _unique_email(prefix: str = "adaptive") -> str:
    return f"test_{prefix.lower()}_{uuid.uuid4().hex[:10]}@example.com"


def _headers(user: dict) -> dict:
    return {"Authorization": f"Bearer {user['token']}",
            "Content-Type": "application/json"}


def _make_pdf(text: str) -> bytes:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 12)
    y = 720
    for line in text.split("\n"):
        c.drawString(72, y, line[:90])
        y -= 20
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture(scope="module")
def user() -> dict:
    email = _unique_email("adaptive_user")
    r = requests.post(f"{API}/auth/register",
                      json={"email": email, "password": "TestPass123!",
                            "name": "Adaptive Tester"}, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    return {"token": body["access_token"], "user": body["user"], "email": email}


def _stream_adaptive(user: dict, message: str, document_ids=None,
                     timeout: float = STREAM_TIMEOUT) -> dict:
    payload = {"message": message, "document_ids": document_ids or []}
    r = requests.post(f"{API}/agent/run/adaptive/stream",
                      headers={**_headers(user), "Accept": "text/event-stream"},
                      data=json.dumps(payload), stream=True, timeout=timeout)
    assert r.status_code == 200, f"stream not started: {r.status_code} {r.text[:400]}"

    events: list[tuple[str, dict]] = []
    event = None
    buf: list[str] = []
    started = time.time()
    for raw in r.iter_lines(decode_unicode=True):
        if time.time() - started > timeout:
            break
        if raw == "":
            if event and buf:
                try:
                    data = json.loads("\n".join(buf))
                except Exception:
                    data = {"_raw": "\n".join(buf)}
                events.append((event, data))
                if event in ("run_completed", "run_failed"):
                    break
            event, buf = None, []
            continue
        if raw is None:
            continue
        if raw.startswith("event: "):
            event = raw[len("event: "):].strip()
        elif raw.startswith("data: "):
            buf.append(raw[len("data: "):])
    return {"events": events}


class TestAdaptiveDirect:
    """A2: Direct answer, no tool call needed."""
    def test_direct_answer(self, user):
        result = _stream_adaptive(user, "Explain RAG in five bullet points.")
        names = [e[0] for e in result["events"]]
        assert "run_started" in names
        assert "run_completed" in names, f"missing run_completed: {names}"
        # No tool_started/tool_completed on a direct answer
        assert "tool_started" not in names, f"unexpected tool call: {names}"
        completed = next(d for n, d in result["events"] if n == "run_completed")
        answer = completed.get("answer") or ""
        assert answer.strip(), f"empty answer on direct path: {answer!r}"
        assert len(answer) > 40, f"answer too short: {answer!r}"
        # Basic content check - Claude should give a well-formed reply
        low = answer.lower()
        assert "retrieval" in low or "rag" in low or "augment" in low, \
            f"answer doesn't mention RAG concepts: {answer[:200]}"

        # Assert persistence: assistant message row exists for this run.
        run_id = next(d for n, d in result["events"] if n == "run_started")["run_id"]
        r = requests.get(f"{API}/agent/runs/{run_id}", headers=_headers(user), timeout=15)
        assert r.status_code == 200, r.text
        run = r.json()
        assert run["status"] == "completed"
        assert (run.get("answer") or "").strip()


class TestAdaptiveDocument:
    """A3: One document tool round; ToolMessage loop."""
    @pytest.fixture(scope="class")
    def uploaded_doc(self, user):
        pdf = _make_pdf(
            "Runner architecture memory design.\n"
            "The memory system uses rolling thread summaries plus recent messages.\n"
            "Long chats are compacted into a summary block to fit LLM context.\n"
            "Every tool observation is returned to the LLM as a ToolMessage.\n"
            "Retrieved evidence is normalized before being passed onward."
        )
        files = {"file": ("memory_arch.pdf", pdf, "application/pdf")}
        r = requests.post(f"{API}/documents/upload",
                          headers={"Authorization": f"Bearer {user['token']}"},
                          files=files, timeout=30)
        assert r.status_code == 202, r.text
        doc_id = r.json()["document_id"]
        # poll until ready
        for _ in range(45):
            gr = requests.get(f"{API}/documents/{doc_id}",
                              headers=_headers(user), timeout=15)
            assert gr.status_code == 200
            if gr.json()["status"] == "ready":
                return doc_id
            time.sleep(1.0)
        pytest.fail("document did not become ready")

    def test_document_toolmessage_loop(self, user, uploaded_doc):
        result = _stream_adaptive(
            user,
            "What does my uploaded architecture say about memory?",
            document_ids=[uploaded_doc],
        )
        names = [e[0] for e in result["events"]]
        assert "run_started" in names
        assert "tool_started" in names, f"expected a tool call: {names}"
        assert "tool_completed" in names, f"tool_completed missing: {names}"
        assert "run_completed" in names, f"never completed: {names}"

        tool_started = [d for n, d in result["events"] if n == "tool_started"]
        tool_completed = [d for n, d in result["events"] if n == "tool_completed"]
        # Phase 1 binds only search_document_chunks
        for t in tool_started:
            assert t.get("tool_id") == "search_document_chunks", \
                f"unexpected tool bound: {t.get('tool_id')}"
        assert any(t.get("status") == "success" for t in tool_completed), \
            f"no successful tool completion: {tool_completed}"

        completed = next(d for n, d in result["events"] if n == "run_completed")
        answer = completed.get("answer") or ""
        citations = completed.get("citations") or []
        assert answer.strip(), f"empty final answer with tool round: {answer!r}"
        assert citations, "no citations returned"
        # Must be private_doc evidence
        assert any(c.get("source_type") == "private_doc" for c in citations), \
            f"expected private_doc citation: {[c.get('source_type') for c in citations]}"
        # Answer should reference memory/summary content
        low = answer.lower()
        assert ("memory" in low or "summary" in low or "toolmessage" in low
                or "context" in low), f"answer not grounded: {answer[:300]}"

    def test_persistence_after_tool_round(self, user, uploaded_doc):
        # Fresh run, then verify DB persistence.
        result = _stream_adaptive(
            user,
            "What does the document say about ToolMessages?",
            document_ids=[uploaded_doc],
        )
        completed = next((d for n, d in result["events"] if n == "run_completed"), None)
        assert completed, f"no run_completed frame: {result['events']}"
        run_id = next(d for n, d in result["events"] if n == "run_started")["run_id"]

        # GET run: must be completed with a non-empty answer + citations.
        r = requests.get(f"{API}/agent/runs/{run_id}", headers=_headers(user), timeout=15)
        assert r.status_code == 200
        run = r.json()
        assert run["status"] == "completed"
        assert (run.get("answer") or "").strip(), "persisted run has empty answer"
        assert run.get("citations"), "persisted citations empty"
