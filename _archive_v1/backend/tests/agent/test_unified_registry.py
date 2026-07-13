"""Phase 40 tests — UnifiedCapabilityRegistry.

Config-free: fake capability sources feed the unified registry over a plain
ToolRegistry. Verifies mount/unmount, duplicate detection, namespace isolation,
source ownership, atomic refresh, and failed-refresh rollback — with no LLM, DB,
or settings.
"""

import asyncio

import pytest

from app.agent.models.tool_spec import RiskLevel, SideEffectType, ToolKind, ToolSpec
from app.agent.registry.registry import ToolRegistry
from app.agent.registry.sources import CapabilitySource
from app.agent.registry.unified import (
    CapabilityCollisionError,
    DuplicateSourceError,
    NamespaceViolationError,
    UnifiedCapabilityRegistry,
    UnknownSourceError,
)


def run(coro):
    return asyncio.run(coro)


def spec(tool_id, *, kind=ToolKind.MCP):
    return ToolSpec(
        id=tool_id, name=tool_id.split(".")[-1], kind=kind, description="d",
        input_schema={}, output_schema={}, risk_level=RiskLevel.LOW,
        side_effects=SideEffectType.READ, requires_approval=False,
    )


class FakeSource(CapabilitySource):
    """A scripted capability source for registry tests."""

    def __init__(self, source_id, namespace, specs, *, kind=ToolKind.MCP,
                 strict=True, fail_reload=False):
        self.source_id = source_id
        self.namespace = namespace
        self.tool_kind = kind
        self.strict_namespace = strict
        self._current = list(specs)
        self._fail_reload = fail_reload
        self.closed = 0
        self.executor = object()

    def set_specs(self, specs):
        self._current = list(specs)

    def _specs(self):
        return list(self._current)

    async def reload(self):
        if self._fail_reload:
            raise RuntimeError("discovery failed")
        return self.snapshot()

    async def close(self):
        self.closed += 1

    def build_executor(self):
        return self.executor


def unified():
    return UnifiedCapabilityRegistry(ToolRegistry())


# --------------------------------------------------------------------------- #
# Mount / unmount
# --------------------------------------------------------------------------- #

def test_mount_registers_specs_into_shared_registry():
    reg = unified()
    reg.mount_preloaded(FakeSource("mcp", "mcp", [spec("mcp.gh.create"), spec("mcp.gh.list")]))
    assert reg.tool_registry.exists("mcp.gh.create")
    assert reg.owned_ids("mcp") == ["mcp.gh.create", "mcp.gh.list"]
    assert reg.owner_of("mcp.gh.create") == "mcp"
    assert reg.namespaces() == {"mcp": "mcp"}


def test_mount_async_uses_load():
    reg = unified()
    src = FakeSource("mcp", "mcp", [spec("mcp.gh.create")])
    run(reg.mount(src))
    assert reg.tool_registry.exists("mcp.gh.create")


def test_unmount_removes_only_that_sources_tools():
    reg = unified()
    a = FakeSource("mcp", "mcp", [spec("mcp.gh.create")])
    b = FakeSource("future", "future", [spec("future.cal.add")], kind=ToolKind.API)
    reg.mount_preloaded(a)
    reg.mount_preloaded(b)
    run(reg.unmount("mcp"))
    assert not reg.tool_registry.exists("mcp.gh.create")
    assert reg.tool_registry.exists("future.cal.add")  # other source untouched
    assert a.closed == 1
    assert "mcp" not in reg.namespaces()


def test_unmount_unknown_source_raises():
    with pytest.raises(UnknownSourceError):
        run(unified().unmount("nope"))


# --------------------------------------------------------------------------- #
# Duplicate / collision / namespace isolation
# --------------------------------------------------------------------------- #

def test_duplicate_source_id_rejected():
    reg = unified()
    reg.mount_preloaded(FakeSource("mcp", "mcp", [spec("mcp.a.x")]))
    with pytest.raises(DuplicateSourceError):
        reg.mount_preloaded(FakeSource("mcp", "mcp2", [spec("mcp2.a.x")]))


def test_duplicate_namespace_rejected():
    reg = unified()
    reg.mount_preloaded(FakeSource("mcp", "mcp", [spec("mcp.a.x")]))
    with pytest.raises(DuplicateSourceError):
        reg.mount_preloaded(FakeSource("mcp-other", "mcp", [spec("mcp.b.y")]))


def test_intra_batch_duplicate_id_rejected():
    reg = unified()
    with pytest.raises(CapabilityCollisionError):
        reg.mount_preloaded(FakeSource("mcp", "mcp", [spec("mcp.a.x"), spec("mcp.a.x")]))


def test_strict_namespace_violation_rejected():
    reg = unified()
    with pytest.raises(NamespaceViolationError):
        reg.mount_preloaded(FakeSource("mcp", "mcp", [spec("notmcp.a.x")]))


