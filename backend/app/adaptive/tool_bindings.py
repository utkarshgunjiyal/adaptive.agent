"""Adaptive tool bindings.

Each tool the graph can call is described here as a plain OpenAI-format
function-tool schema. The executors themselves live in the existing
``app/tools/*`` modules and are unchanged.

Phase 1 binds a single tool: ``search_document_chunks``. This isolates
the "one adaptive tool round" acceptance test from network-dependent
tools. arXiv / Tavily / preference-write tools are bound in Phase 2+.

Tool schemas are OpenAI-compatible (LiteLLM routes them to Anthropic's
tool_use format automatically) so this file is provider-neutral.
"""

from __future__ import annotations

from typing import Any, Callable

from app.tools.document_search import search_document_chunks

# --------------------------------------------------------------------------
# Public tool schemas (OpenAI function-tool format)
# --------------------------------------------------------------------------

_SEARCH_DOCUMENT_CHUNKS_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_document_chunks",
        "description": (
            "Semantic hybrid search across the user's uploaded PDFs. "
            "Returns text chunks with document filename and page number. "
            "Use this when the user asks about content in their uploaded "
            "documents. Do NOT invent a chunk — only cite what the tool "
            "returns."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query. Use the user's phrasing.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "How many chunks to return (1-12).",
                    "minimum": 1,
                    "maximum": 12,
                    "default": 6,
                },
                "document_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional list of document IDs to restrict the "
                        "search to. Omit to search all the user's documents."
                    ),
                },
            },
            "required": ["query"],
        },
    },
}


# --------------------------------------------------------------------------
# Registry: tool_name -> (schema, async executor callable)
# --------------------------------------------------------------------------

class ToolBinding:
    """One tool bound to the adaptive runtime.

    ``executor`` is the async callable in ``app/tools/*`` that already
    returns the internal ``{summary, evidence}`` shape. The normalizer in
    ``app.adaptive.normalize`` handles the envelope so tool modules stay
    unchanged.
    """

    def __init__(
        self,
        *,
        name: str,
        schema: dict[str, Any],
        executor: Callable[..., Any],
    ) -> None:
        self.name = name
        self.schema = schema
        self.executor = executor


_PHASE1_BINDINGS: list[ToolBinding] = [
    ToolBinding(
        name="search_document_chunks",
        schema=_SEARCH_DOCUMENT_CHUNKS_SCHEMA,
        executor=search_document_chunks,
    ),
]


def get_phase1_bindings() -> list[ToolBinding]:
    return list(_PHASE1_BINDINGS)


def get_binding(name: str) -> ToolBinding | None:
    for b in _PHASE1_BINDINGS:
        if b.name == name:
            return b
    return None


def all_tool_schemas() -> list[dict[str, Any]]:
    return [b.schema for b in _PHASE1_BINDINGS]


def bound_tool_names() -> set[str]:
    return {b.name for b in _PHASE1_BINDINGS}
