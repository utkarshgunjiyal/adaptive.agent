"""Phase 34 tests — MongoCheckpointStore (protocol parity + atomic resume).

Config-free: an injected fake *synchronous* Mongo collection (pymongo-style)
backs the store. No MONGO_URL, no driver, no network. Real Mongo integration is
out of scope for the default suite.
"""

import ast
import copy
import inspect
from datetime import datetime, timezone

import pytest

from app.agent.checkpoint import mongo_store as mongo_module
from app.agent.checkpoint.mongo_store import (
    CHECKPOINT_INDEXES,
    MongoCheckpointStore,
    ensure_checkpoint_indexes,
)
from app.agent.checkpoint.models import CheckpointStatus
from app.agent.checkpoint.store import (
    CheckpointConflictError,
    CheckpointNotFoundError,
    NonCheckpointableOutcomeError,
)
from app.agent.runtime.context import (
    BehaviorPath,
    BehaviorProfile,
    EvidenceItem,
    RunContext,
    ToolOutput,
    WorkingContextItem,
)
from app.agent.runtime.outcome import RuntimeOutcome


# --------------------------------------------------------------------------- #
# Fake synchronous Mongo collection
# --------------------------------------------------------------------------- #

class FakeMongoCollection:
    def __init__(self):
        self.docs = {}
        self.created_indexes = []

    def insert_one(self, document):
        _id = document["_id"]
        if _id in self.docs:
            raise Exception("E11000 duplicate key")
        self.docs[_id] = copy.deepcopy(document)  # BSON copy semantics
        return type("R", (), {"inserted_id": _id})()

    @staticmethod
    def _matches(doc, filt):
        return all(doc.get(k) == v for k, v in filt.items())

    def find_one(self, filt):
        for doc in self.docs.values():
            if self._matches(doc, filt):
                return copy.deepcopy(doc)
        return None

    def find_one_and_update(self, filt, update, return_document=False):
        for _id, doc in self.docs.items():
            if self._matches(doc, filt):
                before = copy.deepcopy(doc)
                self._apply(doc, update)
                return copy.deepcopy(doc) if return_document else before
        return None

    @staticmethod
    def _apply(doc, update):
        for field, value in update.get("$set", {}).items():
            if "." in field:
                parts = field.split(".")
                cursor = doc
                for part in parts[:-1]:
                    cursor = cursor.setdefault(part, {})
                cursor[parts[-1]] = value
            else:
                doc[field] = value

    def create_index(self, keys, **options):
        self.created_indexes.append((tuple(tuple(k) for k in keys), options))


class SeqClock:
    def __init__(self):
        self._n = 0

    def __call__(self):
        self._n += 1
        return datetime(2026, 1, 1, 0, 0, self._n, tzinfo=timezone.utc)


def store():
    counter = {"n": 0}

    def ids():
        counter["n"] += 1
        return f"cp-{counter['n']}"

    collection = FakeMongoCollection()
    return MongoCheckpointStore(collection, clock=SeqClock(), id_factory=ids), collection


def waiting_run_context():
    rc = RunContext.create(
        "Summarize and email the team", user_id="u", thread_id="t1",
        working_context=[WorkingContextItem(source="thread_summary", content="prior")],
    )
    rc.attach_behavior_profile(BehaviorProfile(path=BehaviorPath.PLANNER, reason="multi"))
    rc.attach_selected_capabilities(["get_document_summary"])
    rc.append_tool_output(ToolOutput(capability_id="get_document_summary", output={"summary": "ok"}))
    rc.append_evidence(EvidenceItem(source="document_summary", content="summary text", score=0.7))
    rc.metadata["runtime_outcome"] = "waiting_for_user"
    return rc


# --------------------------------------------------------------------------- #
# Save / load parity
# --------------------------------------------------------------------------- #

def test_save_waiting_returns_active_record():
    s, collection = store()
    record = s.save(waiting_run_context(), RuntimeOutcome.WAITING_FOR_USER,
                    pending_action="ask_user_for_clarification", pending_reason="need info")
    assert record.checkpoint_id in collection.docs
    assert record.status == CheckpointStatus.ACTIVE
    # enums serialized as strings on the wire
    assert collection.docs[record.checkpoint_id]["runtime_outcome"] == "waiting_for_user"
    assert collection.docs[record.checkpoint_id]["status"] == "active"


