"""Agent CRUD + lifecycle endpoints."""
from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from core.schemas.api import AgentCreate, AgentUpdate, AgentResponse, AgentCloneRequest, PaginatedResponse
from api.deps import get_current_tenant, require_scope

router = APIRouter()

@router.get("/agents", response_model=PaginatedResponse)
async def list_agents(domain: str | None = None, status: str | None = None, page: int = 1, per_page: int = 20, tenant_id: str = Depends(get_current_tenant)):
    return PaginatedResponse(items=[], total=0, page=page, per_page=per_page)

@router.post("/agents", status_code=201)
async def create_agent(body: AgentCreate, tenant_id: str = Depends(get_current_tenant)):
    return {"agent_id": "new-agent-id", "status": "shadow", "token_issued": True}

@router.get("/agents/{agent_id}")
async def get_agent(agent_id: UUID, tenant_id: str = Depends(get_current_tenant)):
    return {"id": str(agent_id), "status": "active"}

@router.put("/agents/{agent_id}")
async def replace_agent(agent_id: UUID, body: AgentCreate, tenant_id: str = Depends(get_current_tenant)):
    return {"id": str(agent_id), "replaced": True}

@router.patch("/agents/{agent_id}")
async def update_agent(agent_id: UUID, body: AgentUpdate, tenant_id: str = Depends(get_current_tenant)):
    return {"id": str(agent_id), "updated": True}

@router.post("/agents/{agent_id}/run")
async def run_agent(agent_id: UUID, payload: dict = {}, tenant_id: str = Depends(get_current_tenant)):
    return {"task_id": "task-id", "status": "queued"}

@router.post("/agents/{agent_id}/pause")
async def pause_agent(agent_id: UUID, tenant_id: str = Depends(get_current_tenant)):
    return {"id": str(agent_id), "status": "paused", "token_revoked": True}

@router.post("/agents/{agent_id}/resume")
async def resume_agent(agent_id: UUID, tenant_id: str = Depends(get_current_tenant)):
    return {"id": str(agent_id), "status": "active"}

@router.post("/agents/{agent_id}/promote")
async def promote_agent(agent_id: UUID, tenant_id: str = Depends(get_current_tenant)):
    return {"id": str(agent_id), "promoted": True}

@router.post("/agents/{agent_id}/rollback")
async def rollback_agent(agent_id: UUID, tenant_id: str = Depends(get_current_tenant)):
    return {"id": str(agent_id), "rolled_back": True}

@router.post("/agents/{agent_id}/clone")
async def clone_agent(agent_id: UUID, body: AgentCloneRequest, tenant_id: str = Depends(get_current_tenant)):
    return {"clone_id": "clone-id", "status": "shadow", "parent_id": str(agent_id)}
