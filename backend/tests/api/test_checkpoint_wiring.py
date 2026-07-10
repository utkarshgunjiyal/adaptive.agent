"""Phase 35 tests — checkpoint backend composition + API wiring.

Config-free: fake sync Mongo collection injected; no MONGO_URL, no driver. Covers
backend selection/validation, one-time index init, shared store reuse, and the
404/409 mapping over HTTP through the async coordinator.
"""

import ast
import copy
import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routes.agent as agent_module
from app.agent.checkpoint import composition as composition_module
from app.agent.checkpoint.composition import select_checkpoint_store
from app.agent.checkpoint.mongo_store import MongoCheckpointStore
from app.agent.checkpoint.store import InMemoryCheckpointStore


# --------------------------------------------------------------------------- #
# Fake sync Mongo collection (pymongo-style)
# --------------------------------------------------------------------------- #

class FakeMongoCollection:
    def __init__(self):
        self.docs = {}
        self.created_indexes = []

    def insert_one(self, document):
        self.docs[document["_id"]] = copy.deepcopy(document)
        return type("R", (), {"inserted_id": document["_id"]})()

    @staticmethod
    def _match(doc, filt):
        return all(doc.get(k) == v for k, v in filt.items())

    def find_one(self, filt):
        for doc in self.docs.values():
            if self._match(doc, filt):
                return copy.deepcopy(doc)
        return None

    def find_one_and_update(self, filt, update, return_document=False):
        for doc in self.docs.values():
            if self._match(doc, filt):
                for field, value in update.get("$set", {}).items():
                    if "." in field:
                        parts = field.split(".")
                        cur = doc
                        for p in parts[:-1]:
                            cur = cur.setdefault(p, {})
                        cur[parts[-1]] = value
                    else:
                        doc[field] = value
                return copy.deepcopy(doc) if return_document else None
        return None

    def create_index(self, keys, **options):
        self.created_indexes.append(options.get("name"))


# --------------------------------------------------------------------------- #
# Backend selection + validation
# --------------------------------------------------------------------------- #

def test_memory_backend_default():
    assert isinstance(select_checkpoint_store("memory"), InMemoryCheckpointStore)


def test_mongo_backend_selected_and_indexes_created_once():
    collection = FakeMongoCollection()
    store = select_checkpoint_store("mongo", mongo_collection=collection)
    assert isinstance(store, MongoCheckpointStore)
    # indexes ensured exactly once at selection time
    assert "uniq_checkpoint_id" in collection.created_indexes
    assert len(collection.created_indexes) == 4


def test_mongo_backend_can_skip_indexes():
    collection = FakeMongoCollection()
    select_checkpoint_store("mongo", mongo_collection=collection, ensure_indexes=False)
    assert collection.created_indexes == []


def test_unsupported_backend_rejected():
    with pytest.raises(ValueError):
        select_checkpoint_store("cassandra")


# --------------------------------------------------------------------------- #
# Shared store / single coordinator per process
# --------------------------------------------------------------------------- #

def test_configured_store_is_shared_and_coordinator_is_singleton():
    store = select_checkpoint_store("mongo", mongo_collection=FakeMongoCollection())
    agent_module.configure_checkpoint_store(store)
    try:
        assert agent_module.get_checkpoint_store() is store
        c1 = agent_module.get_resume_coordinator()
        c2 = agent_module.get_resume_coordinator()
        assert c1 is c2                 # not rebuilt per request
        assert c1.store is store        # coordinator uses the configured store
    finally:
        agent_module.configure_checkpoint_store(InMemoryCheckpointStore())
        agent_module._coordinator = None


# --------------------------------------------------------------------------- #
# HTTP 404 / 409 through the async coordinator
# --------------------------------------------------------------------------- #

