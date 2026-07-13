"""Tavily web search tool."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.models import ToolBadge

_ENDPOINT = "https://api.tavily.com/search"


async def tavily_web_search(*, query: str, max_results: int = 5, **_: Any) -> dict[str, Any]:
    if not settings.tavily_api_key:
        return {
            "summary": "Web search is not configured (missing TAVILY_API_KEY).",
            "evidence": [],
            "unavailable": True,
        }
    max_results = max(1, min(int(max_results or 5), 10))

    payload = {
        "api_key": settings.tavily_api_key,
        "query": query,
        "search_depth": "basic",
        "include_answer": False,
        "max_results": max_results,
    }
    now = datetime.now(timezone.utc).isoformat()

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(_ENDPOINT, json=payload)
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "summary": f"Tavily returned HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            "evidence": [],
            "error": True,
        }
    except Exception as exc:  # noqa: BLE001
        return {"summary": f"Web search failed: {exc}", "evidence": [], "error": True}

    results = body.get("results", []) or []
    evidence = [
        {
            "source_type": ToolBadge.WEB_SOURCE.value,
            "title": r.get("title") or "(untitled)",
            "snippet": (r.get("content") or "")[:600],
            "url": r.get("url"),
            "published": r.get("published_date") or now,
            "score": r.get("score"),
        }
        for r in results
    ]
    return {
        "summary": f"Tavily returned {len(evidence)} result(s) for '{query[:80]}'.",
        "evidence": evidence,
    }
