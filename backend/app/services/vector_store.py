"""MongoDB-backed vector store.

Chunks are stored in ``db.chunks`` with:

* ``user_id`` — owner (query-time scope)
* ``document_id`` — parent PDF
* ``chunk_id`` — deterministic index within the document
* ``page`` — 1-indexed PDF page number the chunk primarily belongs to
* ``text`` — the raw chunk text
* ``embedding`` — pre-computed float32 vector
* ``created_at``

Search cosine-similarity is done in-process because the preview environment
does not provide Qdrant. The Docker Compose stack ships with real Qdrant and
you can swap the ``search`` function with a Qdrant client without touching
any callers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db import get_db
from app.services.embeddings import cosine, embed_text


async def upsert_chunks(*, user_id: str, document_id: str, filename: str,
                        chunks: list[dict[str, Any]]) -> int:
    """Insert (or replace) all chunks for a document. Idempotent by
    (user_id, document_id) — old chunks for this document are deleted first."""
    db = get_db()
    await db.chunks.delete_many({"user_id": user_id, "document_id": document_id})
    if not chunks:
        return 0
    now = datetime.now(timezone.utc)
    docs = [
        {
            "user_id": user_id,
            "document_id": document_id,
            "filename": filename,
            "chunk_id": i,
            "page": c.get("page", 1),
            "text": c["text"],
            "embedding": c["embedding"],
            "created_at": now,
        }
        for i, c in enumerate(chunks)
    ]
    await db.chunks.insert_many(docs)
    return len(docs)


async def search(*, user_id: str, query: str, top_k: int = 6,
                 document_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Return the top-k chunks for ``query`` scoped to a user (and optionally
    a subset of their documents). Cosine similarity done in Python — fine for
    thousands of chunks per user; swap in Qdrant for millions."""
    db = get_db()
    q = {"user_id": user_id}
    if document_ids:
        q["document_id"] = {"$in": document_ids}

    query_vec = embed_text(query)
    cursor = db.chunks.find(q, {"text": 1, "embedding": 1, "page": 1,
                                "document_id": 1, "filename": 1, "chunk_id": 1})

    scored: list[tuple[float, dict[str, Any]]] = []
    async for row in cursor:
        score = cosine(query_vec, row.get("embedding", []))
        scored.append((score, row))

    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[dict[str, Any]] = []
    for score, row in scored[:top_k]:
        out.append(
            {
                "document_id": row["document_id"],
                "filename": row["filename"],
                "page": row.get("page", 1),
                "chunk_id": row.get("chunk_id", 0),
                "text": row["text"],
                "score": round(float(score), 4),
            }
        )
    return out


async def count_chunks(user_id: str, document_id: str) -> int:
    return await get_db().chunks.count_documents(
        {"user_id": user_id, "document_id": document_id}
    )
