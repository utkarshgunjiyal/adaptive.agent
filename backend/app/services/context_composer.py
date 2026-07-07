from app.schemas.memory_context import MemoryContext
from app.schemas.request_plan import RequestPlan
from app.schemas.context_policy import ContextPolicy
from app.schemas.context_evidence import ContextEvidence


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