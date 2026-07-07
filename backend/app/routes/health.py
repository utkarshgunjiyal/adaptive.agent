from fastapi import APIRouter, HTTPException
from app.database import db

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health_check():
    try:
        await db.command("ping")
        return {
            "status": "healthy",
            "services": {
                "mongodb": "connected"
            }
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail={
            "status": "unhealthy",
            "services": {
                "mongodb": "failed"
            },
            "error": str(e)
        })