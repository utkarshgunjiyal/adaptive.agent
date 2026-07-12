"""Phase 46.3.2.2 — GitHub repository normalization hardening.

The canonical normalized repository must carry a valid owner + name whenever they
can be derived safely from trusted MCP output — including when the server trims the
nested owner object but sends ``full_name = "owner/repo"``. Malformed values must
never become invented resources. These tests also confirm the downstream chain
(extractor → publisher → thread store) resolves cross-turn once owner is present.
"""

import asyncio

import pytest

from app.agent.github.enrich import github_spec_transform
from app.agent.github.identity import GithubIdentity
from app.agent.github.normalize import (
    github_result_normalizer,
    normalize_repository,
    normalize_tool_result,
)
from app.agent.github.publication import GithubResourceExtractor
from app.agent.github.arguments import GithubArgumentBuilder
from app.agent.github.resolver import GithubResourceResolver
from app.agent.mcp.models import MCPServerConfig, MCPToolCallResult, MCPToolDefinition, MCPTransport
from app.agent.mcp.registry import convert_tool_definition
from app.agent.capabilities.models import CapabilityMatch, CapabilityRetrievalResponse
from app.agent.resources import (
    ArgumentBuilderRegistry,
    ResourceAwareArgumentBuilder,
    ResourceExtractorRegistry,
    ResourcePublisher,
    ResourceResolverRegistry,
    ThreadResourceStore,
)
from app.agent.runtime.context import BehaviorPath, BehaviorProfile, RunContext
from app.agent.runtime.direct_runtime import DirectRuntime, ExecutionStatus
from app.agent.tools.result import AdapterResult


def run(coro):
    return asyncio.run(coro)


def _cfg():
    return MCPServerConfig(server_id="github", name="github", transport=MCPTransport.STDIO,
                          command=["srv"], timeout_seconds=5.0)


def spec(tool_name, schema):
    td = MCPToolDefinition(name=tool_name, description="d", input_schema=schema)
    return github_spec_transform(_cfg(), tool_name, convert_tool_definition(_cfg(), td))


_SEARCH_SPEC = spec("search_repositories", {"type": "object", "properties": {"query": {}}, "required": ["query"]})
_ISSUES_SPEC = spec("list_issues", {"type": "object", "properties": {"owner": {}, "repo": {}, "state": {}}, "required": ["owner", "repo"]})


# --------------------------------------------------------------------------- #
# 1–2. Structured owner unchanged
# --------------------------------------------------------------------------- #

def test_nested_owner_object_unchanged():
    r = normalize_repository({"owner": {"login": "utkarshgunjiyal"}, "name": "runner-ai"})
    assert r["owner"] == "utkarshgunjiyal" and r["name"] == "runner-ai"
    assert r["full_name"] == "utkarshgunjiyal/runner-ai"


def test_scalar_owner_unchanged():
    r = normalize_repository({"owner": "utkarshgunjiyal", "name": "runner-ai"})
    assert r["owner"] == "utkarshgunjiyal" and r["name"] == "runner-ai"


# --------------------------------------------------------------------------- #
# 3–5. Derive from full_name
# --------------------------------------------------------------------------- #

def test_missing_owner_derived_from_full_name():
    r = normalize_repository({"name": "runner-ai", "full_name": "utkarshgunjiyal/runner-ai"})
    assert r["owner"] == "utkarshgunjiyal" and r["name"] == "runner-ai"
    assert r["full_name"] == "utkarshgunjiyal/runner-ai"


def test_missing_name_derived_from_full_name():
    r = normalize_repository({"owner": {"login": "utkarshgunjiyal"}, "full_name": "utkarshgunjiyal/runner-ai"})
    assert r["owner"] == "utkarshgunjiyal" and r["name"] == "runner-ai"


def test_both_missing_derived_from_full_name():
    r = normalize_repository({"full_name": "utkarshgunjiyal/runner-ai"})
    assert r["owner"] == "utkarshgunjiyal" and r["name"] == "runner-ai"


# --------------------------------------------------------------------------- #
# 6. Precedence: validated structured fields beat a conflicting full_name
# --------------------------------------------------------------------------- #

def test_structured_fields_take_precedence_over_conflicting_full_name():
    r = normalize_repository({"owner": {"login": "realowner"}, "name": "runner-ai",
                              "full_name": "other-owner/other-repo"})
    assert r["owner"] == "realowner" and r["name"] == "runner-ai"
    # full_name is recomputed canonically from the chosen owner/name.
    assert r["full_name"] == "realowner/runner-ai"


# --------------------------------------------------------------------------- #
# 7. Malformed full_name never invents resources
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("full_name", ["", "runner-ai", "/runner-ai", "owner/", "owner/repo/extra", "  /  ", "   "])
def test_malformed_full_name_does_not_invent(full_name):
    r = normalize_repository({"full_name": full_name})
    assert r["owner"] == "" and r["name"] == ""


