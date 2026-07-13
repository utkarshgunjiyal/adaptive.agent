"""Research digest scheduler.

A user can save a *digest schedule* — a topic + a cadence (daily / weekly)
— and Runner.ai will run an agent for them on schedule. The results land
in ``db.digests`` and are surfaced in the workspace under a Digests tab.

In preview this uses APScheduler's ``AsyncIOScheduler`` running inside the
FastAPI process. In the Docker Compose "production" stack you'd swap this
for a Celery beat schedule pointed at the same digest-runner code.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from bson import ObjectId

from app.db import get_db
from app.services import agent as agent_svc
from app.services import thread_service

log = logging.getLogger("runner.digest")

_scheduler: AsyncIOScheduler | None = None
_JOB_PREFIX = "digest:"


CADENCE_INTERVALS = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
    # "test" cadence exists so we can prove the scheduler works within a
    # QA run; not exposed in the UI.
    "test": timedelta(seconds=30),
}


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


async def start() -> None:
    sched = get_scheduler()
    if not sched.running:
        sched.start()
    await _rehydrate()


async def stop() -> None:
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)


async def _rehydrate() -> None:
    db = get_db()
    cursor = db.digest_schedules.find({"enabled": True})
    count = 0
    async for row in cursor:
        _install_job(str(row["_id"]), row["user_id"], row["topic"], row["cadence"])
        count += 1
    log.info("digest: rehydrated %s schedule(s)", count)


def _install_job(schedule_id: str, user_id: str, topic: str, cadence: str) -> None:
    interval = CADENCE_INTERVALS.get(cadence)
    if not interval:
        return
    sched = get_scheduler()
    job_id = f"{_JOB_PREFIX}{schedule_id}"
    try:
        sched.remove_job(job_id)
    except Exception:  # noqa: BLE001
        pass
    sched.add_job(
        _run_digest,
        trigger=IntervalTrigger(seconds=int(interval.total_seconds())),
        args=[schedule_id, user_id, topic],
        id=job_id,
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=5),
    )


async def _remove_job(schedule_id: str) -> None:
    sched = get_scheduler()
    try:
        sched.remove_job(f"{_JOB_PREFIX}{schedule_id}")
    except Exception:  # noqa: BLE001
        pass


async def create_schedule(user_id: str, topic: str, cadence: str) -> dict[str, Any]:
    if cadence not in CADENCE_INTERVALS:
        raise ValueError(f"Unsupported cadence: {cadence}")
    now = datetime.now(timezone.utc)
    doc = {
        "user_id": user_id,
        "topic": topic.strip()[:200],
        "cadence": cadence,
        "enabled": True,
        "created_at": now,
        "last_run_at": None,
    }
    res = await get_db().digest_schedules.insert_one(doc)
    _install_job(str(res.inserted_id), user_id, doc["topic"], cadence)
    doc["_id"] = res.inserted_id
    return doc


async def list_schedules(user_id: str) -> list[dict[str, Any]]:
    cursor = get_db().digest_schedules.find({"user_id": user_id}).sort("created_at", -1)
    return [d async for d in cursor]


async def delete_schedule(user_id: str, schedule_id: str) -> bool:
    try:
        res = await get_db().digest_schedules.delete_one(
            {"_id": ObjectId(schedule_id), "user_id": user_id}
        )
    except Exception:  # noqa: BLE001
        return False
    if res.deleted_count:
        await _remove_job(schedule_id)
        return True
    return False


async def list_digests(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    cursor = get_db().digests.find({"user_id": user_id}).sort("created_at", -1).limit(limit)
    return [d async for d in cursor]


async def _run_digest(schedule_id: str, user_id: str, topic: str) -> None:
    """Executed by APScheduler. Runs an agent, saves a digest row."""
    log.info("digest run: schedule=%s user=%s topic=%s", schedule_id, user_id, topic)
    db = get_db()
    try:
        thread = await thread_service.create_thread(
            user_id, title=f"Digest · {topic}"[:80]
        )
        thread_id = str(thread["_id"])
        await thread_service.add_message(
            user_id=user_id, thread_id=thread_id, role="user",
            content=f"Prepare a research digest on: {topic}. "
                    f"Use recent arXiv papers and current web signals. "
                    f"Structure the digest as: 1) TL;DR (2 sentences), "
                    f"2) key developments with citations, 3) open questions.",
        )
        # Run a lightweight non-streaming agent execution.
        run_id = await agent_svc.create_run_record(
            user_id=user_id, thread_id=thread_id,
            message=f"Prepare research digest on: {topic}", document_ids=[],
        )
        shortlisted = agent_svc.select_tools(
            message=f"Prepare a research digest on: {topic}", has_documents=False
        )
        plan = await agent_svc.plan(
            message=f"Prepare a research digest on {topic}. "
                    f"Search arXiv and the web for recent developments.",
            tools=shortlisted, document_ids=[], has_docs=False,
            user_id=user_id, run_id=run_id,
        )
        tool_calls, evidence = await agent_svc.execute_plan(plan, user_id=user_id)
        answer = await agent_svc.synthesize(
            user_id=user_id, run_id=run_id,
            question=f"Prepare a research digest on {topic}",
            evidence=evidence, history=[],
        )
        badges = list({e.source_type.value for e in evidence}) if evidence else []
        completed_at = datetime.now(timezone.utc)

        from app.routes.agent import _serialize_evidence, _serialize_plan, _serialize_tool_calls  # noqa: E501
        await agent_svc.update_run(run_id, patch={
            "plan": _serialize_plan(plan),
            "tool_calls": _serialize_tool_calls(tool_calls),
            "evidence": _serialize_evidence(evidence),
            "answer": answer,
            "citations": _serialize_evidence(evidence),
            "status": "completed",
            "completed_at": completed_at,
        })
        await thread_service.add_message(
            user_id=user_id, thread_id=thread_id, role="assistant",
            content=answer,
            citations=_serialize_evidence(evidence),
            tool_badges=badges,
            run_id=run_id,
        )
        await db.digests.insert_one({
            "user_id": user_id,
            "schedule_id": schedule_id,
            "topic": topic,
            "thread_id": thread_id,
            "run_id": run_id,
            "answer_preview": (answer or "")[:400],
            "citation_count": len(evidence),
            "created_at": completed_at,
        })
        await db.digest_schedules.update_one(
            {"_id": ObjectId(schedule_id)},
            {"$set": {"last_run_at": completed_at}},
        )
        log.info("digest done: schedule=%s citations=%s", schedule_id, len(evidence))
    except Exception as exc:  # noqa: BLE001
        log.exception("digest run failed for schedule=%s: %s", schedule_id, exc)
