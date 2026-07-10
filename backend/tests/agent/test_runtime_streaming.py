"""Phase 32 tests — runtime streaming (RuntimeStreamer.run_stream).

Config-free: streams over the real orchestrator wired with fakes (no DB, no LLM).
Verifies the event envelope, per-stage reconstruction, ordering, chunking, and
failure surfacing — without changing any runtime decision.
"""

import ast
import asyncio
import inspect

from app.agent.capabilities.models import CapabilityMatch, CapabilityRetrievalResponse
from app.agent.context.final_builder import FinalContextBuilder
from app.agent.evaluation.models import EvaluationReport, RepairAction, RepairDecision
from app.agent.gate.behavior_gate import BehaviorGate
from app.agent.llm.final_provider import DeterministicFinalProvider
from app.agent.models.tool_spec import RiskLevel, SideEffectType, ToolKind, ToolSpec
from app.agent.runtime import streaming as streaming_module
from app.agent.runtime.context import EvidenceItem, RunContext
from app.agent.runtime.direct_runtime import DirectRuntime
from app.agent.runtime.events import RuntimeEvent, RuntimeEventType as E
from app.agent.runtime.orchestrator import AgentOrchestrator
from app.agent.runtime.planner_runtime import ExecutionPlan, PlannerRuntime, PlannerTask
from app.agent.runtime.streaming import RuntimeStreamer
from app.agent.tools.result import AdapterResult


def collect(agen):
    async def _run():
        return [event async for event in agen]
    return asyncio.run(_run())


DIRECT_REQUEST = "What does the document say about pricing?"
PLANNER_REQUEST = "Summarize the report and then email the team"


def make_tool(tool_id):
    return ToolSpec(
        id=tool_id, name=tool_id, kind=ToolKind.INTERNAL, description=f"{tool_id} tool",
        input_schema={}, output_schema={}, risk_level=RiskLevel.LOW,
        side_effects=SideEffectType.READ, requires_approval=False,
    )


class FakeContextEngine:
    async def build(self, user_request, user_id, thread_id=None, metadata=None):
        return RunContext.create(user_request, user_id=user_id, thread_id=thread_id,
                                 metadata=dict(metadata or {}))


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
        return AdapterResult.ok(output={"answer": "x"}, evidence=[EvidenceItem(source="document", content="g")])


class ScriptedEvaluator:
    def __init__(self, reports):
        self._reports = list(reports)
        self.calls = 0

    def evaluate(self, final_prompt, final_answer, run_context=None):
        report = self._reports[min(self.calls, len(self._reports) - 1)]
        self.calls += 1
        return report


class FailingOrchestrator:
    async def run(self, *a, **kw):
        raise RuntimeError("boom")


def passing():
    return EvaluationReport(passed=True, overall_score=0.9,
                            repair_decision=RepairDecision(action=RepairAction.NONE))


def failing(action):
    return EvaluationReport(passed=False, overall_score=0.2,
                            repair_decision=RepairDecision(action=action, reason="bad", max_attempts=5))


def orchestrator(evaluator=None, plan_source=None):
    retriever = FakeRetriever([make_tool("cap")])
    direct = DirectRuntime(retriever, FakeExecutor())
    return AgentOrchestrator(
        context_engine=FakeContextEngine(),
        behavior_gate=BehaviorGate(),
        direct_runtime=direct,
        planner_runtime=PlannerRuntime(direct, retriever),
        final_context_builder=FinalContextBuilder(),
        final_provider=DeterministicFinalProvider(),
        answer_evaluator=evaluator,
        plan_source=plan_source,
    )


def types(events):
    return [e.type for e in events]


# --------------------------------------------------------------------------- #
# Envelope + ordering
# --------------------------------------------------------------------------- #

def test_stream_starts_and_completes():
    events = collect(RuntimeStreamer(orchestrator()).run_stream(DIRECT_REQUEST, user_id="u"))
    assert all(isinstance(e, RuntimeEvent) for e in events)
    assert events[0].type == E.RUNTIME_STARTED
    assert events[-1].type == E.RUNTIME_COMPLETED
    assert events[-1].data["runtime_outcome"] == "completed"


def test_sequence_numbers_monotonic():
    events = collect(RuntimeStreamer(orchestrator()).run_stream(DIRECT_REQUEST, user_id="u"))
    assert [e.sequence for e in events] == list(range(len(events)))


