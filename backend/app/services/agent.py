"""Autonomous agent orchestration.

Pipeline (mirrors the spec):

1. Persist the user message.
2. Cheap deterministic capability selection → shortlist of allowed tools.
3. LLM planner receives ONLY the shortlisted tool specs and produces a
   schema-validated ``AgentPlan``.
4. Policy engine validates the plan (write/sensitive → approval required).
5. Executor runs read-only tools (in parallel where possible), respecting
   step dependencies, timeouts, and retries. Evidence is normalised.
6. Synthesizer prompt: the LLM produces a grounded answer using ONLY the
   retrieved evidence + short conversation context, with explicit citations.
7. Persist agent_run, tool_calls, evidence_items, and assistant message.

The full run is exposed via ``/api/agent/runs/{run_id}`` for the Execution
Details drawer.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from app.db import get_db
from app.models import (
    AgentPlan,
    EvidenceItem,
    PlanStep,
    ToolBadge,
    ToolCallLog,
)
from app.services import thread_service
from app.services.llm import complete, complete_json, extract_json, stream
from app.services.thread_summary import get_context_for_run, maybe_update_summary
from app.tools.registry import ToolSpec, get_registry

log = logging.getLogger("runner.agent")

_STEP_TIMEOUT_S = 30.0


# --------------------------------------------------------------------------
# 1. Capability selection (deterministic; no LLM)
# --------------------------------------------------------------------------

def select_tools(*, message: str, has_documents: bool) -> list[ToolSpec]:
    """Return the shortlisted tool specs that the planner will see."""
    reg = get_registry()
    text = (message or "").lower()

    scored: list[tuple[float, ToolSpec]] = []
    for tool in reg.all():
        if not tool.available:
            continue
        score = 0.0
        for kw in tool.keywords:
            if kw in text:
                score += 1.0
        # Baseline signal so a well-configured tool is never entirely absent.
        if tool.risk_level == "read":
            score += 0.05
        scored.append((score, tool))

    # Boost document tools when the user has documents; suppress them otherwise
    # (a document search tool would still be shown but with a lower priority
    # so the planner can fall back to web/paper search).
    boosted: list[tuple[float, ToolSpec]] = []
    for score, tool in scored:
        if tool.badge == ToolBadge.PRIVATE_DOC and has_documents:
            score += 0.5
        boosted.append((score, tool))

    boosted.sort(key=lambda x: x[0], reverse=True)

    # Return top 4 tools by score (always include at least one option).
    keep = [t for _, t in boosted[:5]]
    if not keep:
        keep = [t for t in reg.enabled_read_tools()][:3]
    return keep


# --------------------------------------------------------------------------
# 2. LLM planner
# --------------------------------------------------------------------------

_PLANNER_SYSTEM = """You are the planning brain of Runner.ai.

Given a user's question and a list of ALLOWED tools, decide the minimum steps
needed to gather grounded evidence. Prefer parallel independent steps.

You MUST respond with a single JSON object matching this schema:

{
  "goal": "<one sentence description of what you'll do>",
  "reasoning": "<brief plan rationale>",
  "steps": [
    {
      "id": "s1",
      "tool_id": "<one of the allowed tool ids>",
      "arguments": { ... schema-appropriate args ... },
      "depends_on": [],
      "rationale": "<why this step>",
      "expected_output": "<what evidence you expect>"
    }
  ]
}

Rules:
- Only use tool_ids from the ALLOWED list. Never invent tools.
- Prefer 1–3 steps. Multi-step is fine ONLY when the user explicitly asks to
  compare / combine sources.
