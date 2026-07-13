"""Tool registry + health routes."""

from __future__ import annotations

from fastapi import APIRouter

from app.db import get_client
from app.tools.registry import get_registry

router = APIRouter(prefix="/api", tags=["ops"])


@router.get("/tools")
async def list_tools():
    return {"tools": get_registry().public_view()}


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/ready")
async def ready():
    """Deep readiness check — verifies MongoDB is reachable."""
    try:
        client = get_client()
        await client.admin.command("ping")
        mongo_ok = True
    except Exception:  # noqa: BLE001
        mongo_ok = False
    ok = mongo_ok
    return {"ready": ok, "mongodb": mongo_ok}
