"""Thread share links.

A user can generate a read-only public link to any thread they own. The
link uses a random URL-safe token stored on the thread document so the
public endpoint doesn't need auth. Only messages + citations are returned;
the shared thread does not expose ownership or user identity.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_user
from app.db import get_db

router = APIRouter(prefix="/api", tags=["share"])


def _token() -> str:
    return secrets.token_urlsafe(24)


@router.post("/threads/{thread_id}/share")
async def enable_sharing(thread_id: str, user=Depends(get_current_user)):
    db = get_db()
    try:
        t = await db.threads.find_one({"_id": ObjectId(thread_id), "user_id": user["id"]})
    except Exception:  # noqa: BLE001
        t = None
    if not t:
        raise HTTPException(status_code=404, detail="Thread not found")

    token = t.get("share_token") or _token()
    await db.threads.update_one(
        {"_id": t["_id"]},
        {"$set": {"share_token": token, "share_enabled": True,
                  "shared_at": datetime.now(timezone.utc)}},
    )
    return {"share_token": token, "url_suffix": f"/share/{token}"}


@router.delete("/threads/{thread_id}/share", status_code=204)
async def disable_sharing(thread_id: str, user=Depends(get_current_user)):
    db = get_db()
    try:
        res = await db.threads.update_one(
            {"_id": ObjectId(thread_id), "user_id": user["id"]},
            {"$set": {"share_enabled": False}, "$unset": {"share_token": ""}},
        )
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="Thread not found") from None
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Thread not found")
    return None


@router.get("/share/{token}")
async def get_shared_thread(token: str):
    db = get_db()
    t = await db.threads.find_one({"share_token": token, "share_enabled": True})
    if not t:
        raise HTTPException(status_code=404, detail="Shared thread not found")

    thread_out = {
        "title": t.get("title") or "Shared research",
        "shared_at": t.get("shared_at"),
        "message_count": t.get("message_count", 0),
    }

    cursor = db.messages.find(
        {"user_id": t["user_id"], "thread_id": str(t["_id"])}
    ).sort("seq", 1)
    messages = []
    async for m in cursor:
        # Never leak user identifiers on a public read.
        messages.append({
            "role": m["role"],
            "content": m.get("content", ""),
            "citations": m.get("citations", []),
            "tool_badges": m.get("tool_badges", []),
            "created_at": m["created_at"],
        })

    return {"thread": thread_out, "messages": messages}
