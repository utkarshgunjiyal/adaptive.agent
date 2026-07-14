"""Adaptive LangGraph runtime for Runner.ai.

The legacy planner→execute→synthesize path stays intact in
``app.services.agent``. This package hosts the new adaptive loop where every
tool observation is returned to the LLM as a ``ToolMessage`` and the LLM
decides the next action.

Phase 1 scope (this commit):
- provider-neutral chat-model factory (Emergent Claude Sonnet 4.5 today,
  plus Anthropic and OpenRouter env hooks)
- LangGraph state graph: load_context → agent ⇄ tools → finalize
- native bind_tools via the provider's actual tool-calling API
  (no JSON-prose emulation)
- MongoDB checkpointer (``langgraph-checkpoint-mongodb``)
- one adaptive tool bound: ``search_document_chunks``
- direct answer path (no tool) fully working
- guarded finalize (never marks a run completed with an empty answer)
- SSE endpoint at POST /api/agent/run/adaptive/stream
"""
