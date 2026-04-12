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
from fastapi import APIRouter, Depends, HTTPException
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
}

# In-memory execution history (per-process; Redis-backed in production)
_execution_history: list[dict[str, Any]] = []


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

    Built-in scripts are discovered from the ``rpa/scripts/`` directory.
    Custom (tenant-specific) scripts will come from the DB in a future
    release.
    """
    scripts: list[RPAScriptOut] = []

    # Built-in scripts
    for key, meta in _BUILTIN_SCRIPTS.items():
        script_file = _SCRIPTS_DIR / f"{key}.py"
        if script_file.exists():
            scripts.append(RPAScriptOut(
                id=f"builtin-{key}",
                name=meta["name"],
                description=meta["description"],
                category=meta.get("category", "general"),
                script_key=key,
                params_schema=meta.get("params_schema", {}),
                estimated_duration_s=meta.get("estimated_duration_s", 60),
                is_builtin=True,
            ))

    return scripts


@router.get("/history", response_model=list[RPAExecutionOut])
async def list_history(
    limit: int = 50,
    tenant_id: str = Depends(get_current_tenant),
) -> list[RPAExecutionOut]:
    """Return recent RPA execution history."""
    # Filter by tenant (future: when executions are DB-backed)
    return [
        RPAExecutionOut(**h)
        for h in reversed(_execution_history[-limit:])
    ]


@router.post("/scripts/{script_id}/run", response_model=RPAExecutionOut)
async def run_script(
    script_id: str,
    body: RPARunRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> RPAExecutionOut:
    """Execute an RPA script and return the result.

    The script runs in a headless Chromium browser via Playwright.
    Requires ``playwright`` to be installed on the server.
    """
    # Resolve script key
    script_key = script_id.replace("builtin-", "") if script_id.startswith("builtin-") else script_id

    if script_key not in _BUILTIN_SCRIPTS:
        raise HTTPException(404, f"RPA script '{script_key}' not found")

    meta = _BUILTIN_SCRIPTS[script_key]
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

    _execution_history.append(execution.model_dump())

    logger.info(
        "rpa_script_executed",
        script_key=script_key,
        tenant_id=tenant_id,
        success=execution.success,
        elapsed_ms=execution.elapsed_ms,
    )

    return execution
