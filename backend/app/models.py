"""Pydantic domain models for Runner.ai.

MongoDB stores documents as dicts. These models are used for API request/
response shapes and for validating structured LLM output.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# -- Auth ------------------------------------------------------------------

class UserRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=200)
    name: str = Field(min_length=1, max_length=100)


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class UserPublic(BaseModel):
    id: str
    email: str
    name: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic


# -- Threads / messages ----------------------------------------------------

class ThreadCreateRequest(BaseModel):
    title: str | None = None


class ThreadPublic(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class MessagePublic(BaseModel):
    id: str
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: datetime
    citations: list[dict] = Field(default_factory=list)
    tool_badges: list[str] = Field(default_factory=list)
    run_id: str | None = None


# -- Documents / jobs ------------------------------------------------------

class DocumentStatus(str, Enum):
    UPLOADED = "uploaded"
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class DocumentPublic(BaseModel):
    id: str
    filename: str
    size_bytes: int
    status: DocumentStatus
    page_count: int | None = None
    chunk_count: int | None = None
    summary: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class JobPublic(BaseModel):
    id: str
    document_id: str
    status: DocumentStatus
    progress: int = 0
    attempt_count: int = 0
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class UploadResponse(BaseModel):
    document_id: str
    job_id: str
    status: DocumentStatus


# -- Agent runs / plans / tools -------------------------------------------

class PlanStep(BaseModel):
    id: str
    tool_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    rationale: str = ""
    expected_output: str = ""
    requires_approval: bool = False


class AgentPlan(BaseModel):
    goal: str
    steps: list[PlanStep]
    reasoning: str = ""


class ToolBadge(str, Enum):
    PRIVATE_DOC = "private_doc"
    RESEARCH_PAPER = "research_paper"
    WEB_SOURCE = "web_source"
    CONTEXT = "context"


class EvidenceItem(BaseModel):
    id: str
    source_type: ToolBadge
    title: str
    snippet: str
    # For private docs
    document_id: str | None = None
    filename: str | None = None
    page: int | None = None
    # For web + papers
    url: str | None = None
    authors: list[str] = Field(default_factory=list)
    published: str | None = None
    # Score
    score: float | None = None


class ToolCallLog(BaseModel):
    id: str
    step_id: str
    tool_id: str
    status: Literal["pending", "running", "ok", "error", "skipped"] = "pending"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    latency_ms: int | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    output_summary: str | None = None
    error: str | None = None
    evidence_count: int = 0


class AgentRunRequest(BaseModel):
    thread_id: str | None = None
    message: str = Field(min_length=1, max_length=8000)
    document_ids: list[str] = Field(default_factory=list)


class AgentRunPublic(BaseModel):
    id: str
    thread_id: str
    status: Literal["planning", "executing", "synthesizing", "completed", "failed", "waiting_approval"]
    created_at: datetime
    completed_at: datetime | None = None
    plan: AgentPlan | None = None
    tool_calls: list[ToolCallLog] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    answer: str | None = None
    citations: list[EvidenceItem] = Field(default_factory=list)
    selected_tools: list[str] = Field(default_factory=list)
    error: str | None = None
    duration_ms: int | None = None
