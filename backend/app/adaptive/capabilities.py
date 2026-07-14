"""Capability selector for the adaptive graph.

Given the user's request and recent observations, decide which subset of
tools to bind for the next LLM call. Bounded reselection: after an
empty/failed observation the selector may add complementary sources
(e.g. Tavily when arXiv failed) so the LLM can try another approach —
but the LLM still chooses whether to actually call them.
"""

from __future__ import annotations

import re
from typing import Any

from app.adaptive.tool_bindings import (
    CAP_DOCUMENTS,
    CAP_RESEARCH,
    CAP_WEB,
    CAP_WRITE,
    all_names,
    get_binding,
)


# Keyword hints for the *initial* capability selection.
_DOC_HINTS = re.compile(
    r"\b(document|documents|pdf|uploaded|my file|my paper|report|the manual|"
    r"my architecture|my notes)\b",
    re.IGNORECASE,
)
_ARXIV_HINTS = re.compile(
    r"\b(arxiv|paper|papers|preprint|research|literature|study|studies|"
    r"survey|abstract)\b",
    re.IGNORECASE,
)
_WEB_HINTS = re.compile(
    r"\b(news|latest|today|current|website|blog|announcement|release notes)\b",
    re.IGNORECASE,
)
_IMPORT_HINTS = re.compile(
    r"\b(import|save|add|ingest)\b.*\b(paper|arxiv)\b",
    re.IGNORECASE,
)


def initial_capabilities(user_message: str) -> set[str]:
    """First pass — always include document search; add research/web
    based on the query and only bind the import tool when the user asks
    for it."""
    caps: set[str] = {CAP_DOCUMENTS}
    if _ARXIV_HINTS.search(user_message):
        caps.add(CAP_RESEARCH)
    if _WEB_HINTS.search(user_message):
        caps.add(CAP_WEB)
    if _IMPORT_HINTS.search(user_message):
        caps.add(CAP_RESEARCH)
        caps.add(CAP_WRITE)
    if not (caps - {CAP_DOCUMENTS}):
        # For open research/comparison questions, allow arXiv too so the
        # LLM can decide whether to consult literature. This mirrors the
        # problem statement's "multi-source" expectations.
        if re.search(r"\b(compare|comparison|papers|research|state.of.the.art)\b",
                     user_message, re.IGNORECASE):
            caps.add(CAP_RESEARCH)
    return caps


def _tools_in_caps(caps: set[str]) -> set[str]:
    names = set()
    for n in all_names():
        b = get_binding(n)
        if b and b.capability in caps:
            names.add(n)
    return names


def initial_tools(user_message: str) -> set[str]:
    return _tools_in_caps(initial_capabilities(user_message))


def reselect_after_observations(
    *,
    current_tools: set[str],
    observations: list[dict[str, Any]],
    user_message: str,
    reselection_count: int,
    max_reselections: int,
) -> tuple[set[str], str | None]:
    """Return (new_tool_set, reason) if a reselection should happen.

    Rules (bounded):
    - if arXiv returned empty/failed and Tavily isn't bound, add Tavily.
    - if document search returned empty/failed and arXiv isn't bound, add
      arXiv (for public literature fallback).
    - if Tavily is bound and empty and document search isn't, add doc
      search — never remove tools.
    - never reselect more than ``max_reselections`` times.
    """
    if reselection_count >= max_reselections:
        return current_tools, None

    if not observations:
        return current_tools, None

    def last_status(tool: str) -> str | None:
        for o in reversed(observations):
            if o.get("tool_id") == tool:
                return o.get("status")
        return None

    new_tools = set(current_tools)
    reason: str | None = None

    arxiv_bad = last_status("arxiv_search") in {"empty", "failed", "unavailable"}
    tavily_bad = last_status("tavily_web_search") in {"empty", "failed", "unavailable"}
    doc_bad = last_status("search_document_chunks") in {"empty", "failed", "unavailable"}

    if arxiv_bad and "tavily_web_search" not in new_tools:
        new_tools |= _tools_in_caps({CAP_WEB})
        reason = "arXiv returned no usable results; adding web search."
    elif doc_bad and "arxiv_search" not in new_tools:
        new_tools |= _tools_in_caps({CAP_RESEARCH})
        reason = "Document search returned no matches; adding public literature."
    elif tavily_bad and "search_document_chunks" not in new_tools:
        new_tools |= _tools_in_caps({CAP_DOCUMENTS})
        reason = "Web search returned no usable results; adding document search."

    if new_tools == current_tools:
        return current_tools, None
    return new_tools, reason
