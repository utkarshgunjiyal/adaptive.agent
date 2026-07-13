"""V1.5 LLM provider adapters (Phase 36).

Real, provider-agnostic adapters that reuse the existing V1.5 LLM service
(``app.services.llm_client.complete``). No vendor SDK is imported in the agent
runtime — the V1.5 service is resolved lazily, so importing ``app.agent.llm``
stays config-free and default unit tests run without API credentials (they
inject a fake ``complete``).

This module hosts the shared provider errors + resolver, the message renderer,
and the real ``FinalAnswerProvider`` adapter. The planner adapter lives in
``planner_provider.py`` and reuses the resolver here.
"""

import re
from collections.abc import AsyncIterator

from app.agent.llm.final_provider import FinalAnswer, MessageRole, render_final_prompt
from app.agent.models.final_prompt import FinalPrompt

_CITATION_RE = re.compile(r"\[([A-Za-z]+\d+)\]")


class ProviderError(Exception):
    """Base for provider-adapter failures (never a raw vendor exception).

    Carries API-safe classification fields. ``safe_message`` is a generic,
    vendor-free string — the raw exception text (which may hold vendor detail) is
    never exposed beyond the adapter.
    """

    error_code = "provider_error"
    retryable = False
    stage = "provider"
    safe_message = "The provider could not complete the request."


class ProviderUnavailableError(ProviderError):
    """The underlying V1.5 LLM service/credentials could not be resolved."""

    error_code = "provider_unavailable"
    retryable = True
    safe_message = "The language model service is temporarily unavailable."


class FinalProviderError(ProviderError):
    """The final-answer provider failed to produce an answer."""

    error_code = "final_provider_error"
    retryable = False
    stage = "final_provider"
    safe_message = "The final answer could not be generated."


async def resolve_v15_complete():
    """Lazily resolve the V1.5 ``complete`` coroutine. Import happens here (not at
    module load) so the agent package stays config-free until actually invoked."""
    try:
        from app.services.llm_client import complete
    except Exception as exc:  # noqa: BLE001 - surface as a domain error
        raise ProviderUnavailableError(f"V1.5 LLM service unavailable: {exc}") from exc
    return complete


async def resolve_v15_stream():
    """Lazily resolve the V1.5 ``stream`` async iterator (Phase 38). Same lazy
    boundary as ``resolve_v15_complete`` — no vendor SDK at import time. Raises
    ``ProviderUnavailableError`` so callers can gracefully fall back to
    ``complete``-based generation when streaming is not wired."""
    try:
        from app.services.llm_client import stream
    except Exception as exc:  # noqa: BLE001 - surface as a domain error
        raise ProviderUnavailableError(f"V1.5 LLM streaming unavailable: {exc}") from exc
    return stream


def render_messages_to_system_prompt(messages) -> tuple[str, str]:
    """Flatten provider-neutral FinalPromptMessages into (system, user) strings —
    the shape V1.5's ``complete(system, prompt)`` expects."""
    system_parts: list[str] = []
    body_parts: list[str] = []
    for message in messages:
        if message.role == MessageRole.SYSTEM:
            system_parts.append(message.content)
        else:
            body_parts.append(f"[{message.role.value}] {message.content}")
    return "\n\n".join(system_parts), "\n\n".join(body_parts)


class V15FinalAnswerProvider:
    """Real FinalAnswerProvider over the V1.5 LLM service.

    ``complete`` is injectable for tests; when absent it is lazily resolved and
    ``provider``/``model`` are read from V1.5 settings at invocation time.
    """

    def __init__(self, *, complete=None, stream=None, provider: str | None = None, model: str | None = None, max_tokens: int | None = None) -> None:
        self._complete = complete
        self._stream = stream
        self._max_tokens = max_tokens
        # Protocol requires provider/model attributes; filled lazily if unset.
        self.provider = provider or "v15"
        self.model = model or ""
        self._provider_injected = provider
        self._model_injected = model

    def _ensure_identity(self) -> None:
        """Fill provider/model from V1.5 settings when not injected (production)."""
        if self._provider_injected is not None and self._model_injected is not None:
            return
        from app.config import settings  # lazy — production only

        if self._provider_injected is None:
            self.provider = f"v15:{settings.llm_provider}"
        if self._model_injected is None:
            self.model = settings.llm_model

    async def generate(self, final_prompt: FinalPrompt) -> FinalAnswer:
        complete = self._complete
        if complete is None:
            complete = await resolve_v15_complete()
            self._ensure_identity()

        messages = render_final_prompt(final_prompt)
        system, prompt = render_messages_to_system_prompt(messages)

        try:
            if self._max_tokens is not None:
                text = await complete(system, prompt, max_tokens=self._max_tokens)
            else:
                text = await complete(system, prompt)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - wrap raw LLM/vendor errors
            raise FinalProviderError(f"final answer generation failed: {exc}") from exc

        return self.build_final_answer(final_prompt, text or "")

    async def generate_stream(self, final_prompt: FinalPrompt) -> AsyncIterator[str]:
        """Stream answer text as V1.5 produces it (Phase 38).

        Resolution precedence: injected ``stream`` → injected ``complete`` →
        lazy V1.5 ``stream`` → lazy V1.5 ``complete``. The lazy V1.5 stream is only
        resolved when *nothing* was injected, so an injected ``complete`` (with no
        injected ``stream``) falls back to ``generate`` — it is never shadowed by
        the lazily-imported V1.5 stub. Raw LLM/vendor errors are wrapped as
        ``FinalProviderError`` — never leaked.
        """
        stream_fn = self._stream
        if stream_fn is None and self._complete is None:
            # Nothing injected: try the lazy V1.5 stream. If unavailable, generate()
            # will lazily resolve V1.5 complete below.
            try:
                stream_fn = await resolve_v15_stream()
            except ProviderUnavailableError:
                stream_fn = None
            else:
                self._ensure_identity()

        if stream_fn is None:
            # No stream available (an injected ``complete``, or the lazy V1.5 stream
            # is unavailable): assemble via generate() — which honors the injected
            # ``complete`` first, then the lazy V1.5 ``complete`` — and emit once.
            answer = await self.generate(final_prompt)
            if answer.text:
                yield answer.text
            return

        messages = render_final_prompt(final_prompt)
        system, prompt = render_messages_to_system_prompt(messages)

        try:
            stream_iter = (
                stream_fn(system, prompt, max_tokens=self._max_tokens)
                if self._max_tokens is not None
                else stream_fn(system, prompt)
            )
            async for chunk in stream_iter:
                if chunk:
                    yield chunk
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - wrap raw LLM/vendor errors
            raise FinalProviderError(f"final answer streaming failed: {exc}") from exc

    def build_final_answer(self, final_prompt: FinalPrompt, text: str) -> FinalAnswer:
        """Assemble a FinalAnswer from complete answer text (streamed or not).

        Preserves provider/model/finish_reason, extracts the citations actually
        used from the text, and reports char-based usage. Used at completion once
        the live chunks finish, so streamed and non-streamed answers match.
        """
        text = text or ""
        messages = render_final_prompt(final_prompt)
        system, prompt = render_messages_to_system_prompt(messages)
        valid_ids = {c.id for c in final_prompt.citations} | {e.id for e in final_prompt.evidence_sections}
        used = sorted(set(_CITATION_RE.findall(text)) & valid_ids)
        return FinalAnswer(
            text=text,
            used_citations=used,
            usage_metadata={
                "prompt_chars": len(system) + len(prompt),
                "completion_chars": len(text),
            },
            provider=self.provider,
            model=self.model,
            finish_reason="stop",
            metadata={"adapter": "v15"},
        )
