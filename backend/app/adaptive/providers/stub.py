"""Deterministic, network-free chat provider.

Selected when ``LLM_PROVIDER`` resolves to ``stub`` (explicitly, or via ``auto``
when no credentials are configured). It never calls a tool — it always returns
a short final answer — so the adaptive graph can complete a run without any
provider secret. Intended for local development and CI only.
"""

from __future__ import annotations

from typing import Any

from app.adaptive.providers.base import ChatOutput


class StubProvider:
    provider_id = "stub"

    async def invoke(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> ChatOutput:
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = str(m.get("content") or "")
                break
        snippet = last_user.strip().replace("\n", " ")[:280]
        answer = (
            "[stub-llm] No LLM provider is configured, so the adaptive runtime "
            "returned a deterministic placeholder instead of calling a model. "
            f"Your message was: {snippet}"
        )
        return ChatOutput(
            content=answer,
            tool_calls=[],
            finish_reason="stop",
            provider=self.provider_id,
        )
