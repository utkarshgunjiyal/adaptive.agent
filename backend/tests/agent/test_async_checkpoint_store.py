"""Phase 35 tests — AsyncCheckpointStoreAdapter (thread-offloaded boundary).

Config-free: an in-memory store behind the async adapter; the adapter must run
each synchronous store call in a worker thread (not on the event loop). No DB.
"""

import ast
import asyncio
import inspect
import threading

import pytest

from app.agent.checkpoint import async_store as async_module
from app.agent.checkpoint.async_store import AsyncCheckpointStoreAdapter
from app.agent.checkpoint.models import CheckpointStatus
from app.agent.checkpoint.store import (
    CheckpointNotFoundError,
    InMemoryCheckpointStore,
)
from app.agent.runtime.context import RunContext, WorkingContextItem
from app.agent.runtime.outcome import RuntimeOutcome


def run(coro):
    return asyncio.run(coro)


def waiting_rc():
    return RunContext.create("q", user_id="u", thread_id="t1",
                             working_context=[WorkingContextItem(source="thread_summary", content="prior")])


class ThreadRecordingStore(InMemoryCheckpointStore):
    """Records the thread each method executes on."""

    def __init__(self):
        super().__init__()
        self.threads = []

    def save(self, *a, **k):
        self.threads.append(("save", threading.get_ident()))
        return super().save(*a, **k)

    def load(self, *a, **k):
        self.threads.append(("load", threading.get_ident()))
        return super().load(*a, **k)

    def mark_resumed(self, *a, **k):
        self.threads.append(("mark_resumed", threading.get_ident()))
        return super().mark_resumed(*a, **k)

    def cancel(self, *a, **k):
        self.threads.append(("cancel", threading.get_ident()))
        return super().cancel(*a, **k)


# --------------------------------------------------------------------------- #
# Functional parity (adapter forwards to the store)
# --------------------------------------------------------------------------- #

def test_adapter_save_load_roundtrip():
    adapter = AsyncCheckpointStoreAdapter(InMemoryCheckpointStore())

    async def scenario():
        record = await adapter.save(waiting_rc(), RuntimeOutcome.WAITING_FOR_USER,
                                    pending_action="ask_user_for_clarification", pending_reason="need info")
        loaded = await adapter.load(record.checkpoint_id)
        return record, loaded

    record, loaded = run(scenario())
    assert loaded.checkpoint_id == record.checkpoint_id
    assert loaded.pending_action == "ask_user_for_clarification"
    assert loaded.status == CheckpointStatus.ACTIVE


def test_adapter_mark_resumed_and_cancel():
    store = InMemoryCheckpointStore()
    adapter = AsyncCheckpointStoreAdapter(store)

    async def scenario():
        r1 = await adapter.save(waiting_rc(), RuntimeOutcome.WAITING_FOR_USER)
        resumed = await adapter.mark_resumed(r1.checkpoint_id)
        r2 = await adapter.save(waiting_rc(), RuntimeOutcome.WAITING_FOR_APPROVAL)
        cancelled = await adapter.cancel(r2.checkpoint_id, reason="abandoned")
        return resumed, cancelled

    resumed, cancelled = run(scenario())
    assert resumed.status == CheckpointStatus.RESUMED
    assert cancelled.status == CheckpointStatus.CANCELLED
    assert cancelled.metadata["cancel_reason"] == "abandoned"


def test_adapter_propagates_domain_errors():
    adapter = AsyncCheckpointStoreAdapter(InMemoryCheckpointStore())
    with pytest.raises(CheckpointNotFoundError):
        run(adapter.load("missing"))


# --------------------------------------------------------------------------- #
# Thread offloading (I/O is NOT on the event loop thread)
# --------------------------------------------------------------------------- #

def test_store_calls_run_off_the_event_loop_thread():
    store = ThreadRecordingStore()
    adapter = AsyncCheckpointStoreAdapter(store)

    captured = {}

    async def scenario():
        captured["loop_thread"] = threading.get_ident()
        record = await adapter.save(waiting_rc(), RuntimeOutcome.WAITING_FOR_USER)
        await adapter.load(record.checkpoint_id)
        await adapter.mark_resumed(record.checkpoint_id)

    run(scenario())

    loop_thread = captured["loop_thread"]
    assert store.threads, "store methods should have been invoked"
    # every synchronous store call ran on a worker thread, never the loop thread
    for name, tid in store.threads:
        assert tid != loop_thread, f"{name} executed on the event loop thread"


# --------------------------------------------------------------------------- #
# Hygiene
# --------------------------------------------------------------------------- #

def test_no_config_or_mongo_client_at_import():
    tree = ast.parse(inspect.getsource(async_module))
    targets = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            targets += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            targets.append(node.module or "")
    for banned in ("app.config", "app.database", "pymongo", "motor"):
        assert not any(banned in t for t in targets), (banned, targets)
