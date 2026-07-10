"""Execution Bridge executors (Phase 13/39; relocated in Phase 40).

The runtime Execution Bridge contract is ``async execute(tool, args) ->
AdapterResult`` (see ``runtime/direct_runtime.py:CapabilityExecutor``). Two
composition-glue implementations live here:

- ``InternalCapabilityExecutor`` — routes an internal ``ToolSpec`` id to its
  Phase 13 V1.5 adapter capability.
- ``CompositeCapabilityExecutor`` — routes by ``ToolSpec.kind`` to the executor
  for that kind (internal / MCP / future). This is the single seam where
  non-internal capability sources join execution, keeping DirectRuntime /
  PlannerRuntime source-agnostic.

Relocated out of ``runtime/factory.py`` so capability *sources* can reference the
internal executor without importing the factory (which imports sources). The
factory re-exports both names, so existing imports keep working.

Config-free: the internal adapters lazy-import V1.5 only when executed.
"""

from app.agent.models.tool_spec import ToolKind, ToolSpec
from app.agent.tools.internal.document_adapter import DocumentAdapter
from app.agent.tools.internal.job_adapter import JobAdapter
from app.agent.tools.internal.memory_adapter import MemoryAdapter
from app.agent.tools.result import AdapterResult, ErrorCode


class InternalCapabilityExecutor:
    """Execution Bridge for internal tools: binds a ToolSpec id to a Phase 13
    internal adapter capability and returns its AdapterResult.

    Composition glue only — each adapter owns the actual V1.5 call and its
    exception→AdapterResult translation; this just routes by tool id. Satisfies
    DirectRuntime's CapabilityExecutor contract (async execute(tool, args)).
    """

    def __init__(
        self,
        *,
        document_adapter: DocumentAdapter | None = None,
        job_adapter: JobAdapter | None = None,
        memory_adapter: MemoryAdapter | None = None,
    ) -> None:
        documents = document_adapter or DocumentAdapter()
        jobs = job_adapter or JobAdapter()
        memory = memory_adapter or MemoryAdapter()
        # ToolSpec.id -> (adapter, adapter-capability id)
        self._bindings = {
            "search_documents": (documents, DocumentAdapter.RETRIEVE_CHUNKS),
            "get_document_summary": (documents, DocumentAdapter.GET_SUMMARY),
            "get_job_status": (jobs, JobAdapter.GET_STATUS),
            "get_thread_summary": (memory, MemoryAdapter.GET_THREAD_SUMMARY),
            "get_user_preferences": (memory, MemoryAdapter.GET_PREFERENCES),
        }

    def bound_tool_ids(self) -> list[str]:
        return sorted(self._bindings.keys())

    async def execute(self, tool: ToolSpec, args: dict) -> AdapterResult:
        binding = self._bindings.get(tool.id)
        if binding is None:
            return AdapterResult.failure(
                ErrorCode.UNKNOWN_CAPABILITY,
                retryable=False,
                metadata={"tool_id": tool.id, "reason": "no internal adapter binding"},
            )
        adapter, capability = binding
        return await adapter.execute(capability, args)


class CompositeCapabilityExecutor:
    """Kind-routing Execution Bridge (Phase 39).

    Dispatches on ``ToolSpec.kind`` to the executor for that kind — the live-path
    analogue of the Phase 8 AdapterRegistry (which returns ``dict``; this returns
    ``AdapterResult``). Internal tools route to the existing
    ``InternalCapabilityExecutor``; MCP tools route to the ``MCPAdapter``; future
    sources register their own kind. This is the single seam where non-internal
    sources join execution, keeping DirectRuntime/PlannerRuntime source-agnostic.
    """

    def __init__(self, executors: dict[ToolKind, object]) -> None:
        self._executors = dict(executors)

    async def execute(self, tool: ToolSpec, args: dict) -> AdapterResult:
        executor = self._executors.get(tool.kind)
        if executor is None:
            return AdapterResult.failure(
                ErrorCode.UNKNOWN_CAPABILITY,
                retryable=False,
                metadata={"tool_id": tool.id, "kind": tool.kind.value,
                          "reason": "no executor registered for tool kind"},
            )
        return await executor.execute(tool, args)
