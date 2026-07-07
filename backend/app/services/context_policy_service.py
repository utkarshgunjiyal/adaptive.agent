from app.schemas.request_plan import RequestPlan
from app.schemas.context_policy import ContextPolicy


def get_context_policy(request_plan: RequestPlan) -> ContextPolicy:
    """
    Context Policy decides:
    - what to retrieve
    - how much to retrieve
    - priority order
    - context budget
    """

    # -------------------------
    # DOCUMENT SUMMARY
    # -------------------------
    if (
        request_plan.intent == "document"
        and request_plan.operation == "summarize"
    ):

        # Page Summary
        if request_plan.filters.page is not None:
            return ContextPolicy(
                recent_messages_limit=4,
                thread_summary=True,
                knowledge_top_k=0,
                user_preferences_top_k=2,
                document_summary=False,
                page_summary=True,
                section_summaries_top_k=0,
                chunks_top_k=6,
                priority=[
                    "page_summary",
                    "chunks",
                    "recent_messages",
                    "thread_summary",
                    "user_preferences",
                ],
                context_budget_tokens=4000,
            )

        # Full Document Summary
        return ContextPolicy(
            recent_messages_limit=4,
            thread_summary=True,
            knowledge_top_k=0,
            user_preferences_top_k=2,
            document_summary=True,
            page_summary=False,
            section_summaries_top_k=0,
            chunks_top_k=0,
            priority=[
                "document_summary",
                "recent_messages",
                "thread_summary",
                "user_preferences",
            ],
            context_budget_tokens=4000,
        )

    # -------------------------
    # DOCUMENT Q&A / COMPARE
    # -------------------------
    if request_plan.intent == "document":
        return ContextPolicy(
            recent_messages_limit=4,
            thread_summary=True,
            knowledge_top_k=2,
            user_preferences_top_k=2,
            document_summary=False,
            page_summary=False,
            section_summaries_top_k=2,
            chunks_top_k=8,
            priority=[
                "chunks",
                "section_summaries",
                "recent_messages",
                "thread_summary",
                "knowledge",
                "user_preferences",
            ],
            context_budget_tokens=6000,
        )

    # -------------------------
    # MEMORY LOOKUP
    # -------------------------
    if request_plan.intent == "memory":
        return ContextPolicy(
            recent_messages_limit=10,
            thread_summary=True,
            knowledge_top_k=5,
            user_preferences_top_k=2,
            document_summary=False,
            page_summary=False,
            section_summaries_top_k=0,
            chunks_top_k=0,
            priority=[
                "knowledge",
                "thread_summary",
                "recent_messages",
                "user_preferences",
            ],
            context_budget_tokens=5000,
        )

    # -------------------------
    # PREFERENCE UPDATE
    # -------------------------
    if request_plan.intent == "preference":
        return ContextPolicy(
            recent_messages_limit=10,
            thread_summary=True,
            knowledge_top_k=0,
            user_preferences_top_k=5,
            document_summary=False,
            page_summary=False,
            section_summaries_top_k=0,
            chunks_top_k=0,
            priority=[
                "recent_messages",
                "user_preferences",
                "thread_summary",
            ],
            context_budget_tokens=4000,
        )

    # -------------------------
    # GENERAL CHAT
    # -------------------------
    return ContextPolicy(
        recent_messages_limit=10,
        thread_summary=True,
        knowledge_top_k=0,
        user_preferences_top_k=3,
        document_summary=False,
        page_summary=False,
        section_summaries_top_k=0,
        chunks_top_k=0,
        priority=[
            "recent_messages",
            "thread_summary",
            "user_preferences",
        ],
        context_budget_tokens=4000,
    )