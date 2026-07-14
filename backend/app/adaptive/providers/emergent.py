"""Emergent Universal LLM key adapter.

Uses ``emergentintegrations.llm.chat.LlmChat`` — which is a thin wrapper on
LiteLLM's async completion API — as the transport.

Tool calling here is NOT JSON-prose emulation: LiteLLM returns real
structured ``tool_calls`` from the model's native tool-call protocol
(Anthropic's tool_use blocks for Claude, OpenAI's tool_calls for GPT).
See ``LlmChat.send_message_with_tools`` and ``LlmChat._parse_tool_response``.

We route every call through a *fresh* LlmChat session and pass the full
message history explicitly, mirroring how the LangGraph state carries the
conversation. This means state persistence and replay live in the graph
checkpointer, not in the SDK.
"""

from __future__ import annotations

import logging
from typing import Any

from emergentintegrations.llm.chat import (
    ChatError,
    LlmChat,
    UserMessage,
)

from app.adaptive.config import adaptive
from app.adaptive.providers.base import ChatOutput, ChatProvider, ToolCall

log = logging.getLogger("runner.adaptive.provider.emergent")


def _split_system_and_history(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Emergent LlmChat takes system_message as a constructor arg. Extract
    the first system message and return the remaining history verbatim."""
    system = ""
    rest: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "system" and not system:
            system = str(m.get("content") or "")
            continue
        rest.append(m)
    return system, rest


def _pop_last_user_message(history: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], UserMessage | None]:
    """Split history into (initial_messages, final_user_message).

    LlmChat expects an initial_messages list + one final UserMessage per
    send_message call. When the last message is a tool result we pass None
    for the user message (continuation turn) — but our adaptive graph
    replays the whole history each turn, so we always locate the trailing
    user message and hand the earlier messages as initial_messages.
    """
    if not history:
        return [], None

    # Locate the LAST user message; everything before it becomes the
    # initial_messages history. Anything after (assistant/tool turns) is
    # appended into initial_messages too, and the final call sees them
    # already in state. However for the "user asks -> tool call -> tool
    # result -> ask again" loop, the LAST message is the tool result;
    # in that case we send no user message.
    last_role = history[-1].get("role")
    if last_role == "tool":
        return list(history), None
    if last_role == "user":
        initial = list(history[:-1])
        content = history[-1].get("content") or ""
        return initial, UserMessage(text=str(content))

    # Fallback: assistant-terminated history. Emergent requires a user
    # message on the next turn, so we synthesize a lightweight nudge.
    return list(history), UserMessage(text="Continue.")


class EmergentProvider:
    provider_id = "emergent"

    def __init__(self) -> None:
        self._api_key = adaptive.llm_api_key
        self._model = adaptive.llm_model
        # LiteLLM identifies Anthropic models by the "anthropic" provider prefix.
        # Emergent's proxy dispatches on that same convention.
        self._litellm_provider = "anthropic" if self._model.startswith("claude") else "openai"

    async def invoke(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> ChatOutput:
        system, history = _split_system_and_history(messages)
        initial_messages, user_msg = _pop_last_user_message(history)

        # Session id is deterministic per invocation (we replay from state,
        # never persist inside the SDK's history buffer).
        session_id = f"adaptive:{id(messages):x}"
        chat = LlmChat(
            api_key=self._api_key,
            session_id=session_id,
            system_message=system or "You are Runner.ai's adaptive agent.",
            initial_messages=initial_messages or None,
        ).with_model(self._litellm_provider, self._model)
        if max_tokens:
            chat = chat.with_params(max_tokens=max_tokens)

        if tools:
            chat.with_tools(tools)

        try:
            if tools:
                if user_msg is None and (not initial_messages or initial_messages[-1].get("role") != "tool"):
                    # Guardrail: LlmChat.send_message_with_tools requires
                    # either a user message or a trailing tool result.
                    raise ChatError("no user or tool-result message to send")
                response = await chat.send_message_with_tools(user_msg)
            else:
                # No tools bound → plain completion.
                text = await chat.send_message(user_msg or UserMessage(text="Continue."))
                return ChatOutput(
                    content=text if isinstance(text, str) else getattr(text, "text", str(text)),
                    tool_calls=[],
                    finish_reason="stop",
                    provider=self.provider_id,
                )
        except ChatError as exc:
            log.warning("Emergent chat error: %s", exc)
            raise

        tool_calls = [
            ToolCall(id=tc.id, name=tc.name, arguments=dict(tc.arguments or {}))
            for tc in (response.tool_calls or [])
        ]
        usage = None
        if response.usage is not None:
            try:
                usage = {
                    "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
                    "completion_tokens": getattr(response.usage, "completion_tokens", None),
                    "total_tokens": getattr(response.usage, "total_tokens", None),
                }
            except Exception:  # noqa: BLE001
                usage = None

        return ChatOutput(
            content=response.content or "",
            tool_calls=tool_calls,
            finish_reason=response.finish_reason or ("tool_calls" if tool_calls else "stop"),
            usage=usage,
            provider=self.provider_id,
        )


def _make_emergent() -> ChatProvider:
    return EmergentProvider()
