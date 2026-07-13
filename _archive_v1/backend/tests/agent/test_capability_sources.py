"""Phase 40 tests — capability sources + factory composition.

Config-free. Verifies the InternalCapabilitySource / MCPCapabilitySource
contracts and that the factory composes sources into one unified platform:
default runtime unchanged (internal only), MCP runtime enabled by composition,
retrieval/planner/execution wiring unchanged, and lifecycle (mount / refresh /
shutdown).
"""

import asyncio

from app.agent.capabilities.models import CapabilityRetrievalRequest
from app.agent.execution.capability_executor import (
    CompositeCapabilityExecutor,
    InternalCapabilityExecutor,
)
from app.agent.mcp.client import FakeMCPClient
from app.agent.mcp.models import MCPServerConfig, MCPToolDefinition, MCPTransport
from app.agent.mcp.registry import MCPRegistryManager
from app.agent.models.tool_spec import ToolKind
from app.agent.registry.registry import ToolRegistry
from app.agent.registry.sources import InternalCapabilitySource, MCPCapabilitySource
from app.agent.registry.unified import UnifiedCapabilityRegistry
from app.agent.retriever.capability_retriever import HybridCapabilityRetriever
from app.agent.runtime.direct_runtime import DirectRuntime
from app.agent.runtime.factory import build_capability_platform, build_default_runtime
from app.agent.runtime.planner_runtime import PlannerRuntime
from app.agent.tools.internal.specs import internal_tool_specs
from app.agent.tools.mcp_adapter import MCPAdapter


def run(coro):
    return asyncio.run(coro)


def cfg(server_id="github"):
    return MCPServerConfig(server_id=server_id, name=server_id,
                           transport=MCPTransport.STDIO, command=["srv"])


def gh_tools(*names):
    return [MCPToolDefinition(name=n, description=f"github {n}", input_schema={"type": "object"})
            for n in names]


def mcp_manager(tools):
    reg = ToolRegistry()
    client = FakeMCPClient(tools=tools)
    mgr = MCPRegistryManager(reg, client)
    return mgr, client


# --------------------------------------------------------------------------- #
# InternalCapabilitySource
# --------------------------------------------------------------------------- #

def test_internal_source_contract():
    src = InternalCapabilitySource()
    assert src.source_id == "internal"
    assert src.namespace == "internal"
    assert src.tool_kind == ToolKind.INTERNAL
    assert src.strict_namespace is False  # legacy flat ids
    ids = sorted(s.id for s in src.snapshot())
    assert ids == sorted(s.id for s in internal_tool_specs())
    assert isinstance(src.build_executor(), InternalCapabilityExecutor)


def test_internal_source_load_equals_snapshot():
    src = InternalCapabilitySource()
    assert [s.id for s in run(src.load())] == [s.id for s in src.snapshot()]


# --------------------------------------------------------------------------- #
# MCPCapabilitySource
# --------------------------------------------------------------------------- #

def test_mcp_source_discovers_via_load():
    mgr, _ = mcp_manager({"github": gh_tools("create_issue")})
    run(mgr.register_server(cfg("github")))
    src = MCPCapabilitySource(mgr)
    assert src.namespace == "mcp" and src.tool_kind == ToolKind.MCP
    assert src.snapshot() == []                 # nothing discovered yet
    specs = run(src.load())                     # discovery happens here
    assert [s.id for s in specs] == ["mcp.github.create_issue"]
    assert isinstance(src.build_executor(), MCPAdapter)


def test_mcp_source_reload_refreshes():
    mgr, client = mcp_manager({"github": gh_tools("old_tool")})
    run(mgr.register_server(cfg("github")))
    src = MCPCapabilitySource(mgr)
    run(src.load())
    client._tools["github"] = gh_tools("new_tool")
    specs = run(src.reload())
    assert [s.id for s in specs] == ["mcp.github.new_tool"]


def test_mcp_source_close_closes_manager():
    mgr, client = mcp_manager({"github": gh_tools("create_issue")})
    run(mgr.register_server(cfg("github")))
    src = MCPCapabilitySource(mgr)
    run(src.load())
    run(src.close())
    assert "github" in client.closed


