from fastapi import FastAPI

from app.routes.health import router as health_router
from app.routes.chat import router as chat_router

app = FastAPI(title="Runner.ai V1")

app.include_router(health_router)
app.include_router(chat_router)


@app.get("/")
async def root():
    return {"message": "Runner.ai V1 backend is running"}