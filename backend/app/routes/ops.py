"""Tool registry + health routes."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import settings
from app.db import get_client
from app.llm_factory import llm_config_problem, resolve_provider
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
    """Deep readiness check — verifies MongoDB is reachable AND that the LLM
    configuration is valid for the current environment.

    In production a missing or invalid LLM configuration (including the
    deterministic ``stub`` provider, which is not permitted outside
    development / test) fails readiness clearly instead of letting the service
    silently serve stub answers. Returns HTTP 503 when not ready.
    """
    try:
        client = get_client()
        await client.admin.command("ping")
        mongo_ok = True
    except Exception:  # noqa: BLE001
        mongo_ok = False

    llm_problem = llm_config_problem(settings)
    llm_ok = llm_problem is None
    llm_status = {"ok": llm_ok, "provider": resolve_provider(settings)}
    if llm_problem:
        llm_status["detail"] = llm_problem

    ok = mongo_ok and llm_ok
    body = {"ready": ok, "mongodb": mongo_ok, "llm": llm_status}
    return JSONResponse(status_code=200 if ok else 503, content=body)
