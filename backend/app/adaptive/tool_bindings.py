"""Adaptive tool bindings.

Each tool exposed to the LLM as an OpenAI-format function-tool schema.
Executors themselves are unchanged; the adaptive normalizer wraps their
output centrally so tool modules stay untouched.

Bindings are grouped into *capabilities* so the graph can add/remove
tools between rounds (bounded capability reselection) rather than
sending every tool to the LLM upfront.
"""

from __future__ import annotations

from typing import Any, Callable

from app.tools.document_search import (
    get_document_summary,
    list_user_documents,
    search_document_chunks,
)
from app.tools.paper_import import import_arxiv_paper
from app.tools.paper_search import arxiv_search
from app.tools.web_search import tavily_web_search


class ToolBinding:
    def __init__(
        self,
        *,
        name: str,
        capability: str,
        schema: dict[str, Any],
        executor: Callable[..., Any],
        retryable: bool = False,
        max_retries: int = 0,
    ) -> None:
        self.name = name
        self.capability = capability
        self.schema = schema
        self.executor = executor
        self.retryable = retryable
        self.max_retries = max_retries


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------

_SEARCH_DOC = {
    "type": "function",
    "function": {
        "name": "search_document_chunks",
        "description": (
            "Semantic hybrid search across the user's uploaded PDFs. "
            "Returns text chunks with document filename and page number. "
            "Use this when the user asks about content in their uploaded "
            "documents."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Focused search query."},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 12,
                          "default": 6},
                "document_ids": {"type": "array", "items": {"type": "string"},
                                 "description": "Restrict to these documents."},
            },
            "required": ["query"],
        },
    },
}

_LIST_DOCS = {
    "type": "function",
    "function": {
        "name": "list_user_documents",
        "description": (
            "List the user's uploaded documents (id, filename, status). "
            "Call this only when the user asks what documents they have."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_DOC_SUMMARY = {
    "type": "function",
    "function": {
        "name": "get_document_summary",
        "description": (
            "Return the overall summary of a single document by id. "
            "Useful when the user asks 'what is document X about?'"
        ),
        "parameters": {
            "type": "object",
            "properties": {"document_id": {"type": "string"}},
            "required": ["document_id"],
        },
    },
}

_ARXIV_SEARCH = {
    "type": "function",
    "function": {
        "name": "arxiv_search",
        "description": (
            "Search recent research papers on arXiv. Returns paper "
            "titles, authors, abstracts and URLs. Use this for questions "
            "about scientific literature."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Plain keywords, not quoted."},
                "max_results": {"type": "integer", "minimum": 1,
                                "maximum": 10, "default": 5},
            },
            "required": ["query"],
        },
    },
}

_TAVILY_SEARCH = {
    "type": "function",
    "function": {
        "name": "tavily_web_search",
        "description": (
            "General web search via Tavily. Use this ONLY when you cannot "
            "answer from documents or arXiv, or when the user asks about "
            "current events or non-academic web content."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1,
                                "maximum": 10, "default": 5},
            },
            "required": ["query"],
        },
    },
}

_IMPORT_ARXIV = {
    "type": "function",
    "function": {
        "name": "import_arxiv_paper",
        "description": (
            "Permanently import an arXiv paper into the user's document "
            "library (downloads the PDF and starts ingestion). REQUIRES "
            "USER APPROVAL — the system will interrupt for confirmation "
            "before executing. Only call when the user explicitly asks "
            "to save/import/add a paper."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "arxiv_url": {"type": "string",
                              "description": "arXiv URL or id (e.g. 2401.12345)."},
                "title": {"type": "string",
                          "description": "Paper title for the filename."},
            },
            "required": ["arxiv_url"],
        },
    },
}


# --------------------------------------------------------------------------
# Capability groups
# --------------------------------------------------------------------------

CAP_DOCUMENTS = "documents"
CAP_RESEARCH = "research"
CAP_WEB = "web"
CAP_WRITE = "write"


_ALL_BINDINGS: dict[str, ToolBinding] = {
    "search_document_chunks": ToolBinding(
        name="search_document_chunks", capability=CAP_DOCUMENTS,
        schema=_SEARCH_DOC, executor=search_document_chunks,
        retryable=True, max_retries=1,
    ),
    "list_user_documents": ToolBinding(
        name="list_user_documents", capability=CAP_DOCUMENTS,
        schema=_LIST_DOCS, executor=list_user_documents,
    ),
    "get_document_summary": ToolBinding(
        name="get_document_summary", capability=CAP_DOCUMENTS,
        schema=_DOC_SUMMARY, executor=get_document_summary,
    ),
    "arxiv_search": ToolBinding(
        name="arxiv_search", capability=CAP_RESEARCH,
        schema=_ARXIV_SEARCH, executor=arxiv_search,
        retryable=True, max_retries=2,
    ),
    "tavily_web_search": ToolBinding(
        name="tavily_web_search", capability=CAP_WEB,
        schema=_TAVILY_SEARCH, executor=tavily_web_search,
        retryable=True, max_retries=1,
    ),
    "import_arxiv_paper": ToolBinding(
        name="import_arxiv_paper", capability=CAP_WRITE,
        schema=_IMPORT_ARXIV, executor=import_arxiv_paper,
    ),
}


def get_binding(name: str) -> ToolBinding | None:
    return _ALL_BINDINGS.get(name)


def all_names() -> set[str]:
    return set(_ALL_BINDINGS.keys())


def schemas_for(names: set[str]) -> list[dict[str, Any]]:
    return [b.schema for n, b in _ALL_BINDINGS.items() if n in names]
