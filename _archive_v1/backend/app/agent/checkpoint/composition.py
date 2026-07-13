"""Checkpoint backend selection (Phase 35).

Pure composition helper: given a backend name (and, for Mongo, a
lazily-resolved collection), return the configured CheckpointStore. Kept
config-free — it takes explicit arguments, so the application composition root
(main.py lifespan) reads settings and calls this; the routes never do.

Selecting "mongo" runs ``ensure_checkpoint_indexes`` once here (at
startup/selection time), not per request.
"""

from app.agent.checkpoint.mongo_store import (
    DEFAULT_COLLECTION_NAME,
    MongoCheckpointStore,
    ensure_checkpoint_indexes,
    mongo_collection_from_uri,
)
from app.agent.checkpoint.store import InMemoryCheckpointStore

SUPPORTED_BACKENDS = ("memory", "mongo")


def select_checkpoint_store(
    backend: str,
    *,
    mongo_uri: str | None = None,
    database: str | None = None,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    mongo_collection=None,
    ensure_indexes: bool = True,
):
    """Return the CheckpointStore for ``backend``.

    - "memory" → a fresh InMemoryCheckpointStore.
    - "mongo"  → MongoCheckpointStore over ``mongo_collection`` (injected for
      tests) or a lazily-built pymongo collection; indexes ensured once.
    Unsupported values raise ValueError.
    """

    normalized = (backend or "").strip().lower()
    if normalized == "memory":
        return InMemoryCheckpointStore()
    if normalized == "mongo":
        collection = mongo_collection
        if collection is None:
            collection = mongo_collection_from_uri(mongo_uri, database, collection_name)
        if ensure_indexes:
            ensure_checkpoint_indexes(collection)
        return MongoCheckpointStore(collection)
    raise ValueError(
        f"unsupported checkpoint backend {backend!r}; expected one of {list(SUPPORTED_BACKENDS)}"
    )
