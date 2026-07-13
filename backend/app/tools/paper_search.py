"""arXiv paper search tool.

arXiv exposes a lightweight ATOM-XML API on
``http://export.arxiv.org/api/query``. No key required.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlencode

import httpx

from app.models import ToolBadge

_ENDPOINT = "https://export.arxiv.org/api/query"
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


async def arxiv_search(*, query: str, max_results: int = 5, **_: Any) -> dict[str, Any]:
    max_results = max(1, min(int(max_results or 5), 10))
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    url = f"{_ENDPOINT}?{urlencode(params)}"

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Runner.ai/1.0"})
            resp.raise_for_status()
            body = resp.text
    except Exception as exc:  # noqa: BLE001
        return {"summary": f"arXiv search failed: {exc}", "evidence": [], "error": True}

    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        return {"summary": f"arXiv returned malformed XML: {exc}", "evidence": [], "error": True}

    evidence = []
    for entry in root.findall("atom:entry", _NS):
        title = _clean(entry.findtext("atom:title", "", _NS))
        summary = _clean(entry.findtext("atom:summary", "", _NS))
        published = _clean(entry.findtext("atom:published", "", _NS))[:10]
        arxiv_id_full = _clean(entry.findtext("atom:id", "", _NS))
        # Prefer the abstract HTML link.
        link = arxiv_id_full
        for l in entry.findall("atom:link", _NS):
            if l.attrib.get("rel") == "alternate" and l.attrib.get("type") == "text/html":
                link = l.attrib.get("href") or link
        authors = [
            _clean(a.findtext("atom:name", "", _NS))
            for a in entry.findall("atom:author", _NS)
        ]
        evidence.append(
            {
                "source_type": ToolBadge.RESEARCH_PAPER.value,
                "title": title,
                "snippet": summary[:600],
                "url": link,
                "authors": [a for a in authors if a][:6],
                "published": published,
            }
        )

    return {
        "summary": f"arXiv returned {len(evidence)} paper(s) for '{query[:80]}'.",
        "evidence": evidence,
    }
