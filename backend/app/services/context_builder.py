from app.schemas.request_plan import RequestPlan


def build_context(
    question: str,
    request_plan: RequestPlan,
    memory: dict,
) -> dict:
    return {
        "request_plan": request_plan.model_dump(),
        "system_prompt": (
            "You are Runner.ai, a context-aware AI assistant. "
            "Use the provided context according to the request plan."
        ),
        "recent_messages": [
            {
                "role": msg["role"],
                "content": msg["content"],
                "seq": msg["seq"],
            }
            for msg in memory.get("recent_messages", [])
        ],
        "thread_summary": memory.get("thread_summary"),
        "knowledge": memory.get("knowledge", []),
        "user_preferences": memory.get("user_preferences", []),
        "documents": memory.get("documents", []),
        "document_summary": memory.get("document_summary"),
        "page_summary": memory.get("page_summary"),
        "section_summaries": memory.get("section_summaries", []),
        "chunks": memory.get("chunks", []),
        "question": question,
    }