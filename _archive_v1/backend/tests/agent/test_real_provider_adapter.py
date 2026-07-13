"""Phase 36 tests — V15FinalAnswerProvider + provider composition.

Config-free: no LLM, no credentials — the adapter is driven by an injected fake
``complete``. Also covers factory/orchestrator wiring (planner runs only on the
PLANNER path; deterministic defaults; provider sharing).
"""

import ast
import asyncio
import inspect

import pytest

from app.agent.capabilities.models import CapabilityMatch, CapabilityRetrievalResponse
from app.agent.context.final_builder import FinalContextBuilder
from app.agent.gate.behavior_gate import BehaviorGate
from app.agent.llm import provider_adapter as adapter_module
from app.agent.llm.final_provider import FinalAnswer, render_final_prompt
from app.agent.llm.provider_adapter import (
    FinalProviderError,
    V15FinalAnswerProvider,
    render_messages_to_system_prompt,
)
from app.agent.models.final_prompt import Citation, EvidenceSection, ExecutionSummary, FinalPrompt
from app.agent.models.tool_spec import RiskLevel, SideEffectType, ToolKind, ToolSpec
from app.agent.runtime.context import EvidenceItem, RunContext
from app.agent.runtime.direct_runtime import DirectRuntime
from app.agent.runtime.orchestrator import AgentOrchestrator
from app.agent.runtime.planner_runtime import PlannerRuntime
from app.agent.tools.result import AdapterResult


def run(coro):
    return asyncio.run(coro)


def drain(agen):
    async def _run():
        return [chunk async for chunk in agen]
    return asyncio.run(_run())


def final_prompt():
    return FinalPrompt(
        system_prompt="system",
        user_request="What is the price?",
        evidence_sections=[EvidenceSection(id="E1", source="document", content="Price is $10.", score=0.9)],
        citations=[Citation(id="E1", source="document", score=0.9)],
        execution_summary=ExecutionSummary(path="direct", status="success"),
        final_instructions="answer using [E1]",
    )


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def test_render_final_prompt_is_provider_neutral():
    messages = render_final_prompt(final_prompt())
    system, prompt = render_messages_to_system_prompt(messages)
    assert "system" in system
    assert "What is the price?" in prompt
    assert "[E1]" in prompt  # evidence carried with its citation id


# --------------------------------------------------------------------------- #
# Adapter maps provider output → FinalAnswer
# --------------------------------------------------------------------------- #

def test_adapter_maps_response_to_final_answer():
    async def fake_complete(system, prompt, **kw):
        return "The price is $10 per month [E1]."

    answer = run(V15FinalAnswerProvider(complete=fake_complete, provider="test", model="m1").generate(final_prompt()))
    assert isinstance(answer, FinalAnswer)
    assert answer.text.endswith("[E1].")
    assert answer.provider == "test"
    assert answer.model == "m1"
    assert answer.finish_reason == "stop"
    assert answer.used_citations == ["E1"]          # citation extracted safely
    assert "prompt_chars" in answer.usage_metadata  # usage metadata preserved


def test_adapter_ignores_unknown_citation_markers():
    async def fake_complete(system, prompt, **kw):
        return "Made up [E9] and real [E1]."

    answer = run(V15FinalAnswerProvider(complete=fake_complete, provider="t", model="m").generate(final_prompt()))
    assert answer.used_citations == ["E1"]  # E9 is not a valid citation id


def test_adapter_wraps_backend_error():
    async def boom(system, prompt, **kw):
        raise RuntimeError("vendor 500")

    with pytest.raises(FinalProviderError):
        run(V15FinalAnswerProvider(complete=boom, provider="t", model="m").generate(final_prompt()))


def test_adapter_only_receives_final_prompt():
    # The adapter's generate() signature takes a FinalPrompt — never a RunContext.
    sig = inspect.signature(V15FinalAnswerProvider.generate)
    params = list(sig.parameters)
    assert params == ["self", "final_prompt"]


# --------------------------------------------------------------------------- #
# Phase 38 — V1.5 token streaming adapter
# --------------------------------------------------------------------------- #

def test_adapter_streams_chunks_from_v15_stream():
    async def fake_stream(system, prompt, **kw):
        for piece in ("The price ", "is $10 ", "[E1]."):
            yield piece

    provider = V15FinalAnswerProvider(stream=fake_stream, provider="test", model="m1")
    chunks = drain(provider.generate_stream(final_prompt()))
    assert chunks == ["The price ", "is $10 ", "[E1]."]
    # build_final_answer assembles the streamed text into a FinalAnswer.
    answer = provider.build_final_answer(final_prompt(), "".join(chunks))
    assert isinstance(answer, FinalAnswer)
    assert answer.text == "The price is $10 [E1]."
    assert answer.used_citations == ["E1"]
    assert answer.provider == "test" and answer.model == "m1"


