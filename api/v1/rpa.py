"""RPA script management and execution API.

Provides CRUD for browser RPA scripts, execution history, and a
run endpoint that delegates to ``core.rpa.executor.execute_rpa_script``.

Scripts are either:
  - Built-in: discovered from ``rpa/scripts/`` directory
  - Custom: stored in the ``rpa_scripts`` DB table (future)

For now we surface the built-in scripts as read-only items.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import get_current_tenant

logger = structlog.get_logger()
router = APIRouter(prefix="/rpa", tags=["RPA"])

# Discover built-in scripts from the rpa/scripts directory
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "rpa" / "scripts"

# Metadata for known built-in scripts
_BUILTIN_SCRIPTS: dict[str, dict[str, Any]] = {
    "epfo_ecr_download": {
        "name": "EPFO ECR Download",
        "description": "Download Electronic Challan-cum-Return (ECR) from EPFO portal for PF compliance.",
        "category": "compliance",
        "params_schema": {
            "establishment_id": {"type": "string", "label": "Establishment ID", "required": True},
            "month": {"type": "string", "label": "Month (MMYYYY)", "required": True},
            "username": {"type": "string", "label": "EPFO Username", "required": True},
            "password": {"type": "password", "label": "EPFO Password", "required": True},
        },
        "estimated_duration_s": 45,
    },
    "mca_company_search": {
        "name": "MCA Company Search",
        "description": "Search and download company master data from the Ministry of Corporate Affairs (MCA) portal.",
        "category": "compliance",
        "params_schema": {
            "company_name": {"type": "string", "label": "Company Name", "required": True},
            "cin": {"type": "string", "label": "CIN (optional)", "required": False},
        },
        "estimated_duration_s": 30,
    },
    "generic_portal": {
        "name": "Generic Portal Automator",
        "description": (
            "Automate any web portal that doesn't have APIs. "
            "Provide the login URL, credentials, and what to do after login. "
            "Supports auto-detection of login forms, data extraction, file downloads, and screenshots."
        ),
        "category": "general",
        "params_schema": {
            "portal_url": {"type": "string", "label": "Portal Login URL", "required": True},
            "username": {"type": "string", "label": "Username / Email", "required": True},
            "password": {"type": "password", "label": "Password", "required": True},
            "username_field": {
                "type": "string",
                "label": "Username field CSS selector (leave blank for auto-detect)",
                "required": False,
            },
            "password_field": {
                "type": "string",
                "label": "Password field CSS selector (leave blank for auto-detect)",
                "required": False,
            },
            "login_button": {
                "type": "string",
                "label": "Login button CSS selector (leave blank for auto-detect)",
                "required": False,
            },
            "target_url": {
                "type": "string",
                "label": "URL to navigate after login (optional)",
                "required": False,
            },
            "action": {
                "type": "string",
                "label": "Action: screenshot / extract / download",
                "required": False,
            },
            "extract_selector": {
                "type": "string",
                "label": "CSS selector to extract text from (for action=extract)",
                "required": False,
            },
            "download_link": {
                "type": "string",
                "label": "CSS selector for download link (for action=download)",
                "required": False,
            },
        },
        "estimated_duration_s": 60,
    },
}

# SEC-015 (2026-05-01): RPA execution history is now persisted via
# ``core.rpa.history_store`` (Redis-backed, tenant-scoped, retention +
# size capped). The previous module-level ``_execution_history`` dict
# lost state on restart and produced inconsistent behavior across
# replicas. The store falls back to a process-local dict ONLY in
# relaxed envs (local/dev/test/ci) so unit tests still work without
# Redis. SECURITY_AUDIT-2026-04-19 HIGH-08 (no cross-tenant reads) is
# preserved by the store: every read + write is keyed by tenant_id at
# the storage layer with no path that crosses tenants.


# Generic portal RPA has no domain allowlist. It can be pointed at any
# URL, login with arbitrary credentials, and navigate to any target —
# SECURITY_AUDIT-2026-04-19 HIGH-09 flagged this as SSRF-like server-side
# automation. Gate it behind tenant admin until a real allowlist lands.
_ADMIN_ONLY_SCRIPTS: frozenset[str] = frozenset({"generic_portal"})


# ── Schemas ──────────────────────────────────────────────────────────


class RPAScriptOut(BaseModel):
    id: str
    name: str
    description: str
    category: str
    script_key: str
    params_schema: dict
    estimated_duration_s: int
    is_builtin: bool = True


class RPARunRequest(BaseModel):
    params: dict = Field(default_factory=dict)


class RPAExecutionOut(BaseModel):
    id: str
    script_key: str
    script_name: str
    status: str  # running | completed | failed
    started_at: str
    completed_at: str | None = None
    elapsed_ms: int = 0
    success: bool = False
    error: str | None = None


# ── Endpoints ────────���───────────────────────────────────────────────


@router.get("/scripts", response_model=list[RPAScriptOut])
async def list_scripts(
    tenant_id: str = Depends(get_current_tenant),
) -> list[RPAScriptOut]:
    """List available RPA scripts (built-in + custom).

    Delegates to ``rpa.scripts._registry.discover_scripts`` so adding
    a new RPA is as simple as dropping a ``*.py`` file with SCRIPT_META
    + ``async def run(...)`` — no edits needed here or elsewhere. The
    hardcoded ``_BUILTIN_SCRIPTS`` dict above is kept as a
    back-compat augmentation: anything it declares that the registry
    also declares prefers the registry metadata.
    """
    del tenant_id  # unused — listing is tenant-agnostic catalog
    from rpa.scripts._registry import discover_scripts

    registry = discover_scripts()
    scripts: list[RPAScriptOut] = []
    for key, meta in sorted(registry.items()):
        # Merge hardcoded metadata for back-compat where it exists.
        merged = {**_BUILTIN_SCRIPTS.get(key, {}), **meta}
        scripts.append(
            RPAScriptOut(
                id=f"builtin-{key}",
                name=merged.get("name") or key,
                description=merged.get("description") or "",
                category=merged.get("category", "general"),
                script_key=key,
                params_schema=merged.get("params_schema", {}),
                estimated_duration_s=merged.get("estimated_duration_s", 60),
                is_builtin=True,
            )
        )
    return scripts


@router.get("/history", response_model=list[RPAExecutionOut])
async def list_history(
    limit: int = 50,
    tenant_id: str = Depends(get_current_tenant),
) -> list[RPAExecutionOut]:
    """Return the caller's tenant RPA execution history.

    HIGH-08 fix: the store is keyed by tenant and this endpoint will only
    ever return entries tagged with the caller's authenticated tenant_id.
    SEC-015 fix: durable store survives restarts + scales across replicas.
    """
    from core.rpa import history_store  # noqa: PLC0415 — lazy keeps cold path lean

    # Cap user-supplied limit to prevent unbounded reads.
    safe_limit = max(1, min(int(limit), 500))
    rows = await history_store.list_history(str(tenant_id), limit=safe_limit)
    return [RPAExecutionOut(**row) for row in rows]


@router.post("/scripts/{script_id}/run", response_model=RPAExecutionOut)
async def run_script(
    script_id: str,
    body: RPARunRequest,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
) -> RPAExecutionOut:
    """Execute an RPA script and return the result.

    The script runs in a headless Chromium browser via Playwright.
    Requires ``playwright`` to be installed on the server.

    HIGH-09: scripts in ``_ADMIN_ONLY_SCRIPTS`` (currently just
    ``generic_portal``) require tenant admin because they can be
    pointed at arbitrary URLs and are therefore a server-side
    automation/SSRF surface.
    """
    # Resolve script key against the generic registry first; fall
    # back to the legacy hardcoded _BUILTIN_SCRIPTS map for scripts
    # that haven't yet been normalised to SCRIPT_META.
    from rpa.scripts._registry import discover_scripts

    script_key = script_id.replace("builtin-", "") if script_id.startswith("builtin-") else script_id

    registry = discover_scripts()
    if script_key not in registry and script_key not in _BUILTIN_SCRIPTS:
        raise HTTPException(404, f"RPA script '{script_key}' not found")

    meta = {**_BUILTIN_SCRIPTS.get(script_key, {}), **registry.get(script_key, {})}

    # Admin gate: honour both the legacy set and the per-script flag.
    if script_key in _ADMIN_ONLY_SCRIPTS or meta.get("admin_only"):
        scopes = getattr(request.state, "scopes", []) or []
        claims = getattr(request.state, "claims", {}) or {}
        is_admin = (
            "agenticorg:admin" in scopes
            or any(s.startswith("agenticorg:admin") for s in scopes)
            or claims.get("role") == "admin"
        )
        if not is_admin:
            raise HTTPException(
                403,
                f"RPA script '{script_key}' requires tenant admin — "
                "it can be pointed at arbitrary URLs and is gated to "
                "privileged operators.",
            )
    execution_id = str(uuid.uuid4())
    started_at = datetime.now(UTC)

    execution = RPAExecutionOut(
        id=execution_id,
        script_key=script_key,
        script_name=meta["name"],
        status="running",
        started_at=started_at.isoformat(),
    )

    try:
        from core.rpa.executor import execute_rpa_script

        result = await execute_rpa_script(
            script_name=script_key,
            params=body.params,
            timeout_s=meta.get("estimated_duration_s", 60) * 2,
        )

        execution.status = "completed" if result.get("success") else "failed"
        execution.success = result.get("success", False)
        execution.elapsed_ms = result.get("elapsed_ms", 0)
        execution.error = result.get("error")
        execution.completed_at = datetime.now(UTC).isoformat()

    except Exception as exc:
        execution.status = "failed"
        execution.success = False
        execution.error = str(exc)[:500]
        execution.completed_at = datetime.now(UTC).isoformat()
        logger.exception(
            "rpa_script_execution_failed",
            script_key=script_key,
            tenant_id=tenant_id,
        )

    # SEC-015: persist via durable, tenant-scoped store. ``durable``
    # is False when only the process-local fallback was used (relaxed
    # envs, or strict-env Redis outage); strict envs already log a
    # warning at the store layer in that case.
    from core.rpa import history_store  # noqa: PLC0415

    durable = await history_store.append(
        str(tenant_id), execution.model_dump()
    )

    logger.info(
        "rpa_script_executed",
        script_key=script_key,
        tenant_id=tenant_id,
        success=execution.success,
        elapsed_ms=execution.elapsed_ms,
        history_durable=durable,
    )

    return execution
