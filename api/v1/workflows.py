"""Workflow endpoints."""
from uuid import UUID
from fastapi import APIRouter, Depends
from core.schemas.api import WorkflowCreate, WorkflowRunTrigger, PaginatedResponse
from api.deps import get_current_tenant

router = APIRouter()

@router.get("/workflows", response_model=PaginatedResponse)
async def list_workflows(tenant_id: str = Depends(get_current_tenant)):
    return PaginatedResponse(items=[], total=0)

@router.post("/workflows", status_code=201)
async def create_workflow(body: WorkflowCreate, tenant_id: str = Depends(get_current_tenant)):
    return {"workflow_id": "wf-id", "version": body.version}

@router.get("/workflows/runs/{run_id}")
async def get_workflow_run(run_id: UUID, tenant_id: str = Depends(get_current_tenant)):
    return {"run_id": str(run_id), "status": "running", "started_at": "2026-03-21T00:00:00Z"}

@router.post("/workflows/{wf_id}/run")
async def run_workflow(wf_id: UUID, body: WorkflowRunTrigger = WorkflowRunTrigger(), tenant_id: str = Depends(get_current_tenant)):
    return {"run_id": "wfr-id", "status": "running"}
