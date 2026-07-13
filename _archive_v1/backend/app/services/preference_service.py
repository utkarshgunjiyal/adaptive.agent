"""User preferences — long-lived, user-scoped facts captured from
"remember that…" / "from now on…" / "I prefer…" style messages.

Deterministic: the trigger is detected by the existing behavior_router
(intent="preference"); this module extracts and persists the preference text.
No HITL — the save is applied directly.
"""

from datetime import datetime

from app.database import user_preferences_collection

# Leading phrases stripped to keep the stored preference concise. Order matters
# (longest / most specific first).
_LEAD_PHRASES = [
    "please remember that",
    "remember that",
    "from now on,",
    "from now on",
    "i prefer that",
    "i'd prefer",
    "i would prefer",
    "i prefer",
    "note that",
    "keep in mind that",
]


def extract_preference_text(message: str) -> str:
    text = message.strip()
    lowered = text.lower()
    for lead in _LEAD_PHRASES:
        if lowered.startswith(lead):
            stripped = text[len(lead):].strip(" ,:.-")
            return stripped or text
    return text


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


async def save_preference(
    user_id: str,
    message: str,
    source_seq: int | None = None,
) -> dict:
    """Persist a preference (idempotent on normalized text)."""
    text = extract_preference_text(message)
    normalized = _normalize(text)

    existing = await user_preferences_collection.find_one(
        {"user_id": user_id, "text_normalized": normalized}
    )
    if existing:
        return existing

    now = datetime.utcnow()
    doc = {
        "user_id": user_id,
        "text": text,
        "text_normalized": normalized,
        "source_seq": source_seq,
        "created_at": now,
        "updated_at": now,
    }
    result = await user_preferences_collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def get_preferences(user_id: str, limit: int = 5) -> list[dict]:
    if limit <= 0:
        return []
    cursor = (
        user_preferences_collection.find({"user_id": user_id})
        .sort([("created_at", -1), ("_id", -1)])  # _id tiebreak = stable recency
        .limit(limit)
    )
    return [pref async for pref in cursor]
