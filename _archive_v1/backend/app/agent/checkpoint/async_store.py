"""Async-safe checkpoint boundary (Phase 35).

The CheckpointStore Protocol is synchronous and performs blocking network I/O
(pymongo). Calling it directly on FastAPI's event loop would block the loop.
``AsyncCheckpointStoreAdapter`` is the single place that offloads each
synchronous store call to a worker thread via ``anyio.to_thread.run_sync`` —
thread offloading lives here, never scattered across routes.

The underlying domain store is unchanged. The in-memory store may also be
wrapped for interface consistency (offloading a trivial op is harmless). Domain
errors raised by the store (CheckpointNotFoundError, CheckpointConflictError, …)
propagate unchanged out of the awaited call.

Config-free: imports anyio + the store types only. No Mongo client, no settings.
"""

from functools import partial

import anyio

from app.agent.checkpoint.models import CheckpointRecord


class AsyncCheckpointStoreAdapter:
    """Async facade that runs a synchronous CheckpointStore off the event loop."""

    def __init__(self, store) -> None:
        self._store = store

    @property
    def store(self):
        return self._store

    async def save(
        self,
        run_context,
        runtime_outcome,
        pending_action: str | None = None,
        pending_reason: str | None = None,
        metadata: dict | None = None,
    ) -> CheckpointRecord:
        return await anyio.to_thread.run_sync(
            partial(
                self._store.save,
                run_context,
                runtime_outcome,
                pending_action=pending_action,
                pending_reason=pending_reason,
                metadata=metadata,
            )
        )

    async def load(self, checkpoint_id: str) -> CheckpointRecord:
        return await anyio.to_thread.run_sync(self._store.load, checkpoint_id)

    async def mark_resumed(self, checkpoint_id: str) -> CheckpointRecord:
        return await anyio.to_thread.run_sync(self._store.mark_resumed, checkpoint_id)

    async def cancel(self, checkpoint_id: str, reason: str | None = None) -> CheckpointRecord:
        return await anyio.to_thread.run_sync(partial(self._store.cancel, checkpoint_id, reason))
