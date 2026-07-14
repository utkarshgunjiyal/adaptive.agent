"""Adaptive LangGraph runtime for Runner.ai.

The legacy planner→execute→synthesize path stays intact in
``app.services.agent``. This package hosts the new adaptive loop where every
tool observation is returned to the LLM as a ``ToolMessage`` and the LLM
decides the next action.

Scope:
- provider-neutral chat-model factory over user-owned credentials
  (OpenRouter's OpenAI-compatible API or the direct Anthropic API)
- LangGraph state graph: load_context → agent ⇄ tools → finalize
- native bind_tools via the provider's actual tool-calling API
  (no JSON-prose emulation)
- MongoDB checkpointer (``langgraph-checkpoint-mongodb``)
- one adaptive tool bound: ``search_document_chunks``
- direct answer path (no tool) fully working
- guarded finalize (never marks a run completed with an empty answer)
- SSE endpoint at POST /api/agent/run/adaptive/stream
"""
