from pydantic import BaseModel, Field
from typing import Literal


EvidenceSource = Literal[
    "recent_message",
    "thread_summary",
    "knowledge",
    "user_preference",
    "document_summary",
    "page_summary",
    "section_summary",
    "document_chunk",
]


class ContextEvidence(BaseModel):
    source: EvidenceSource
    content: str

    header: str = ""
    score: float = 1.0
    priority: int = 100

    metadata: dict = Field(default_factory=dict)