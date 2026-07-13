"""Internal document retrieval tools."""

from __future__ import annotations

from typing import Any

from bson import ObjectId

from app.db import get_db
from app.models import ToolBadge
from app.services.vector_store import search as vector_search


async def search_document_chunks(
    *,
    user_id: str,
    query: str,
    document_ids: list[str] | None = None,
    top_k: int = 6,
    **_: Any,
) -> dict[str, Any]:
    """Semantic search over the user's PDF chunks."""
    top_k = max(1, min(int(top_k or 6), 20))
    scoped_docs: list[str] | None = None
    if document_ids:
        scoped_docs = [str(d) for d in document_ids]

    hits = await vector_search(user_id=user_id, query=query, top_k=top_k, document_ids=scoped_docs)

    evidence = [
        {
            "source_type": ToolBadge.PRIVATE_DOC.value,
            "title": f"{h['filename']} — page {h['page']}",
            "snippet": h["text"][:600],
            "document_id": h["document_id"],
            "filename": h["filename"],
            "page": h["page"],
            "score": h["score"],
        }
        for h in hits
    ]
    return {
        "summary": f"Retrieved {len(evidence)} chunk(s) from user documents matching '{query[:80]}'.",
        "evidence": evidence,
    }


async def get_document_summary(*, user_id: str, document_id: str, **_: Any) -> dict[str, Any]:
    db = get_db()
    try:
        doc = await db.documents.find_one({"_id": ObjectId(document_id), "user_id": user_id})
    except Exception:  # noqa: BLE001
        doc = None
    if not doc:
        return {"summary": "Document not found or not accessible.", "evidence": []}

    summary = doc.get("summary") or "(no summary yet — the document may still be processing)"
    return {
        "summary": summary,
        "evidence": [
            {
                "source_type": ToolBadge.PRIVATE_DOC.value,
                "title": f"{doc['filename']} — overall summary",
                "snippet": summary,
                "document_id": str(doc["_id"]),
                "filename": doc["filename"],
                "page": None,
            }
        ],
    }


async def list_user_documents(*, user_id: str, **_: Any) -> dict[str, Any]:
    db = get_db()
    cursor = db.documents.find(
        {"user_id": user_id},
        {"filename": 1, "status": 1, "page_count": 1, "created_at": 1},
    ).sort("created_at", -1)

    docs = []
    async for row in cursor:
        docs.append(
            {
                "document_id": str(row["_id"]),
                "filename": row.get("filename"),
                "status": row.get("status"),
                "page_count": row.get("page_count"),
            }
        )
    if not docs:
        summary = "You haven't uploaded any documents yet."
    else:
        summary = "Available documents: " + ", ".join(
            f"{d['filename']} ({d['status']})" for d in docs[:10]
        )
    return {
        "summary": summary,
        "evidence": [
            {
                "source_type": ToolBadge.CONTEXT.value,
                "title": f"{d['filename']} — {d['status']}",
                "snippet": f"{d.get('page_count') or '?'} pages · document_id={d['document_id']}",
            }
            for d in docs
        ],
        "documents": docs,
    }