- For document tools include a concise "query" argument.
- For web/paper search include a "query" argument.
- Do NOT synthesize the answer yet — only produce the plan.
- Output ONLY the JSON object. No prose, no code fences.
"""


def _shortlisted_prompt(tools: list[ToolSpec], message: str,
                       document_ids: list[str], has_docs: bool) -> str:
    lines: list[str] = ["ALLOWED TOOLS:"]
    for t in tools:
        lines.append(
            f"- id: {t.id}\n  name: {t.name}\n  description: {t.description}"
        )
    lines.append("")
    lines.append(f"USER HAS UPLOADED DOCUMENTS: {'yes' if has_docs else 'no'}")
    if document_ids:
        lines.append(f"USER SCOPED THE QUESTION TO document_ids: {document_ids}")
    lines.append("")
    lines.append("USER QUESTION:")
    lines.append(message)
    return "\n".join(lines)


async def plan(*, message: str, tools: list[ToolSpec], document_ids: list[str],
               has_docs: bool, user_id: str, run_id: str) -> AgentPlan:
    prompt = _shortlisted_prompt(tools, message, document_ids, has_docs)

    # Preferred path: schema-validated JSON planner. Falls back to the older
    # prompt-parsing approach if the model can't produce a valid plan.
    plan_obj = await complete_json(
        session_id=f"planner:{user_id}:{run_id}",
        system=_PLANNER_SYSTEM,
        user=prompt,
        schema=AgentPlan,
        max_tokens=800,
        retries=1,
    )
    if plan_obj is not None:
        # Filter to allowed tool ids and inject query defaults.
        allowed_ids = {t.id for t in tools}
        cleaned_steps: list[PlanStep] = []
        for i, s in enumerate(plan_obj.steps or []):
            if s.tool_id not in allowed_ids:
                continue
            args = dict(s.arguments or {})
            if not args.get("query") and s.tool_id in {
                "search_document_chunks", "web_search", "paper_search"
            }:
                args["query"] = message
            if s.tool_id == "search_document_chunks" and document_ids and not args.get("document_ids"):
                args["document_ids"] = document_ids
            cleaned_steps.append(PlanStep(
                id=s.id or f"s{i+1}",
                tool_id=s.tool_id,
                arguments=args,
                depends_on=s.depends_on,
                rationale=s.rationale,
                expected_output=s.expected_output,
                requires_approval=s.requires_approval,
            ))
        if cleaned_steps:
            return AgentPlan(goal=plan_obj.goal, reasoning=plan_obj.reasoning, steps=cleaned_steps)

    # Fallback: old prompt-parsing planner.
    try:
        raw = await complete(
            session_id=f"planner:{user_id}:{run_id}#fb",
            system=_PLANNER_SYSTEM,
            user=prompt,
            max_tokens=800,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("planner LLM failed: %s — falling back to single-step plan", exc)
        return _fallback_plan(message, tools, has_docs)

    parsed = extract_json(raw)
    if not isinstance(parsed, dict) or "steps" not in parsed:
        log.warning("planner produced non-JSON; falling back")
        return _fallback_plan(message, tools, has_docs)

    steps: list[PlanStep] = []
    allowed_ids = {t.id for t in tools}
    for i, s in enumerate(parsed.get("steps") or []):
        tool_id = s.get("tool_id")
        if tool_id not in allowed_ids:
            continue
        step_id = str(s.get("id") or f"s{i+1}")
        args = s.get("arguments") or {}
        if isinstance(args, str):
            args = {"query": args}
        if not args.get("query") and tool_id in {
            "search_document_chunks", "web_search", "paper_search"
        }:
            args["query"] = message
        if tool_id == "search_document_chunks" and document_ids and not args.get("document_ids"):
            args["document_ids"] = document_ids
        steps.append(
            PlanStep(
                id=step_id,
                tool_id=tool_id,
                arguments=args,
                depends_on=[str(x) for x in (s.get("depends_on") or [])],
                rationale=str(s.get("rationale") or ""),
                expected_output=str(s.get("expected_output") or ""),
                requires_approval=False,
            )
        )

    if not steps:
        return _fallback_plan(message, tools, has_docs)

    return AgentPlan(
        goal=str(parsed.get("goal") or "Answer the user's question with grounded evidence."),
        reasoning=str(parsed.get("reasoning") or ""),
        steps=steps,
    )


def _fallback_plan(message: str, tools: list[ToolSpec], has_docs: bool) -> AgentPlan:
    """Deterministic single-step plan used when the planner LLM is unavailable
    or emits unparseable output. Picks the highest-signal read tool."""
    if not tools:
        return AgentPlan(goal="No tools available", steps=[])
    chosen = tools[0]
    step = PlanStep(
        id="s1",
        tool_id=chosen.id,
        arguments={"query": message},
        rationale="Deterministic fallback plan (planner LLM unavailable).",
        expected_output="Retrieved evidence for user question.",
    )
    return AgentPlan(
        goal="Answer using a single tool call.",
        steps=[step],
        reasoning="LLM planner unavailable; used deterministic capability selection.",
    )


# --------------------------------------------------------------------------
# 3. Policy engine — read-only tools run automatically
# --------------------------------------------------------------------------

def validate_plan(agent_plan: AgentPlan) -> tuple[bool, list[str], list[PlanStep]]:
    """Split plan steps into (auto-runnable, approval-required, invalid).

    Returns ``(ok, problems, approval_needed)`` where:
    * ``problems`` — unknown/unavailable tools that block the whole plan
    * ``approval_needed`` — plan steps that must be user-approved before
      executing (write / sensitive / any tool with ``requires_approval``)
    * ``ok`` is True iff there are no blocking problems.

    Notice that ``approval_needed`` being non-empty does NOT set ``ok=False``
    on its own. The executor uses ``approval_needed`` to decide whether to
    pause; the run is only *failed* when there are actual policy problems.
    """
    reg = get_registry()
    problems: list[str] = []
    approval_needed: list[PlanStep] = []
    for step in agent_plan.steps:
        spec = reg.get(step.tool_id)
        if spec is None:
            problems.append(f"Unknown tool_id: {step.tool_id}")
            continue
        if not spec.available:
            problems.append(f"Tool '{spec.id}' is not available: {spec.unavailable_reason}")
            continue
        if spec.requires_approval or spec.risk_level != "read":
            approval_needed.append(step)
    ok = not problems
    return ok, problems, approval_needed


def steps_needing_approval(agent_plan: AgentPlan) -> list[PlanStep]:
    reg = get_registry()
    out: list[PlanStep] = []
    for step in agent_plan.steps:
        spec = reg.get(step.tool_id)
        if spec and (spec.requires_approval or spec.risk_level != "read"):
            out.append(step)
    return out


# --------------------------------------------------------------------------
# 4. Executor
# --------------------------------------------------------------------------

async def _execute_step(step: PlanStep, *, user_id: str) -> tuple[ToolCallLog, list[dict]]:
    reg = get_registry()
    spec = reg.get(step.tool_id)
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    log_entry = ToolCallLog(
        id=str(uuid.uuid4()),
        step_id=step.id,
        tool_id=step.tool_id,
        status="running",
        started_at=started,
        arguments=step.arguments,
    )
    if spec is None:
        log_entry.status = "error"
        log_entry.error = f"Unknown tool: {step.tool_id}"
        log_entry.completed_at = datetime.now(timezone.utc)
        log_entry.latency_ms = int((time.monotonic() - t0) * 1000)
        return log_entry, []
    if not spec.available:
        log_entry.status = "skipped"
        log_entry.error = spec.unavailable_reason
        log_entry.completed_at = datetime.now(timezone.utc)
        log_entry.latency_ms = int((time.monotonic() - t0) * 1000)
        return log_entry, []

    args = dict(step.arguments or {})
    args["user_id"] = user_id

    try:
        result = await asyncio.wait_for(spec.executor(**args), timeout=_STEP_TIMEOUT_S)
    except asyncio.TimeoutError:
        log_entry.status = "error"
        log_entry.error = f"Timeout after {_STEP_TIMEOUT_S}s"
        log_entry.completed_at = datetime.now(timezone.utc)
        log_entry.latency_ms = int((time.monotonic() - t0) * 1000)
        return log_entry, []
    except Exception as exc:  # noqa: BLE001
        log_entry.status = "error"
        log_entry.error = str(exc)[:400]
        log_entry.completed_at = datetime.now(timezone.utc)
        log_entry.latency_ms = int((time.monotonic() - t0) * 1000)
        return log_entry, []

    evidence = result.get("evidence") or []
    log_entry.output_summary = result.get("summary")
    log_entry.evidence_count = len(evidence)
    log_entry.status = "skipped" if result.get("unavailable") else "ok"
    log_entry.completed_at = datetime.now(timezone.utc)
    log_entry.latency_ms = int((time.monotonic() - t0) * 1000)
    return log_entry, evidence


async def execute_plan(agent_plan: AgentPlan, *, user_id: str
                       ) -> tuple[list[ToolCallLog], list[EvidenceItem]]:
    tool_calls: list[ToolCallLog] = []
    evidence: list[EvidenceItem] = []
    if not agent_plan.steps:
        return tool_calls, evidence

    # Simple parallel execution: steps with no depends_on go first (as one
    # batch), then dependents. In practice this covers the "run all
    # read-only searches concurrently" case.
    remaining = list(agent_plan.steps)
    done_ids: set[str] = set()

    while remaining:
        batch = [s for s in remaining if all(dep in done_ids for dep in s.depends_on)]
        if not batch:
            # Broken dependency → run whatever is left sequentially.
            batch = [remaining[0]]

        results = await asyncio.gather(
            *[_execute_step(step, user_id=user_id) for step in batch]
        )
        for step, (call_log, ev_list) in zip(batch, results):
            tool_calls.append(call_log)
            for e in ev_list:
                try:
                    evidence.append(EvidenceItem(id=str(uuid.uuid4()), **e))
                except Exception:  # noqa: BLE001 - skip malformed evidence
                    log.warning("dropped malformed evidence for tool %s", step.tool_id)
            done_ids.add(step.id)
            remaining = [s for s in remaining if s.id != step.id]

    return tool_calls, evidence


# --------------------------------------------------------------------------
# 5. Synthesizer (streamed)
# --------------------------------------------------------------------------

_SYNTH_SYSTEM = """You are Runner.ai's grounded-answer synthesizer.

