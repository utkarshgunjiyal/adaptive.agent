"""Graph builder and public runtime.

    START
      -> load_context
      -> select_capabilities
      -> agent  <----------------------------------------\
      -> route:                                          |
           tool_calls -> policy_check -> maybe_reselect -/
           final     -> finalize -> END
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.mongodb.aio import AsyncMongoDBSaver
from langgraph.graph import END, START, StateGraph

from app.adaptive.config import adaptive
from app.adaptive.nodes import (
    agent,
    finalize,
    load_context,
    maybe_reselect,
    policy_check,
    select_capabilities,
)
from app.adaptive.state import AdaptiveState
from app.config import settings

log = logging.getLogger("runner.adaptive.builder")


_CHECKPOINT_DB_NAME = "runner_ai_langgraph"


def _route_after_agent(state: AdaptiveState) -> str:
    msgs = state.get("messages") or []
    last = msgs[-1] if msgs else None
    has_calls = bool(last and last.get("role") == "assistant" and last.get("tool_calls"))

    if not has_calls:
        return "finalize"

    if state.get("iterations", 0) >= adaptive.max_iterations:
        log.info("adaptive: iteration cap; forcing finalize")
        return "finalize"
    if state.get("tool_call_count", 0) >= adaptive.max_tool_calls_total:
        log.info("adaptive: total tool-call cap; forcing finalize")
        return "finalize"

    return "policy_check"


def build_graph(*, checkpointer: Any | None = None):
    g = StateGraph(AdaptiveState)
    g.add_node("load_context", load_context)
    g.add_node("select_capabilities", select_capabilities)
    g.add_node("agent", agent)
    g.add_node("policy_check", policy_check)
    g.add_node("maybe_reselect", maybe_reselect)
    g.add_node("finalize", finalize)

    g.add_edge(START, "load_context")
    g.add_edge("load_context", "select_capabilities")
    g.add_edge("select_capabilities", "agent")
    g.add_conditional_edges(
        "agent",
        _route_after_agent,
        {"policy_check": "policy_check", "finalize": "finalize"},
    )
    g.add_edge("policy_check", "maybe_reselect")
    g.add_edge("maybe_reselect", "agent")
    g.add_edge("finalize", END)

    return g.compile(checkpointer=checkpointer)


# --------------------------------------------------------------------------
# Runtime facade — MongoDB checkpointer
# --------------------------------------------------------------------------

_saver: AsyncMongoDBSaver | None = None
_saver_ctx = None


async def get_saver() -> AsyncMongoDBSaver:
    global _saver, _saver_ctx
    if _saver is None:
        _saver_ctx = AsyncMongoDBSaver.from_conn_string(
            conn_string=settings.mongo_url,
            db_name=_CHECKPOINT_DB_NAME,
        )
        _saver = await _saver_ctx.__aenter__()
    return _saver


async def shutdown_saver() -> None:
    global _saver, _saver_ctx
    if _saver_ctx is not None:
        try:
            await _saver_ctx.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
    _saver = None
    _saver_ctx = None
