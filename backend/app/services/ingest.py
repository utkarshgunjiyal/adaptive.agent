"""PDF ingestion pipeline.

Given a document row (already stored via ``storage.put_object``) this module:

1. Extracts text page-by-page with ``pypdf``.
2. Splits page text into overlapping chunks with sentence-friendly breaks.
3. Embeds each chunk (deterministic hashed embedding in preview).
4. Upserts chunks into MongoDB with (user_id, document_id) scoping.
5. Generates a per-document summary via gpt-5.2.
6. Updates the document + job records to ``ready`` (or ``failed``).

The pipeline is **idempotent**: rerunning it on the same ``document_id``
deletes and rewrites all chunks (see ``vector_store.upsert_chunks``) so a
retry cannot double-index a document.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pypdf import PdfReader

from app.config import settings
from app.db import get_db
from app.models import DocumentStatus
from app.services import storage
from app.services.embeddings import embed_texts
from app.services.llm import complete
from app.services.ocr import ocr_pages
from app.services.vector_store import upsert_chunks

log = logging.getLogger("runner.ingest")


# ---- Text extraction ------------------------------------------------------

def _extract_pages(data: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(data))
    if len(reader.pages) > settings.max_pages:
        raise ValueError(
            f"PDF has {len(reader.pages)} pages (max {settings.max_pages})"
        )
    pages: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            text = ""
        pages.append(text.strip())
    return pages


# ---- Chunking --------------------------------------------------------------

def _split_page(page_text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split a page into overlapping chunks, breaking on paragraph/sentence
    boundaries where possible so context is preserved."""
    if not page_text:
        return []
    if len(page_text) <= chunk_size:
        return [page_text]

    chunks: list[str] = []
    start = 0
    text = page_text
    while start < len(text):
        end = min(start + chunk_size, len(text))
        window = text[start:end]
        if end < len(text):
            # Try to break at the last paragraph or sentence boundary.
            for marker in ["\n\n", ". ", ".\n", "\n"]:
                idx = window.rfind(marker)
                if idx > int(chunk_size * 0.4):
                    end = start + idx + len(marker)
                    window = text[start:end]
                    break
        chunks.append(window.strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def _chunk_pages(pages: list[str]) -> list[dict[str, Any]]:
    all_chunks: list[dict[str, Any]] = []
    for page_no, page_text in enumerate(pages, start=1):
        for text in _split_page(page_text, settings.chunk_size, settings.chunk_overlap):
            all_chunks.append({"page": page_no, "text": text})
    return all_chunks


# ---- Summarizer -----------------------------------------------------------

_SUMMARY_SYSTEM = (
    "You are a careful research assistant. Summarize the user's PDF for a "
    "future retrieval agent. Return 4–7 sentences describing: what this "
    "document is about, its main sections, and any concrete claims/numbers "
    "worth remembering. Plain prose, no bullet points, no preamble."
)


async def _summarize(user_id: str, document_id: str, filename: str,
                     pages: list[str]) -> str:
    joined = "\n\n".join(p for p in pages if p)[:12000]
    if not joined.strip():
        return f"'{filename}' contains no extractable text (it may be a scanned PDF)."
    prompt = (
        f"Document filename: {filename}\n"
        f"Pages: {len(pages)}\n\n"
        f"--- PDF text (truncated) ---\n{joined}"
    )
    try:
        return (await complete(
            session_id=f"summary:{user_id}:{document_id}",
            system=_SUMMARY_SYSTEM,
            user=prompt,
            max_tokens=500,
        )).strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("summary failed for %s: %s", document_id, exc)
        return f"'{filename}' — {len(pages)} pages. (Automatic summary unavailable.)"


# ---- Public entry point ---------------------------------------------------

async def ingest_document(*, user_id: str, document_id: str, job_id: str) -> None:
    """Async background ingestion. Updates document + job status as it runs."""
    db = get_db()
    doc = await db.documents.find_one({"_id": ObjectId(document_id), "user_id": user_id})
    if not doc:
        log.error("ingest: document not found %s", document_id)
        return
    job_oid = ObjectId(job_id)
    now = datetime.now(timezone.utc)
    await db.documents.update_one(
        {"_id": doc["_id"]},
        {"$set": {"status": DocumentStatus.PROCESSING, "updated_at": now}},
    )
    await db.jobs.update_one(
        {"_id": job_oid},
        {
            "$set": {"status": DocumentStatus.PROCESSING, "started_at": now, "progress": 5},
            "$inc": {"attempt_count": 1},
        },
    )

    try:
        data = storage.get_object(user_id, doc["storage_key"])

        pages = _extract_pages(data)

        # OCR fallback: pages that yielded no text may be scanned images.
        empty_pages = [i + 1 for i, p in enumerate(pages) if not p]
        if empty_pages:
            log.info("ingest: %s empty page(s), attempting OCR", len(empty_pages))
            ocr_result = ocr_pages(data, empty_pages[:20])  # cap at 20 pages
            for page_no, text in ocr_result.items():
                pages[page_no - 1] = text
            if ocr_result:
                await db.documents.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"ocr_pages_extracted": len(ocr_result)}},
                )

        await db.jobs.update_one({"_id": job_oid}, {"$set": {"progress": 30}})

        chunk_records = _chunk_pages(pages)
        if not chunk_records:
            raise ValueError("PDF yielded no extractable text (scanned PDF?).")

        embeddings = embed_texts([c["text"] for c in chunk_records])
        for chunk, vec in zip(chunk_records, embeddings):
            chunk["embedding"] = vec

        await db.jobs.update_one({"_id": job_oid}, {"$set": {"progress": 65}})

        chunk_count = await upsert_chunks(
            user_id=user_id,
            document_id=document_id,
            filename=doc["filename"],
            chunks=chunk_records,
        )

        await db.jobs.update_one({"_id": job_oid}, {"$set": {"progress": 85}})

        summary = await _summarize(user_id, document_id, doc["filename"], pages)

        completed_at = datetime.now(timezone.utc)
        await db.documents.update_one(
            {"_id": doc["_id"]},
            {
                "$set": {
                    "status": DocumentStatus.READY,
                    "page_count": len(pages),
                    "chunk_count": chunk_count,
                    "summary": summary,
                    "error": None,
                    "updated_at": completed_at,
                }
            },
        )
        await db.jobs.update_one(
            {"_id": job_oid},
            {
                "$set": {
                    "status": DocumentStatus.READY,
                    "progress": 100,
                    "completed_at": completed_at,
                    "error": None,
                }
            },
        )
        log.info("ingest ok: document=%s chunks=%s pages=%s", document_id, chunk_count, len(pages))

    except Exception as exc:  # noqa: BLE001
        log.exception("ingest failed for %s", document_id)
        error_at = datetime.now(timezone.utc)
        error_msg = str(exc)[:400] or "Ingestion failed"
        await db.documents.update_one(
            {"_id": doc["_id"]},
            {"$set": {"status": DocumentStatus.FAILED, "error": error_msg, "updated_at": error_at}},
        )
        await db.jobs.update_one(
            {"_id": job_oid},
            {
                "$set": {
                    "status": DocumentStatus.FAILED,
                    "error": error_msg,
                    "completed_at": error_at,
                }
            },
        )
