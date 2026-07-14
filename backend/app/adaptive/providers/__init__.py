"""Provider factory.

Phase 1 wires only the Emergent provider. Anthropic / OpenRouter adapters
are called out in the requirements plan and will be added in a subsequent
commit that plugs a LangChain BaseChatModel into the same ``ChatProvider``
protocol. No graph node needs to change when they do — they normalise to
the same ChatOutput envelope.
"""

from __future__ import annotations

from functools import lru_cache

from app.adaptive.config import adaptive
from app.adaptive.providers.base import ChatProvider


class ProviderNotConfigured(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_chat_provider() -> ChatProvider:
    provider = (adaptive.llm_provider or "emergent").lower()
    if provider == "emergent":
        from app.adaptive.providers.emergent import EmergentProvider
        if not adaptive.llm_api_key:
            raise ProviderNotConfigured("LLM_API_KEY / EMERGENT_LLM_KEY is empty")
        return EmergentProvider()
    if provider in {"anthropic", "openrouter"}:
        # Phase 1 does not ship these adapters — they require a LangChain
        # BaseChatModel wrap. The runtime should never reach them yet.
        raise ProviderNotConfigured(
            f"LLM_PROVIDER={provider!r} is declared but its adapter is not yet enabled. "
            "Set LLM_PROVIDER=emergent for Phase 1."
        )
    raise ProviderNotConfigured(f"Unknown LLM_PROVIDER: {provider!r}")
