"""Agent team endpoints."""
from fastapi import APIRouter, Depends
from api.deps import get_current_tenant

router = APIRouter()

@router.post("/agent-teams", status_code=201)
async def create_team(body: dict, tenant_id: str = Depends(get_current_tenant)):
    return {"team_id": "team-id", "status": "active"}
