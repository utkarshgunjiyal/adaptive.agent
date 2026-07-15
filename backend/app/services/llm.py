"""LLM client wrapper over the provider-neutral factory.

Runner.ai talks to user-owned LLM credentials through LangChain chat models
(OpenRouter's OpenAI-compatible API or the direct Anthropic API). There is no
Emergent dependency here. We expose three call shapes used by the legacy
planner / summariser / synthesiser path:

* ``complete(system, user, ...)`` — one-shot completion. Waits for the full
  response before returning (e.g. to parse JSON, evaluate a plan).
* ``complete_json(system, user, schema=...)`` — one-shot completion asking the
  model to return JSON conforming to a Pydantic schema.
* ``stream(system, user, ...)`` — token-streaming generator used by the SSE
  endpoint so the frontend can render tokens as they arrive.

Each call builds a fresh, stateless chat model; conversation history lives in
our own MongoDB thread/message collections and is passed in explicitly, so
history stays persistent across processes.

When no provider is configured (``LLM_PROVIDER`` resolves to ``stub``) these
functions degrade to a deterministic, network-free response so the app remains
bootable and testable without credentials.
"""

from __future__ import annotations

import json
import re
from typing import AsyncIterator, Type

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.llm_factory import (
    LLMConfigError,
    get_chat_model,
    llm_config_problem,
    resolve_provider,
    stub_allowed,
)

# ``session_id`` is accepted for backwards compatibility with call sites but is
# unused: the models are stateless and history is supplied explicitly.


def _content_text(message) -> str:
    """Flatten a LangChain message's content to a plain string.

    Anthropic returns a list of content blocks; OpenAI returns a string.
    """
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" or "text" in block:
                    parts.append(str(block.get("text", "")))
        return "".join(parts)
    return "" if content is None else str(content)


def _use_stub() -> bool:
    """Whether the deterministic stub path should be used.

    Only when the provider resolves to ``stub`` AND stub mode is permitted for
    the current APP_ENV (development / test). In production a resolved-stub or
    otherwise-invalid LLM configuration is a hard error — we never silently
    return stub / demo answers.
    """
    if resolve_provider(settings) != "stub":
        return False
    if stub_allowed(settings):
        return True
    raise LLMConfigError(
        llm_config_problem(settings) or "stub LLM provider is not allowed in this environment"
    )


def _stub_answer(system: str, user: str) -> str:
    """Deterministic, network-free response used only in development / test."""
    snippet = (user or "").strip().replace("\n", " ")[:280]
    return (
        "[stub-llm] No LLM provider is configured; returning a deterministic "
        f"placeholder. Prompt was: {snippet}"
    )


async def complete(*, session_id: str, system: str, user: str, max_tokens: int | None = None) -> str:
    """One-shot completion — waits for the full response."""
    if _use_stub():
        return _stub_answer(system, user)
    model = get_chat_model(settings, max_tokens=max_tokens)
    result = await model.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
    return _content_text(result)


async def complete_json(
    *,
    session_id: str,
    system: str,
    user: str,
    schema: Type[BaseModel],
    max_tokens: int | None = None,
    retries: int = 1,
) -> BaseModel | None:
    """One-shot completion that MUST return JSON matching ``schema``.

    We ask the model for JSON, parse it, and validate against the Pydantic
    schema. On validation failure we retry with the error message appended to
    the prompt.
    """
    schema_json = json.dumps(schema.model_json_schema(), indent=2)[:4000]
    hard_system = (
        f"{system}\n\n"
        "You MUST respond with a single JSON object that conforms exactly "
        "to the following JSON schema. Do not include prose, do not include "
        "code fences, output ONLY the JSON object.\n\n"
        f"JSON SCHEMA:\n{schema_json}"
    )

    last_err: str | None = None
    prompt = user
    for attempt in range(retries + 1):
        try:
            raw = await complete(
                session_id=f"{session_id}#json{attempt}",
                system=hard_system,
                user=prompt,
                max_tokens=max_tokens,
            )
        except Exception:  # noqa: BLE001
            return None
        parsed = extract_json(raw)
        if parsed is None:
            last_err = "response was not parseable JSON"
        else:
            try:
                return schema.model_validate(parsed)
            except ValidationError as ve:
                last_err = str(ve)[:600]
        prompt = (
            f"{user}\n\n"
            f"Your previous attempt failed validation: {last_err}\n"
            "Return corrected JSON that satisfies the schema."
        )
    return None


async def stream(*, session_id: str, system: str, user: str) -> AsyncIterator[str]:
    """Yield token deltas as they arrive."""
    if _use_stub():
        for word in _stub_answer(system, user).split(" "):
            yield word + " "
        return
    model = get_chat_model(settings, streaming=True)
    async for chunk in model.astream([SystemMessage(content=system), HumanMessage(content=user)]):
        text = _content_text(chunk)
        if text:
            yield text


# ---- JSON extraction ------------------------------------------------------

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def extract_json(text: str) -> dict | list | None:
    """Try to parse a JSON object/array out of an LLM response."""
    if text is None:
        return None
    text = text.strip()
    match = _JSON_FENCE.search(text)
    candidate = match.group(1).strip() if match else text
    for opener, closer in [("{", "}"), ("[", "]")]:
        start = candidate.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(candidate)):
            ch = candidate[i]
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    slice_ = candidate[start : i + 1]
                    try:
                        return json.loads(slice_)
                    except json.JSONDecodeError:
                        break
    try:
        return json.loads(candidate)
    except Exception:  # noqa: BLE001
        return None
