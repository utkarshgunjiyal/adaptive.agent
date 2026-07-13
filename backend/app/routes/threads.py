"""Thread + message routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import get_current_user
from app.models import MessagePublic, ThreadCreateRequest, ThreadPublic
from app.services import thread_service

router = APIRouter(prefix="/api/threads", tags=["threads"])


def _thread_public(t: dict) -> ThreadPublic:
    return ThreadPublic(
        id=str(t["_id"]),
        title=t.get("title") or "New thread",
        created_at=t["created_at"],
        updated_at=t["updated_at"],
        message_count=t.get("message_count", 0),
    )


def _message_public(m: dict) -> MessagePublic:
    return MessagePublic(
        id=str(m["_id"]),
        role=m["role"],
        content=m.get("content", ""),
        created_at=m["created_at"],
        citations=m.get("citations", []),
        tool_badges=m.get("tool_badges", []),
        run_id=m.get("run_id"),
    )


@router.get("", response_model=list[ThreadPublic])
async def list_threads(user=Depends(get_current_user)):
    rows = await thread_service.list_threads(user["id"], limit=100)
    return [_thread_public(t) for t in rows]


@router.post("", response_model=ThreadPublic, status_code=201)
async def create_thread(payload: ThreadCreateRequest, user=Depends(get_current_user)):
    t = await thread_service.create_thread(user["id"], payload.title)
    return _thread_public(t)


@router.get("/{thread_id}", response_model=ThreadPublic)
async def get_thread(thread_id: str, user=Depends(get_current_user)):
    t = await thread_service.get_thread(user["id"], thread_id)
    if not t:
        raise HTTPException(status_code=404, detail="Thread not found")
    return _thread_public(t)


@router.get("/{thread_id}/messages", response_model=list[MessagePublic])
async def list_messages(
    thread_id: str,
    user=Depends(get_current_user),
    limit: int = Query(200, ge=1, le=500),
):
    t = await thread_service.get_thread(user["id"], thread_id)
    if not t:
        raise HTTPException(status_code=404, detail="Thread not found")
    rows = await thread_service.list_messages(user["id"], thread_id, limit=limit)
    return [_message_public(m) for m in rows]
