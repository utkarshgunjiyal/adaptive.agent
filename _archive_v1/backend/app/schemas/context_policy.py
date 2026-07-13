from pydantic import BaseModel, Field


class ContextPolicy(BaseModel):
    recent_messages_limit: int = 10
    thread_summary: bool = True

    knowledge_top_k: int = 0
    user_preferences_top_k: int = 0

    document_summary: bool = False
    page_summary: bool = False
    section_summaries_top_k: int = 0
    chunks_top_k: int = 0

    priority: list[str] = Field(default_factory=list)
    context_budget_tokens: int = 4000