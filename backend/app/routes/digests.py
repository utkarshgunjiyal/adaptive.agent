"""Research digest routes.

A user creates a schedule (topic + cadence). APScheduler runs an agent on
that cadence and stores each output in ``db.digests`` for the user to
review under the Digests tab.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.services import digest as digest_svc

router = APIRouter(prefix="/api/digests", tags=["digests"])


class ScheduleCreate(BaseModel):
    topic: str = Field(min_length=3, max_length=200)
    cadence: Literal["hourly", "daily", "weekly"] = "daily"


def _schedule_public(row: dict) -> dict:
    return {
        "id": str(row["_id"]),
        "topic": row["topic"],
        "cadence": row["cadence"],
        "enabled": row.get("enabled", True),
        "created_at": row["created_at"],
        "last_run_at": row.get("last_run_at"),
    }


def _digest_public(row: dict) -> dict:
    return {
        "id": str(row["_id"]),
        "schedule_id": row.get("schedule_id"),
        "topic": row["topic"],
        "thread_id": row.get("thread_id"),
        "run_id": row.get("run_id"),
        "answer_preview": row.get("answer_preview") or "",
        "citation_count": row.get("citation_count", 0),
        "created_at": row["created_at"],
    }


@router.get("/schedules")
async def list_schedules(user=Depends(get_current_user)):
    rows = await digest_svc.list_schedules(user["id"])
    return [_schedule_public(r) for r in rows]


@router.post("/schedules", status_code=201)
async def create_schedule(payload: ScheduleCreate, user=Depends(get_current_user)):
    row = await digest_svc.create_schedule(user["id"], payload.topic, payload.cadence)
    return _schedule_public(row)


@router.delete("/schedules/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: str, user=Depends(get_current_user)):
    ok = await digest_svc.delete_schedule(user["id"], schedule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return None


@router.get("")
async def list_digests(user=Depends(get_current_user)):
    rows = await digest_svc.list_digests(user["id"], limit=50)
    return [_digest_public(r) for r in rows]
