"""Phase 31 tests — POST /agent/resume.

The run + resume requests share ONE in-memory coordinator (as production does via
the module-level singleton), so a run can pause and a later resume continues it.
Dependencies are overridden so the runtime executes without a DB or a real LLM.
"""

import ast
import inspect

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.capabilities.models import CapabilityMatch, CapabilityRetrievalResponse
from app.agent.checkpoint.store import InMemoryCheckpointStore
from app.agent.context.final_builder import FinalContextBuilder
from app.agent.evaluation.models import EvaluationReport, RepairAction, RepairDecision
from app.agent.gate.behavior_gate import BehaviorGate
from app.agent.llm.final_provider import DeterministicFinalProvider
from app.agent.models.tool_spec import RiskLevel, SideEffectType, ToolKind, ToolSpec
from app.agent.runtime.context import EvidenceItem, RunContext
from app.agent.runtime.direct_runtime import DirectRuntime
from app.agent.runtime.orchestrator import AgentOrchestrator
from app.agent.runtime.planner_runtime import PlannerRuntime
from app.agent.runtime.resume_coordinator import ResumeCoordinator
from app.agent.tools.result import AdapterResult
from app.routes import agent as agent_module
from app.routes.agent import get_resume_coordinator, router


class FakeContextEngine:
    async def build(self, user_request, user_id, thread_id=None, metadata=None):
        return RunContext.create(user_request, user_id=user_id, thread_id=thread_id,
                                 metadata=dict(metadata or {}))


def make_tool(tool_id):
    return ToolSpec(
        id=tool_id, name=tool_id, kind=ToolKind.INTERNAL, description=f"{tool_id} tool",
        input_schema={}, output_schema={}, risk_level=RiskLevel.LOW,
        side_effects=SideEffectType.READ, requires_approval=False,
    )


class FakeRetriever:
    def __init__(self, tools):
        self._tools = tools

    def _resp(self, q):
        return CapabilityRetrievalResponse(query=q, matches=[CapabilityMatch(tool=t, score=1.0) for t in self._tools])

    def retrieve(self, request):
        return self._resp(request.query)

    def retrieve_for_run_context(self, run_context, *, top_k=5, **kw):
        return self._resp(run_context.user_request)


class FakeExecutor:
    async def execute(self, tool, args):
        return AdapterResult.ok(output={"a": 1}, evidence=[EvidenceItem(source="document", content="g")])


class ScriptedEvaluator:
    def __init__(self, reports):
        self._reports = list(reports)
        self.calls = 0

    def evaluate(self, final_prompt, final_answer, run_context=None):
        report = self._reports[min(self.calls, len(self._reports) - 1)]
        self.calls += 1
        return report


def waiting():
    return EvaluationReport(passed=False, overall_score=0.2,
                            repair_decision=RepairDecision(action=RepairAction.ASK_USER_FOR_CLARIFICATION,
                                                           reason="need info", max_attempts=5))


def passing():
    return EvaluationReport(passed=True, overall_score=0.9,
                            repair_decision=RepairDecision(action=RepairAction.NONE))


def shared_client(evaluator):
    """One coordinator shared across /run and /resume for the whole client."""
    retriever = FakeRetriever([make_tool("cap")])
    direct = DirectRuntime(retriever, FakeExecutor())
    orch = AgentOrchestrator(
        context_engine=FakeContextEngine(),
        behavior_gate=BehaviorGate(),
        direct_runtime=direct,
        planner_runtime=PlannerRuntime(direct, retriever),
        final_context_builder=FinalContextBuilder(),
        final_provider=DeterministicFinalProvider(),
        answer_evaluator=evaluator,
    )
    coordinator = ResumeCoordinator(orch, InMemoryCheckpointStore())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_resume_coordinator] = lambda: coordinator
    return TestClient(app)


def _start_waiting(client):
    body = client.post("/agent/run", json={"user_request": "What does the document say?"}).json()
    assert body["runtime_outcome"] == "waiting_for_user"
    return body["checkpoint_id"]


# --------------------------------------------------------------------------- #
# Resume happy paths
# --------------------------------------------------------------------------- #

def test_resume_completes_after_waiting():
    client = shared_client(ScriptedEvaluator([waiting(), passing()]))
    cid = _start_waiting(client)
    resp = client.post("/agent/resume", json={
        "checkpoint_id": cid,
        "resolution": {"kind": "clarification", "value": "the Q3 report"},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["runtime_outcome"] == "completed"
    assert isinstance(body["answer"], str) and body["answer"]
    assert body["checkpoint_id"] is None


def test_resume_waits_again_returns_new_checkpoint():
    client = shared_client(ScriptedEvaluator([waiting(), waiting()]))
    cid = _start_waiting(client)
    body = client.post("/agent/resume", json={
        "checkpoint_id": cid,
        "resolution": {"kind": "clarification", "value": "still unclear"},
    }).json()
    assert body["runtime_outcome"] == "waiting_for_user"
    assert body["checkpoint_id"] and body["checkpoint_id"] != cid
    assert body["pending_action"] == "ask_user_for_clarification"
    assert body["pending_reason"]
    assert body["answer"] is None


def test_resume_response_stays_api_safe():
    client = shared_client(ScriptedEvaluator([waiting(), passing()]))
    cid = _start_waiting(client)
    body = client.post("/agent/resume", json={
        "checkpoint_id": cid, "resolution": {"kind": "approval", "value": True},
    }).json()
    assert set(body.keys()) == {
        "run_id", "thread_id", "runtime_outcome", "answer",
        "checkpoint_id", "pending_action", "pending_reason", "metadata",
    }
    for leaked in ("run_context", "working_context", "final_prompt"):
        assert leaked not in body


# --------------------------------------------------------------------------- #
# Errors + validation
# --------------------------------------------------------------------------- #

def test_unknown_checkpoint_returns_404():
    client = shared_client(ScriptedEvaluator([passing()]))
    resp = client.post("/agent/resume", json={
        "checkpoint_id": "does-not-exist",
        "resolution": {"kind": "approval", "value": True},
    })
    assert resp.status_code == 404


def test_blank_or_missing_checkpoint_id_is_422():
    client = shared_client(ScriptedEvaluator([passing()]))
    assert client.post("/agent/resume", json={
        "checkpoint_id": "", "resolution": {"kind": "approval"}}).status_code == 422
    assert client.post("/agent/resume", json={
        "checkpoint_id": "   ", "resolution": {"kind": "approval"}}).status_code == 422
    assert client.post("/agent/resume", json={
        "resolution": {"kind": "approval"}}).status_code == 422


def test_invalid_resolution_is_422():
    client = shared_client(ScriptedEvaluator([passing()]))
    # missing resolution
    assert client.post("/agent/resume", json={"checkpoint_id": "cp"}).status_code == 422
    # invalid kind
    assert client.post("/agent/resume", json={
        "checkpoint_id": "cp", "resolution": {"kind": "not_a_kind"}}).status_code == 422
    # extra field forbidden
    assert client.post("/agent/resume", json={
        "checkpoint_id": "cp", "resolution": {"kind": "approval", "bogus": 1}}).status_code == 422


# --------------------------------------------------------------------------- #
# Hygiene
# --------------------------------------------------------------------------- #

def test_route_module_imports_config_free():
    tree = ast.parse(inspect.getsource(agent_module))
    targets = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            targets += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            targets.append(node.module or "")
    for banned in ("app.config", "app.database", "motor", "app.services"):
        assert not any(banned in t for t in targets), (banned, targets)
