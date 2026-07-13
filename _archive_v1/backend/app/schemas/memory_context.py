from pydantic import BaseModel, Field
from app.schemas.context_evidence import ContextEvidence


class MemoryContext(BaseModel):
    recent_messages: list[ContextEvidence] = Field(default_factory=list)
    thread_summary: list[ContextEvidence] = Field(default_factory=list)
    knowledge: list[ContextEvidence] = Field(default_factory=list)
    user_preferences: list[ContextEvidence] = Field(default_factory=list)

    documents: list[ContextEvidence] = Field(default_factory=list)
    document_summary: list[ContextEvidence] = Field(default_factory=list)
    page_summary: list[ContextEvidence] = Field(default_factory=list)
    section_summaries: list[ContextEvidence] = Field(default_factory=list)
    chunks: list[ContextEvidence] = Field(default_factory=list)