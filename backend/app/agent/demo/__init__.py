"""Demo composition (Phase 42B).

An *opt-in*, off-by-default seam for a deterministic, interview-ready demo. It
adds no new runtime architecture: the only public surface is ``DemoEvaluator``,
which implements the EXISTING ``AnswerEvaluatorLike`` protocol (the Phase 22
answer-evaluator seam that the test suite already uses to drive HITL). Wired
only when ``settings.demo_mode`` is true, it makes specifically-marked demo
prompts reach a genuine ``WAITING_FOR_APPROVAL`` / ``WAITING_FOR_USER`` pause
that flows through the real orchestrator → checkpoint → ``/agent/resume``.

It never fabricates events, never bypasses the runtime state machine, and is
inert unless demo mode is explicitly enabled.
"""

from app.agent.demo.evaluator import DemoEvaluator

__all__ = ["DemoEvaluator"]
