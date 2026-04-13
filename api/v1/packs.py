"""Industry pack management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_current_tenant, require_tenant_admin
from core.agents.packs.installer import (
    ensure_ca_pack_subscription_sync_async,
    get_installed_packs_async,
    get_pack_detail,
    install_pack_async,
    list_packs,
    uninstall_pack_async,
)

router = APIRouter(dependencies=[require_tenant_admin])


@router.get("/packs")
async def list_available_packs():
    """List all available industry packs."""
    return {"packs": list_packs()}


@router.get("/packs/installed")
async def list_installed(tenant_id: str = Depends(get_current_tenant)):
    """List packs installed for the current tenant."""
    await ensure_ca_pack_subscription_sync_async(tenant_id)
    return {"installed": await get_installed_packs_async(tenant_id)}


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
        result = await install_pack_async(name, tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@router.delete("/packs/{name}")
async def uninstall(name: str, tenant_id: str = Depends(get_current_tenant)):
    """Uninstall an industry pack for the current tenant."""
    result = await uninstall_pack_async(name, tenant_id)
    return result
