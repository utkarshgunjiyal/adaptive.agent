"""LangChain-backed chat provider for the adaptive runtime.

A single adapter serves both supported providers (OpenRouter via ``ChatOpenAI``
and the direct Anthropic API via ``ChatAnthropic``) because both are LangChain
``BaseChatModel`` instances that speak the same ``bind_tools`` / structured
tool-call protocol.

Tool calling here is NATIVE — never JSON scraped from ordinary model text. We
bind the OpenAI-format tool schemas with ``model.bind_tools(...)`` and read the
structured ``AIMessage.tool_calls`` the provider returns. Tool-call ids round-
trip through the adaptive graph state so the matching ``ToolMessage`` /
``tool_call_id`` pairing (required by Anthropic's tool_use/tool_result and
OpenAI's tool_calls protocols) is preserved.

The adapter converts between the graph's OpenAI-compatible message dicts and
LangChain message objects, and normalises the response back into the provider-
neutral :class:`ChatOutput` envelope so no graph node depends on a
provider-specific class.
"""

from __future__ import annotations

import json
import logging
import uuid as _uuid
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.adaptive.providers.base import ChatOutput, ChatProvider, ToolCall

log = logging.getLogger("runner.adaptive.provider.langchain")


def _coerce_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _to_lc_messages(messages: list[dict[str, Any]]) -> list[BaseMessage]:
    """Convert OpenAI-format message dicts to LangChain message objects."""
    out: list[BaseMessage] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content") or ""
        if role == "system":
            out.append(SystemMessage(content=content))
        elif role == "user":
            out.append(HumanMessage(content=content))
        elif role == "assistant":
            tool_calls = []
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                tool_calls.append(
                    {
                        "id": tc.get("id") or _uuid.uuid4().hex,
                        "name": fn.get("name") or "",
                        "args": _coerce_args(fn.get("arguments")),
                    }
                )
            out.append(AIMessage(content=content, tool_calls=tool_calls))
        elif role == "tool":
            out.append(
                ToolMessage(
                    content=content,
                    tool_call_id=m.get("tool_call_id") or "",
                )
            )
        # Unknown roles are dropped defensively.
    return out


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and (block.get("type") == "text" or "text" in block):
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return "" if content is None else str(content)


def _to_chat_output(ai: AIMessage, provider_id: str) -> ChatOutput:
    tool_calls: list[ToolCall] = []
    for tc in ai.tool_calls or []:
        tool_calls.append(
            ToolCall(
                id=tc.get("id") or _uuid.uuid4().hex,
                name=tc.get("name") or "",
                arguments=dict(tc.get("args") or {}),
            )
        )

    usage = None
    um = getattr(ai, "usage_metadata", None)
    if um:
        usage = {
            "prompt_tokens": um.get("input_tokens"),
            "completion_tokens": um.get("output_tokens"),
            "total_tokens": um.get("total_tokens"),
        }

    return ChatOutput(
        content=_content_to_text(ai.content) or "",
        tool_calls=tool_calls,
        finish_reason="tool_calls" if tool_calls else "stop",
        usage=usage,
        provider=provider_id,
    )


class LangChainChatProvider:
    """Adapts a LangChain ``BaseChatModel`` to the :class:`ChatProvider` API."""

    def __init__(self, model: Any, provider_id: str) -> None:
        self._model = model
        self.provider_id = provider_id

    async def invoke(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> ChatOutput:
        lc_messages = _to_lc_messages(messages)
        model = self._model
        if tools:
            # Native tool binding — the model returns structured tool_calls.
            model = model.bind_tools(tools)
        ai = await model.ainvoke(lc_messages)
        if not isinstance(ai, AIMessage):  # pragma: no cover - defensive
            ai = AIMessage(content=_content_to_text(getattr(ai, "content", ai)))
        return _to_chat_output(ai, self.provider_id)


def make_provider(model: Any, provider_id: str) -> ChatProvider:
    return LangChainChatProvider(model, provider_id)