def _api(store):
    from app.agent.capabilities.models import CapabilityMatch, CapabilityRetrievalResponse
    from app.agent.context.final_builder import FinalContextBuilder
    from app.agent.evaluation.models import EvaluationReport, RepairAction, RepairDecision
    from app.agent.gate.behavior_gate import BehaviorGate
    from app.agent.llm.final_provider import DeterministicFinalProvider
    from app.agent.models.tool_spec import RiskLevel, SideEffectType, ToolKind, ToolSpec
    from app.agent.runtime.context import RunContext
    from app.agent.runtime.direct_runtime import DirectRuntime
    from app.agent.runtime.orchestrator import AgentOrchestrator
    from app.agent.runtime.planner_runtime import PlannerRuntime
    from app.agent.runtime.resume_coordinator import AsyncResumeCoordinator
    from app.agent.tools.result import AdapterResult
    from app.routes.agent import get_resume_coordinator, router

    tool = ToolSpec(id="cap", name="cap", kind=ToolKind.INTERNAL, description="t",
                    input_schema={}, output_schema={}, risk_level=RiskLevel.LOW,
                    side_effects=SideEffectType.READ, requires_approval=False)

    class FakeContextEngine:
        async def build(self, user_request, user_id, thread_id=None, metadata=None):
            return RunContext.create(user_request, user_id=user_id, thread_id=thread_id)

    class FakeRetriever:
        def retrieve(self, request):
            return CapabilityRetrievalResponse(query=request.query, matches=[CapabilityMatch(tool=tool, score=1.0)])

        def retrieve_for_run_context(self, run_context, *, top_k=5, **kw):
            return CapabilityRetrievalResponse(query=run_context.user_request, matches=[CapabilityMatch(tool=tool, score=1.0)])

    class FakeExecutor:
        async def execute(self, t, args):
            return AdapterResult.ok(output={"a": 1})

    class WaitOnce:
        def __init__(self):
            self.calls = 0

        def evaluate(self, final_prompt, final_answer, run_context=None):
            self.calls += 1
            if self.calls == 1:
                return EvaluationReport(passed=False, overall_score=0.2,
                                        repair_decision=RepairDecision(action=RepairAction.ASK_USER_FOR_CLARIFICATION,
                                                                       reason="need info", max_attempts=5))
            return EvaluationReport(passed=True, overall_score=0.9,
                                    repair_decision=RepairDecision(action=RepairAction.NONE))

    direct = DirectRuntime(FakeRetriever(), FakeExecutor())
    orch = AgentOrchestrator(
        context_engine=FakeContextEngine(), behavior_gate=BehaviorGate(),
        direct_runtime=direct, planner_runtime=PlannerRuntime(direct, FakeRetriever()),
        final_context_builder=FinalContextBuilder(), final_provider=DeterministicFinalProvider(),
        answer_evaluator=WaitOnce(),
    )
    coordinator = AsyncResumeCoordinator(orch, store)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_resume_coordinator] = lambda: coordinator
    return TestClient(app)


def test_resume_unknown_checkpoint_returns_404():
    client = _api(select_checkpoint_store("mongo", mongo_collection=FakeMongoCollection()))
    resp = client.post("/agent/resume", json={"checkpoint_id": "nope",
                                              "resolution": {"kind": "approval", "value": True}})
    assert resp.status_code == 404


def test_second_resume_conflict_returns_409():
    store = select_checkpoint_store("mongo", mongo_collection=FakeMongoCollection())
    client = _api(store)
    started = client.post("/agent/run", json={"user_request": "What does the doc say?"}).json()
    cid = started["checkpoint_id"]
    assert cid
    # Simulate a concurrent resume that already claimed the checkpoint.
    store.mark_resumed(cid)
    resp = client.post("/agent/resume", json={"checkpoint_id": cid,
                                             "resolution": {"kind": "clarification", "value": "x"}})
    assert resp.status_code == 409


def test_run_and_resume_share_the_same_store():
    store = select_checkpoint_store("mongo", mongo_collection=FakeMongoCollection())
    client = _api(store)
    started = client.post("/agent/run", json={"user_request": "What does the doc say?"}).json()
    cid = started["checkpoint_id"]
    resumed = client.post("/agent/resume", json={"checkpoint_id": cid,
                                                "resolution": {"kind": "clarification", "value": "yes"}}).json()
    assert resumed["runtime_outcome"] == "completed"  # loaded from the same store


# --------------------------------------------------------------------------- #
# Hygiene
# --------------------------------------------------------------------------- #

def _module_level_import_targets(module):
    tree = ast.parse(inspect.getsource(module))
    targets = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            targets += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            targets.append(node.module or "")
    return targets


def test_routes_and_composition_config_free_at_import():
    for module in (agent_module, composition_module):
        targets = _module_level_import_targets(module)
        for banned in ("app.config", "app.database", "pymongo", "motor"):
            assert not any(banned in t for t in targets), (module.__name__, banned, targets)