def test_source_cannot_claim_another_sources_id():
    reg = unified()
    reg.mount_preloaded(FakeSource("mcp", "mcp", [spec("mcp.a.x")]))
    # a non-strict source trying to register an id owned by mcp
    intruder = FakeSource("evil", "evil", [spec("mcp.a.x")], strict=False)
    with pytest.raises(CapabilityCollisionError):
        reg.mount_preloaded(intruder)


def test_foreign_preexisting_id_rejected():
    base = ToolRegistry()
    base.register(spec("mcp.a.x"))  # present but unowned by any source
    reg = UnifiedCapabilityRegistry(base)
    with pytest.raises(CapabilityCollisionError):
        reg.mount_preloaded(FakeSource("mcp", "mcp", [spec("mcp.a.x")]))


def test_non_strict_source_allows_flat_ids():
    reg = unified()
    reg.mount_preloaded(FakeSource("internal", "internal",
                                   [spec("search_documents", kind=ToolKind.INTERNAL)],
                                   kind=ToolKind.INTERNAL, strict=False))
    assert reg.tool_registry.exists("search_documents")


# --------------------------------------------------------------------------- #
# Refresh (atomic) + rollback
# --------------------------------------------------------------------------- #

def test_refresh_replaces_stale_specs():
    reg = unified()
    src = FakeSource("mcp", "mcp", [spec("mcp.a.old")])
    reg.mount_preloaded(src)
    src.set_specs([spec("mcp.a.new")])
    run(reg.refresh("mcp"))
    assert not reg.tool_registry.exists("mcp.a.old")
    assert reg.tool_registry.exists("mcp.a.new")
    assert reg.owned_ids("mcp") == ["mcp.a.new"]


def test_failed_refresh_keeps_previous_capabilities():
    reg = unified()
    src = FakeSource("mcp", "mcp", [spec("mcp.a.good")], fail_reload=True)
    reg.mount_preloaded(src)
    with pytest.raises(RuntimeError):
        run(reg.refresh("mcp"))
    # discovery failed → old capability remains active (no corruption/removal)
    assert reg.tool_registry.exists("mcp.a.good")
    assert reg.owned_ids("mcp") == ["mcp.a.good"]


def test_refresh_validation_failure_keeps_old_and_does_not_partially_apply():
    reg = unified()
    src = FakeSource("mcp", "mcp", [spec("mcp.a.good")])
    reg.mount_preloaded(src)
    # new batch is invalid (intra-batch duplicate) → refresh must abort atomically,
    # validating the WHOLE batch before mutating the registry.
    src.set_specs([spec("mcp.a.new"), spec("mcp.a.new")])
    with pytest.raises(CapabilityCollisionError):
        run(reg.refresh("mcp"))
    assert reg.tool_registry.exists("mcp.a.good")       # old kept
    assert not reg.tool_registry.exists("mcp.a.new")    # new not partially applied
    assert reg.owned_ids("mcp") == ["mcp.a.good"]


def test_refresh_all_refreshes_each_source():
    reg = unified()
    a = FakeSource("mcp", "mcp", [spec("mcp.a.1")])
    b = FakeSource("future", "future", [spec("future.b.1", kind=ToolKind.API)], kind=ToolKind.API)
    reg.mount_preloaded(a)
    reg.mount_preloaded(b)
    a.set_specs([spec("mcp.a.2")])
    b.set_specs([spec("future.b.2", kind=ToolKind.API)])
    run(reg.refresh_all())
    assert reg.tool_registry.exists("mcp.a.2")
    assert reg.tool_registry.exists("future.b.2")


# --------------------------------------------------------------------------- #
# Read surface + lifecycle
# --------------------------------------------------------------------------- #

def test_list_and_resolve():
    reg = unified()
    reg.mount_preloaded(FakeSource("mcp", "mcp", [spec("mcp.a.x")]))
    assert [t.id for t in reg.list()] == ["mcp.a.x"]
    assert reg.resolve("mcp.a.x").id == "mcp.a.x"
    assert reg.resolve("missing") is None


def test_executors_by_kind_maps_each_source():
    reg = unified()
    a = FakeSource("internal", "internal", [spec("search_documents", kind=ToolKind.INTERNAL)],
                   kind=ToolKind.INTERNAL, strict=False)
    b = FakeSource("mcp", "mcp", [spec("mcp.a.x")])
    reg.mount_preloaded(a)
    reg.mount_preloaded(b)
    kmap = reg.executors_by_kind()
    assert kmap[ToolKind.INTERNAL] is a.executor
    assert kmap[ToolKind.MCP] is b.executor


def test_shutdown_closes_all_sources():
    reg = unified()
    a = FakeSource("mcp", "mcp", [spec("mcp.a.x")])
    b = FakeSource("future", "future", [spec("future.b.y", kind=ToolKind.API)], kind=ToolKind.API)
    reg.mount_preloaded(a)
    reg.mount_preloaded(b)
    run(reg.shutdown())
    assert a.closed == 1 and b.closed == 1
