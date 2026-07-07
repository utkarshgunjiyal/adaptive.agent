from datetime import datetime

from app.database import thread_summaries_collection
from app.services.message_service import get_messages_by_seq_range


async def get_thread_summary(user_id: str, thread_id: str) -> dict | None:
    return await thread_summaries_collection.find_one({
        "user_id": user_id,
        "thread_id": thread_id,
    })


async def create_empty_thread_summary(user_id: str, thread_id: str) -> dict:
    summary_doc = {
        "user_id": user_id,
        "thread_id": thread_id,
        "summary": "",
        "last_summarized_seq": 0,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    result = await thread_summaries_collection.insert_one(summary_doc)
    summary_doc["_id"] = result.inserted_id

    return summary_doc


async def should_update_thread_summary(
    user_id: str,
    thread_id: str,
    latest_seq: int,
    threshold: int = 20,
) -> bool:
    summary_doc = await get_thread_summary(
        user_id=user_id,
        thread_id=thread_id,
    )

    if not summary_doc:
        return False

    last_summarized_seq = summary_doc.get("last_summarized_seq", 0)
    unsummarized_count = latest_seq - last_summarized_seq

    return unsummarized_count >= threshold


async def update_thread_summary(
    user_id: str,
    thread_id: str,
    from_seq: int,
    to_seq: int,
) -> dict | None:
    summary_doc = await get_thread_summary(
        user_id=user_id,
        thread_id=thread_id,
    )

    if not summary_doc:
        return None

    old_summary = summary_doc.get("summary", "")

    messages = await get_messages_by_seq_range(
        user_id=user_id,
        thread_id=thread_id,
        from_seq=from_seq,
        to_seq=to_seq,
    )

    formatted_messages = "\n".join(
        f"{msg['role'].upper()} [{msg['seq']}]: {msg['content']}"
        for msg in messages
    )

    new_summary = (
        f"{old_summary}\n\n"
        f"Summary update for messages {from_seq}-{to_seq}:\n"
        f"{formatted_messages}"
    ).strip()

    await thread_summaries_collection.update_one(
        {
            "user_id": user_id,
            "thread_id": thread_id,
        },
        {
            "$set": {
                "summary": new_summary,
                "last_summarized_seq": to_seq,
                "updated_at": datetime.utcnow(),
            }
        },
    )

    return await get_thread_summary(
        user_id=user_id,
        thread_id=thread_id,
    )