You will receive:
- A CONVERSATION context (recent messages).
- A collection of EVIDENCE items retrieved by tools.
- The user's current QUESTION.

Answer the user's question using ONLY the evidence and the conversation
context. Requirements:

1. Cite every factual claim inline as [n] where n is the evidence number.
   Multiple citations are fine, e.g. [1][3].
2. Never invent citations. If evidence is missing, say so plainly.
3. Distinguish source types when relevant:
   - "Your document (<filename>, page X)" for private_doc evidence
   - "Research paper" for research_paper evidence
   - "Web source" for web_source evidence
4. Prefer concise, well-structured prose. Use short paragraphs. Bullet lists
   only when the user asked for a comparison or list.
5. If no evidence was retrieved OR all tools failed, say so and suggest what
   the user could do next (e.g. upload a document, enable web search). Do NOT
   attempt to answer from general knowledge in that case.
6. Never claim you performed a tool call that isn't in the evidence.
"""


def _format_evidence(evidence: list[EvidenceItem]) -> str:
    if not evidence:
        return "(no evidence retrieved)"
    lines: list[str] = []
    for i, e in enumerate(evidence, start=1):
        if e.source_type == ToolBadge.PRIVATE_DOC:
            head = f"[{i}] YOUR DOCUMENT — {e.filename or ''}"
            if e.page:
                head += f", page {e.page}"
        elif e.source_type == ToolBadge.RESEARCH_PAPER:
            authors = ", ".join(e.authors[:3]) if e.authors else "unknown authors"
            head = f"[{i}] RESEARCH PAPER — {e.title} ({authors}, {e.published or 'n.d.'}) {e.url or ''}"
        elif e.source_type == ToolBadge.WEB_SOURCE:
            head = f"[{i}] WEB — {e.title} {e.url or ''}"
        else:
            head = f"[{i}] CONTEXT — {e.title}"
        body = (e.snippet or "").strip().replace("\n", " ")[:500]
        lines.append(f"{head}\n    {body}")
    return "\n\n".join(lines)


def _format_history(history: list[dict[str, Any]]) -> str:
    if not history:
        return "(no prior turns)"
    parts: list[str] = []
    for m in history[-6:]:
        role = m.get("role", "user")
        content = (m.get("content") or "").replace("\n", " ")[:500]
        parts.append(f"{role.upper()}: {content}")
    return "\n".join(parts)


def build_synth_prompt(*, question: str, evidence: list[EvidenceItem],
                       history: list[dict[str, Any]]) -> str:
    return (
        f"CONVERSATION HISTORY:\n{_format_history(history)}\n\n"
        f"EVIDENCE:\n{_format_evidence(evidence)}\n\n"
        f"CURRENT QUESTION:\n{question}\n\n"
        f"Answer now, citing [n] for each factual claim."
    )


async def synthesize_stream(*, user_id: str, run_id: str, question: str,
                            evidence: list[EvidenceItem], history: list[dict[str, Any]]):
    prompt = build_synth_prompt(question=question, evidence=evidence, history=history)
    async for chunk in stream(
        session_id=f"synth:{user_id}:{run_id}",
        system=_SYNTH_SYSTEM,
        user=prompt,
    ):
        yield chunk


async def synthesize(*, user_id: str, run_id: str, question: str,
                     evidence: list[EvidenceItem], history: list[dict[str, Any]]) -> str:
    prompt = build_synth_prompt(question=question, evidence=evidence, history=history)
    try:
        return await complete(
            session_id=f"synth:{user_id}:{run_id}",
            system=_SYNTH_SYSTEM,
            user=prompt,
            max_tokens=1200,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("synth LLM failed: %s", exc)
        return _fallback_answer(evidence)


def _fallback_answer(evidence: list[EvidenceItem]) -> str:
    if not evidence:
        return (
            "I couldn't retrieve any evidence for this question and the answer "
            "LLM is currently unavailable. Please try again, or upload a "
            "document so I can search it."
        )
    top = evidence[:3]
    lines = ["I retrieved the following evidence but the answer LLM is currently unavailable:"]
    for i, e in enumerate(top, start=1):
        lines.append(f"[{i}] {e.title}: {(e.snippet or '')[:200]}")
    return "\n\n".join(lines)


# --------------------------------------------------------------------------
# 6. Full run helpers (persist to Mongo)
# --------------------------------------------------------------------------

async def create_run_record(*, user_id: str, thread_id: str,
                            message: str, document_ids: list[str]) -> str:
    db = get_db()
    now = datetime.now(timezone.utc)
    doc = {
        "user_id": user_id,
        "thread_id": thread_id,
        "message": message,
        "document_ids": document_ids,
        "status": "planning",
        "created_at": now,
        "plan": None,
        "tool_calls": [],
        "evidence": [],
        "answer": None,
        "error": None,
    }
    res = await db.agent_runs.insert_one(doc)
    return str(res.inserted_id)


async def update_run(run_id: str, *, patch: dict[str, Any]) -> None:
    await get_db().agent_runs.update_one({"_id": ObjectId(run_id)}, {"$set": patch})


async def get_run(user_id: str, run_id: str) -> dict[str, Any] | None:
    try:
        return await get_db().agent_runs.find_one(
            {"_id": ObjectId(run_id), "user_id": user_id}
        )
    except Exception:  # noqa: BLE001
        return None
