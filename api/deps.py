"""FastAPI dependencies."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    async for session in get_session():
        yield session

def get_current_tenant(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", None)
    if not tid:
        raise HTTPException(401, "No tenant context")
    return tid

def get_current_user(request: Request) -> dict:
    claims = getattr(request.state, "claims", None)
    if not claims:
        raise HTTPException(401, "Not authenticated")
    return claims

def require_scope(scope: str):
    def checker(request: Request):
        scopes = getattr(request.state, "scopes", [])
        if scope not in scopes and not any(s.startswith("agentflow:admin") for s in scopes):
            raise HTTPException(403, f"Missing scope: {scope}")
    return Depends(checker)
