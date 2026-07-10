"""Runtime Orchestrator (Phase 18).

The single in-memory flow that chains every runtime stage end-to-end:

    ContextEngine.build → BehaviorGate.decide
      → DirectRuntime.run  (DIRECT)  |  PlannerRuntime.run (PLANNER)
      → FinalContextBuilder.build → FinalAnswerProvider.generate
      → attach_final_answer → AgentRunResult

Every dependency is injected — the orchestrator owns sequencing only, not
construction. This keeps it deterministic and config-free: no LLM, no database,
no application settings, no production endpoint, no streaming. Planner reasoning
is not implemented here; a ``plan_source`` callable supplies the ExecutionPlan
for the PLANNER path (a static plan in tests). See ARCHITECTURE.md §5.
"""

import inspect
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.agent.llm.final_provider import (
    FinalAnswer,
    FinalAnswerProvider,
    attach_final_answer,
)
from app.agent.models.final_prompt import FinalPrompt
from app.agent.runtime.context import BehaviorPath, RunContext
from app.agent.runtime.planner_runtime import ExecutionPlan


class OrchestratorError(Exception):
    """Base error for the Runtime Orchestrator."""


class MissingPlanSourceError(OrchestratorError):
    """Raised when the PLANNER path is taken but no plan_source was injected."""


# -- Injected-dependency contracts (duck-typed; kept import-light) ----------- #

class ContextEngineLike(Protocol):
    async def build(
        self, user_request: str, user_id: str, thread_id: str | None = None,
        metadata: dict | None = None,
    ) -> RunContext:
        ...


class BehaviorGateLike(Protocol):
    def decide(self, run_context: RunContext, attach: bool = True): ...


class DirectRuntimeLike(Protocol):
    async def run(self, run_context: RunContext) -> RunContext: ...


class PlannerRuntimeLike(Protocol):
    async def run(self, run_context: RunContext, plan: ExecutionPlan) -> RunContext: ...


class FinalContextBuilderLike(Protocol):
    def build(self, run_context: RunContext) -> FinalPrompt: ...


class PlanSource(Protocol):
    def __call__(self, run_context: RunContext) -> ExecutionPlan: ...


class AgentRunResult(BaseModel):
    """Structured result of a single orchestrated agent run."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    user_id: str
    thread_id: str | None = None
    behavior_path: str
    answer: FinalAnswer
    final_prompt: FinalPrompt
    run_context: RunContext
    metadata: dict = Field(default_factory=dict)


class AgentOrchestrator:
    def __init__(
        self,
        *,
        context_engine: ContextEngineLike,
        behavior_gate: BehaviorGateLike,
        direct_runtime: DirectRuntimeLike,
        planner_runtime: PlannerRuntimeLike,
        final_context_builder: FinalContextBuilderLike,
        final_provider: FinalAnswerProvider,
        plan_source: PlanSource | None = None,
    ) -> None:
        self._context_engine = context_engine
        self._behavior_gate = behavior_gate
        self._direct_runtime = direct_runtime
        self._planner_runtime = planner_runtime
        self._final_context_builder = final_context_builder
        self._final_provider = final_provider
        self._plan_source = plan_source

    async def run(
        self,
        user_request: str,
        user_id: str,
        thread_id: str | None = None,
        metadata: dict | None = None,
    ) -> AgentRunResult:
        # 1. Build the RunContext (working context assembled by the engine).
        run_context = await self._context_engine.build(
            user_request, user_id, thread_id=thread_id, metadata=metadata
        )

        # 2. Behavior Gate — attaches behavior_profile + metadata["behavior_decision"].
        self._behavior_gate.decide(run_context)
        path = run_context.behavior_profile.path

        # 3. Dispatch to the one execution engine (planner orchestrates direct).
        if path == BehaviorPath.PLANNER:
            plan = await self._resolve_plan(run_context)
            run_context = await self._planner_runtime.run(run_context, plan)
        else:
            run_context = await self._direct_runtime.run(run_context)

        # 4-6. Build the final prompt, generate, and record the answer.
        final_prompt = self._final_context_builder.build(run_context)
        answer = await self._final_provider.generate(final_prompt)
        attach_final_answer(run_context, answer)

        # 7. Structured result.
        return AgentRunResult(
            run_id=run_context.run_id,
            user_id=run_context.user_id,
            thread_id=run_context.thread_id,
            behavior_path=path.value,
            answer=answer,
            final_prompt=final_prompt,
            run_context=run_context,
            metadata={
                "behavior_decision": run_context.metadata.get("behavior_decision"),
                "execution_status": run_context.metadata.get("execution_status"),
                "runtime_status": run_context.metadata.get("planner_runtime", {}).get(
                    "runtime_status"
                ),
                "provider": answer.provider,
                "model": answer.model,
            },
        )

    async def _resolve_plan(self, run_context: RunContext) -> ExecutionPlan:
        if self._plan_source is None:
            raise MissingPlanSourceError(
                "PLANNER path requires an injected plan_source"
            )
        plan = self._plan_source(run_context)
        if inspect.isawaitable(plan):
            plan = await plan
        return plan
