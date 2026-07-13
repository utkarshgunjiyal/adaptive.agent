"""LLM client wrapper over emergentintegrations.LlmChat.

Runner.ai uses gpt-5.2 via the Emergent Universal LLM key. We expose two
call shapes:

* ``complete(system, user, ...)`` — one-shot completion (planner, summarizer,
  synthesizer). Uses ``send_message`` because the caller needs the full
  response before continuing (e.g. to parse JSON, evaluate a plan).
* ``stream(system, user, ...)`` — token-streaming generator used by the SSE
  endpoint so the frontend can render tokens as they arrive.

Every call opens a fresh ``LlmChat`` session; message history is managed by
our own MongoDB thread/message collections and passed in explicitly on each
call. This keeps chat history persistent across processes.
"""

from __future__ import annotations

import json
import re
from typing import AsyncIterator

from emergentintegrations.llm.chat import (
    LlmChat,
    StreamDone,
    TextDelta,
    UserMessage,
)

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
    # Some providers return an object with `.text` attribute.
    return getattr(result, "text", str(result))


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
    """Try to parse a JSON object/array out of an LLM response.

    Handles common patterns:
    * raw JSON,
    * a fenced ```json ... ``` block,
    * JSON preceded by an explanatory sentence.
    """
    if text is None:
        return None
    text = text.strip()
    # Fenced?
    match = _JSON_FENCE.search(text)
    candidate = match.group(1).strip() if match else text
    # First open brace / bracket forward.
    for opener, closer in [("{", "}"), ("[", "]")]:
        start = candidate.find(opener)
        if start == -1:
            continue
        # Scan forward tracking nesting to find the matching close.
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