def test_invalid_structured_owner_falls_back_to_valid_full_name():
    r = normalize_repository({"owner": {"login": "bad owner!"}, "full_name": "utkarshgunjiyal/runner-ai"})
    assert r["owner"] == "utkarshgunjiyal" and r["name"] == "runner-ai"


# --------------------------------------------------------------------------- #
# 8. Normalized repo feeds the extractor and yields owner + repo
# --------------------------------------------------------------------------- #

def _repos_result(items):
    return MCPToolCallResult(success=True, structured_content={"items": items})


def test_normalized_repo_feeds_extractor_owner_and_repo():
    # Trimmed payload: full_name only (the live shape) → normalizer derives owner.
    output = normalize_tool_result("search_repositories",
                                   _repos_result([{"name": "runner-ai", "full_name": "utkarshgunjiyal/runner-ai"}]))
    got = GithubResourceExtractor().extract(_SEARCH_SPEC, {"repo": "runner-ai"}, output)
    assert {(r.type, r.value) for r in got} == {("owner", "utkarshgunjiyal"), ("repo", "runner-ai")}


# --------------------------------------------------------------------------- #
# 9. Broad multi-repo listing behaviour unchanged (no active repo)
# --------------------------------------------------------------------------- #

def test_multi_repo_output_still_yields_no_active_repo():
    output = normalize_tool_result("search_repositories", _repos_result([
        {"name": "one", "full_name": "a/one"}, {"name": "two", "full_name": "b/two"},
        {"name": "three", "full_name": "c/three"}]))
    got = GithubResourceExtractor().extract(_SEARCH_SPEC, {}, output)  # no requested repo
    assert got == []


# --------------------------------------------------------------------------- #
# 10. End-to-end cross-turn regression through DirectRuntime
# --------------------------------------------------------------------------- #

class _Retriever:
    def __init__(self, tool):
        self._tool = tool

    def retrieve(self, request):
        return CapabilityRetrievalResponse(query=request.query, matches=[CapabilityMatch(tool=self._tool, score=9.0)])


class _Executor:
    def __init__(self, result):
        self._result = result
        self.calls = []

    async def execute(self, tool, args):
        self.calls.append((tool.id, dict(args)))
        return self._result


def _pipeline_and_publisher(store):
    resolvers = ResourceResolverRegistry()
    resolvers.register(GithubResourceResolver(identity=GithubIdentity()))  # UNKNOWN identity
    builders = ArgumentBuilderRegistry()
    builders.register(GithubArgumentBuilder())
    extractors = ResourceExtractorRegistry()
    extractors.register(GithubResourceExtractor())
    pipeline = ResourceAwareArgumentBuilder(resolvers, builders, store=store)
    return pipeline, ResourcePublisher(store, extractors=extractors)


def _ctx(text):
    c = RunContext.create(text, user_id="u1", thread_id="t1")
    c.attach_behavior_profile(BehaviorProfile(path=BehaviorPath.DIRECT, reason="gh", confidence=1.0))
    return c


def test_end_to_end_cross_turn_lookup_then_list_issues():
    store = ThreadResourceStore()
    pipeline, publisher = _pipeline_and_publisher(store)

    # Turn 1: trimmed search output (full_name only) — owner must survive to the store.
    out = normalize_tool_result("search_repositories",
                                _repos_result([{"name": "runner-ai", "full_name": "utkarshgunjiyal/runner-ai"}]))
    dr1 = DirectRuntime(_Retriever(_SEARCH_SPEC), _Executor(AdapterResult.ok(output=out)),
                        argument_builder=pipeline.build, execution_observer=publisher)
    run(dr1.run(_ctx("Find my runner-ai repository.")))
    assert store.remembered("u1", "t1", "github") == {"owner": "utkarshgunjiyal", "repo": "runner-ai"}

    # Turn 2: no repo in the request → resolved from thread state → list_issues executes.
    ex2 = _Executor(AdapterResult.ok(output={"kind": "issues", "issues": []}))
    dr2 = DirectRuntime(_Retriever(_ISSUES_SPEC), ex2,
                        argument_builder=pipeline.build, execution_observer=publisher)
    rc = run(dr2.run(_ctx("List open issues.")))
    assert ex2.calls and ex2.calls[-1][1] == {"owner": "utkarshgunjiyal", "repo": "runner-ai", "state": "open"}
    assert rc.metadata["execution_status"] == ExecutionStatus.SUCCESS.value


# --------------------------------------------------------------------------- #
# 11. No secret / raw private payload leaks through normalization
# --------------------------------------------------------------------------- #

def test_no_secret_leaks_through_normalization():
    raw = {"name": "runner-ai", "full_name": "utkarshgunjiyal/runner-ai",
           "token": "ghp_LEAK", "node_id": "secret", "owner": {"login": "utkarshgunjiyal", "email": "x@y.z"}}
    output, evidence = github_result_normalizer("search_repositories", _repos_result([raw]))
    blob = str(output) + evidence[0].content
    assert "ghp_LEAK" not in blob and "node_id" not in blob and "x@y.z" not in blob
    assert output["repositories"][0]["owner"] == "utkarshgunjiyal"
