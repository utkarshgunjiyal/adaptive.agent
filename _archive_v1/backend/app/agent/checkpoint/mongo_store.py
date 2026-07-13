"""Mongo-backed CheckpointStore (Phase 34).

Durable implementation of the existing (synchronous) ``CheckpointStore`` Protocol
so checkpoints survive process restarts, redeploys, and cross-worker resumes.

Why synchronous. The Protocol — and its call sites in ResumeCoordinator /
ResumeRuntime — are synchronous, and those runtime components must not change.
So this store is synchronous and takes a *synchronous* Mongo collection
(pymongo-style: ``insert_one`` / ``find_one`` / ``find_one_and_update`` return
values directly). ``find_one_and_update`` is atomic server-side regardless of
driver, which is what the concurrency guarantee below needs.

Reuses ``snapshot_run_context``, ``CheckpointRecord``, ``CheckpointStatus``, and
``RuntimeOutcome`` — no duplicated serialization. Config-free at import: no Mongo
client is created here; the collection is injected (or built lazily by
``build_mongo_checkpoint_store`` / ``mongo_collection_from_uri``). The default
unit suite injects a fake collection and needs no MONGO_URL.
"""

from datetime import datetime, timezone

from app.agent.checkpoint.models import CheckpointRecord, CheckpointStatus
from app.agent.checkpoint.store import (
    CheckpointConflictError,
    CheckpointError,
    CheckpointNotFoundError,
    NonCheckpointableOutcomeError,
    is_checkpointable,
    snapshot_run_context,
)
from app.agent.runtime.context import RunContext
from app.agent.runtime.outcome import RuntimeOutcome

DEFAULT_COLLECTION_NAME = "agent_checkpoints"

# (index keys, options). _id is already unique; the checkpoint_id index mirrors
# it for parity. No TTL — expiry is not modeled.
CHECKPOINT_INDEXES = [
    ([("checkpoint_id", 1)], {"unique": True, "name": "uniq_checkpoint_id"}),
    ([("run_id", 1)], {"name": "by_run_id"}),
    ([("user_id", 1), ("created_at", -1)], {"name": "by_user_created"}),
    ([("status", 1), ("updated_at", -1)], {"name": "by_status_updated"}),
]


def _to_document(record: CheckpointRecord) -> dict:
    return {
        "_id": record.checkpoint_id,
        "checkpoint_id": record.checkpoint_id,
        "run_id": record.run_id,
        "user_id": record.user_id,
        "thread_id": record.thread_id,
        "runtime_outcome": record.runtime_outcome.value,  # enum → string
        "pending_action": record.pending_action,
        "pending_reason": record.pending_reason,
        "run_context_snapshot": record.run_context_snapshot,
        "status": record.status.value,  # enum → string
        "created_at": record.created_at,  # BSON-native datetime
        "updated_at": record.updated_at,
        "metadata": record.metadata,
    }


