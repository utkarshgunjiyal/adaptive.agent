"""Adaptive graph state."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def _replace(a: Any, b: Any) -> Any:  # simple replace reducer
    return b if b is not None else a


class AdaptiveState(TypedDict, total=False):
    # --- run-scoped identifiers ---
    run_id: str
    user_id: str
    thread_id: str
    request_id: str

    # --- inputs ---
    user_message: str
    document_ids: list[str]

    # --- conversation history (append-only) ---
    messages: Annotated[list[dict[str, Any]], operator.add]

    # --- normalized observations (append-only) ---
    observations: Annotated[list[dict[str, Any]], operator.add]
    evidence: Annotated[list[dict[str, Any]], operator.add]

    # --- audit trail (append-only) ---
    tool_calls_log: Annotated[list[dict[str, Any]], operator.add]
    call_fingerprints: Annotated[list[str], operator.add]

    # --- capability state (replace) ---
    bound_tools: set[str]
    reselection_count: int
    reselection_events: Annotated[list[dict[str, Any]], operator.add]

    # --- loop controls ---
    iterations: int
    tool_call_count: int
    calls_per_tool: dict[str, int]

    # --- outputs ---
    final_answer: str
    stop_reason: str
