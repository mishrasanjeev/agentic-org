"""Audit log endpoint."""
from fastapi import APIRouter, Depends, Query
from core.schemas.api import PaginatedResponse
from api.deps import get_current_tenant

router = APIRouter()

@router.get("/audit", response_model=PaginatedResponse)
async def query_audit(event_type: str | None = None, agent_id: str | None = None, date_from: str | None = None, date_to: str | None = None, page: int = 1, per_page: int = 50, tenant_id: str = Depends(get_current_tenant)):
    return PaginatedResponse(items=[], total=0, page=page, per_page=per_page)
