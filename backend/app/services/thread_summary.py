"""Incremental thread summarizer.

Long chats blow the LLM context window. This service keeps a rolling
summary in ``db.thread_summaries`` with:

* ``user_id`` + ``thread_id`` — scope
* ``summary`` — text
* ``last_summarized_seq`` — highest message seq already folded in
* ``updated_at``

When ``recent_messages`` is called by the agent, it returns:
  1. The prior thread summary (if any) as a synthetic ``system``-role
     message. This preserves older context without re-sending it token by
     token.
  2. The last N raw messages so recency is verbatim.

Summarisation is triggered when ``unsummarized_count`` exceeds a threshold
so we don't call the LLM on every message.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.db import get_db
from app.services.llm import complete

log = logging.getLogger("runner.summary")

RECENT_KEEP = 6              # messages left verbatim
SUMMARISE_EVERY = 8          # summarise when this many new messages beyond last summary
MAX_SUMMARY_CHARS = 2000


_SYSTEM = (
    "You are the memory compressor for an AI research chat. You will receive "
    "an existing rolling summary (may be empty) plus a batch of new user + "
    "assistant messages. Produce an updated summary that:\n"
    " - stays under 400 words,\n"
    " - preserves stable facts, decisions, and referenced documents,\n"
    " - drops greetings/small-talk,\n"
    " - is neutral prose, no bullet points."
)


async def _summarise(existing: str, new_messages: list[dict[str, Any]],
                     user_id: str, thread_id: str) -> str:
    if not new_messages:
        return existing or ""
    joined = []
    for m in new_messages:
        role = m.get("role", "user")
        content = (m.get("content") or "").replace("\n", " ")[:1200]
        joined.append(f"{role.upper()}: {content}")
    prompt = (
        "EXISTING SUMMARY (may be empty):\n"
        f"{existing or '(empty)'}\n\n"
        "NEW MESSAGES SINCE LAST SUMMARY:\n"
        + "\n".join(joined)
        + "\n\nReturn the updated summary now."
    )
    try:
        text = await complete(
            session_id=f"tsum:{user_id}:{thread_id}",
            system=_SYSTEM,
            user=prompt,
            max_tokens=500,
        )
        return (text or "").strip()[:MAX_SUMMARY_CHARS]
    except Exception as exc:  # noqa: BLE001
        log.warning("thread summary LLM failed: %s", exc)
        return existing or ""


async def get_summary_doc(user_id: str, thread_id: str) -> dict[str, Any] | None:
    return await get_db().thread_summaries.find_one(
        {"user_id": user_id, "thread_id": thread_id}
    )


async def maybe_update_summary(user_id: str, thread_id: str) -> None:
    """Rolls the thread summary forward if enough new messages have accumulated."""
    db = get_db()
    existing = await get_summary_doc(user_id, thread_id)
    last_seq = existing["last_summarized_seq"] if existing else 0

    unsummarized = await db.messages.count_documents(
        {"user_id": user_id, "thread_id": thread_id, "seq": {"$gt": last_seq}}
    )
    if unsummarized < SUMMARISE_EVERY:
        return

    # Take everything from (last_seq+1) up to (max_seq - RECENT_KEEP) so we
    # never summarise messages that are still "recent context".
    max_doc = await db.messages.find_one(
        {"user_id": user_id, "thread_id": thread_id},
        sort=[("seq", -1)], projection={"seq": 1},
    )
    max_seq = int(max_doc["seq"]) if max_doc else 0
    upto = max_seq - RECENT_KEEP
    if upto <= last_seq:
        return

    cursor = db.messages.find(
        {"user_id": user_id, "thread_id": thread_id,
         "seq": {"$gt": last_seq, "$lte": upto}}
    ).sort("seq", 1)
    batch = [m async for m in cursor]
    if not batch:
        return

    prior = existing["summary"] if existing else ""
    updated = await _summarise(prior, batch, user_id, thread_id)
    now = datetime.now(timezone.utc)

    await db.thread_summaries.update_one(
        {"user_id": user_id, "thread_id": thread_id},
        {"$set": {
            "user_id": user_id,
            "thread_id": thread_id,
            "summary": updated,
            "last_summarized_seq": upto,
            "updated_at": now,
        }},
        upsert=True,
    )
    log.info("thread summary rolled forward: user=%s thread=%s upto_seq=%s",
             user_id, thread_id, upto)


async def get_context_for_run(user_id: str, thread_id: str) -> list[dict[str, Any]]:
    """Return (summary-as-system + last RECENT_KEEP messages) for the agent
    synthesizer. The current user message is expected to be excluded by the
    caller."""
    db = get_db()
    summary_doc = await get_summary_doc(user_id, thread_id)
    out: list[dict[str, Any]] = []
    if summary_doc and summary_doc.get("summary"):
        out.append(
            {"role": "system",
             "content": f"[Prior thread summary]: {summary_doc['summary']}"}
        )

    max_doc = await db.messages.find_one(
        {"user_id": user_id, "thread_id": thread_id},
        sort=[("seq", -1)], projection={"seq": 1},
    )
    max_seq = int(max_doc["seq"]) if max_doc else 0
    start_seq = max(1, max_seq - RECENT_KEEP + 1)
    cursor = db.messages.find(
        {"user_id": user_id, "thread_id": thread_id, "seq": {"$gte": start_seq}}
    ).sort("seq", 1)
    async for m in cursor:
        out.append({"role": m["role"], "content": m.get("content", "")})
    return out
