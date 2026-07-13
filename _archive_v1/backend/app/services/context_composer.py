from app.schemas.memory_context import MemoryContext
from app.schemas.request_plan import RequestPlan
from app.schemas.context_policy import ContextPolicy
from app.schemas.context_evidence import ContextEvidence
from app.config import settings
from app.logging_config import get_logger

logger = get_logger("context_composer")


def _estimate_tokens(text: str) -> int:
    ratio = max(1, settings.context_chars_per_token)
    return (len(text) + ratio - 1) // ratio  # ceil


def _apply_token_budget(
    blocks: list[str],
    budget_tokens: int,
) -> tuple[list[str], dict]:
    """Keep evidence blocks in priority order until the token budget is spent.

    The block that crosses the boundary is truncated to fit if a meaningful
    amount of budget remains; lower-priority blocks are dropped.
    """
    if budget_tokens <= 0:
        return blocks, {"kept": len(blocks), "dropped": 0, "truncated": 0}

    ratio = max(1, settings.context_chars_per_token)
    kept: list[str] = []
    used = 0
    truncated = 0

    for block in blocks:
        cost = _estimate_tokens(block)
        if used + cost <= budget_tokens:
            kept.append(block)
            used += cost
            continue

        remaining = budget_tokens - used
        if remaining >= 20:  # worth including a partial block
            suffix = "\n…[truncated]"
            max_chars = max(0, remaining * ratio - len(suffix))
            kept.append(block[:max_chars].rstrip() + suffix)
            truncated = 1
        break

    stats = {"kept": len(kept), "dropped": len(blocks) - len(kept), "truncated": truncated}
    return kept, stats


def _collect_evidence_by_priority(
    memory: MemoryContext,
    context_policy: ContextPolicy,
) -> list[ContextEvidence]:
    source_map = {
        "recent_messages": memory.recent_messages,
        "thread_summary": memory.thread_summary,
        "knowledge": memory.knowledge,
        "user_preferences": memory.user_preferences,
        "document_summary": memory.document_summary,
        "page_summary": memory.page_summary,
        "section_summaries": memory.section_summaries,
        "chunks": memory.chunks,
    }

    evidence: list[ContextEvidence] = []

    for source_name in context_policy.priority:
        evidence.extend(source_map.get(source_name, []))

    return evidence


def compose_context(
    question: str,
    request_plan: RequestPlan,
    context_policy: ContextPolicy,
    memory: MemoryContext,
) -> dict:
    evidence = _collect_evidence_by_priority(
        memory=memory,
        context_policy=context_policy,
    )

    formatted_evidence = [
        f"{item.header or f'[{item.source}]'}\n{item.content}"
        for item in evidence
    ]

    formatted_evidence, budget_stats = _apply_token_budget(
        formatted_evidence,
        context_policy.context_budget_tokens,
    )
    if budget_stats["dropped"] or budget_stats["truncated"]:
        logger.info(
            "context.budget_enforced",
            extra={
                "budget_tokens": context_policy.context_budget_tokens,
                **budget_stats,
            },
        )

    return {
        "request_plan": request_plan.model_dump(),
        "context_policy": context_policy.model_dump(),
        "system_prompt": (
            "You are Runner.ai, a context-aware AI assistant. "
            "Use the evidence in priority order. "
            "The current user message has highest priority."
        ),
        "evidence": formatted_evidence,
        "question": question,
    }