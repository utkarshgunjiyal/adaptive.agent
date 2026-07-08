from datetime import datetime

from pydantic import BaseModel, Field


class PreferencePublic(BaseModel):
    id: str
    text: str
    created_at: datetime


class KnowledgeCreate(BaseModel):
    text: str = Field(..., min_length=1)


class KnowledgePublic(BaseModel):
    id: str
    text: str
    source: str
    created_at: datetime
