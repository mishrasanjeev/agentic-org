"""Industry pack management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_current_tenant
from core.agents.packs.installer import (
    get_installed_packs,
    get_pack_detail,
    install_pack,
    list_packs,
    uninstall_pack,
)

router = APIRouter()


@router.get("/packs")
async def list_available_packs():
    """List all available industry packs."""
    return {"packs": list_packs()}


@router.get("/packs/installed")
async def list_installed(tenant_id: str = Depends(get_current_tenant)):
    """List packs installed for the current tenant."""
    return {"installed": get_installed_packs(tenant_id)}


@router.get("/packs/{name}")
async def pack_detail(name: str):
    """Get detailed config for a specific pack."""
    detail = get_pack_detail(name)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Pack '{name}' not found")
    return detail


@router.post("/packs/{name}/install")
async def install(name: str, tenant_id: str = Depends(get_current_tenant)):
    """Install an industry pack for the current tenant."""
    try:
        result = install_pack(name, tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@router.delete("/packs/{name}")
async def uninstall(name: str, tenant_id: str = Depends(get_current_tenant)):
    """Uninstall an industry pack for the current tenant."""
    result = uninstall_pack(name, tenant_id)
    return result