# --------------------------------------------------------------------------- #
# Platform composition (async mount)
# --------------------------------------------------------------------------- #

def test_platform_mounts_internal_and_mcp():
    mgr, _ = mcp_manager({"github": gh_tools("create_issue")})
    run(mgr.register_server(cfg("github")))
    platform = UnifiedCapabilityRegistry()
    run(platform.mount(InternalCapabilitySource()))
    run(platform.mount(MCPCapabilitySource(mgr)))
    assert platform.tool_registry.exists("search_documents")            # internal
    assert platform.tool_registry.exists("mcp.github.create_issue")     # mcp
    assert platform.namespaces() == {"internal": "internal", "mcp": "mcp"}


def test_platform_refresh_picks_up_new_mcp_tools():
    mgr, client = mcp_manager({"github": gh_tools("create_issue")})
    run(mgr.register_server(cfg("github")))
    platform = UnifiedCapabilityRegistry()
    run(platform.mount(MCPCapabilitySource(mgr)))
    client._tools["github"] = gh_tools("create_issue", "list_repos")
    run(platform.refresh("mcp"))
    assert platform.tool_registry.exists("mcp.github.list_repos")


# --------------------------------------------------------------------------- #
# Factory composition
# --------------------------------------------------------------------------- #

def test_default_runtime_is_internal_only_and_unchanged():
    orch = build_default_runtime()
    assert isinstance(orch._direct_runtime._executor, InternalCapabilityExecutor)
    assert isinstance(orch._direct_runtime, DirectRuntime)
    assert isinstance(orch._planner_runtime, PlannerRuntime)
    # internal capabilities retrievable through the unchanged hybrid retriever
    assert isinstance(orch._capability_retriever, HybridCapabilityRetriever)
    resp = orch._capability_retriever.retrieve(
        CapabilityRetrievalRequest(query="search documents", top_k=5))
    assert any(m.tool.id == "search_documents" for m in resp.matches)


def test_mcp_runtime_enabled_by_composition():
    mgr, _ = mcp_manager({"github": gh_tools("create_github_issue")})
    run(mgr.register_server(cfg("github")))
    run(mgr.discover_server_tools("github"))
    orch = build_default_runtime(mcp_registry_manager=mgr)
    # by-kind execution bridge (internal + MCP)
    assert isinstance(orch._direct_runtime._executor, CompositeCapabilityExecutor)
    # both origins visible in ONE retrieval view
    resp = orch._capability_retriever.retrieve(
        CapabilityRetrievalRequest(query="create a github issue", top_k=8))
    ids = {m.tool.id for m in resp.matches}
    assert "mcp.github.create_github_issue" in ids


def test_factory_accepts_prebuilt_platform_for_lifecycle_ownership():
    mgr, client = mcp_manager({"github": gh_tools("create_github_issue")})
    run(mgr.register_server(cfg("github")))
    platform = build_capability_platform(mcp_registry_manager=mgr)  # sync, preloaded
    run(mgr.discover_server_tools("github"))                        # discover, then...
    run(platform.refresh("mcp"))                                    # ...sync platform sees it
    orch = build_default_runtime(capability_registry=platform)
    resp = orch._capability_retriever.retrieve(
        CapabilityRetrievalRequest(query="create a github issue", top_k=8))
    assert any(m.tool.id == "mcp.github.create_github_issue" for m in resp.matches)
    run(platform.shutdown())
    assert "github" in client.closed


def test_planner_and_direct_wiring_unchanged_with_mcp():
    mgr, _ = mcp_manager({"github": gh_tools("create_github_issue")})
    run(mgr.register_server(cfg("github")))
    run(mgr.discover_server_tools("github"))
    orch = build_default_runtime(mcp_registry_manager=mgr)
    # the planner path still orchestrates the same DirectRuntime object (unchanged)
    assert orch._planner_runtime._direct is orch._direct_runtime
    # planner provider is the same deterministic default (MCP does not change it)
    from app.agent.llm.planner_provider import DeterministicPlannerProvider
    assert isinstance(orch._planner_provider, DeterministicPlannerProvider)
