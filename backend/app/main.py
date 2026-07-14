"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import close_client, ensure_indexes
from app.routes.agent import router as agent_router
from app.routes.adaptive_agent import router as adaptive_agent_router
from app.routes.auth import router as auth_router
from app.routes.digests import router as digests_router
from app.routes.documents import router as documents_router
from app.routes.ops import router as ops_router
from app.routes.share import router as share_router
from app.routes.threads import router as threads_router
from app.services import digest as digest_svc
from app.services.mcp import discover_and_register

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("runner")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
    await ensure_indexes()

    # MCP tools — best-effort registration.
    try:
        n = await discover_and_register()
        log.info("MCP: registered %s tool(s)", n)
    except Exception as exc:  # noqa: BLE001
        log.warning("MCP discovery error: %s", exc)

    # Digest scheduler.
    try:
        await digest_svc.start()
    except Exception as exc:  # noqa: BLE001
        log.warning("digest scheduler failed to start: %s", exc)

    log.info("runner.ai backend ready")
    yield

    try:
        await digest_svc.stop()
    except Exception:  # noqa: BLE001
        pass
    await close_client()


app = FastAPI(title="Runner.ai", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_credentials=settings.cors_origin_list() != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ops_router)
app.include_router(auth_router)
app.include_router(threads_router)
app.include_router(documents_router)
app.include_router(agent_router)
app.include_router(adaptive_agent_router)
app.include_router(digests_router)
app.include_router(share_router)


@app.get("/api")
async def api_root():
    return {"app": "Runner.ai", "status": "ok", "version": "2.0.0"}
