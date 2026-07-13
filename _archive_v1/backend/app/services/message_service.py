from datetime import datetime

from app.database import messages_collection


async def save_message(
    user_id: str,
    thread_id: str,
    seq: int,
    role: str,
    content: str,
    metadata: dict | None = None,
) -> dict:
    message_doc = {
        "user_id": user_id,
        "thread_id": thread_id,
        "seq": seq,
        "role": role,
        "content": content,
        "metadata": metadata or {},
        "created_at": datetime.utcnow(),
    }

    result = await messages_collection.insert_one(message_doc)
    message_doc["_id"] = result.inserted_id

    return message_doc


async def get_recent_messages(
    user_id: str,
    thread_id: str,
    limit: int = 10,
) -> list[dict]:
    cursor = (
        messages_collection
        .find({
            "user_id": user_id,
            "thread_id": thread_id,
        })
        .sort("seq", -1)
        .limit(limit)
    )

    messages = []

    async for msg in cursor:
        messages.append(msg)

    return list(reversed(messages))

async def get_messages_by_seq_range(
    user_id: str,
    thread_id: str,
    from_seq: int,
    to_seq: int,
) -> list[dict]:
    cursor = (
        messages_collection
        .find({
            "user_id": user_id,
            "thread_id": thread_id,
            "seq": {
                "$gte": from_seq,
                "$lte": to_seq,
            },
        })
        .sort("seq", 1)
    )

    messages = []

    async for msg in cursor:
        messages.append(msg)

    return messages