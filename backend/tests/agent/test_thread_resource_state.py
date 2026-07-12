"""Phase 46.3.2 — thread resource state + cross-turn resolution.

Proves that resources resolved for a SUCCESSFUL call are published into
thread-scoped state and reused by a later request in the same thread (no LLM);
that explicit resources always override memory; that memory never leaks across
threads or users; that failed/missing/ambiguous outcomes publish nothing; and that
publication diagnostics carry provenance but never values.
"""

import asyncio

from app.agent.github.arguments import GithubArgumentBuilder
from app.agent.github.enrich import github_spec_transform
from app.agent.github.identity import GithubIdentity
from app.agent.github.resolver import GithubResourceResolver
from app.agent.mcp.models import MCPServerConfig, MCPToolDefinition, MCPTransport
from app.agent.mcp.registry import convert_tool_definition
from app.agent.resources import (
    ArgumentBuilderRegistry,
    ResourceAwareArgumentBuilder,
    ResourcePublisher,
    ResourceResolverRegistry,
    ThreadResourceStore,
)
from app.agent.resources.models import Resource, ResourceSource
from app.agent.capabilities.models import CapabilityMatch, CapabilityRetrievalResponse
from app.agent.runtime.context import BehaviorPath, BehaviorProfile, RunContext
from app.agent.runtime.direct_runtime import DirectRuntime, ExecutionStatus
from app.agent.tools.result import AdapterResult, ErrorCode


def run(coro):
    return asyncio.run(coro)


_SCHEMAS = {
    "search_repositories": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    "list_issues": {"type": "object", "properties": {"owner": {}, "repo": {}, "state": {}}, "required": ["owner", "repo"]},
    "issue_read": {"type": "object", "properties": {"owner": {}, "repo": {}, "issue_number": {}}, "required": ["owner", "repo", "issue_number"]},
}


def _cfg():
    return MCPServerConfig(server_id="github", name="github", transport=MCPTransport.STDIO,
                          command=["srv"], timeout_seconds=5.0)


def spec(tool_name):
    td = MCPToolDefinition(name=tool_name, description="d", input_schema=_SCHEMAS[tool_name])
    return github_spec_transform(_cfg(), tool_name, convert_tool_definition(_cfg(), td))


IDENT = GithubIdentity(owner="octocat", source="deployment_setting")


class _Retriever:
    def __init__(self, tool):
        self._tool = tool

    def retrieve(self, request):
        return CapabilityRetrievalResponse(
            query=request.query, matches=[CapabilityMatch(tool=self._tool, score=10.0)])


class _Executor:
    def __init__(self, result=None):
        self._result = result or AdapterResult.ok(output={"items": []})
        self.calls = []

    async def execute(self, tool, args):
        self.calls.append((tool.id, dict(args)))
        return self._result


def _harness(identity=IDENT, store=None):
    store = store or ThreadResourceStore()
    resolvers = ResourceResolverRegistry()
    resolvers.register(GithubResourceResolver(identity=identity))
    builders = ArgumentBuilderRegistry()
    builders.register(GithubArgumentBuilder())
    pipeline = ResourceAwareArgumentBuilder(resolvers, builders, store=store)
    publisher = ResourcePublisher(store)
    return store, pipeline, publisher


def _ctx(text, *, user="u1", thread="t1"):
    c = RunContext.create(text, user_id=user, thread_id=thread)
    c.attach_behavior_profile(BehaviorProfile(path=BehaviorPath.DIRECT, reason="gh", confidence=1.0))
    return c


def _turn(pipeline, publisher, tool, text, *, user="u1", thread="t1", result=None):
    executor = _Executor(result)
    dr = DirectRuntime(_Retriever(tool), executor,
                       argument_builder=pipeline.build, execution_observer=publisher)
    rc = run(dr.run(_ctx(text, user=user, thread=thread)))
    return rc, executor


# --------------------------------------------------------------------------- #
# ThreadResourceStore isolation (unit)
# --------------------------------------------------------------------------- #

def test_store_scopes_by_user_thread_provider():
    store = ThreadResourceStore()
    r = Resource(type="repo", value="runner-ai", source=ResourceSource.PRIOR_OUTPUT, provider="github")
    store.publish("u1", "t1", "github", [r])
    assert store.remembered("u1", "t1", "github") == {"repo": "runner-ai"}
    assert store.remembered("u1", "t2", "github") == {}     # other thread
    assert store.remembered("u2", "t1", "github") == {}     # other user
    assert store.remembered("u1", "t1", "gmail") == {}      # other provider


def test_store_ignores_incomplete_scope():
    store = ThreadResourceStore()
    r = Resource(type="repo", value="x", source=ResourceSource.PRIOR_OUTPUT, provider="github")
    assert store.publish("u1", None, "github", [r]) == []   # no thread → not stored
    assert store.remembered("u1", None, "github") == {}


# --------------------------------------------------------------------------- #
# A. Cross-turn resolution in the same thread
# --------------------------------------------------------------------------- #

