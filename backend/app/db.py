"""MongoDB client + collection handles.

Every collection is scoped by ``user_id`` at the query level so a single
process serves multiple users without leaking data across accounts.
"""

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import settings

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.mongo_url)
    return _client


def get_db() -> AsyncIOMotorDatabase:
    global _db
    if _db is None:
        _db = get_client()[settings.db_name]
    return _db


async def ensure_indexes() -> None:
    db = get_db()

    await db.users.create_index("email", unique=True)

    await db.threads.create_index([("user_id", 1), ("updated_at", -1)])
    await db.messages.create_index([("user_id", 1), ("thread_id", 1), ("seq", 1)])
    await db.thread_summaries.create_index(
        [("user_id", 1), ("thread_id", 1)], unique=True
    )

    await db.documents.create_index([("user_id", 1), ("created_at", -1)])
    await db.jobs.create_index([("user_id", 1), ("created_at", -1)])
    await db.jobs.create_index([("document_id", 1)])

    # Vector chunks stored in Mongo with (user_id, document_id) scoping.
    await db.chunks.create_index([("user_id", 1), ("document_id", 1)])
    await db.chunks.create_index([("user_id", 1), ("document_id", 1), ("chunk_id", 1)])

    await db.agent_runs.create_index([("user_id", 1), ("created_at", -1)])
    await db.agent_runs.create_index([("user_id", 1), ("thread_id", 1)])
    await db.tool_calls.create_index([("run_id", 1)])
    await db.evidence_items.create_index([("run_id", 1)])
    await db.approval_requests.create_index([("run_id", 1)])

    await db.user_preferences.create_index([("user_id", 1), ("key", 1)], unique=True)


async def close_client() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
        _client = None
        _db = None
