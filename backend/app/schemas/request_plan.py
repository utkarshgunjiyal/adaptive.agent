from pydantic import BaseModel, Field
from typing import Literal


Intent = Literal[
    "general",
    "document",
    "memory",
    "preference",
]

Operation = Literal[
    "qa",
    "summarize",
    "compare",
    "lookup",
    "update",
]


class RequestFilters(BaseModel):
    page: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    section: str | None = None
    topic: str | None = None


class RequestPlan(BaseModel):
    intent: Intent
    operation: Operation = "qa"
    filters: RequestFilters = Field(default_factory=RequestFilters)

    confidence: float = 1.0
    route_reason: str = ""

    tool: str | None = None
    hitl: bool = False