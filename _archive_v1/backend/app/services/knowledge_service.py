"""Knowledge memory — user-scoped facts/notes for cross-thread recall.

Kept intentionally simple: an explicit store (save_knowledge) plus keyword-overlap
retrieval (search_knowledge). This avoids depending on a Mongo text index and
works identically in tests. Automatic extraction of "what is worth remembering"
from a conversation is deliberately out of scope for Phase 4 (that needs LLM
extraction / HITL); facts are added explicitly via the memory API.
"""

import re
from datetime import datetime

from app.database import knowledge_collection

# How many recent entries to consider when ranking by keyword overlap.
_SEARCH_POOL = 200


async def save_knowledge(
    user_id: str,
    text: str,
    source: str = "api",
    thread_id: str | None = None,
) -> dict:
    now = datetime.utcnow()
    doc = {
        "user_id": user_id,
        "text": text.strip(),
        "source": source,
        "thread_id": thread_id,
        "created_at": now,
        "updated_at": now,
    }
    result = await knowledge_collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def list_knowledge(user_id: str, limit: int = 50) -> list[dict]:
    cursor = (
        knowledge_collection.find({"user_id": user_id})
        .sort([("created_at", -1), ("_id", -1)])  # _id tiebreak = stable recency
        .limit(limit)
    )
    return [item async for item in cursor]


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}


async def search_knowledge(user_id: str, query: str, top_k: int = 5) -> list[dict]:
    """Rank a user's knowledge entries by keyword overlap with the query."""
    if top_k <= 0:
        return []

    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    pool = await list_knowledge(user_id, limit=_SEARCH_POOL)

    scored = []
    for item in pool:
        overlap = len(query_tokens & _tokenize(item.get("text", "")))
        if overlap > 0:
            scored.append((overlap, item))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:top_k]]
