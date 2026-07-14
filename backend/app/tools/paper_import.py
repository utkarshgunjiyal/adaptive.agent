"""arXiv paper import — the HITL-gated adaptive tool.

Downloads a paper's PDF from arXiv and starts the existing Runner.ai
ingestion pipeline. Approval is enforced *in the backend* by the graph's
policy layer, not by the frontend. This module only performs the actual
import once policy has confirmed approval.
"""

from __future__ import annotations

import io
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.db import get_db
from app.models import DocumentStatus, ToolBadge
from app.services import ingest, storage

log = logging.getLogger("runner.tool.paper_import")

_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,6})(v\d+)?")


def _extract_arxiv_id(url_or_id: str) -> str | None:
    m = _ARXIV_ID_RE.search(url_or_id or "")
    return m.group(1) if m else None


def _pdf_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/pdf/{arxiv_id}.pdf"


async def import_arxiv_paper(
    *,
    user_id: str,
    arxiv_url: str,
    title: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Download the paper and start ingestion. Returns a summary + evidence.

    The graph's policy MUST have confirmed approval before this executor
    runs. The executor is registered as approval-required so this
    invariant is enforced structurally.
    """
    arxiv_id = _extract_arxiv_id(arxiv_url)
    if not arxiv_id:
        return {
            "summary": f"Could not parse arXiv id from: {arxiv_url!r}",
            "evidence": [],
            "error": True,
        }

    pdf_url = _pdf_url(arxiv_id)
    try:
        async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
            r = await client.get(pdf_url, headers={"User-Agent": "Runner.ai/1.0"})
            r.raise_for_status()
            data = r.content
    except Exception as exc:  # noqa: BLE001
        return {
            "summary": f"Failed to download arXiv paper {arxiv_id}: {exc}",
            "evidence": [],
            "error": True,
        }

    if not data.startswith(b"%PDF-"):
        return {
            "summary": f"arXiv returned a non-PDF response for {arxiv_id}.",
            "evidence": [],
            "error": True,
        }

    # Persist to storage + create document + job rows (same shape as upload).
    filename_base = title or arxiv_id
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename_base)[:120] + ".pdf"
    storage_key = f"{uuid.uuid4().hex}_{filename}"
    storage.put_object(user_id, storage_key, data)

    now = datetime.now(timezone.utc)
    db = get_db()
    doc = {
        "user_id": user_id,
        "filename": filename,
        "content_type": "application/pdf",
        "size_bytes": len(data),
        "storage_key": storage_key,
        "status": DocumentStatus.QUEUED,
        "created_at": now,
        "updated_at": now,
        "summary": None,
        "page_count": None,
        "chunk_count": None,
        "error": None,
        "origin": "arxiv",
        "arxiv_id": arxiv_id,
        "arxiv_url": arxiv_url,
    }
    doc_res = await db.documents.insert_one(doc)
    document_id = str(doc_res.inserted_id)
    job = {
        "user_id": user_id,
        "document_id": document_id,
        "status": DocumentStatus.QUEUED,
        "progress": 0,
        "attempt_count": 0,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "error": None,
        "origin": "arxiv_import",
    }
    job_res = await db.jobs.insert_one(job)
    job_id = str(job_res.inserted_id)

    # Kick off ingestion in the background (idempotent — vector_store
    # replaces chunks on re-run, so this never double-indexes).
    import asyncio
    asyncio.create_task(ingest.ingest_document(
        user_id=user_id, document_id=document_id, job_id=job_id,
    ))

    log.info("arxiv import kicked off user=%s arxiv=%s doc=%s job=%s",
             user_id, arxiv_id, document_id, job_id)

    return {
        "summary": (
            f"Import started for arXiv paper {arxiv_id}. "
            f"The PDF is queued for indexing (document_id={document_id}). "
            f"You can query it via search_document_chunks once processing "
            f"completes."
        ),
        "evidence": [
            {
                "source_type": ToolBadge.RESEARCH_PAPER.value,
                "title": f"arXiv:{arxiv_id} — import queued",
                "snippet": f"Filename: {filename} · {len(data)} bytes",
                "url": arxiv_url,
                "document_id": document_id,
                "arxiv_id": arxiv_id,
                "job_id": job_id,
            }
        ],
        "import": {
            "document_id": document_id,
            "job_id": job_id,
            "arxiv_id": arxiv_id,
            "filename": filename,
        },
    }
