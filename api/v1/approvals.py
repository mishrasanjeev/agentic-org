"""HITL approval endpoints."""
from uuid import UUID
from fastapi import APIRouter, Depends
from core.schemas.api import HITLDecision, PaginatedResponse
from api.deps import get_current_tenant

router = APIRouter()

@router.get("/approvals", response_model=PaginatedResponse)
async def list_approvals(domain: str | None = None, priority: str | None = None, tenant_id: str = Depends(get_current_tenant)):
    return PaginatedResponse(items=[], total=0)

@router.post("/approvals/{hitl_id}/decide")
async def decide(hitl_id: UUID, body: HITLDecision, tenant_id: str = Depends(get_current_tenant)):
    return {"hitl_id": str(hitl_id), "decision": body.decision, "status": "decided"}