def test_load_preserves_all_fields():
    s, _ = store()
    saved = s.save(waiting_run_context(), RuntimeOutcome.WAITING_FOR_APPROVAL,
                   pending_action="human_review", pending_reason="risky")
    loaded = s.load(saved.checkpoint_id)
    assert loaded.run_id == saved.run_id
    assert loaded.user_id == "u"
    assert loaded.thread_id == "t1"
    assert loaded.runtime_outcome == RuntimeOutcome.WAITING_FOR_APPROVAL
    assert loaded.pending_action == "human_review"
    assert loaded.pending_reason == "risky"
    assert loaded.status == CheckpointStatus.ACTIVE


def test_snapshot_round_trip():
    s, _ = store()
    saved = s.save(waiting_run_context(), RuntimeOutcome.WAITING_FOR_REPLAN)
    snap = s.load(saved.checkpoint_id).run_context_snapshot
    assert snap["user_request"] == "Summarize and email the team"
    assert snap["selected_capabilities"] == ["get_document_summary"]
    assert snap["evidence"][0]["content"] == "summary text"
    assert snap["working_context"][0]["content"] == "prior"


# --------------------------------------------------------------------------- #
# Lifecycle + atomic resume
# --------------------------------------------------------------------------- #

def test_mark_resumed_updates_status():
    s, _ = store()
    saved = s.save(waiting_run_context(), RuntimeOutcome.WAITING_FOR_USER)
    resumed = s.mark_resumed(saved.checkpoint_id)
    assert resumed.status == CheckpointStatus.RESUMED
    assert s.load(saved.checkpoint_id).status == CheckpointStatus.RESUMED


def test_second_mark_resumed_is_rejected():
    s, _ = store()
    saved = s.save(waiting_run_context(), RuntimeOutcome.WAITING_FOR_USER)
    s.mark_resumed(saved.checkpoint_id)
    with pytest.raises(CheckpointConflictError):
        s.mark_resumed(saved.checkpoint_id)  # already resumed → atomic reject


def test_cancel_updates_status_and_reason():
    s, _ = store()
    saved = s.save(waiting_run_context(), RuntimeOutcome.WAITING_FOR_USER)
    cancelled = s.cancel(saved.checkpoint_id, reason="user abandoned")
    assert cancelled.status == CheckpointStatus.CANCELLED
    assert cancelled.metadata["cancel_reason"] == "user abandoned"


def test_mark_resumed_missing_raises_not_found():
    s, _ = store()
    with pytest.raises(CheckpointNotFoundError):
        s.mark_resumed("nope")


# --------------------------------------------------------------------------- #
# Errors + isolation
# --------------------------------------------------------------------------- #

def test_missing_checkpoint_raises():
    s, _ = store()
    with pytest.raises(CheckpointNotFoundError):
        s.load("does-not-exist")


def test_terminal_outcome_cannot_be_checkpointed():
    s, _ = store()
    with pytest.raises(NonCheckpointableOutcomeError):
        s.save(waiting_run_context(), RuntimeOutcome.COMPLETED)
    with pytest.raises(NonCheckpointableOutcomeError):
        s.save(waiting_run_context(), RuntimeOutcome.FAILED)


def test_snapshot_isolated_from_later_mutation():
    s, _ = store()
    rc = waiting_run_context()
    saved = s.save(rc, RuntimeOutcome.WAITING_FOR_USER)
    rc.metadata["late"] = "added after save"
    assert "late" not in s.load(saved.checkpoint_id).run_context_snapshot["metadata"]


def test_invalid_record_fails_clearly():
    s, collection = store()
    collection.docs["bad"] = {"_id": "bad", "checkpoint_id": "bad"}  # missing required fields
    with pytest.raises(Exception):  # CheckpointError
        s.load("bad")


# --------------------------------------------------------------------------- #
# Indexes
# --------------------------------------------------------------------------- #

def test_ensure_indexes_creates_expected_indexes():
    _, collection = store()
    ensure_checkpoint_indexes(collection)
    assert len(collection.created_indexes) == len(CHECKPOINT_INDEXES)
    names = {opts.get("name") for _, opts in collection.created_indexes}
    assert "uniq_checkpoint_id" in names
    assert "by_status_updated" in names


