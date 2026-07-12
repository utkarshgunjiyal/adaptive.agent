"""Resource-aware argument pipeline (Phase 46.3.1; thread state in 46.3.2).

The single object injected into ``DirectRuntime`` as its ``argument_builder``. It
keeps the runtime's existing seam (``build(tool, run_context, default_args) ->
ArgumentBuildResult``) but internally runs the new layered flow:

    provider = provider_of(tool)
    ctx      = deterministic sources (request + thread state + hints)
    resolved = resolver_registry[provider].resolve(ctx)     # resolve WHAT resources
    result   = builder_registry[provider].build(resolved)   # shape onto the schema

A tool with no registered provider resolver (internal, or an unregistered MCP
server) is a passthrough — the caller's default args are returned unchanged.

Phase 46.3.2: when a ``ThreadResourceStore`` is present, the pipeline surfaces the
thread's remembered resources into the ``ResolutionContext`` (so the resolver can
fill owner/repo from a prior turn) and attaches the resolved resources to a
successful result so the runtime can publish them after execution.
"""

from __future__ import annotations

from app.agent.models.tool_spec import ToolSpec
from app.agent.resources.models import ResolutionContext
from app.agent.resources.resolver import (
    ArgumentBuilderRegistry,
    ResourceResolverRegistry,
    provider_of,
)
from app.agent.runtime import diagnostics
from app.agent.runtime.arguments import ArgumentBuildResult


class ResourceAwareArgumentBuilder:
    """Resolve resources, then build arguments — the DirectRuntime seam."""

    def __init__(
        self,
        resolvers: ResourceResolverRegistry,
        builders: ArgumentBuilderRegistry,
        *,
        store=None,
    ) -> None:
        self._resolvers = resolvers
        self._builders = builders
        # Optional ThreadResourceStore (Phase 46.3.2). None → no cross-turn memory
        # (behaviour is byte-identical to 46.3.1).
        self._store = store

    def build(self, tool: ToolSpec, run_context, default_args: dict) -> ArgumentBuildResult:
        provider = provider_of(tool)
        resolver = self._resolvers.for_provider(provider)
        builder = self._builders.for_provider(provider)
        if resolver is None or builder is None:
            # No provider resolution registered → leave the caller's args untouched.
            return ArgumentBuildResult.build_ok(default_args)

        ctx = self._context(provider, tool, run_context)
        diagnostics.resource_resolution_started(run_context, tool, provider=provider)
        resolved = resolver.resolve(ctx)
        diagnostics.resource_resolved(run_context, tool, resolved)

        result = builder.build(
            tool, resolved,
            planner_args=ctx.hints.get("capability_args") or {},
            request_text=ctx.user_request,
        )
        # Attach the resolved resources so a SUCCESSFUL execution can publish them
        # into thread state (Phase 46.3.2). Only on a buildable result; a
        # missing/ambiguous result never executes, so it never publishes.
        if result.ok and resolved.resources:
            published = [
                {"type": t, "value": r.value, "source": r.source.value}
                for t, r in resolved.resources.items()
            ]
            result = result.model_copy(update={"published_resources": published})
        return result

    def _context(self, provider: str, tool: ToolSpec, run_context) -> ResolutionContext:
        meta = getattr(run_context, "metadata", {}) or {}
        # Deterministic sources the resolver may read. ``remembered`` is the
        # thread's prior-output/thread-state resources (Phase 46.3.2), strictly
        # scoped to (user_id, thread_id, provider). Legacy/test hooks under
        # ``resource_state`` are merged for explicit ambiguity injection.
        execution_state: dict = {}
        if self._store is not None:
            remembered = self._store.remembered(
                getattr(run_context, "user_id", None),
                getattr(run_context, "thread_id", None),
                provider,
            )
            if remembered:
                execution_state["remembered"] = remembered
        legacy = meta.get("resource_state")
        if isinstance(legacy, dict):
            execution_state.update(legacy)

        hints = {}
        if isinstance(meta.get("capability_args"), dict):
            hints["capability_args"] = meta["capability_args"]
        return ResolutionContext(
            provider=provider,
            capability_id=getattr(tool, "id", ""),
            user_request=getattr(run_context, "user_request", "") or "",
            execution_state=execution_state,
            hints=hints,
        )
