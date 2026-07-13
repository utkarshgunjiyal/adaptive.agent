"""Tool registry.

Each capability the agent can call is registered here with:

* ``id`` — stable string used in plans and audit logs.
* ``name`` / ``description`` — human-readable, safe to show the LLM.
* ``kind`` — ``internal | api | mcp``.
* ``risk_level`` — ``read | write | sensitive``.
* ``requires_approval`` — read-only tools run automatically; anything else
  must be approved by the user before executing.
* ``keywords`` — deterministic hints for the capability selector so we do
  NOT hand every tool spec to the planner LLM.
* ``badge`` — how retrieved evidence is labelled in the UI (private_doc /
  research_paper / web_source / context).
* ``available`` — a callable that resolves at request time (a tool with a
  missing API key is registered but ``available()`` returns ``False``, and
  the capability selector filters it out).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

from app.config import settings
from app.models import ToolBadge


@dataclass
class ToolSpec:
    id: str
    name: str
    description: str
    kind: str  # internal | api | mcp
    risk_level: str  # read | write | sensitive
    requires_approval: bool
    keywords: list[str]
    badge: ToolBadge
    typical_questions: list[str]
    executor: Callable[..., Awaitable[dict]]
    is_available: Callable[[], bool] = field(default=lambda: True)
    unavailable_reason: str = "Not configured"

    @property
    def available(self) -> bool:
        return bool(self.is_available())


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.id] = spec

    def get(self, tool_id: str) -> ToolSpec | None:
        return self._tools.get(tool_id)

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def enabled_read_tools(self) -> list[ToolSpec]:
        return [t for t in self._tools.values() if t.risk_level == "read" and t.available]

    def public_view(self) -> list[dict]:
        return [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "kind": t.kind,
                "risk_level": t.risk_level,
                "requires_approval": t.requires_approval,
                "available": t.available,
                "unavailable_reason": None if t.available else t.unavailable_reason,
                "badge": t.badge.value,
                "typical_questions": t.typical_questions,
            }
            for t in self._tools.values()
        ]


_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = _build_registry()
    return _registry


def _build_registry() -> ToolRegistry:
    from app.tools.document_search import (
        get_document_summary,
        list_user_documents,
        search_document_chunks,
    )
    from app.tools.web_search import tavily_web_search
    from app.tools.paper_search import arxiv_search

    reg = ToolRegistry()

    reg.register(ToolSpec(
        id="search_document_chunks",
        name="Search my private documents",
        description=(
            "Semantic search across the user's uploaded PDFs. Returns text "
            "chunks with document filename and page number for citation."
        ),
        kind="internal",
        risk_level="read",
        requires_approval=False,
        keywords=["document", "pdf", "uploaded", "my file", "page", "chapter",
                  "section", "in the doc", "paper i uploaded", "handbook"],
        badge=ToolBadge.PRIVATE_DOC,
        typical_questions=[
            "What does my uploaded document say about X?",
            "Find the section on the executor in my design doc.",
        ],
        executor=search_document_chunks,
        is_available=lambda: True,
    ))

    reg.register(ToolSpec(
        id="get_document_summary",
        name="Get document summary",
        description="Return the auto-generated summary of one of the user's documents.",
        kind="internal",
        risk_level="read",
        requires_approval=False,
        keywords=["summarize", "summary", "tl;dr", "overview", "gist"],
        badge=ToolBadge.PRIVATE_DOC,
        typical_questions=[
            "Summarize my uploaded architecture document.",
            "Give me the gist of the RAG paper I uploaded.",
        ],
        executor=get_document_summary,
        is_available=lambda: True,
    ))

    reg.register(ToolSpec(
        id="list_user_documents",
        name="List my documents",
        description="Return the user's available documents with processing status.",
        kind="internal",
        risk_level="read",
        requires_approval=False,
        keywords=["list", "which documents", "what have i uploaded", "my library"],
        badge=ToolBadge.CONTEXT,
        typical_questions=["Which documents do I have uploaded?"],
        executor=list_user_documents,
        is_available=lambda: True,
    ))

    reg.register(ToolSpec(
        id="web_search",
        name="Web search",
        description=(
            "Search the current public web via Tavily. Returns snippets with "
            "title and URL. Use for recent news or general facts NOT in the "
            "user's documents."
        ),
        kind="api",
        risk_level="read",
        requires_approval=False,
        keywords=["current", "recent", "web", "news", "today", "internet",
                  "latest", "online", "google"],
        badge=ToolBadge.WEB_SOURCE,
        typical_questions=[
            "Find current web information on MCP.",
            "What's happening with autonomous agents this month?",
        ],
        executor=tavily_web_search,
        is_available=lambda: bool(settings.tavily_api_key),
        unavailable_reason="TAVILY_API_KEY not configured",
    ))

    reg.register(ToolSpec(
        id="paper_search",
        name="Research paper search (arXiv)",
        description=(
            "Search arXiv for academic papers on a topic. Returns titles, "
            "authors, abstracts, publication dates, and URLs."
        ),
        kind="api",
        risk_level="read",
        requires_approval=False,
        keywords=["paper", "research", "arxiv", "academic", "publication",
                  "study", "recent papers", "literature"],
        badge=ToolBadge.RESEARCH_PAPER,
        typical_questions=[
            "Find recent papers about autonomous agents.",
            "What research exists on RAG evaluation?",
        ],
        executor=arxiv_search,
        is_available=lambda: True,
    ))

    return reg
