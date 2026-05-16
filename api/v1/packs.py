"""Industry pack management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
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
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="packs.catalog.control_plane.read",
    rate_limit="packs-read",
    idempotency="read-only",
    audit_event="packs.catalog.list",
)
async def list_available_packs():
    """List all available industry packs."""
    return {"packs": list_packs()}


@router.get("/packs/installed")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="packs.installation.sensitive.read",
    rate_limit="packs-read",
    idempotency="read-only",
    audit_event="packs.installed.list",
)
async def list_installed(tenant_id: str = Depends(get_current_tenant)):
    """List packs installed for the current tenant."""
    await ensure_ca_pack_subscription_sync_async(tenant_id)
    return {"installed": await get_installed_packs_async(tenant_id)}


@router.get("/packs/{name}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="packs.catalog.control_plane.read",
    rate_limit="packs-read",
    idempotency="read-only",
    audit_event="packs.catalog.detail",
)
async def pack_detail(name: str):
    """Get detailed config for a specific pack."""
    detail = get_pack_detail(name)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Pack '{name}' not found")
    return detail


@router.post("/packs/{name}/install")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="packs.installation.high_risk.write",
    rate_limit="packs-install",
    idempotency="idempotent-install-by-pack-name",
    audit_event="packs.install",
)
async def install(name: str, tenant_id: str = Depends(get_current_tenant)):
    """Install an industry pack for the current tenant."""
    try:
        result = await install_pack_async(name, tenant_id)
    except ValueError as exc:
        if "not installable" in str(exc):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@router.delete("/packs/{name}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="packs.installation.high_risk.write",
    rate_limit="packs-install",
    idempotency="idempotent-uninstall-by-pack-name",
    audit_event="packs.uninstall",
)
async def uninstall(name: str, tenant_id: str = Depends(get_current_tenant)):
    """Uninstall an industry pack for the current tenant."""
    result = await uninstall_pack_async(name, tenant_id)
    return result
