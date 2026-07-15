"""Document upload + list + retry routes.

Files are validated, stored, then a Mongo job is created and dispatched. With
``JOB_QUEUE_BACKEND=inline`` (preview/default) ingestion runs as an in-process
``asyncio`` task. With ``JOB_QUEUE_BACKEND=redis`` (Docker Compose production
stack) the job payload is pushed onto the Redis queue and consumed by the
dedicated worker process (``python -m app.worker``). The API layer is
identical either way, and a Redis dispatch failure falls back to inline so an
upload is never silently dropped.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile

from app.auth import get_current_user
from app.config import settings
from app.db import get_db
from app.models import DocumentPublic, DocumentStatus, JobPublic, UploadResponse
from app.services import ingest, job_queue, storage

router = APIRouter(prefix="/api", tags=["documents"])
_PDF_MAGIC = b"%PDF-"


def _safe_filename(name: str | None) -> str:
    base = (name or "").split("/")[-1].split("\\")[-1].strip() or "upload.pdf"
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    return base[:200]


def _document_public(doc: dict) -> DocumentPublic:
    return DocumentPublic(
        id=str(doc["_id"]),
        filename=doc["filename"],
        size_bytes=doc["size_bytes"],
        status=doc["status"],
        page_count=doc.get("page_count"),
        chunk_count=doc.get("chunk_count"),
        summary=doc.get("summary"),
        error=doc.get("error"),
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


def _job_public(job: dict) -> JobPublic:
    return JobPublic(
        id=str(job["_id"]),
        document_id=str(job["document_id"]),
        status=job["status"],
        progress=job.get("progress", 0),
        attempt_count=job.get("attempt_count", 0),
        error=job.get("error"),
        created_at=job["created_at"],
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
    )


async def _kick_off_ingest(user_id: str, document_id: str, job_id: str) -> None:
    """Dispatch background ingestion: Redis queue when configured, else an
    in-process asyncio task (also the fallback if the enqueue fails)."""
    if job_queue.enabled():
        if await job_queue.enqueue_ingest(
            user_id=user_id, document_id=document_id, job_id=job_id
        ):
            return
    asyncio.create_task(ingest.ingest_document(
        user_id=user_id, document_id=document_id, job_id=job_id
    ))


@router.post("/documents/upload", response_model=UploadResponse, status_code=202)
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type != "application/pdf":
        raise HTTPException(status_code=415, detail="Only application/pdf is supported.")

    data = await file.read()
    size = len(data)
    if size == 0:
        raise HTTPException(status_code=400, detail="Empty file.")
    if size > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({size} bytes). Max is {settings.max_upload_bytes}.",
        )
    if not data.startswith(_PDF_MAGIC):
        raise HTTPException(status_code=415, detail="File does not look like a valid PDF.")

    filename = _safe_filename(file.filename)
    storage_key = f"{uuid.uuid4().hex}_{filename}"
    storage.put_object(user["id"], storage_key, data)

    db = get_db()
    now = datetime.now(timezone.utc)
    doc = {
        "user_id": user["id"],
        "filename": filename,
        "content_type": content_type,
        "size_bytes": size,
        "storage_key": storage_key,
        "status": DocumentStatus.QUEUED,
        "created_at": now,
        "updated_at": now,
        "summary": None,
        "page_count": None,
        "chunk_count": None,
        "error": None,
    }
    doc_res = await db.documents.insert_one(doc)
    document_id = str(doc_res.inserted_id)

    job = {
        "user_id": user["id"],
        "document_id": document_id,
        "status": DocumentStatus.QUEUED,
        "progress": 0,
        "attempt_count": 0,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "error": None,
    }
    job_res = await db.jobs.insert_one(job)
    job_id = str(job_res.inserted_id)

    background.add_task(_kick_off_ingest, user["id"], document_id, job_id)

    return UploadResponse(
        document_id=document_id, job_id=job_id, status=DocumentStatus.QUEUED
    )


@router.post("/documents/upload_bulk", status_code=202)
async def upload_documents_bulk(
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
    user=Depends(get_current_user),
):
    """Upload multiple PDFs at once. Each file is validated independently
    and either accepted (with document_id + job_id) or rejected (with
    reason). The response reports both lists — the client can update the
    upload queue UI accordingly."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")
    if len(files) > 10:
        raise HTTPException(status_code=413, detail="Max 10 files per request.")

    db = get_db()
    accepted: list[dict] = []
    rejected: list[dict] = []
    now = datetime.now(timezone.utc)

    for file in files:
        content_type = (file.content_type or "").split(";")[0].strip().lower()
        if content_type != "application/pdf":
            rejected.append({"filename": file.filename or "?",
                             "reason": "Only application/pdf is supported."})
            continue
        data = await file.read()
        size = len(data)
        if size == 0:
            rejected.append({"filename": file.filename or "?", "reason": "Empty file."})
            continue
        if size > settings.max_upload_bytes:
            rejected.append({"filename": file.filename or "?",
                             "reason": f"Too large ({size} bytes)."})
            continue
        if not data.startswith(_PDF_MAGIC):
            rejected.append({"filename": file.filename or "?",
                             "reason": "Not a valid PDF (magic bytes mismatch)."})
            continue

        filename = _safe_filename(file.filename)
        storage_key = f"{uuid.uuid4().hex}_{filename}"
        storage.put_object(user["id"], storage_key, data)

        doc = {
            "user_id": user["id"], "filename": filename,
            "content_type": content_type, "size_bytes": size,
            "storage_key": storage_key, "status": DocumentStatus.QUEUED,
            "created_at": now, "updated_at": now,
            "summary": None, "page_count": None,
            "chunk_count": None, "error": None,
        }
        doc_res = await db.documents.insert_one(doc)
        document_id = str(doc_res.inserted_id)
        job = {
            "user_id": user["id"], "document_id": document_id,
            "status": DocumentStatus.QUEUED, "progress": 0,
            "attempt_count": 0, "created_at": now,
            "started_at": None, "completed_at": None, "error": None,
        }
        job_res = await db.jobs.insert_one(job)
        job_id = str(job_res.inserted_id)
        background.add_task(_kick_off_ingest, user["id"], document_id, job_id)
        accepted.append({"document_id": document_id, "job_id": job_id,
                         "filename": filename, "status": DocumentStatus.QUEUED.value})

    return {"accepted": accepted, "rejected": rejected}


