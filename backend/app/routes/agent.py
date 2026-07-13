"""Agent run + SSE streaming routes."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.auth import check_rate_limit, get_current_user
from app.config import settings
from app.db import get_db
from app.models import (
    AgentPlan,
    AgentRunPublic,
    AgentRunRequest,
    EvidenceItem,
    PlanStep,
    ToolCallLog,
)
from app.services import agent as agent_svc
from app.services import thread_service
from app.services.thread_summary import get_context_for_run, maybe_update_summary

log = logging.getLogger("runner.route.agent")

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _serialize_evidence(items: list[EvidenceItem]) -> list[dict]:
    return [e.model_dump(mode="json") for e in items]


def _serialize_tool_calls(items: list[ToolCallLog]) -> list[dict]:
    return [c.model_dump(mode="json") for c in items]


def _serialize_plan(plan: AgentPlan | None) -> dict | None:
    return plan.model_dump(mode="json") if plan else None


def _run_public(row: dict) -> AgentRunPublic:
    plan = None
    if row.get("plan"):
        try:
            plan = AgentPlan(**row["plan"])
        except Exception:  # noqa: BLE001
            plan = None
    tool_calls = [ToolCallLog(**t) for t in row.get("tool_calls") or []]
    evidence = [EvidenceItem(**e) for e in row.get("evidence") or []]
    citations = [EvidenceItem(**e) for e in row.get("citations") or []]
    return AgentRunPublic(
        id=str(row["_id"]),
        thread_id=row["thread_id"],
        status=row["status"],
        created_at=row["created_at"],
        completed_at=row.get("completed_at"),
        plan=plan,
        tool_calls=tool_calls,
        evidence=evidence,
        answer=row.get("answer"),
        citations=citations,
        selected_tools=row.get("selected_tools") or [],
        error=row.get("error"),
        duration_ms=row.get("duration_ms"),
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# -- Helpers for finalising an approval-resume path -------------------------

async def _run_after_approval(
    run_id: str,
    user_id: str,
    thread_id: str,
    plan: AgentPlan,
    user_message_content: str,
    user_message_id: str,
) -> dict:
    """Execute a plan whose steps needed approval — called from the resume
    endpoint. Returns the final run document. Since the client is not
    streaming here we run non-streaming synthesis and persist everything."""
    started = datetime.now(timezone.utc)
    tool_calls, evidence = await agent_svc.execute_plan(plan, user_id=user_id)
    history = await get_context_for_run(user_id, thread_id)
    # Drop the current user message if it landed in the history window.
    history = [m for m in history if m.get("content") != user_message_content]
    answer = await agent_svc.synthesize(
        user_id=user_id, run_id=run_id,
        question=user_message_content, evidence=evidence, history=history,
    )
    badges = list({e.source_type.value for e in evidence}) if evidence else []
    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    completed_at = datetime.now(timezone.utc)

    await thread_service.add_message(
        user_id=user_id, thread_id=thread_id, role="assistant",
        content=answer, citations=_serialize_evidence(evidence),
        tool_badges=badges, run_id=run_id,
    )
    await thread_service.touch_thread(user_id, thread_id)
    await agent_svc.update_run(run_id, patch={
        "tool_calls": _serialize_tool_calls(tool_calls),
        "evidence": _serialize_evidence(evidence),
        "answer": answer,
        "citations": _serialize_evidence(evidence),
        "status": "completed",
        "completed_at": completed_at,
        "duration_ms": duration_ms,
    })
    return await agent_svc.get_run(user_id, run_id)


# -- Streaming endpoint -----------------------------------------------------

@router.post("/run/stream")
async def run_stream(payload: AgentRunRequest, user=Depends(get_current_user)):
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
        user_id=user_id, thread_id=thread_id, role="user", content=payload.message
    )
    await thread_service.touch_thread(user_id, thread_id)
    run_id = await agent_svc.create_run_record(
        user_id=user_id, thread_id=thread_id,
        message=payload.message, document_ids=payload.document_ids,
    )

    async def event_source():
        started = datetime.now(timezone.utc)
        try:
            yield _sse("run_started", {
                "run_id": run_id, "thread_id": thread_id,
                "user_message_id": str(user_msg["_id"]),
            })

            db = get_db()
            has_docs = await db.documents.count_documents({
                "user_id": user_id, "status": "ready",
            }) > 0

            # 1. Capability selection.
            shortlisted = agent_svc.select_tools(
                message=payload.message, has_documents=has_docs
            )
            selected_ids = [t.id for t in shortlisted]
            await agent_svc.update_run(run_id, patch={"selected_tools": selected_ids})
            yield _sse("capabilities_selected", {
                "tools": [
                    {"id": t.id, "name": t.name, "badge": t.badge.value,
                     "risk_level": t.risk_level, "requires_approval": t.requires_approval}
                    for t in shortlisted
                ]
            })

            # 2. LLM planner.
            yield _sse("planning", {"message": "Planning steps…"})
            plan = await agent_svc.plan(
                message=payload.message, tools=shortlisted,
                document_ids=payload.document_ids, has_docs=has_docs,
                user_id=user_id, run_id=run_id,
            )
            await agent_svc.update_run(run_id, patch={
                "plan": _serialize_plan(plan), "status": "executing",
            })
            yield _sse("plan_ready", {"plan": _serialize_plan(plan)})

            # 3. Policy check.
            ok, problems, needs_approval = agent_svc.validate_plan(plan)
            if not ok:
                await agent_svc.update_run(run_id, patch={
                    "status": "failed", "error": " ; ".join(problems),
                    "completed_at": datetime.now(timezone.utc),
                })
                yield _sse("run_failed", {"run_id": run_id, "error": " ; ".join(problems)})
                return
            if needs_approval:
                pending = [s.model_dump(mode="json") for s in needs_approval]
                await get_db().approval_requests.insert_one({
                    "run_id": run_id, "user_id": user_id,
                    "steps": pending, "created_at": datetime.now(timezone.utc),
                    "status": "pending",
                })
                await agent_svc.update_run(run_id, patch={
                    "status": "waiting_approval",
                    "pending_steps": pending,
                })
                yield _sse("waiting_approval", {"run_id": run_id, "steps": pending})
                # Note: the SSE ends here. The client will call
                # /agent/runs/{id}/approve which resumes the run
                # non-streaming and updates the persisted answer.
                return

            # 4. Execute plan (read-only steps).
            yield _sse("executing", {"message": "Running tools…"})
            tool_calls, evidence = await agent_svc.execute_plan(plan, user_id=user_id)
            await agent_svc.update_run(run_id, patch={
                "tool_calls": _serialize_tool_calls(tool_calls),
                "evidence": _serialize_evidence(evidence),
                "status": "synthesizing",
            })
            for c in tool_calls:
                yield _sse("tool_call", c.model_dump(mode="json"))
            yield _sse("evidence_ready", {
                "count": len(evidence),
                "items": _serialize_evidence(evidence)[:20],
            })

            # 5. Synthesize answer using incremental thread summary + recent.
            history = await get_context_for_run(user_id, thread_id)
            history = [m for m in history if m.get("content") != payload.message]

            parts: list[str] = []
            async for delta in agent_svc.synthesize_stream(
                user_id=user_id, run_id=run_id, question=payload.message,
                evidence=evidence, history=history,
            ):
                parts.append(delta)
                yield _sse("answer_delta", {"text": delta})
            answer_text = "".join(parts).strip()
            if not answer_text:
                answer_text = agent_svc._fallback_answer(evidence)  # noqa: SLF001

            citations = evidence
            badges = list({e.source_type.value for e in evidence}) if evidence else []
            duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

            await thread_service.add_message(
                user_id=user_id, thread_id=thread_id, role="assistant",
                content=answer_text,
                citations=_serialize_evidence(citations),
                tool_badges=badges,
                run_id=run_id,
            )
            await thread_service.touch_thread(user_id, thread_id)
            await agent_svc.update_run(run_id, patch={
                "answer": answer_text,
                "citations": _serialize_evidence(citations),
                "status": "completed",
                "completed_at": datetime.now(timezone.utc),
                "duration_ms": duration_ms,
            })
            yield _sse("run_completed", {
                "run_id": run_id, "answer": answer_text,
                "citations": _serialize_evidence(citations),
                "duration_ms": duration_ms,
                "tool_badges": badges,
            })

            # 6. Fold history into a rolling summary (best-effort).
            try:
                await maybe_update_summary(user_id, thread_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("thread summary update failed: %s", exc)

        except asyncio.CancelledError:
            log.info("run %s cancelled by client", run_id)
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("run %s failed", run_id)
            await agent_svc.update_run(run_id, patch={
                "status": "failed", "error": str(exc)[:400],
                "completed_at": datetime.now(timezone.utc),
            })
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


@router.get("/runs/{run_id}", response_model=AgentRunPublic)
async def get_run(run_id: str, user=Depends(get_current_user)):
    row = await agent_svc.get_run(user["id"], run_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_public(row)


@router.post("/runs/{run_id}/approve", response_model=AgentRunPublic)
async def approve_run(run_id: str, user=Depends(get_current_user)):
    row = await agent_svc.get_run(user["id"], run_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    if row["status"] != "waiting_approval":
        raise HTTPException(status_code=409, detail="Run is not waiting for approval")

    db = get_db()
    await db.approval_requests.update_many(
        {"run_id": run_id, "status": "pending"},
        {"$set": {"status": "approved", "resolved_at": datetime.now(timezone.utc)}},
    )
    await agent_svc.update_run(run_id, patch={
        "status": "executing",
        "approved_at": datetime.now(timezone.utc),
    })

    plan_dict = row.get("plan") or {}
    try:
        plan = AgentPlan(**plan_dict)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Stored plan is unreadable") from None
    # Find the user message for this run (last user message on the thread
    # prior to this run's creation).
    latest_user_msg = await db.messages.find_one(
        {"user_id": user["id"], "thread_id": row["thread_id"], "role": "user"},
        sort=[("seq", -1)],
    )
    umsg_content = row.get("message") or (latest_user_msg or {}).get("content", "")
    umsg_id = str((latest_user_msg or {}).get("_id", ""))

    final_row = await _run_after_approval(
        run_id=run_id, user_id=user["id"], thread_id=row["thread_id"],
        plan=plan, user_message_content=umsg_content,
        user_message_id=umsg_id,
    )
    return _run_public(final_row)


@router.post("/runs/{run_id}/reject", response_model=AgentRunPublic)
async def reject_run(run_id: str, user=Depends(get_current_user)):
    row = await agent_svc.get_run(user["id"], run_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    if row["status"] != "waiting_approval":
        raise HTTPException(status_code=409, detail="Run is not waiting for approval")

    db = get_db()
    await db.approval_requests.update_many(
        {"run_id": run_id, "status": "pending"},
        {"$set": {"status": "rejected", "resolved_at": datetime.now(timezone.utc)}},
    )
    reject_note = "The user rejected the proposed action. No write occurred."
    await thread_service.add_message(
        user_id=user["id"], thread_id=row["thread_id"], role="assistant",
        content=reject_note, run_id=run_id,
    )
    await thread_service.touch_thread(user["id"], row["thread_id"])
    await agent_svc.update_run(run_id, patch={
        "status": "failed", "answer": reject_note,
        "error": "Rejected by user.",
        "completed_at": datetime.now(timezone.utc),
    })
    return _run_public(await agent_svc.get_run(user["id"], run_id))
