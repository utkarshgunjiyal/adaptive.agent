"""Thread + message + agent-run persistence helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from app.db import get_db


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- Threads ---------------------------------------------------------------

async def create_thread(user_id: str, title: str | None = None) -> dict[str, Any]:
    db = get_db()
    now = _now()
    doc = {
        "user_id": user_id,
        "title": (title or "New thread").strip()[:200],
        "created_at": now,
        "updated_at": now,
        "message_count": 0,
    }
    res = await db.threads.insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


async def list_threads(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    cursor = get_db().threads.find({"user_id": user_id}).sort("updated_at", -1).limit(limit)
    return [t async for t in cursor]


async def get_thread(user_id: str, thread_id: str) -> dict[str, Any] | None:
    try:
        return await get_db().threads.find_one({"_id": ObjectId(thread_id), "user_id": user_id})
    except Exception:  # noqa: BLE001
        return None


async def touch_thread(user_id: str, thread_id: str, *, title: str | None = None) -> None:
    update: dict[str, Any] = {"updated_at": _now()}
    if title:
        update["title"] = title[:200]
    await get_db().threads.update_one(
        {"_id": ObjectId(thread_id), "user_id": user_id},
        {"$set": update, "$inc": {"message_count": 1}},
    )


# ---- Messages --------------------------------------------------------------

async def next_seq(user_id: str, thread_id: str) -> int:
    doc = await get_db().messages.find_one(
        {"user_id": user_id, "thread_id": thread_id},
        sort=[("seq", -1)],
        projection={"seq": 1},
    )
    return int(doc["seq"]) + 1 if doc else 1


async def add_message(
    *,
    user_id: str,
    thread_id: str,
    role: str,
    content: str,
    citations: list[dict] | None = None,
    tool_badges: list[str] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    db = get_db()
    seq = await next_seq(user_id, thread_id)
    doc = {
        "user_id": user_id,
        "thread_id": thread_id,
        "seq": seq,
        "role": role,
        "content": content,
        "citations": citations or [],
        "tool_badges": tool_badges or [],
        "run_id": run_id,
        "created_at": _now(),
    }
    res = await db.messages.insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


async def list_messages(user_id: str, thread_id: str, limit: int = 200) -> list[dict[str, Any]]:
    cursor = get_db().messages.find(
        {"user_id": user_id, "thread_id": thread_id}
    ).sort("seq", 1).limit(limit)
    return [m async for m in cursor]


async def recent_messages(user_id: str, thread_id: str, limit: int = 8) -> list[dict[str, Any]]:
    cursor = get_db().messages.find(
        {"user_id": user_id, "thread_id": thread_id}
    ).sort("seq", -1).limit(limit)
    rows = [m async for m in cursor]
    rows.reverse()
    return rows
