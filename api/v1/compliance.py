"""DSAR and compliance endpoints."""
from fastapi import APIRouter, Depends
from core.schemas.api import DSARRequest
from api.deps import get_current_tenant

router = APIRouter()

@router.post("/dsar/access")
async def dsar_access(body: DSARRequest, tenant_id: str = Depends(get_current_tenant)):
    return {"request_id": "dsar-id", "type": "access", "status": "processing"}

@router.post("/dsar/erase")
async def dsar_erase(body: DSARRequest, tenant_id: str = Depends(get_current_tenant)):
    return {"request_id": "dsar-id", "type": "erase", "status": "processing", "deadline_days": 30}

@router.post("/dsar/export")
async def dsar_export(body: DSARRequest, tenant_id: str = Depends(get_current_tenant)):
    return {"request_id": "dsar-id", "type": "export", "status": "processing", "format": "json", "estimated_size_mb": 0}

@router.get("/compliance/evidence-package")
async def evidence_package(tenant_id: str = Depends(get_current_tenant)):
    return {"package_id": "pkg-id", "generated_at": "2026-03-21T00:00:00Z", "sections": ["access_controls", "audit_logs", "deployment_records", "incident_history"]}
