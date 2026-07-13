from fastapi import APIRouter

from app.schemas.memory import KnowledgeCreate, KnowledgePublic, PreferencePublic
from app.services import knowledge_service, preference_service

router = APIRouter(prefix="/memory", tags=["memory"])

# Single-user placeholder until auth lands (Phase 5).
DEV_USER_ID = "dev_user"


@router.get("/preferences", response_model=list[PreferencePublic])
async def list_preferences(limit: int = 50) -> list[PreferencePublic]:
    prefs = await preference_service.get_preferences(DEV_USER_ID, limit=limit)
    return [
        PreferencePublic(id=str(p["_id"]), text=p["text"], created_at=p["created_at"])
        for p in prefs
    ]


@router.get("/knowledge", response_model=list[KnowledgePublic])
async def list_knowledge(limit: int = 50) -> list[KnowledgePublic]:
    items = await knowledge_service.list_knowledge(DEV_USER_ID, limit=limit)
    return [
        KnowledgePublic(
            id=str(k["_id"]),
            text=k["text"],
            source=k.get("source", "api"),
            created_at=k["created_at"],
        )
        for k in items
    ]


@router.post("/knowledge", response_model=KnowledgePublic, status_code=201)
async def add_knowledge(payload: KnowledgeCreate) -> KnowledgePublic:
    item = await knowledge_service.save_knowledge(DEV_USER_ID, payload.text, source="api")
    return KnowledgePublic(
        id=str(item["_id"]),
        text=item["text"],
        source=item.get("source", "api"),
        created_at=item["created_at"],
    )
