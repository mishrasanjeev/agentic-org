"""Multi-company support — CA firm use case (one tenant, many client companies)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_current_tenant

router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory store (MVP — replaced by DB model in prod)
# ---------------------------------------------------------------------------

_companies: dict[str, dict[str, dict]] = {}  # tenant_id -> {company_id -> data}

# Seed demo data so the UI has something on first load
_DEMO_TENANT = "__demo__"
_demo_id_1 = str(uuid.uuid4())
_demo_id_2 = str(uuid.uuid4())
_companies[_DEMO_TENANT] = {
    _demo_id_1: {
        "id": _demo_id_1,
        "name": "Sharma & Associates",
        "gstin": "27AABCS1234F1Z5",
        "pan": "AABCS1234F",
        "industry": "Manufacturing",
        "created_at": "2026-01-15T10:00:00Z",
        "updated_at": "2026-01-15T10:00:00Z",
    },
    _demo_id_2: {
        "id": _demo_id_2,
        "name": "Gupta Traders Pvt Ltd",
        "gstin": "07AADCG5678H1Z3",
        "pan": "AADCG5678H",
        "industry": "Retail",
        "created_at": "2026-02-10T10:00:00Z",
        "updated_at": "2026-02-10T10:00:00Z",
    },
}

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CompanyCreate(BaseModel):
    name: str
    gstin: str | None = None
    pan: str | None = None
    industry: str | None = None


class CompanyUpdate(BaseModel):
    name: str | None = None
    gstin: str | None = None
    pan: str | None = None
    industry: str | None = None


class CompanyOut(BaseModel):
    id: str
    name: str
    gstin: str | None = None
    pan: str | None = None
    industry: str | None = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tenant_store(tenant_id: str) -> dict[str, dict]:
    """Return (or create) the company dict for a tenant."""
    if tenant_id not in _companies:
        _companies[tenant_id] = {}
    return _companies[tenant_id]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/companies", response_model=list[CompanyOut])
async def list_companies(tenant_id: str = Depends(get_current_tenant)):
    """List all companies for the current tenant."""
    store = _tenant_store(tenant_id)
    return [CompanyOut(**c) for c in store.values()]


@router.post("/companies", response_model=CompanyOut, status_code=201)
async def create_company(
    body: CompanyCreate,
    tenant_id: str = Depends(get_current_tenant),
):
    """Create a new client company under this tenant (CA firm adding a client)."""
    store = _tenant_store(tenant_id)
    now = datetime.now(UTC).isoformat()
    company_id = str(uuid.uuid4())
    record = {
        "id": company_id,
        "name": body.name,
        "gstin": body.gstin,
        "pan": body.pan,
        "industry": body.industry,
        "created_at": now,
        "updated_at": now,
    }
    store[company_id] = record
    return CompanyOut(**record)


@router.get("/companies/{company_id}", response_model=CompanyOut)
async def get_company(
    company_id: str,
    tenant_id: str = Depends(get_current_tenant),
):
    """Get details of a specific company."""
    store = _tenant_store(tenant_id)
    if company_id not in store:
        raise HTTPException(404, "Company not found")
    return CompanyOut(**store[company_id])


@router.patch("/companies/{company_id}", response_model=CompanyOut)
async def update_company(
    company_id: str,
    body: CompanyUpdate,
    tenant_id: str = Depends(get_current_tenant),
):
    """Update a company's details."""
    store = _tenant_store(tenant_id)
    if company_id not in store:
        raise HTTPException(404, "Company not found")

    record = store[company_id]
    updates = body.model_dump(exclude_unset=True)
    for key, value in updates.items():
        record[key] = value
    record["updated_at"] = datetime.now(UTC).isoformat()
    store[company_id] = record
    return CompanyOut(**record)
