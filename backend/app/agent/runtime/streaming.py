"""Runtime streaming (Phase 32; Phase 38 true token streaming).

Exposes a runtime execution as an async stream of ``RuntimeEvent``s, without
changing any runtime decision, planning, or retrieval. ``RuntimeStreamer`` wraps
an injected orchestrator and adds ``run_stream()`` alongside the unchanged
``run()``.

Phase 38. Streaming is now *live*: the streamer runs ``orchestrator.run`` with a
``stream_sink`` and drains the events off a queue as the pipeline produces them.
Answer chunks are emitted as the provider yields them — not reconstructed after
the answer already exists. The streamer owns only the envelope: it emits
``runtime_started`` up front and the single terminal event
(``runtime_completed`` on success, ``runtime_failed`` on a raised error or a
provider-failure outcome) after ``run`` returns. Everything in between — context,
retrieval, planner, tools, answer_started/chunk/completed, evaluation, repair —
is emitted by the orchestrator in true pipeline order.

Config-free and fully injectable: no LLM, no database, no settings. Never
inspects planner/evaluation/repair internals beyond the API-safe metadata the
runtime already recorded.
"""

import asyncio
from collections.abc import AsyncIterator

from app.agent.runtime.events import RuntimeEvent, RuntimeEventType as E
from app.agent.runtime.outcome import RuntimeOutcome

_SENTINEL = object()


class _Sequencer:
    def __init__(self) -> None:
        self._n = 0

    def make(self, event_type: E, *, run_id=None, data=None) -> RuntimeEvent:
        event = RuntimeEvent(type=event_type, sequence=self._n, run_id=run_id, data=data or {})
        self._n += 1
        return event


class RuntimeStreamer:
    def __init__(self, orchestrator) -> None:
        self._orchestrator = orchestrator

    async def run_stream(
        self,
        user_request: str,
        user_id: str,
        thread_id: str | None = None,
        metadata: dict | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        seq = _Sequencer()
        yield seq.make(
            E.RUNTIME_STARTED,
            data={"user_request": user_request, "user_id": user_id, "thread_id": thread_id},
        )

        # The orchestrator emits pipeline events into this queue live via the
        # sink; the drain loop below yields them as they arrive, then the terminal
        # event is derived from how ``run`` finished.
        queue: asyncio.Queue = asyncio.Queue()

        async def sink(event_type: E, run_id, data: dict) -> None:
            await queue.put((event_type, run_id, data))

        outcome: dict = {}

        async def _drive() -> None:
            try:
                outcome["result"] = await self._orchestrator.run(
                    user_request, user_id, thread_id=thread_id, metadata=metadata,
                    stream_sink=sink,
                )
            except Exception as exc:  # noqa: BLE001 - surface as a terminal event
                outcome["error"] = exc
            finally:
                await queue.put(_SENTINEL)

        task = asyncio.create_task(_drive())
        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                event_type, run_id, data = item
                yield seq.make(event_type, run_id=run_id, data=data)
        finally:
            await task

        # Terminal event (never emitted mid-stream by the orchestrator).
        if "error" in outcome:
            exc = outcome["error"]
            yield seq.make(
                E.RUNTIME_FAILED,
                data={"error": str(exc), "error_type": type(exc).__name__},
            )
            return

        result = outcome["result"]
        # A provider failure the orchestrator converted into a FAILED outcome:
        # terminate with runtime_failed (API-safe metadata), never completed.
        if result.runtime_outcome == RuntimeOutcome.FAILED:
            yield seq.make(
                E.RUNTIME_FAILED,
                run_id=result.run_id,
                data={
                    "runtime_outcome": result.runtime_outcome.value,
                    "failure_stage": result.metadata.get("failure_stage"),
                    "error_code": result.metadata.get("error_code"),
                    "retryable": result.metadata.get("retryable"),
                    "reason": result.pending_reason,
                },
            )
            return

        yield seq.make(
            E.RUNTIME_COMPLETED,
            run_id=result.run_id,
            data={
                "runtime_outcome": result.runtime_outcome.value,
                "pending_action": result.pending_action,
                "pending_reason": result.pending_reason,
            },
        )
