"""Provider-neutral chat-model interface.

Every provider adapter exposes the same async method:

    invoke(messages, tools) -> ChatOutput

where ``messages`` is the current conversation (a list of dicts with the
LiteLLM/OpenAI-compatible ``{role, content, tool_calls, tool_call_id}``
shape) and ``tools`` is a list of OpenAI-format function-tool schemas.

The output is normalised so LangGraph nodes never depend on
provider-specific classes:

    ChatOutput(
        content: str,            # may be "" when only tool calls are emitted
        tool_calls: [ToolCall],  # 0..N calls
        finish_reason: str,      # "stop" | "tool_calls" | "length" | ...
        usage: dict | None,
    )

    ToolCall(id, name, arguments)

Because every provider (Anthropic / OpenAI / OpenRouter / Emergent) uses
the OpenAI-compatible tool-call schema under LiteLLM (and langchain-core
speaks the same), this envelope stays identical across providers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatOutput:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, Any] | None = None
    provider: str = ""

    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    def is_final(self) -> bool:
        return not self.tool_calls and bool((self.content or "").strip())


class ChatProvider(Protocol):
    provider_id: str

    async def invoke(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> ChatOutput:
        ...
