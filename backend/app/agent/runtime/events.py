"""Runtime event model (Phase 32).

A structured, provider-agnostic event emitted while a runtime execution is
streamed. These are internal events feeding a future API (SSE/WebSocket); this
phase defines the model + the ordered vocabulary only.

Config-free: pydantic + enum. No LLM, no database, no application settings.
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class RuntimeEventType(str, Enum):
    RUNTIME_STARTED = "runtime_started"
    CONTEXT_STARTED = "context_started"
    CONTEXT_COMPLETED = "context_completed"
    RETRIEVAL_STARTED = "retrieval_started"
    RETRIEVAL_COMPLETED = "retrieval_completed"
    PLANNER_STARTED = "planner_started"
    PLANNER_COMPLETED = "planner_completed"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    EVALUATION_STARTED = "evaluation_started"
    EVALUATION_COMPLETED = "evaluation_completed"
    REPAIR_STARTED = "repair_started"
    REPAIR_COMPLETED = "repair_completed"
    ANSWER_STARTED = "answer_started"
    ANSWER_CHUNK = "answer_chunk"
    ANSWER_COMPLETED = "answer_completed"
    RUNTIME_COMPLETED = "runtime_completed"
    RUNTIME_FAILED = "runtime_failed"


class RuntimeEvent(BaseModel):
    """One event in a runtime stream. ``sequence`` is a monotonically increasing
    per-stream counter; ``data`` carries event-specific, API-safe fields."""

    model_config = ConfigDict(frozen=True)

    type: RuntimeEventType
    sequence: int
    run_id: str | None = None
    data: dict = Field(default_factory=dict)
