"""Adaptive agent SSE endpoint.

POST /api/agent/run/adaptive/stream

Streams the adaptive graph's activity as Server-Sent Events. The event
vocabulary is a superset of the legacy /api/agent/run/stream so the
frontend can render both paths without conditional code.

Emitted events (Phase 1):
  - run_started          {run_id, thread_id, user_message_id}
  - llm_thinking         {iteration}
  - tool_started         {tool_call_id, tool_id, arguments}
  - tool_completed       {tool_call_id, tool_id, status, evidence_count,
                          duration_ms}
  - evidence_added       {count, items}
  - answer_delta         {text}      (Phase 1 emits ONE delta at finalize;
                                      token-level deltas come in Phase 2)
  - run_completed        {run_id, answer, citations, duration_ms,
                          tool_badges}
  - run_failed           {run_id, error}
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.adaptive.config import adaptive
from app.adaptive.graph import build_graph, get_saver
from app.adaptive.tool_bindings import bound_tool_names
from app.auth import check_rate_limit, get_current_user
from app.config import settings
from app.db import get_db
from app.models import AgentRunRequest
from app.services import agent as agent_svc
from app.services import thread_service

log = logging.getLogger("runner.route.adaptive")

router = APIRouter(prefix="/api/agent", tags=["adaptive-agent"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


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
    # Mark this run as adaptive up front so the UI can distinguish.
    await get_db().agent_runs.update_one(
        {"_id": ObjectId(run_id)},
        {"$set": {"runtime": "adaptive", "status": "executing",
                  "selected_tools": sorted(bound_tool_names())}},
    )

    request_id = uuid.uuid4().hex
    log.info("adaptive.run started run=%s user=%s thread=%s request=%s",
             run_id, user_id, thread_id, request_id)

    async def event_source() -> AsyncIterator[str]:
        started = datetime.now(timezone.utc)
        try:
            yield _sse("run_started", {
                "run_id": run_id, "thread_id": thread_id,
                "user_message_id": str(user_msg["_id"]),
                "runtime": "adaptive",
            })

            saver = await get_saver()
            graph = build_graph(checkpointer=saver)
            config = {"configurable": {"thread_id": f"run:{run_id}"}}
            input_state = {
                "run_id": run_id,
                "user_id": user_id,
                "thread_id": thread_id,
                "request_id": request_id,
                "user_message": payload.message,
                "document_ids": payload.document_ids or [],
            }

            emitted_tools: set[str] = set()
            emitted_evidence = 0
            last_iteration = -1

            # astream_events wraps every node output. We surface node-level
            # events into the SSE stream in the order they happen.
            async for chunk in graph.astream(
                input_state, config=config, stream_mode="updates",
            ):
                # chunk is {node_name: node_output_dict}
                for node_name, delta in chunk.items():
                    if not isinstance(delta, dict):
                        continue

                    # llm_thinking on new agent iteration
                    if node_name == "agent":
                        iteration = delta.get("iterations", last_iteration + 1)
                        if iteration != last_iteration:
                            last_iteration = iteration
                            yield _sse("llm_thinking", {"iteration": iteration})

                    # tool node emissions
                    if node_name == "tools":
                        tool_log = delta.get("tool_calls_log") or []
                        for entry in tool_log:
                            call_id = entry.get("id")
                            if call_id and call_id not in emitted_tools:
                                emitted_tools.add(call_id)
                                yield _sse("tool_started", {
                                    "tool_call_id": call_id,
                                    "tool_id": entry.get("tool_id"),
                                    "arguments": entry.get("arguments") or {},
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
                        new_evidence = delta.get("evidence") or []
                        if new_evidence:
                            emitted_evidence += len(new_evidence)
                            yield _sse("evidence_added", {
                                "count": len(new_evidence),
                                "total": emitted_evidence,
                                "items": new_evidence[:20],
                            })

                    if node_name == "finalize":
                        answer = delta.get("final_answer") or ""
                        if answer:
                            yield _sse("answer_delta", {"text": answer})

            # Read the persisted run record for the final SSE frame.
            row = await get_db().agent_runs.find_one({"_id": ObjectId(run_id)})
            answer = (row or {}).get("answer") or ""
            citations = (row or {}).get("citations") or []
            tool_badges = sorted({(c.get("source_type") or "context")
                                  for c in citations})
            duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            # Guarantee a persisted duration.
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

        except asyncio.CancelledError:
            log.info("adaptive run %s cancelled by client", run_id)
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
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/adaptive/config")
async def adaptive_config(user=Depends(get_current_user)):
    """Frontend feature-flag lookup + bound tools."""
    return {
        "enabled": True,
        "default": adaptive.default_adaptive,
        "provider": adaptive.llm_provider,
        "model": adaptive.llm_model,
        "bound_tools": sorted(bound_tool_names()),
        "limits": {
            "max_iterations": adaptive.max_iterations,
            "max_tool_calls_total": adaptive.max_tool_calls_total,
            "max_calls_per_tool": adaptive.max_calls_per_tool,
        },
    }
