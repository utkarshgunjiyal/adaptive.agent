"""Adaptive-runtime configuration.

All limits and provider selection are read from environment so operations
can tune the runtime without code changes. The values below match the
Phase 1 acceptance envelope; later phases will expose additional knobs.
"""

from __future__ import annotations

import os

from app.config import settings as base_settings


class AdaptiveConfig:
    """Read-once configuration for the adaptive graph."""

    # -- provider abstraction ---------------------------------------------
    llm_provider: str = os.getenv("LLM_PROVIDER", "emergent").strip().lower()
    # NOTE: LLM_MODEL_ADAPTIVE takes precedence so the legacy `LLM_MODEL`
    # (kept for /run/stream compatibility) can stay pinned to gpt-5.2.
    llm_model: str = (
        os.getenv("LLM_MODEL_ADAPTIVE")
        or os.getenv("LLM_MODEL")
        or "claude-sonnet-4-5-20250929"
    ).strip()
    llm_api_key: str = (os.getenv("LLM_API_KEY") or base_settings.emergent_llm_key or "").strip()
    llm_base_url: str = (os.getenv("LLM_BASE_URL") or "").strip()

    # -- runtime limits ---------------------------------------------------
    max_iterations: int = int(os.getenv("ADAPTIVE_MAX_ITERATIONS", "12"))
    max_tool_calls_total: int = int(os.getenv("ADAPTIVE_MAX_TOOL_CALLS", "20"))
    max_calls_per_tool: int = int(os.getenv("ADAPTIVE_MAX_CALLS_PER_TOOL", "6"))
    per_tool_timeout_s: float = float(os.getenv("ADAPTIVE_TOOL_TIMEOUT_S", "25"))
    overall_run_timeout_s: float = float(os.getenv("ADAPTIVE_RUN_TIMEOUT_S", "120"))

    # -- feature flag -----------------------------------------------------
    # When true, the frontend defaults to /api/agent/run/adaptive/stream.
    # Legacy /run/stream stays reachable for rollback and regression.
    default_adaptive: bool = os.getenv("ADAPTIVE_DEFAULT", "true").strip().lower() in {
        "1", "true", "yes", "on",
    }

    # Latest ToolMessage is always preserved; older tool outputs are
    # compacted (snippet-truncated) if they exceed this length. Evidence
    # references remain in the run record regardless.
    tool_message_keep_chars: int = int(os.getenv("ADAPTIVE_TOOL_MESSAGE_KEEP", "4000"))
    tool_message_compact_chars: int = int(os.getenv("ADAPTIVE_TOOL_MESSAGE_COMPACT", "600"))


adaptive = AdaptiveConfig()
