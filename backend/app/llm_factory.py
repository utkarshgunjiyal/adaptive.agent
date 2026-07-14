"""Provider-neutral LangChain chat-model factory.

Runner.ai runs on user-owned LLM credentials — there is no hosted key and no
Emergent dependency anywhere in this module. Two providers are supported:

* ``openrouter`` — OpenRouter's OpenAI-compatible API (``ChatOpenAI`` pointed at
  ``OPENROUTER_BASE_URL``). This is the default production provider because it
  lets the operator switch models by changing ``LLM_MODEL`` alone, with no code
  changes. IMPORTANT: the adaptive runtime uses NATIVE tool calling
  (``bind_tools`` → structured ``tool_calls``), never JSON scraped from model
  text — so the configured ``LLM_MODEL`` MUST be an OpenRouter model that
  supports native tool / function calling. Models without tool-calling support
  will not drive the adaptive tool loop.
* ``anthropic`` — the direct Anthropic API (``ChatAnthropic``).

A ``stub`` mode (selected explicitly, or by ``auto`` when no key is present)
lets the application boot and run deterministic tests without any network
access or credentials.

The returned objects are LangChain ``BaseChatModel`` instances, so callers get
native ``bind_tools`` / structured tool-calls / ``ToolMessage`` round-trips for
free. Credentials are read from settings and are never logged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from langchain_core.language_models.chat_models import BaseChatModel

    from app.config import Settings


class UnsupportedLLMProvider(RuntimeError):
    """Raised when ``LLM_PROVIDER`` names a provider we cannot build."""


class LLMConfigError(RuntimeError):
    """Raised when the LLM configuration is invalid for the current APP_ENV
    (e.g. stub selected in production, or a real provider with no key)."""


# The deterministic, network-free ``stub`` provider is a development / test
# convenience ONLY. It must never answer in production.
_STUB_ALLOWED_ENVS = {"development", "dev", "test", "testing", "local"}


def stub_allowed(settings: "Settings") -> bool:
    """True only when APP_ENV is a development / test environment."""
    return (settings.app_env or "development").strip().lower() in _STUB_ALLOWED_ENVS


def resolve_provider(settings: "Settings", override: str | None = None) -> str:
    """Resolve the effective provider id.

    ``auto`` prefers OpenRouter (the recommended production provider), then the
    direct Anthropic API, and finally falls back to ``stub`` when neither key is
    configured. Whether ``stub`` is actually *permitted* is a separate question
    answered by :func:`stub_allowed` / :func:`llm_config_problem`; this function
    only reports what the settings select.
    """
    provider = (override or settings.llm_provider or "auto").strip().lower()
    if provider == "auto":
        if settings.openrouter_api_key:
            return "openrouter"
        if settings.anthropic_api_key:
            return "anthropic"
        return "stub"
    return provider


def llm_config_problem(settings: "Settings", override: str | None = None) -> str | None:
    """Return a human-readable reason the LLM config is unusable, else ``None``.

    Used by the readiness probe and by the runtime stub guards so a
    misconfigured production deployment fails clearly instead of silently
    returning stub / demo answers.
    """
    provider = resolve_provider(settings, override)

    if provider == "stub":
        if not stub_allowed(settings):
            return (
                f"No usable LLM provider is configured (LLM_PROVIDER resolves to "
                f"'stub'), and stub mode is not allowed when APP_ENV={settings.app_env!r}. "
                "Set LLM_PROVIDER=openrouter with OPENROUTER_API_KEY (recommended) "
                "or LLM_PROVIDER=anthropic with ANTHROPIC_API_KEY."
            )
        return None  # stub is permitted in development / test

    if provider == "openrouter" and not settings.openrouter_api_key:
        return "LLM_PROVIDER=openrouter but OPENROUTER_API_KEY is not set."
    if provider == "anthropic" and not settings.anthropic_api_key:
        return "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set."
    if provider not in {"openrouter", "anthropic"}:
        return f"Unsupported LLM_PROVIDER: {provider!r}."
    if not (settings.llm_model or "").strip():
        return "LLM_MODEL is not set (required for the selected provider)."
    return None


def get_chat_model(
    settings: "Settings",
    *,
    provider: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    streaming: bool = False,
) -> "BaseChatModel":
    """Build a LangChain chat model for the configured provider.

    ``provider`` / ``model`` override the values from ``settings`` (used by the
    adaptive runtime, which may pin its own model). ``stub`` is not a real chat
    model and must be handled by the caller via :func:`resolve_provider`; asking
    the factory to build it raises :class:`UnsupportedLLMProvider`.
    """
    resolved = resolve_provider(settings, provider)
    model_id = (model or settings.llm_model or "").strip()
    max_tokens = settings.llm_max_tokens if max_tokens is None else max_tokens
    temperature = settings.llm_temperature if temperature is None else temperature

    if resolved == "openrouter":
        from langchain_openai import ChatOpenAI

        if not settings.openrouter_api_key:
            raise UnsupportedLLMProvider(
                "LLM_PROVIDER=openrouter but OPENROUTER_API_KEY is not set."
            )
        headers: dict[str, str] = {}
        if settings.openrouter_http_referer:
            headers["HTTP-Referer"] = settings.openrouter_http_referer
        if settings.openrouter_app_name:
            headers["X-Title"] = settings.openrouter_app_name
        return ChatOpenAI(
            model=model_id,
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            default_headers=headers or None,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            streaming=streaming,
        )

    if resolved == "anthropic":
        from langchain_anthropic import ChatAnthropic

        if not settings.anthropic_api_key:
            raise UnsupportedLLMProvider(
                "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set."
            )
        return ChatAnthropic(
            model=model_id,
            api_key=settings.anthropic_api_key,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            streaming=streaming,
        )

    if resolved == "stub":
        raise UnsupportedLLMProvider(
            "No LLM provider is configured. Set OPENROUTER_API_KEY (recommended) "
            "or ANTHROPIC_API_KEY, or choose LLM_PROVIDER explicitly."
        )

    raise UnsupportedLLMProvider(f"Unsupported LLM_PROVIDER: {resolved!r}")
