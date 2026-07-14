"""Adaptive agent SSE endpoint + HITL resume.

Endpoints:
  POST /api/agent/run/adaptive/stream           — start / stream a run
  POST /api/agent/runs/{run_id}/adaptive/approve — approve pending tool calls
  POST /api/agent/runs/{run_id}/adaptive/reject  — reject pending tool calls
  GET  /api/agent/adaptive/config                — feature flag lookup

SSE event vocabulary (superset of legacy):
  run_started, llm_thinking, capability_reselected,
  tool_started, tool_completed, evidence_added,
  waiting_approval, answer_delta, run_completed, run_failed
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.adaptive.config import adaptive
from app.adaptive.graph import build_graph, get_saver
from app.adaptive.policy import approval_fingerprint
from app.adaptive.tool_bindings import all_names
from app.auth import check_rate_limit, get_current_user
from app.config import settings
from app.db import get_db
from app.models import AgentRunRequest
from app.services import agent as agent_svc
from app.services import thread_service
from langgraph.types import Command

log = logging.getLogger("runner.route.adaptive")

router = APIRouter(prefix="/api/agent", tags=["adaptive-agent"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _stream_graph_events(
    *, run_id: str, thread_id: str, user_id: str,
    input_or_command: Any, user_message_id: str | None = None,
    started_at: datetime,
) -> AsyncIterator[str]:
    saver = await get_saver()
    graph = build_graph(checkpointer=saver)
    config = {"configurable": {"thread_id": f"run:{run_id}"}}

    if user_message_id is not None:
        yield _sse("run_started", {
            "run_id": run_id, "thread_id": thread_id,
            "user_message_id": user_message_id, "runtime": "adaptive",
        })

    emitted_tools: set[str] = set()
    emitted_evidence = 0
    last_iteration = -1
    saw_interrupt = False
    seen_bound_tools: list[str] = []
    deadline = asyncio.get_event_loop().time() + adaptive.overall_run_timeout_s

    stream = graph.astream(
        input_or_command, config=config, stream_mode="updates",
    )
    try:
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                log.warning("adaptive run %s hit overall timeout", run_id)
                # force-finalize below
                yield _sse("run_failed", {
                    "run_id": run_id,
                    "error": f"Run exceeded overall timeout ({adaptive.overall_run_timeout_s:.0f}s)",
                })
                await get_db().agent_runs.update_one(
                    {"_id": ObjectId(run_id)},
                    {"$set": {
                        "status": "completed",
                        "answer": ("The run took too long and was stopped. "
                                   "Please refine the question or try again."),
                        "stop_reason": "overall_timeout",
                        "completed_at": datetime.now(timezone.utc),
                        "duration_ms": int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000),
                        "runtime": "adaptive",
                    }},
                )
                return
            try:
                chunk = await asyncio.wait_for(stream.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                continue

            for node_name, delta in chunk.items():
                # Interrupts appear as a special node called "__interrupt__" with
                # a tuple of Interrupt values.
                if node_name == "__interrupt__":
                    saw_interrupt = True
                    irupts = delta if isinstance(delta, (list, tuple)) else [delta]
                    for irupt in irupts:
                        payload = getattr(irupt, "value", None) or {}
                        yield _sse("waiting_approval", {
                            "run_id": run_id,
                            "proposals": payload.get("proposals") or [],
                            "reason": payload.get("reason") or "approval_required",
                        })
                    continue

                if not isinstance(delta, dict):
                    continue

                if node_name == "select_capabilities":
                    bt = sorted(delta.get("bound_tools") or [])
                    if bt and bt != seen_bound_tools:
                        seen_bound_tools = bt
                        yield _sse("capabilities_selected",
                                   {"tools": bt, "reason": "initial"})

                if node_name == "agent":
                    iteration = delta.get("iterations", last_iteration + 1)
                    if iteration != last_iteration:
                        last_iteration = iteration
                        yield _sse("llm_thinking", {"iteration": iteration})

                if node_name == "policy_check":
                    tool_log = delta.get("tool_calls_log") or []
                    for entry in tool_log:
                        call_id = entry.get("id")
                        if call_id and call_id not in emitted_tools:
                            emitted_tools.add(call_id)
                            yield _sse("tool_started", {
                                "tool_call_id": call_id,
                                "tool_id": entry.get("tool_id"),
                                "arguments": entry.get("arguments") or {},
                                "approval_status": entry.get("approval_status"),
                            })
                            yield _sse("tool_completed", {
                                "tool_call_id": call_id,
                                "tool_id": entry.get("tool_id"),
                                "status": entry.get("status"),
                                "evidence_count": entry.get("evidence_count") or 0,
                                "duration_ms": entry.get("duration_ms"),
                                "summary": entry.get("summary"),
                                "error": entry.get("error"),
                            })
                    new_ev = delta.get("evidence") or []
                    if new_ev:
                        emitted_evidence += len(new_ev)
                        yield _sse("evidence_added", {
                            "count": len(new_ev),
                            "total": emitted_evidence,
                            "items": new_ev[:20],
                        })

                if node_name == "maybe_reselect":
                    for ev in delta.get("reselection_events") or []:
                        bound = sorted(delta.get("bound_tools") or [])
                        yield _sse("capability_reselected", {
                            "reason": ev.get("reason"),
                            "added": ev.get("added") or [],
                            "bound_tools": bound,
                        })

                if node_name == "finalize":
                    answer = delta.get("final_answer") or ""
                    if answer:
                        yield _sse("answer_delta", {"text": answer})
    finally:
        # Best effort stream cleanup.
        try:
            await stream.aclose()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass

    # Emit run_completed unless we exited via an interrupt (in which case
    # the run remains waiting_approval and the frontend will call resume).
    if saw_interrupt:
        return

    row = await get_db().agent_runs.find_one({"_id": ObjectId(run_id)})
    answer = (row or {}).get("answer") or ""
    citations = (row or {}).get("citations") or []
    tool_badges = sorted({(c.get("source_type") or "context") for c in citations})
    duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
    await get_db().agent_runs.update_one(
        {"_id": ObjectId(run_id)},
        {"$set": {"duration_ms": duration_ms}},
    )
    yield _sse("run_completed", {
        "run_id": run_id,
        "answer": answer,
        "citations": citations,
        "duration_ms": duration_ms,
        "tool_badges": tool_badges,
        "runtime": "adaptive",
    })


@router.post("/run/adaptive/stream")
async def run_adaptive_stream(
    payload: AgentRunRequest, user=Depends(get_current_user),
):
    check_rate_limit(f"agent:{user['id']}", settings.rate_limit_agent_per_minute)

    user_id = user["id"]
    thread_id = payload.thread_id
    if not thread_id:
        title = payload.message[:60] + ("…" if len(payload.message) > 60 else "")
        thread = await thread_service.create_thread(user_id, title=title)
        thread_id = str(thread["_id"])
    else:
        thread = await thread_service.get_thread(user_id, thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")

    user_msg = await thread_service.add_message(
        user_id=user_id, thread_id=thread_id, role="user", content=payload.message,
    )
    await thread_service.touch_thread(user_id, thread_id)

    run_id = await agent_svc.create_run_record(
        user_id=user_id, thread_id=thread_id,
        message=payload.message, document_ids=payload.document_ids,
    )
    await get_db().agent_runs.update_one(
        {"_id": ObjectId(run_id)},
        {"$set": {"runtime": "adaptive", "status": "executing"}},
    )

    request_id = uuid.uuid4().hex
    started_at = datetime.now(timezone.utc)
    log.info("adaptive.run start run=%s user=%s request=%s", run_id, user_id, request_id)

    input_state = {
        "run_id": run_id, "user_id": user_id, "thread_id": thread_id,
        "request_id": request_id,
        "user_message": payload.message,
        "document_ids": payload.document_ids or [],
    }

    async def event_source() -> AsyncIterator[str]:
        try:
            async for frame in _stream_graph_events(
                run_id=run_id, thread_id=thread_id, user_id=user_id,
                input_or_command=input_state,
                user_message_id=str(user_msg["_id"]),
                started_at=started_at,
            ):
                yield frame
        except asyncio.CancelledError:
            log.info("adaptive run %s cancelled", run_id)
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("adaptive run %s failed", run_id)
            await get_db().agent_runs.update_one(
                {"_id": ObjectId(run_id)},
                {"$set": {
                    "status": "failed",
                    "error": str(exc)[:400],
                    "completed_at": datetime.now(timezone.utc),
                    "runtime": "adaptive",
                }},
            )
            yield _sse("run_failed", {"run_id": run_id, "error": str(exc)[:400]})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


async def _resume_run(*, run_id: str, user_id: str,
                      decisions: dict[str, str]) -> StreamingResponse:
    run = await get_db().agent_runs.find_one(
        {"_id": ObjectId(run_id), "user_id": user_id})
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.get("status") not in ("waiting_approval", "executing"):
        raise HTTPException(status_code=409,
                            detail=f"Run is not waiting for approval (status={run.get('status')}).")

    thread_id = run["thread_id"]
    started_at = datetime.now(timezone.utc)

    # Verify decision fingerprints match the persisted pending_approval.
    pending = (run.get("pending_approval") or {}).get("proposals") or []
    valid_ids = {p["tool_call_id"]: p["fingerprint"] for p in pending}
    coerced: dict[str, str] = {}
    for cid, dec in (decisions or {}).items():
        if cid not in valid_ids:
            # unknown tool_call_id → treat as rejected
            coerced[cid] = "reject"
        else:
            d = str(dec).lower()
            coerced[cid] = "approve" if d in {"approve", "approved", "yes"} else "reject"
    # Any pending call not in decisions defaults to rejected.
    for cid in valid_ids:
        coerced.setdefault(cid, "reject")

    async def event_source() -> AsyncIterator[str]:
        yield _sse("run_resumed", {"run_id": run_id, "decisions": coerced})
        try:
            async for frame in _stream_graph_events(
                run_id=run_id, thread_id=thread_id, user_id=user_id,
                input_or_command=Command(resume={"decisions": coerced}),
                user_message_id=None,
                started_at=started_at,
            ):
                yield frame
        except Exception as exc:  # noqa: BLE001
            log.exception("adaptive resume %s failed", run_id)
            await get_db().agent_runs.update_one(
                {"_id": ObjectId(run_id)},
                {"$set": {"status": "failed", "error": str(exc)[:400],
                          "completed_at": datetime.now(timezone.utc),
                          "runtime": "adaptive"}},
            )
            yield _sse("run_failed", {"run_id": run_id, "error": str(exc)[:400]})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


@router.post("/runs/{run_id}/adaptive/approve")
async def approve_adaptive(
    run_id: str,
    body: dict[str, Any] = Body(default={}),
    user=Depends(get_current_user),
):
    """Approve one or more tool calls and resume the graph.

    Body: {"decisions": {"<tool_call_id>": "approve"|"reject", ...}}
    If ``decisions`` is omitted, approve all pending calls in the run.
    """
    check_rate_limit(f"agent:{user['id']}", settings.rate_limit_agent_per_minute)
    decisions = (body or {}).get("decisions")
    if not decisions:
        # approve-all shortcut
        run = await get_db().agent_runs.find_one(
            {"_id": ObjectId(run_id), "user_id": user["id"]})
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        pending = (run.get("pending_approval") or {}).get("proposals") or []
        decisions = {p["tool_call_id"]: "approve" for p in pending}
    return await _resume_run(run_id=run_id, user_id=user["id"], decisions=decisions)


@router.post("/runs/{run_id}/adaptive/reject")
async def reject_adaptive(
    run_id: str,
    body: dict[str, Any] = Body(default={}),
    user=Depends(get_current_user),
):
    """Reject all pending tool calls and resume so the LLM can respond."""
    check_rate_limit(f"agent:{user['id']}", settings.rate_limit_agent_per_minute)
    run = await get_db().agent_runs.find_one(
        {"_id": ObjectId(run_id), "user_id": user["id"]})
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    pending = (run.get("pending_approval") or {}).get("proposals") or []
    decisions = {p["tool_call_id"]: "reject" for p in pending}
    return await _resume_run(run_id=run_id, user_id=user["id"], decisions=decisions)


@router.get("/adaptive/config")
async def adaptive_config(user=Depends(get_current_user)):
    return {
        "enabled": True,
        "default": adaptive.default_adaptive,
        "provider": adaptive.llm_provider,
        "model": adaptive.llm_model,
        "bound_tools": sorted(all_names()),
        "limits": {
            "max_iterations": adaptive.max_iterations,
            "max_tool_calls_total": adaptive.max_tool_calls_total,
            "max_calls_per_tool": adaptive.max_calls_per_tool,
        },
    }
