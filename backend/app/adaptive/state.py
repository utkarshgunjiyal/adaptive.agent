"""Adaptive graph state.

The state is a plain dict compatible with LangGraph's ``StateGraph``.
Reducers append to lists so parallel tool calls in one round don't stomp
each other.

Message shape (OpenAI-compatible, provider-neutral):

    {"role": "system"|"user"|"assistant"|"tool",
     "content": str,
     "tool_calls": [                # only on role=assistant
        {"id": str, "type": "function",
         "function": {"name": str, "arguments": str}},
     ],
     "tool_call_id": str}          # only on role=tool
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


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

    # --- audit trail for the run record (append-only) ---
    tool_calls_log: Annotated[list[dict[str, Any]], operator.add]

    # --- loop controls ---
    iterations: int
    tool_call_count: int
    calls_per_tool: dict[str, int]

    # --- outputs ---
    final_answer: str
    stop_reason: str
