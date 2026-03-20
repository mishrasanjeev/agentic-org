"""Connector endpoints."""
from uuid import UUID
from fastapi import APIRouter, Depends
from core.schemas.api import ConnectorCreate
from api.deps import get_current_tenant

router = APIRouter()

@router.post("/connectors", status_code=201)
async def register_connector(body: ConnectorCreate, tenant_id: str = Depends(get_current_tenant)):
    return {"connector_id": "conn-id", "name": body.name, "status": "active"}

@router.get("/connectors/{conn_id}/health")
async def connector_health(conn_id: UUID, tenant_id: str = Depends(get_current_tenant)):
    return {"connector_id": str(conn_id), "status": "healthy"}
