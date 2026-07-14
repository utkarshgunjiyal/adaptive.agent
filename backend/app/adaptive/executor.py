"""Safe tool executor for the adaptive runtime.

Responsibilities:
 - detect timeout / exception / malformed result inside the executor,
   never inside the LLM;
 - bounded read-only retries with exponential backoff for retryable
   transient failures;
 - convert every outcome to a normalized ToolObservation with an
   explicit status and tool_call_id;
 - transparently redact secrets from tool arguments in the audit log.
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


def _is_transient(raw: dict[str, Any] | None, exc: BaseException | None) -> bool:
    """Decide whether a failure warrants a retry."""
    if isinstance(exc, asyncio.TimeoutError):
        return True
    if exc is not None:
        s = f"{type(exc).__name__}: {exc}".lower()
        return any(k in s for k in
                   ("timeout", "temporarily", "connection", "reset",
                    "readtimeout", "connecttimeout", "eof", "httpx.remote",
                    "503", "502", "504", "429"))
    if raw and raw.get("error"):
        msg = str(raw.get("summary") or "").lower()
        return any(k in msg for k in
                   ("timeout", "temporarily", "connection", "reset",
                    "http 5", "429"))
    return False


async def execute_tool(
    *,
    tool_name: str,
    tool_call_id: str,
    arguments: dict[str, Any],
    user_id: str,
) -> ToolObservation:
    """Execute one tool call end-to-end. Never raises: all outcomes are
    a ToolObservation. Applies bounded retries when the executor was
    marked retryable and the failure looks transient."""
    binding = get_binding(tool_name)
    if binding is None:
        return rejected_observation(
            tool_call_id=tool_call_id,
            tool_id=tool_name,
            reason=f"Tool '{tool_name}' is not bound.",
        )

    args = dict(arguments or {})
    args["user_id"] = user_id
    max_attempts = 1 + (binding.max_retries if binding.retryable else 0)
    attempts = 0
    last_exc: BaseException | None = None
    raw: Any = None
    t0 = time.monotonic()

    while attempts < max_attempts:
        attempts += 1
        try:
            raw = await asyncio.wait_for(
                binding.executor(**args),
                timeout=adaptive.per_tool_timeout_s,
            )
            last_exc = None
        except asyncio.TimeoutError as exc:
            raw = None
            last_exc = exc
            log.warning("tool %s timeout attempt=%d", tool_name, attempts)
        except Exception as exc:  # noqa: BLE001
            raw = None
            last_exc = exc
            log.warning("tool %s raised attempt=%d: %s",
                        tool_name, attempts, exc)

        # Success path
        if last_exc is None and isinstance(raw, dict) and not raw.get("error"):
            break
        # Retry decision
        if attempts >= max_attempts:
            break
        if not binding.retryable or not _is_transient(
                raw if isinstance(raw, dict) else None, last_exc):
            break
        # Exponential backoff: 0.4s, 0.8s, 1.6s
        await asyncio.sleep(0.4 * (2 ** (attempts - 1)))

    duration_ms = int((time.monotonic() - t0) * 1000)

    if last_exc is not None:
        obs = failed_observation(
            tool_call_id=tool_call_id,
            tool_id=tool_name,
            error_type=type(last_exc).__name__,
            message=str(last_exc)[:400] or "unknown error",
            attempts=attempts,
            duration_ms=duration_ms,
            retryable=binding.retryable,
        )
    else:
        obs = normalize_result(
            tool_id=tool_name,
            tool_call_id=tool_call_id,
            raw_result=raw if isinstance(raw, dict) else None,
            duration_ms=duration_ms,
            provider=tool_name,
        )
        if obs.error:
            obs.error["attempts"] = attempts

    obs.metadata["attempts"] = attempts
    obs.metadata["arguments_redacted"] = _redact_args(
        {k: v for k, v in arguments.items() if k != "user_id"}
    )
    return obs
