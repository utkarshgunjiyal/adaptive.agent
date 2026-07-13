"""Auth routes: register, login, logout, me."""

from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import (
    check_rate_limit,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.config import settings
from app.db import get_db
from app.models import TokenResponse, UserLoginRequest, UserPublic, UserRegisterRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _public(user_doc: dict) -> UserPublic:
    return UserPublic(
        id=str(user_doc["_id"]),
        email=user_doc["email"],
        name=user_doc.get("name", ""),
        created_at=user_doc["created_at"],
    )


@router.post("/register", response_model=TokenResponse)
async def register(payload: UserRegisterRequest, request: Request):
    key = f"auth:register:{request.client.host if request.client else 'anon'}"
    check_rate_limit(key, settings.rate_limit_auth_per_minute)

    db = get_db()
    email = payload.email.lower().strip()
    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=409, detail="An account with that email already exists.")

    now = datetime.now(timezone.utc)
    user_doc = {
        "email": email,
        "name": payload.name.strip()[:100],
        "password_hash": hash_password(payload.password),
        "created_at": now,
        "updated_at": now,
    }
    res = await db.users.insert_one(user_doc)
    user_doc["_id"] = res.inserted_id

    token = create_access_token(str(res.inserted_id), email)
    return TokenResponse(access_token=token, user=_public(user_doc))


@router.post("/login", response_model=TokenResponse)
async def login(payload: UserLoginRequest, request: Request):
    key = f"auth:login:{request.client.host if request.client else 'anon'}"
    check_rate_limit(key, settings.rate_limit_auth_per_minute)

    db = get_db()
    email = payload.email.lower().strip()
    user_doc = await db.users.find_one({"email": email})
    if not user_doc or not verify_password(payload.password, user_doc["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    token = create_access_token(str(user_doc["_id"]), email)
    return TokenResponse(access_token=token, user=_public(user_doc))


@router.post("/logout")
async def logout(user=Depends(get_current_user)):
    # Stateless JWT: client should discard the token. No server state to
    # invalidate in the preview build.
    return {"ok": True}


@router.get("/me", response_model=UserPublic)
async def me(user=Depends(get_current_user)):
    db = get_db()
    doc = await db.users.find_one({"_id": ObjectId(user["id"])})
    if not doc:
        raise HTTPException(status_code=404, detail="User not found")
    return _public(doc)
