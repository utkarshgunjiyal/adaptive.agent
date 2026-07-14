"""Tool-observation normalizer.

Every tool executor in the existing registry returns the internal shape:

    { "summary": str, "evidence": [ {...}, ... ], "error": bool?,
      "unavailable": bool? }

The adaptive runtime turns that into a ToolMessage envelope that both the
LLM (as JSON content on a role=tool message) and the run record can
consume. The envelope carries an explicit ``status`` so the LLM can
distinguish success / empty / failed / unavailable / rejected without
having to guess from prose.

The centralised normalisation lets us keep existing tool executors
completely unchanged.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


TOOL_STATUS_SUCCESS = "success"
TOOL_STATUS_EMPTY = "empty"
TOOL_STATUS_FAILED = "failed"
TOOL_STATUS_REJECTED = "rejected"
TOOL_STATUS_UNAVAILABLE = "unavailable"
TOOL_STATUS_UNCERTAIN = "uncertain"


@dataclass
class ToolObservation:
    """Normalized outcome of one tool call. Roundtrips as JSON on the
    ToolMessage sent back to the LLM."""

    tool_call_id: str
    tool_id: str
    status: str
    summary: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_llm_content(self, *, compact_snippet_chars: int = 600) -> str:
        """Serialise for the ToolMessage body.

        We keep the JSON envelope small so context stays manageable: the
        summary and top evidence titles/snippets are included verbatim, but
        evidence chunks are truncated. Full evidence objects are still
        persisted to the agent_run row via ``evidence`` in state.
        """
        compact_evidence: list[dict[str, Any]] = []
        for e in self.evidence[:8]:
            item = {
                "source_type": e.get("source_type"),
                "title": e.get("title"),
                "snippet": (e.get("snippet") or "")[:compact_snippet_chars],
            }
            for k in ("filename", "page", "url", "authors", "published",
                      "document_id", "score"):
                if e.get(k) is not None:
                    item[k] = e.get(k)
            compact_evidence.append(item)

        body = {
            "status": self.status,
            "tool_id": self.tool_id,
            "summary": self.summary,
            "evidence": compact_evidence,
        }
        if self.error:
            body["error"] = self.error
        if self.metadata:
            body["metadata"] = self.metadata
        return json.dumps(body, ensure_ascii=False)


def normalize_result(
    *,
    tool_id: str,
    tool_call_id: str,
    raw_result: dict[str, Any] | None,
    duration_ms: int,
    provider: str = "internal",
) -> ToolObservation:
    """Convert an executor result into a normalized ToolObservation."""
    now_iso = datetime.now(timezone.utc).isoformat()
    metadata = {
        "provider": provider,
        "duration_ms": duration_ms,
        "retrieved_at": now_iso,
    }
    if raw_result is None:
        return ToolObservation(
            tool_call_id=tool_call_id,
            tool_id=tool_id,
            status=TOOL_STATUS_FAILED,
            summary=f"Tool '{tool_id}' returned no result.",
            evidence=[],
            error={"type": "no_result", "message": "executor returned None",
                   "retryable": False, "attempts": 1},
            metadata=metadata,
        )
    if raw_result.get("unavailable"):
        return ToolObservation(
            tool_call_id=tool_call_id,
            tool_id=tool_id,
            status=TOOL_STATUS_UNAVAILABLE,
            summary=str(raw_result.get("summary") or f"Tool '{tool_id}' is not configured."),
            evidence=[],
            error={"type": "unavailable",
                   "message": str(raw_result.get("summary") or ""),
                   "retryable": False, "attempts": 1},
            metadata=metadata,
        )
    if raw_result.get("error"):
        return ToolObservation(
            tool_call_id=tool_call_id,
            tool_id=tool_id,
            status=TOOL_STATUS_FAILED,
            summary=str(raw_result.get("summary") or f"Tool '{tool_id}' failed."),
            evidence=list(raw_result.get("evidence") or []),
            error={"type": "executor_error",
                   "message": str(raw_result.get("summary") or "unknown"),
                   "retryable": False, "attempts": 1},
            metadata=metadata,
        )
    evidence = list(raw_result.get("evidence") or [])
    status = TOOL_STATUS_SUCCESS if evidence else TOOL_STATUS_EMPTY
    return ToolObservation(
        tool_call_id=tool_call_id,
        tool_id=tool_id,
        status=status,
        summary=str(raw_result.get("summary")
                    or (f"Tool '{tool_id}' returned {len(evidence)} item(s).")),
        evidence=evidence,
        error=None,
        metadata=metadata,
    )


def rejected_observation(
    *,
    tool_call_id: str,
    tool_id: str,
    reason: str,
) -> ToolObservation:
    return ToolObservation(
        tool_call_id=tool_call_id,
        tool_id=tool_id,
        status=TOOL_STATUS_REJECTED,
        summary=reason,
        evidence=[],
        error={"type": "rejected", "message": reason,
               "retryable": False, "attempts": 0},
        metadata={"provider": "policy",
                  "duration_ms": 0,
                  "retrieved_at": datetime.now(timezone.utc).isoformat()},
    )


def failed_observation(
    *,
    tool_call_id: str,
    tool_id: str,
    error_type: str,
    message: str,
    attempts: int,
    duration_ms: int,
    retryable: bool = False,
) -> ToolObservation:
    return ToolObservation(
        tool_call_id=tool_call_id,
        tool_id=tool_id,
        status=TOOL_STATUS_FAILED,
        summary=f"Tool '{tool_id}' failed: {message}",
        evidence=[],
        error={"type": error_type, "message": message,
               "retryable": retryable, "attempts": attempts},
        metadata={"provider": "internal",
                  "duration_ms": duration_ms,
                  "retrieved_at": datetime.now(timezone.utc).isoformat()},
    )


def new_call_id(prefix: str = "call") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def now_ms() -> int:
    return int(time.monotonic() * 1000)
