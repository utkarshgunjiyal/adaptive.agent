"""Adaptive-runtime policy layer.

Two responsibilities:

1. Classify tools as auto-executable vs. approval-required. Approval is
   enforced in the backend at policy time — the frontend cannot bypass it.
2. Canonicalise arguments so an approval, once granted, is bound to the
   *exact* invocation. If the LLM changes arguments after approval, that
   counts as a new invocation and requires new approval.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Tools whose execution requires explicit user approval.
APPROVAL_REQUIRED: frozenset[str] = frozenset({
    "import_arxiv_paper",
})


def requires_approval(tool_name: str) -> bool:
    return tool_name in APPROVAL_REQUIRED


def canonical_args(arguments: dict[str, Any]) -> str:
    """Canonical JSON of arguments for hashing/comparison."""
    scrubbed = {k: v for k, v in (arguments or {}).items() if k != "user_id"}
    return json.dumps(scrubbed, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def approval_fingerprint(tool_name: str, arguments: dict[str, Any]) -> str:
    """Deterministic hash of (tool_name, canonical_args). An approval is
    only valid for a matching fingerprint."""
    payload = f"{tool_name}|{canonical_args(arguments)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def call_fingerprint(tool_name: str, arguments: dict[str, Any]) -> str:
    """Same as approval_fingerprint — reused for duplicate-call detection."""
    return approval_fingerprint(tool_name, arguments)
