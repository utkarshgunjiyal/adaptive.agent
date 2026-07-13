"""Hybrid retrieval: BM25 (lexical) + hashed dense (semantic) + LLM rerank.

The preview environment can't ship a real embedding model or a cross-encoder
reranker without a huge dep pull. So we build a strong hybrid pipeline from
free primitives:

1. **BM25** — lexical scoring via ``rank_bm25`` over per-document token lists.
   BM25 is a very hard baseline to beat on Q&A over user PDFs.
2. **Dense** — hashed n-gram cosine (kept as a "typo/synonym" signal).
3. **Fusion** — reciprocal rank fusion (RRF) combining both rankings.
4. **LLM rerank (optional)** — for top-K candidates, ask gpt-5.2 to rerank
   by relevance to the query. Cheap (only titles+snippets), and dramatically
   improves precision on ambiguous queries. Skipped when top-K is small.

The output shape is identical to the original ``vector_store.search``
function so callers don't change.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from rank_bm25 import BM25Okapi

from app.db import get_db
from app.services.embeddings import cosine, embed_text
from app.services.llm import complete, extract_json

log = logging.getLogger("runner.retrieval")

_WORD_RE = re.compile(r"[A-Za-z0-9]+")

BM25_K = 40
DENSE_K = 40
RERANK_TOP_K = 12
FINAL_TOP_K = 8


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _WORD_RE.findall(text or "")]


async def _fetch_chunks(user_id: str, document_ids: list[str] | None
                        ) -> list[dict[str, Any]]:
    q: dict[str, Any] = {"user_id": user_id}
    if document_ids:
        q["document_id"] = {"$in": document_ids}
    cursor = get_db().chunks.find(q, {
        "text": 1, "embedding": 1, "page": 1,
        "document_id": 1, "filename": 1, "chunk_id": 1,
    })
    return [row async for row in cursor]


def _bm25_scores(query: str, chunks: list[dict[str, Any]]) -> list[float]:
    if not chunks:
        return []
    corpus = [_tokenize(c["text"]) for c in chunks]
    bm25 = BM25Okapi(corpus)
    query_tokens = _tokenize(query)
    if not query_tokens:
        return [0.0] * len(chunks)
    return list(bm25.get_scores(query_tokens))


def _dense_scores(query: str, chunks: list[dict[str, Any]]) -> list[float]:
    if not chunks:
        return []
    q = embed_text(query)
    return [cosine(q, c.get("embedding", [])) for c in chunks]


def _rrf(rankings: list[list[int]], k: int = 60) -> list[tuple[int, float]]:
    """Reciprocal rank fusion. rankings = list of ranked index lists (best first)."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


_RERANK_SYSTEM = (
    "You are a search re-ranker. Given a query and a list of candidate "
    "passages (each with an id), return a JSON object with the field "
    "`order` containing the ids sorted from most relevant to least. "
    "Consider both semantic relevance and specificity — passages that "
    "directly answer the query rank higher. Output ONLY the JSON object."
)


async def _llm_rerank(query: str, candidates: list[dict[str, Any]],
                      user_id: str) -> list[dict[str, Any]]:
    """Ask gpt-5.2 to rerank up to ``RERANK_TOP_K`` candidates."""
    if len(candidates) <= 3:
        return candidates
    lines = ["QUERY:", query, "", "CANDIDATES:"]
    for c in candidates:
        snippet = (c["text"] or "").replace("\n", " ")[:300]
        lines.append(f"id={c['_idx']} | {c['filename']} p.{c['page']} | {snippet}")

    try:
        raw = await complete(
            session_id=f"rerank:{user_id}",
            system=_RERANK_SYSTEM,
            user="\n".join(lines),
            max_tokens=200,
        )
        parsed = extract_json(raw)
        if isinstance(parsed, dict):
            order = parsed.get("order") or []
        elif isinstance(parsed, list):
            order = parsed
        else:
            order = []
        if not order:
            return candidates
        # Normalise to ints
        order_ids = [int(x) for x in order if str(x).lstrip("-").isdigit()]
        by_idx = {c["_idx"]: c for c in candidates}
        reranked = [by_idx[i] for i in order_ids if i in by_idx]
        # Append anything the reranker dropped so we don't lose evidence.
        seen = {c["_idx"] for c in reranked}
        for c in candidates:
            if c["_idx"] not in seen:
                reranked.append(c)
        return reranked
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM rerank failed: %s — falling back to RRF order", exc)
        return candidates


async def hybrid_search(*, user_id: str, query: str,
                        document_ids: list[str] | None = None,
                        top_k: int = FINAL_TOP_K,
                        use_rerank: bool = True) -> list[dict[str, Any]]:
    chunks = await _fetch_chunks(user_id, document_ids)
    if not chunks:
        return []

    bm = _bm25_scores(query, chunks)
    dn = _dense_scores(query, chunks)

    bm_rank = sorted(range(len(chunks)), key=lambda i: bm[i], reverse=True)[:BM25_K]
    dn_rank = sorted(range(len(chunks)), key=lambda i: dn[i], reverse=True)[:DENSE_K]

    fused = _rrf([bm_rank, dn_rank])
    top_candidates = [
        {**chunks[idx], "_idx": idx, "_fused": score, "_bm25": bm[idx], "_dense": dn[idx]}
        for idx, score in fused[:RERANK_TOP_K]
    ]

    if use_rerank and len(top_candidates) > 3:
        top_candidates = await _llm_rerank(query, top_candidates, user_id)

    top_candidates = top_candidates[:top_k]
    return [
        {
            "document_id": c["document_id"],
            "filename": c["filename"],
            "page": c.get("page", 1),
            "chunk_id": c.get("chunk_id", 0),
            "text": c["text"],
            "score": round(float(c.get("_fused", 0.0)), 4),
            "bm25": round(float(c.get("_bm25", 0.0)), 3),
            "dense": round(float(c.get("_dense", 0.0)), 3),
        }
        for c in top_candidates
    ]
