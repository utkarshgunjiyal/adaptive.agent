"""LLM client wrapper over emergentintegrations.LlmChat.

Runner.ai uses gpt-5.2 via the Emergent Universal LLM key. We expose three
call shapes:

* ``complete(system, user, ...)`` — one-shot completion (planner, summarizer,
  synthesizer). Uses ``send_message`` because the caller needs the full
  response before continuing (e.g. to parse JSON, evaluate a plan).
* ``complete_json(system, user, schema=...)`` — one-shot completion asking
  the model to return JSON conforming to a Pydantic schema. This is used
  by the planner so an invalid plan is rare.
* ``stream(system, user, ...)`` — token-streaming generator used by the SSE
  endpoint so the frontend can render tokens as they arrive.

Every call opens a fresh ``LlmChat`` session; message history is managed by
our own MongoDB thread/message collections and passed in explicitly on each
call. This keeps chat history persistent across processes.
"""

from __future__ import annotations

import json
import re
from typing import AsyncIterator, Type

from emergentintegrations.llm.chat import (
    LlmChat,
    StreamDone,
    TextDelta,
    UserMessage,
)
from pydantic import BaseModel, ValidationError

from app.config import settings


def _chat(session_id: str, system: str) -> LlmChat:
    return LlmChat(
        api_key=settings.emergent_llm_key,
        session_id=session_id,
        system_message=system,
    ).with_model(settings.llm_provider, settings.llm_model)


async def complete(*, session_id: str, system: str, user: str, max_tokens: int | None = None) -> str:
    """One-shot completion — waits for the full response."""
    chat = _chat(session_id, system)
    if max_tokens:
        chat = chat.with_params(max_tokens=max_tokens)
    result = await chat.send_message(UserMessage(text=user))
    if isinstance(result, str):
        return result
    return getattr(result, "text", str(result))


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

    We ask gpt-5.2 for JSON, parse it, and validate against the Pydantic
    schema. On validation failure we retry once with the error message
    appended to the prompt.
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
    chat = _chat(session_id, system)
    async for event in chat.stream_message(UserMessage(text=user)):
        if isinstance(event, TextDelta):
            yield event.content
        elif isinstance(event, StreamDone):
            break


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