@router.get("/documents", response_model=list[DocumentPublic])
async def list_documents(user=Depends(get_current_user)):
    db = get_db()
    cursor = db.documents.find({"user_id": user["id"]}).sort("created_at", -1)
    return [_document_public(d) async for d in cursor]


@router.get("/documents/{document_id}", response_model=DocumentPublic)
async def get_document(document_id: str, user=Depends(get_current_user)):
    db = get_db()
    try:
        doc = await db.documents.find_one({"_id": ObjectId(document_id), "user_id": user["id"]})
    except Exception:  # noqa: BLE001
        doc = None
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return _document_public(doc)


@router.post("/documents/{document_id}/retry", status_code=202)
async def retry_document(
    document_id: str, background: BackgroundTasks, user=Depends(get_current_user)
):
    db = get_db()
    try:
        doc = await db.documents.find_one({"_id": ObjectId(document_id), "user_id": user["id"]})
    except Exception:  # noqa: BLE001
        doc = None
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc["status"] not in {DocumentStatus.FAILED, DocumentStatus.READY}:
        raise HTTPException(status_code=409, detail="Document is already being processed.")

    now = datetime.now(timezone.utc)
    job = {
        "user_id": user["id"],
        "document_id": document_id,
        "status": DocumentStatus.QUEUED,
        "progress": 0,
        "attempt_count": 0,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "error": None,
    }
    job_res = await db.jobs.insert_one(job)
    job_id = str(job_res.inserted_id)

    await db.documents.update_one(
        {"_id": doc["_id"]},
        {"$set": {"status": DocumentStatus.QUEUED, "error": None, "updated_at": now}},
    )
    background.add_task(_kick_off_ingest, user["id"], document_id, job_id)
    return {"job_id": job_id, "document_id": document_id}


@router.get("/jobs/{job_id}", response_model=JobPublic)
async def get_job(job_id: str, user=Depends(get_current_user)):
    db = get_db()
    try:
        job = await db.jobs.find_one({"_id": ObjectId(job_id), "user_id": user["id"]})
    except Exception:  # noqa: BLE001
        job = None
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_public(job)
