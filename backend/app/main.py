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
from app.routes.auth import router as auth_router
from app.routes.documents import router as documents_router
from app.routes.ops import router as ops_router
from app.routes.threads import router as threads_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("runner")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
    await ensure_indexes()
    log.info("runner.ai backend ready")
    yield
    await close_client()


app = FastAPI(title="Runner.ai", version="1.0.0", lifespan=lifespan)

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


@app.get("/api")
async def api_root():
    return {"app": "Runner.ai", "status": "ok"}
