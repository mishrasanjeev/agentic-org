"""Schema registry endpoints."""
from fastapi import APIRouter, Depends
from core.schemas.api import SchemaCreate, PaginatedResponse
from api.deps import get_current_tenant

router = APIRouter()

@router.get("/schemas", response_model=PaginatedResponse)
async def list_schemas(tenant_id: str = Depends(get_current_tenant)):
    return PaginatedResponse(items=[], total=0)

@router.post("/schemas", status_code=201)
async def create_schema(body: SchemaCreate, tenant_id: str = Depends(get_current_tenant)):
    return {"name": body.name, "version": body.version, "created": True}

@router.put("/schemas/{name}")
async def upsert_schema(name: str, body: SchemaCreate, tenant_id: str = Depends(get_current_tenant)):
    return {"name": name, "version": body.version, "updated": True}
