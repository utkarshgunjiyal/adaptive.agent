"""Redis-backed background job queue.

The API process pushes job payloads onto a Redis list (``JOB_QUEUE_NAME``)
and the dedicated worker process (``python -m app.worker``) pops and executes
them. This is the Docker Compose production path; the preview / development
default (``JOB_QUEUE_BACKEND=inline``) keeps running jobs as in-process
asyncio tasks and never touches Redis.

Payloads are small JSON documents — ids only, never file contents:

    {"type": "document_ingest", "user_id": ..., "document_id": ..., "job_id": ...}

Producer uses LPUSH and the worker BRPOPs, so jobs are processed FIFO.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config import settings

log = logging.getLogger("runner.job_queue")

_redis = None  # lazily-created shared asyncio Redis client


def enabled() -> bool:
    """True when jobs should be dispatched through Redis."""
    return (settings.job_queue_backend or "inline").strip().lower() == "redis" and bool(
        settings.redis_url
    )


def get_redis():
    """Shared asyncio Redis client (created on first use)."""
    global _redis
    if _redis is None:
        import redis.asyncio as aioredis

        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def enqueue(payload: dict[str, Any]) -> bool:
    """Push one job payload. Returns False (and logs) on any Redis failure so
    the caller can fall back to inline execution instead of losing the job."""
    try:
        await get_redis().lpush(settings.job_queue_name, json.dumps(payload))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("job enqueue failed (%s); falling back to inline", type(exc).__name__)
        return False


async def enqueue_ingest(*, user_id: str, document_id: str, job_id: str) -> bool:
    return await enqueue({
        "type": "document_ingest",
        "user_id": user_id,
        "document_id": document_id,
        "job_id": job_id,
    })
