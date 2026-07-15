"""Background worker: consumes the Redis job queue and runs ingestion.

Run as a dedicated process (Docker Compose `worker` service):

    python -m app.worker

It BRPOPs JSON job payloads from ``JOB_QUEUE_NAME`` (see
``app.services.job_queue`` for the producer side) and executes the same
``ingest.ingest_document`` pipeline the API runs inline in preview mode —
extraction, OCR fallback, chunking, embeddings, indexing, summary, and
document/job status updates all happen here. Job status lives in MongoDB, so
the API's job/document endpoints observe worker progress with no extra
plumbing.

Supported job types:

* ``document_ingest`` — {user_id, document_id, job_id}: full ingestion.
* ``ping`` — {reply_to, nonce}: deterministic health/test task; the worker
  LPUSHes ``{"pong": nonce}`` to ``reply_to``. Used by deploy smoke tests to
  prove the queue → worker → Redis roundtrip without touching user data.

Shutdown: SIGTERM/SIGINT finish the in-flight job, then exit 0.
Payloads contain ids only — no credentials or file contents are ever logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal

from app.config import settings
from app.services import ingest
from app.services.job_queue import get_redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("runner.worker")


async def handle_job(payload: dict) -> None:
    job_type = str(payload.get("type") or "document_ingest")

    if job_type == "ping":
        reply_to = payload.get("reply_to")
        if reply_to:
            await get_redis().lpush(
                str(reply_to), json.dumps({"pong": payload.get("nonce")})
            )
        log.info("job done type=ping")
        return

    if job_type == "document_ingest":
        user_id = str(payload.get("user_id") or "")
        document_id = str(payload.get("document_id") or "")
        job_id = str(payload.get("job_id") or "")
        if not (user_id and document_id and job_id):
            log.error("document_ingest payload missing ids; dropping")
            return
        await ingest.ingest_document(
            user_id=user_id, document_id=document_id, job_id=job_id
        )
        log.info("job done type=document_ingest document_id=%s", document_id)
        return

    log.error("unknown job type %r; dropping", job_type)


async def main() -> None:
    if not settings.redis_url:
        raise SystemExit(
            "REDIS_URL is not set — the worker requires the Redis job queue."
        )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    redis = get_redis()
    timeout = max(1, int(settings.worker_dequeue_timeout))
    log.info(
        "worker started queue=%s timeout=%ss", settings.job_queue_name, timeout
    )

    while not stop.is_set():
        try:
            item = await redis.brpop(settings.job_queue_name, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            log.warning("queue read failed (%s); retrying", type(exc).__name__)
            await asyncio.sleep(min(timeout, 5))
            continue
        if item is None:  # idle timeout — loop to re-check the stop flag
            continue
        _, raw = item
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("payload is not an object")
        except Exception:  # noqa: BLE001
            log.error("undecodable job payload; dropping")
            continue
        try:
            await handle_job(payload)
        except Exception:  # noqa: BLE001
            # ingest_document marks the job failed in Mongo on its own error
            # paths; this guard just keeps the worker loop alive.
            log.exception("job execution failed type=%r", payload.get("type"))

    log.info("worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
