"""Fleet configuration endpoints."""
from fastapi import APIRouter, Depends
from core.schemas.api import FleetLimits
from api.deps import get_current_tenant

router = APIRouter()

@router.get("/config/fleet_limits")
async def get_fleet_limits(tenant_id: str = Depends(get_current_tenant)):
    return FleetLimits().model_dump()

@router.put("/config/fleet_limits")
async def update_fleet_limits(body: FleetLimits, tenant_id: str = Depends(get_current_tenant)):
    return body.model_dump()