def test_direct_run_emits_core_stages_in_order():
    events = collect(RuntimeStreamer(orchestrator()).run_stream(DIRECT_REQUEST, user_id="u"))
    t = types(events)
    for expected in (E.CONTEXT_STARTED, E.CONTEXT_COMPLETED, E.RETRIEVAL_STARTED,
                     E.RETRIEVAL_COMPLETED, E.TOOL_STARTED, E.TOOL_COMPLETED,
                     E.ANSWER_STARTED, E.ANSWER_COMPLETED):
        assert expected in t
    # planner events must NOT appear on a direct run
    assert E.PLANNER_STARTED not in t
    # ordering: context < retrieval < tool < answer < completed
    assert t.index(E.CONTEXT_COMPLETED) < t.index(E.RETRIEVAL_STARTED)
    assert t.index(E.RETRIEVAL_COMPLETED) < t.index(E.TOOL_STARTED)
    assert t.index(E.TOOL_COMPLETED) < t.index(E.ANSWER_STARTED)
    assert t.index(E.ANSWER_COMPLETED) < t.index(E.RUNTIME_COMPLETED)


def test_answer_completed_carries_text():
    events = collect(RuntimeStreamer(orchestrator()).run_stream(DIRECT_REQUEST, user_id="u"))
    answer = next(e for e in events if e.type == E.ANSWER_COMPLETED)
    assert answer.data["text"]
    assert answer.data["provider"] == "deterministic"
    # deterministic provider → no chunks by default
    assert E.ANSWER_CHUNK not in types(events)


# --------------------------------------------------------------------------- #
# Planner path
# --------------------------------------------------------------------------- #

def test_planner_run_emits_planner_and_multiple_tools():
    def plan(run_context):
        return ExecutionPlan(id="p", goal=run_context.user_request, tasks=[
            PlannerTask(id="t1", request="summarize the report"),
            PlannerTask(id="t2", request="email the team", optional=True),
        ])

    events = collect(RuntimeStreamer(orchestrator(plan_source=plan)).run_stream(PLANNER_REQUEST, user_id="u"))
    t = types(events)
    assert E.PLANNER_STARTED in t and E.PLANNER_COMPLETED in t
    assert t.count(E.TOOL_COMPLETED) == 2  # one per task


# --------------------------------------------------------------------------- #
# Evaluation + repair
# --------------------------------------------------------------------------- #

def test_evaluation_events_emitted_when_evaluator_present():
    events = collect(RuntimeStreamer(orchestrator(ScriptedEvaluator([passing()]))).run_stream(DIRECT_REQUEST, user_id="u"))
    t = types(events)
    assert E.EVALUATION_STARTED in t and E.EVALUATION_COMPLETED in t
    ev = next(e for e in events if e.type == E.EVALUATION_COMPLETED)
    assert ev.data["passed"] is True


def test_repair_events_emitted_on_regeneration():
    evaluator = ScriptedEvaluator([failing(RepairAction.REGENERATE_WITH_STRONGER_INSTRUCTIONS), passing()])
    events = collect(RuntimeStreamer(orchestrator(evaluator)).run_stream(DIRECT_REQUEST, user_id="u"))
    t = types(events)
    assert E.REPAIR_STARTED in t and E.REPAIR_COMPLETED in t


def test_no_evaluation_events_without_evaluator():
    events = collect(RuntimeStreamer(orchestrator()).run_stream(DIRECT_REQUEST, user_id="u"))
    t = types(events)
    assert E.EVALUATION_STARTED not in t
    assert E.REPAIR_STARTED not in t


# --------------------------------------------------------------------------- #
# Chunking + failure
# --------------------------------------------------------------------------- #

def test_chunk_answer_emits_answer_chunks():
    events = collect(RuntimeStreamer(orchestrator(), chunk_answer=True, chunk_size=8)
                     .run_stream(DIRECT_REQUEST, user_id="u"))
    t = types(events)
    assert E.ANSWER_CHUNK in t
    # chunks appear between answer_started and answer_completed
    assert t.index(E.ANSWER_STARTED) < t.index(E.ANSWER_CHUNK) < t.index(E.ANSWER_COMPLETED)
    reassembled = "".join(e.data["text"] for e in events if e.type == E.ANSWER_CHUNK)
    completed = next(e for e in events if e.type == E.ANSWER_COMPLETED)
    assert reassembled == completed.data["text"]


def test_runtime_failed_on_orchestrator_error():
    events = collect(RuntimeStreamer(FailingOrchestrator()).run_stream(DIRECT_REQUEST, user_id="u"))
    assert types(events) == [E.RUNTIME_STARTED, E.RUNTIME_FAILED]
    assert events[-1].data["error_type"] == "RuntimeError"
    assert E.RUNTIME_COMPLETED not in types(events)


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


def test_no_config_db_or_vendor_imports():
    import app.agent.runtime.events as events_mod
    for module in (streaming_module, events_mod):
        targets = _module_level_import_targets(module)
        for banned in ("app.config", "app.services", "app.db", "motor", "qdrant",
                       "redis", "openai", "anthropic", "genai"):
            assert not any(banned in t for t in targets), (module.__name__, banned, targets)
