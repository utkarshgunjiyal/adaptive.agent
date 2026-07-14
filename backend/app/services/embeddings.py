"""Deterministic hashed n-gram embeddings.

Runner.ai's preview build stores vectors in MongoDB and searches them with
cosine similarity. A high-quality embedding model would be OpenAI's
``text-embedding-3-small``, but Runner.ai's chat providers (OpenRouter /
Anthropic) are used for chat only — this build does not depend on a separate
embeddings endpoint. To keep the app fully functional without a second paid
provider, we use a deterministic, tokenised hashed-feature embedding (a
well-known baseline that captures lexical overlap well enough for cosine-based
retrieval on user documents).

This module is intentionally isolated so a real embeddings provider can be
dropped in later without touching the rest of the pipeline: change
``embed_texts`` and everything else keeps working.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Iterable

import numpy as np

EMBED_DIM = 512
_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return [tok.lower() for tok in _WORD_RE.findall(text or "")]


def _hash(tok: str, salt: str = "") -> int:
    h = hashlib.blake2s((salt + tok).encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(h, "big")


def embed_text(text: str) -> list[float]:
    """Deterministic hashed n-gram embedding (unit-normalised)."""
    vec = np.zeros(EMBED_DIM, dtype=np.float32)
    tokens = _tokenize(text)
    if not tokens:
        return vec.tolist()

    for tok in tokens:
        idx = _hash(tok) % EMBED_DIM
        vec[idx] += 1.0
        # Character 3-gram signal helps with typos + partial matches.
        for i in range(max(0, len(tok) - 2)):
            trigram = tok[i : i + 3]
            j = _hash(trigram, "tri:") % EMBED_DIM
            vec[j] += 0.5
    # Bigrams give the vector a bit of positional info.
    for a, b in zip(tokens, tokens[1:]):
        k = _hash(f"{a}_{b}", "bi:") % EMBED_DIM
        vec[k] += 0.7

    # L2 normalise so cosine similarity == dot product.
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec /= norm
    return vec.tolist()


def embed_texts(texts: Iterable[str]) -> list[list[float]]:
    return [embed_text(t) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    if va.size == 0 or vb.size == 0:
        return 0.0
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0 or math.isnan(denom):
        return 0.0
    return float(np.dot(va, vb) / denom)