# --------------------------------------------------------------------------- #
# API dependencies share the same Mongo-backed coordinator
# --------------------------------------------------------------------------- #

def test_api_shares_mongo_backed_coordinator():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.agent.capabilities.models import CapabilityMatch, CapabilityRetrievalResponse
    from app.agent.context.final_builder import FinalContextBuilder
    from app.agent.evaluation.models import EvaluationReport, RepairAction, RepairDecision
    from app.agent.gate.behavior_gate import BehaviorGate
    from app.agent.llm.final_provider import DeterministicFinalProvider
    from app.agent.models.tool_spec import RiskLevel, SideEffectType, ToolKind, ToolSpec
    from app.agent.runtime.direct_runtime import DirectRuntime
    from app.agent.runtime.orchestrator import AgentOrchestrator
    from app.agent.runtime.planner_runtime import PlannerRuntime
    from app.agent.runtime.resume_coordinator import ResumeCoordinator
    from app.agent.tools.result import AdapterResult
    from app.routes.agent import get_resume_coordinator, router

    def make_tool(tid):
        return ToolSpec(id=tid, name=tid, kind=ToolKind.INTERNAL, description="t",
                        input_schema={}, output_schema={}, risk_level=RiskLevel.LOW,
                        side_effects=SideEffectType.READ, requires_approval=False)

    class FakeContextEngine:
        async def build(self, user_request, user_id, thread_id=None, metadata=None):
            return RunContext.create(user_request, user_id=user_id, thread_id=thread_id)

    class FakeRetriever:
        def __init__(self, tools):
            self._tools = tools

        def _r(self, q):
            return CapabilityRetrievalResponse(query=q, matches=[CapabilityMatch(tool=t, score=1.0) for t in self._tools])

        def retrieve(self, request):
            return self._r(request.query)

        def retrieve_for_run_context(self, run_context, *, top_k=5, **kw):
            return self._r(run_context.user_request)

    class FakeExecutor:
        async def execute(self, tool, args):
            return AdapterResult.ok(output={"a": 1})

    class ScriptedEvaluator:
        def __init__(self, reports):
            self._reports = list(reports)
            self.calls = 0

        def evaluate(self, final_prompt, final_answer, run_context=None):
            r = self._reports[min(self.calls, len(self._reports) - 1)]
            self.calls += 1
            return r

    def waiting():
        return EvaluationReport(passed=False, overall_score=0.2,
                                repair_decision=RepairDecision(action=RepairAction.ASK_USER_FOR_CLARIFICATION,
                                                               reason="need info", max_attempts=5))

    def passing():
        return EvaluationReport(passed=True, overall_score=0.9,
                                repair_decision=RepairDecision(action=RepairAction.NONE))

    mongo_store_instance, collection = store()
    retriever = FakeRetriever([make_tool("cap")])
    direct = DirectRuntime(retriever, FakeExecutor())
    orch = AgentOrchestrator(
        context_engine=FakeContextEngine(), behavior_gate=BehaviorGate(),
        direct_runtime=direct, planner_runtime=PlannerRuntime(direct, retriever),
        final_context_builder=FinalContextBuilder(),
        final_provider=DeterministicFinalProvider(),
        answer_evaluator=ScriptedEvaluator([waiting(), passing()]),
    )
    coordinator = ResumeCoordinator(orch, mongo_store_instance)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_resume_coordinator] = lambda: coordinator
    client = TestClient(app)

    started = client.post("/agent/run", json={"user_request": "What does the doc say?"}).json()
    assert started["runtime_outcome"] == "waiting_for_user"
    cid = started["checkpoint_id"]
    assert cid in collection.docs  # persisted to the Mongo-backed store

    resumed = client.post("/agent/resume", json={
        "checkpoint_id": cid, "resolution": {"kind": "clarification", "value": "yes"}}).json()
    assert resumed["runtime_outcome"] == "completed"
    assert collection.docs[cid]["status"] == "resumed"  # same store shared across routes


# --------------------------------------------------------------------------- #
# Hygiene
# --------------------------------------------------------------------------- #

def test_no_mongo_client_or_config_at_import_time():
    tree = ast.parse(inspect.getsource(mongo_module))
    targets = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            targets += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            targets.append(node.module or "")
    for banned in ("pymongo", "motor", "app.config", "app.database"):
        assert not any(banned in t for t in targets), (banned, targets)
