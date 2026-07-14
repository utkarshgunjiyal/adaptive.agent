"""Provider factory for the adaptive runtime.

Runner.ai runs on user-owned LLM credentials. Two providers are wired:

* ``openrouter`` — OpenRouter's OpenAI-compatible API (default production
  provider; switch models with ``LLM_MODEL`` alone).
* ``anthropic`` — the direct Anthropic API.

Both are LangChain ``BaseChatModel`` instances wrapped by
:class:`LangChainChatProvider`, which does native ``bind_tools`` tool calling
and normalises responses to the shared ``ChatOutput`` envelope — so no graph
node depends on a provider-specific class. A ``stub`` provider (no network) is
used when no credentials are configured.
"""

from __future__ import annotations

from functools import lru_cache

from app.adaptive.config import adaptive
from app.adaptive.providers.base import ChatProvider
from app.config import settings as base_settings
from app.llm_factory import (
    UnsupportedLLMProvider,
    get_chat_model,
    llm_config_problem,
    resolve_provider,
    stub_allowed,
)


class ProviderNotConfigured(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_chat_provider() -> ChatProvider:
    provider = resolve_provider(base_settings, adaptive.llm_provider)

    if provider == "stub":
        # Stub is a development / test convenience only — never in production.
        if not stub_allowed(base_settings):
            raise ProviderNotConfigured(
                llm_config_problem(base_settings, adaptive.llm_provider)
                or "stub LLM provider is not allowed in this environment"
            )
        from app.adaptive.providers.stub import StubProvider

        return StubProvider()

    if provider in {"openrouter", "anthropic"}:
        from app.adaptive.providers.langchain import make_provider

        try:
            model = get_chat_model(
                base_settings,
                provider=provider,
                model=adaptive.llm_model,
            )
        except UnsupportedLLMProvider as exc:
            raise ProviderNotConfigured(str(exc)) from exc
        return make_provider(model, provider)

    raise ProviderNotConfigured(f"Unknown LLM_PROVIDER: {provider!r}")
