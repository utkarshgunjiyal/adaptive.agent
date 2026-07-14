"""Safe tool executor for the adaptive runtime.

Responsibilities (per problem statement):
 - detect timeout / exception / malformed result inside the executor,
   never inside the LLM;
 - convert every outcome to a normalized ToolObservation with an explicit
   status and tool_call_id;
 - transparently redact secrets from tool arguments in the run record.

Phase 1 keeps retries at zero to avoid masking issues while we prove the
answer path. Phase 2 will add bounded retries for transient
read-only failures and a per-provider circuit breaker.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.adaptive.config import adaptive
from app.adaptive.normalize import (
    ToolObservation,
    failed_observation,
    normalize_result,
    rejected_observation,
)
from app.adaptive.tool_bindings import get_binding

log = logging.getLogger("runner.adaptive.executor")


_REDACTED_KEYS = {"api_key", "token", "password", "secret", "authorization"}


def _redact_args(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in args.items():
        if k.lower() in _REDACTED_KEYS:
            out[k] = "***redacted***"
        elif isinstance(v, dict):
            out[k] = _redact_args(v)
        else:
            out[k] = v
    return out


async def execute_tool(
    *,
    tool_name: str,
    tool_call_id: str,
    arguments: dict[str, Any],
    user_id: str,
) -> ToolObservation:
    """Execute one tool call end-to-end. Never raises: all outcomes are
    a ToolObservation."""
    binding = get_binding(tool_name)
    if binding is None:
        log.warning("adaptive: unknown tool_id=%s", tool_name)
        return rejected_observation(
            tool_call_id=tool_call_id,
            tool_id=tool_name,
            reason=f"Tool '{tool_name}' is not bound in the adaptive runtime.",
        )

    args = dict(arguments or {})
    args["user_id"] = user_id
    t0 = time.monotonic()

    try:
        raw = await asyncio.wait_for(
            binding.executor(**args),
            timeout=adaptive.per_tool_timeout_s,
        )
    except asyncio.TimeoutError:
        duration_ms = int((time.monotonic() - t0) * 1000)
        log.warning("adaptive: tool %s timed out after %ss",
                    tool_name, adaptive.per_tool_timeout_s)
        return failed_observation(
            tool_call_id=tool_call_id,
            tool_id=tool_name,
            error_type="timeout",
            message=f"Tool timed out after {adaptive.per_tool_timeout_s:.0f}s",
            attempts=1,
            duration_ms=duration_ms,
            retryable=True,
        )
    except Exception as exc:  # noqa: BLE001
        duration_ms = int((time.monotonic() - t0) * 1000)
        log.exception("adaptive: tool %s raised", tool_name)
        return failed_observation(
            tool_call_id=tool_call_id,
            tool_id=tool_name,
            error_type=type(exc).__name__,
            message=str(exc)[:400],
            attempts=1,
            duration_ms=duration_ms,
        )

    duration_ms = int((time.monotonic() - t0) * 1000)
    obs = normalize_result(
        tool_id=tool_name,
        tool_call_id=tool_call_id,
        raw_result=raw if isinstance(raw, dict) else None,
        duration_ms=duration_ms,
        provider=binding.name,
    )
    # Redact any secret-looking args for the audit log.
    obs.metadata["arguments_redacted"] = _redact_args(
        {k: v for k, v in arguments.items() if k != "user_id"}
    )
    return obs