def _from_document(document: dict) -> CheckpointRecord:
    try:
        return CheckpointRecord(
            checkpoint_id=document["checkpoint_id"],
            run_id=document["run_id"],
            user_id=document["user_id"],
            thread_id=document.get("thread_id"),
            runtime_outcome=RuntimeOutcome(document["runtime_outcome"]),
            pending_action=document.get("pending_action"),
            pending_reason=document.get("pending_reason"),
            run_context_snapshot=document.get("run_context_snapshot", {}),
            status=CheckpointStatus(document["status"]),
            created_at=document["created_at"],
            updated_at=document["updated_at"],
            metadata=document.get("metadata", {}),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise CheckpointError(f"invalid checkpoint record: {exc}") from exc


class MongoCheckpointStore:
    """CheckpointStore over a synchronous Mongo collection."""

    def __init__(self, collection, *, clock=None, id_factory=None) -> None:
        self._collection = collection
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        import uuid

        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)

    def save(
        self,
        run_context: RunContext,
        runtime_outcome: RuntimeOutcome,
        pending_action: str | None = None,
        pending_reason: str | None = None,
        metadata: dict | None = None,
    ) -> CheckpointRecord:
        if not is_checkpointable(runtime_outcome):
            raise NonCheckpointableOutcomeError(
                f"outcome '{runtime_outcome.value}' is terminal and not checkpointable"
            )
        now = self._clock()
        record = CheckpointRecord(
            checkpoint_id=self._id_factory(),
            run_id=run_context.run_id,
            user_id=run_context.user_id,
            thread_id=run_context.thread_id,
            runtime_outcome=runtime_outcome,
            pending_action=pending_action,
            pending_reason=pending_reason,
            run_context_snapshot=snapshot_run_context(run_context),
            status=CheckpointStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            metadata=dict(metadata or {}),
        )
        self._io(lambda: self._collection.insert_one(_to_document(record)))
        return record

    def load(self, checkpoint_id: str) -> CheckpointRecord:
        document = self._io(lambda: self._collection.find_one({"_id": checkpoint_id}))
        if document is None:
            raise CheckpointNotFoundError(f"no checkpoint '{checkpoint_id}'")
        return _from_document(document)

    def mark_resumed(self, checkpoint_id: str) -> CheckpointRecord:
        now = self._clock()
        # Atomic: only an ACTIVE checkpoint transitions to RESUMED. Two concurrent
        # resume requests cannot both succeed.
        updated = self._io(
            lambda: self._collection.find_one_and_update(
                {"_id": checkpoint_id, "status": CheckpointStatus.ACTIVE.value},
                {"$set": {"status": CheckpointStatus.RESUMED.value, "updated_at": now}},
                return_document=True,  # pymongo ReturnDocument.AFTER == True
            )
        )
        if updated is not None:
            return _from_document(updated)

        # The conditional update matched nothing: either missing or not active.
        existing = self._io(lambda: self._collection.find_one({"_id": checkpoint_id}))
        if existing is None:
            raise CheckpointNotFoundError(f"no checkpoint '{checkpoint_id}'")
        raise CheckpointConflictError(
            f"checkpoint '{checkpoint_id}' is not active (status={existing.get('status')})"
        )

    def cancel(self, checkpoint_id: str, reason: str | None = None) -> CheckpointRecord:
        now = self._clock()
        set_fields = {"status": CheckpointStatus.CANCELLED.value, "updated_at": now}
        if reason is not None:
            set_fields["metadata.cancel_reason"] = reason
        updated = self._io(
            lambda: self._collection.find_one_and_update(
                {"_id": checkpoint_id}, {"$set": set_fields}, return_document=True
            )
        )
        if updated is None:
            raise CheckpointNotFoundError(f"no checkpoint '{checkpoint_id}'")
        return _from_document(updated)

    # -- Internals -----------------------------------------------------------

    @staticmethod
    def _io(operation):
        """Run a collection operation, converting raw driver errors into a domain
        CheckpointError so they never leak to the route layer."""
        try:
            return operation()
        except CheckpointError:
            raise
        except Exception as exc:  # noqa: BLE001 - wrap raw Mongo/driver errors
            raise CheckpointError(f"checkpoint store I/O error: {exc}") from exc


def ensure_checkpoint_indexes(collection) -> None:
    """Create the checkpoint indexes on a synchronous Mongo collection.

    For an async (Motor) collection, mirror these specs and await create_index.
    """
    for keys, options in CHECKPOINT_INDEXES:
        collection.create_index(keys, **options)


def mongo_collection_from_uri(uri: str, database: str, collection: str = DEFAULT_COLLECTION_NAME):
    """Lazily build a synchronous pymongo collection. Imported here (not at module
    top) so the checkpoint package stays config-free / driver-free at import."""
    from pymongo import MongoClient  # lazy import

    return MongoClient(uri)[database][collection]


def build_mongo_checkpoint_store(collection, *, clock=None, id_factory=None) -> MongoCheckpointStore:
    """Composition-root helper: wrap an injected/lazily-resolved collection."""
    return MongoCheckpointStore(collection, clock=clock, id_factory=id_factory)