def test_find_repo_then_list_issues_resolves_from_thread_state():
    store, pipeline, publisher = _harness()
    # Turn 1: establish the active repository (succeeds → published).
    _turn(pipeline, publisher, spec("search_repositories"), "Find my runner-ai repository.")
    assert store.remembered("u1", "t1", "github") == {"owner": "octocat", "repo": "runner-ai"}

    # Turn 2: no repo in the request → filled from thread state, no LLM.
    rc, executor = _turn(pipeline, publisher, spec("list_issues"), "List open issues.")
    assert executor.calls, "list_issues should have executed"
    _, args = executor.calls[-1]
    assert args == {"owner": "octocat", "repo": "runner-ai", "state": "open"}
    assert rc.metadata["execution_status"] == ExecutionStatus.SUCCESS.value


# --------------------------------------------------------------------------- #
# B. Explicit resources override remembered ones
# --------------------------------------------------------------------------- #

def test_explicit_repo_overrides_remembered():
    store, pipeline, publisher = _harness()
    _turn(pipeline, publisher, spec("search_repositories"), "Find my runner-ai repository.")
    # Turn 2 names a DIFFERENT repo explicitly → memory must not win.
    _, executor = _turn(pipeline, publisher, spec("list_issues"), "List open issues in other-repo.")
    _, args = executor.calls[-1]
    assert args["repo"] == "other-repo"
    assert args["owner"] == "octocat"


# --------------------------------------------------------------------------- #
# C/D. No cross-thread / cross-user leakage
# --------------------------------------------------------------------------- #

def test_no_cross_thread_leakage():
    store, pipeline, publisher = _harness()
    _turn(pipeline, publisher, spec("search_repositories"), "Find my runner-ai repository.",
          thread="t1")
    # Same user, DIFFERENT thread → nothing remembered → clarify, no MCP call.
    rc, executor = _turn(pipeline, publisher, spec("list_issues"), "List open issues.",
                         thread="t2")
    assert executor.calls == []
    assert rc.metadata["execution_status"] == ExecutionStatus.NEEDS_USER.value


def test_no_cross_user_leakage():
    store, pipeline, publisher = _harness()
    _turn(pipeline, publisher, spec("search_repositories"), "Find my runner-ai repository.",
          user="u1", thread="t1")
    # DIFFERENT user, same thread id → nothing remembered → clarify, no MCP call.
    rc, executor = _turn(pipeline, publisher, spec("list_issues"), "List open issues.",
                         user="u2", thread="t1")
    assert executor.calls == []
    assert rc.metadata["execution_status"] == ExecutionStatus.NEEDS_USER.value


# --------------------------------------------------------------------------- #
# E/F. Failed / missing / ambiguous outcomes publish nothing
# --------------------------------------------------------------------------- #

def test_failed_execution_publishes_nothing():
    store, pipeline, publisher = _harness()
    failure = AdapterResult.failure(ErrorCode.UPSTREAM_ERROR, retryable=False)
    _turn(pipeline, publisher, spec("search_repositories"), "Find my runner-ai repository.",
          result=failure)
    assert store.remembered("u1", "t1", "github") == {}     # nothing published on failure


def test_missing_resource_turn_publishes_nothing_and_makes_no_call():
    store, pipeline, publisher = _harness(identity=GithubIdentity())  # unknown identity
    rc, executor = _turn(pipeline, publisher, spec("issue_read"), "Show issue 12.")
    assert executor.calls == []                              # missing owner/repo → no MCP call
    assert rc.metadata["execution_status"] == ExecutionStatus.NEEDS_USER.value
    assert store.remembered("u1", "t1", "github") == {}     # missing → nothing published


def test_ambiguous_turn_publishes_nothing():
    store, pipeline, publisher = _harness()
    # Inject an ambiguous repo-name context; the resolver must not guess or execute.
    executor = _Executor()
    dr = DirectRuntime(_Retriever(spec("list_issues")), executor,
                       argument_builder=pipeline.build, execution_observer=publisher)
    rc = _ctx("List issues in dup.")
    rc.metadata["resource_state"] = {"github_active_repositories": [
        {"owner": "a", "repo": "dup"}, {"owner": "b", "repo": "dup"}]}
    run(dr.run(rc))
    assert executor.calls == []
    assert store.remembered("u1", "t1", "github") == {}


# --------------------------------------------------------------------------- #
# G. Diagnostics carry provenance, never values
# --------------------------------------------------------------------------- #

def test_publication_diagnostics_have_types_not_values():
    store, pipeline, publisher = _harness()
    rc, _ = _turn(pipeline, publisher, spec("search_repositories"), "Find my runner-ai repository.")
    events = {e["event"]: e for e in rc.metadata.get("diagnostics", [])}
    assert "agent.resources_published" in events
    published = events["agent.resources_published"]
    assert set(published["published_types"]) == {"owner", "repo"}
    # provenance/type names only — resolved VALUES never appear in the event.
    assert "runner-ai" not in str(published) and "octocat" not in str(published)