def test_adapter_stream_falls_back_to_generate_when_stream_absent():
    # No stream injected and lazy resolution unavailable → fall back to
    # complete()-based generation, yielding the whole answer as one chunk.
    async def fake_complete(system, prompt, **kw):
        return "Full answer [E1]."

    provider = V15FinalAnswerProvider(complete=fake_complete, provider="t", model="m")
    chunks = drain(provider.generate_stream(final_prompt()))
    assert chunks == ["Full answer [E1]."]


def test_adapter_stream_wraps_backend_error():
    async def boom(system, prompt, **kw):
        raise RuntimeError("vendor stream 500")
        yield  # pragma: no cover - makes this an async generator

    with pytest.raises(FinalProviderError):
        drain(V15FinalAnswerProvider(stream=boom, provider="t", model="m").generate_stream(final_prompt()))


def test_adapter_stream_never_leaks_vendor_error_text():
    secret = "sk-vendor-SECRET-42"

    async def boom(system, prompt, **kw):
        raise RuntimeError(secret)
        yield  # pragma: no cover

    try:
        drain(V15FinalAnswerProvider(stream=boom, provider="t", model="m").generate_stream(final_prompt()))
        assert False, "expected FinalProviderError"
    except FinalProviderError as exc:
        assert exc.safe_message == "The final answer could not be generated."
        # The safe_message (surfaced to callers) carries no vendor detail.
        assert secret not in exc.safe_message


# --------------------------------------------------------------------------- #
# Composition: planner only on PLANNER path
# --------------------------------------------------------------------------- #

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


class SpyPlannerProvider:
    def __init__(self):
        self.calls = 0

    async def plan(self, planner_prompt):
        self.calls += 1
        from app.agent.runtime.planner_runtime import ExecutionPlan, PlannerTask
        # confirm we only received a PlannerPrompt with top-k capabilities
        assert planner_prompt.capabilities
        return ExecutionPlan(id="p", goal=planner_prompt.user_request,
                             tasks=[PlannerTask(id="t1", request="do it")])


def _orchestrator(planner_spy):
    from app.agent.llm.final_provider import DeterministicFinalProvider
    retriever = FakeRetriever([make_tool("cap")])
    direct = DirectRuntime(retriever, FakeExecutor())
    return AgentOrchestrator(
        context_engine=FakeContextEngine(), behavior_gate=BehaviorGate(),
        direct_runtime=direct, planner_runtime=PlannerRuntime(direct, retriever),
        final_context_builder=FinalContextBuilder(), final_provider=DeterministicFinalProvider(),
        planner_provider=planner_spy, capability_retriever=retriever,
    )


def test_planner_provider_invoked_only_on_planner_path():
    spy = SpyPlannerProvider()
    # DIRECT request → planner provider never called
    run(_orchestrator(spy).run("What does the document say about pricing?", user_id="u"))
    assert spy.calls == 0
    # PLANNER request → planner provider called once
    run(_orchestrator(spy).run("Summarize the report and then email the team", user_id="u"))
    assert spy.calls == 1


def test_factory_defaults_are_deterministic_and_shared():
    from app.agent.llm.final_provider import DeterministicFinalProvider
    from app.agent.llm.planner_provider import DeterministicPlannerProvider
    from app.agent.runtime.factory import build_default_runtime

    orch = build_default_runtime()
    assert isinstance(orch._final_provider, DeterministicFinalProvider)
    assert isinstance(orch._planner_provider, DeterministicPlannerProvider)
    # planner uses the same retriever instance as the direct runtime (shared)
    assert orch._capability_retriever is orch._direct_runtime._retriever


def test_factory_use_real_llm_selects_v15_adapters():
    from app.agent.llm.planner_provider import V15PlannerProvider
    from app.agent.runtime.factory import build_default_runtime

    orch = build_default_runtime(use_real_llm=True)
    assert isinstance(orch._final_provider, V15FinalAnswerProvider)
    assert isinstance(orch._planner_provider, V15PlannerProvider)


# --------------------------------------------------------------------------- #
# Hygiene
# --------------------------------------------------------------------------- #

def test_no_vendor_sdk_imports_at_module_level():
    tree = ast.parse(inspect.getsource(adapter_module))
    targets = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            targets += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            targets.append(node.module or "")
    for banned in ("openai", "anthropic", "google.generativeai", "genai",
                   "app.config", "app.services"):
        assert not any(banned in t for t in targets), (banned, targets)
