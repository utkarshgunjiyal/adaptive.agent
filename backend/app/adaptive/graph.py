"""Graph builder and public runtime.

Builds a small LangGraph state graph:

    START -> load_context -> agent -> route
    route:
       - tool_calls present AND under limits -> tools -> agent
       - no tool_calls OR limits hit           -> finalize -> END

The graph runs with the MongoDB checkpointer so HITL interrupts (Phase 3)
survive restarts. Even in Phase 1 the checkpointer is wired so runs are
durably resumable.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.mongodb.aio import AsyncMongoDBSaver
from langgraph.graph import END, START, StateGraph

from app.adaptive.config import adaptive
from app.adaptive.nodes import agent, finalize, load_context, tools
from app.adaptive.state import AdaptiveState
from app.config import settings

log = logging.getLogger("runner.adaptive.builder")


_CHECKPOINT_DB_NAME = "runner_ai_langgraph"


def _route_after_agent(state: AdaptiveState) -> str:
    """Conditional edge decider."""
    msgs = state.get("messages") or []
    last = msgs[-1] if msgs else None
    has_calls = bool(last and last.get("role") == "assistant" and last.get("tool_calls"))

    if not has_calls:
        return "finalize"

    if state.get("iterations", 0) >= adaptive.max_iterations:
        log.info("adaptive: iteration cap hit; forcing finalize")
        return "finalize"
    if state.get("tool_call_count", 0) >= adaptive.max_tool_calls_total:
        log.info("adaptive: total tool-call cap hit; forcing finalize")
        return "finalize"

    return "tools"


def build_graph(*, checkpointer: Any | None = None):
    g = StateGraph(AdaptiveState)
    g.add_node("load_context", load_context)
    g.add_node("agent", agent)
    g.add_node("tools", tools)
    g.add_node("finalize", finalize)

    g.add_edge(START, "load_context")
    g.add_edge("load_context", "agent")
    g.add_conditional_edges(
        "agent",
        _route_after_agent,
        {"tools": "tools", "finalize": "finalize"},
    )
    g.add_edge("tools", "agent")
    g.add_edge("finalize", END)

    return g.compile(checkpointer=checkpointer)


# --------------------------------------------------------------------------
# Runtime facade with a Mongo checkpointer over the existing Mongo cluster
# --------------------------------------------------------------------------

_saver: AsyncMongoDBSaver | None = None
_saver_ctx = None


async def get_saver() -> AsyncMongoDBSaver:
    """Initialise (once) the official MongoDB checkpointer.

    The saver manages its own checkpoint collections and indexes; we only
    need to give it the connection string + a database name. We use a
    dedicated ``runner_ai_langgraph`` database on the existing cluster so
    application collections stay untouched.
    """
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
