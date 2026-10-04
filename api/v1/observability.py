# SPDX-License-Identifier: Apache-2.0
"""Observability endpoints: run timelines for the console's waterfall, the live workload and synthetic checks.

Admin-only. The timeline answers only what ``run_spans`` holds: with
``AGENTICORG_TRACING_TIMELINE_ENABLED`` off (the default) the list is empty
and says so (``enabled``), so a console never mistakes an unrecorded run for
a missing one. Synthetic checks (``observability.synthetic``) are behind
``AGENTICORG_SYNTHETIC_CHECKS_ENABLED`` (off by default): off, adding a check
and running one are refused, the list says so, and what is stored can still
be read, changed and removed.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, Field

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core.config import settings
from core.ownership import Caller, caller_from_request
from observability import synthetic, timeline, tracing, workload

router = APIRouter(prefix="/observability", tags=["Observability"], dependencies=[require_tenant_admin])


class RunSummary(BaseModel):
    trace_id: str
    run_id: str
    span_id: str
    name: str
    agent_id: str | None = None
    status: str
    run_status: str | None = None
    started_at: str | None = None
    duration_ms: int = 0
    provider: str | None = None
    model: str | None = None
    tokens: int | None = None
    correlation_id: str | None = None


class RunsOut(BaseModel):
    enabled: bool
    tracing: bool
    runs: list[RunSummary]


class SpanOut(BaseModel):
    span_id: str
    parent_span_id: str | None = None
    name: str
    kind: str
    status: str
    agent_id: str | None = None
    offset_ms: int
    duration_ms: int
    attributes: dict[str, Any]
    events: list[dict[str, Any]]


class RunOut(BaseModel):
    run_id: str
    trace_id: str
    started_at: str | None = None
    duration_ms: int
    spans: list[SpanOut]


@router.get("/runs", response_model=RunsOut)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="observability.runs.sensitive.read",
    rate_limit="standard",
    idempotency="idempotent-read",
    audit_event="observability.runs.list",
)
async def list_runs(
    agent_id: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    tenant_id: str = Depends(get_current_tenant),
) -> RunsOut:
    """The newest stored runs, one entry per run, and whether runs are being recorded at all."""
    rows = await timeline.recent_runs(uuid.UUID(tenant_id), agent_id=agent_id, limit=limit)
    return RunsOut(
        enabled=bool(settings.tracing_timeline_enabled) and timeline.enabled(),
        tracing=tracing.enabled(),
        runs=[RunSummary(**row) for row in rows],
    )


@router.get("/runs/{run_id}", response_model=RunOut)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="observability.runs.sensitive.read",
    rate_limit="standard",
    idempotency="idempotent-read",
    audit_event="observability.runs.read",
)
async def get_run(
    run_id: Annotated[str, Path(pattern="^[0-9a-f]{16}$")],
    tenant_id: str = Depends(get_current_tenant),
) -> RunOut:
    """Every stored span of one run (named by its root span id) with its offset from the run's start: the waterfall."""
    detail = await timeline.run_detail(uuid.UUID(tenant_id), run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunOut(**detail)


@router.get("/workload")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="observability.workload.sensitive.read",
    rate_limit="standard",
    idempotency="idempotent-read",
    audit_event="observability.workload.read",
)
async def get_workload(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """The tenant's review deadlines and the last hour's run, model-call and guardrail outcomes."""
    return await workload.workload(uuid.UUID(tenant_id))


# ── Synthetic checks ─────────────────────────────────────────────────────────


class CheckIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    kind: str = Field(..., max_length=16)
    config: dict[str, Any] = Field(default_factory=dict)
    interval_minutes: int = Field(60, ge=synthetic.MIN_INTERVAL_MINUTES, le=synthetic.MAX_INTERVAL_MINUTES)
    enabled: bool = True


class CheckUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    config: dict[str, Any] | None = None
    interval_minutes: int | None = Field(None, ge=synthetic.MIN_INTERVAL_MINUTES, le=synthetic.MAX_INTERVAL_MINUTES)
    enabled: bool | None = None


def _actor(request: Request, caller: Caller | None) -> str:
    """The authenticated principal a check change is attributed to."""
    user_id = getattr(caller, "user_id", None)
    if user_id:
        return f"user:{user_id}"
    claims = getattr(request.state, "claims", None) or {}
    subject = str(claims.get("sub") or "").strip()
    auth_mode = str(getattr(request.state, "auth_mode", None) or "").strip()
    if subject and auth_mode:
        return f"{auth_mode}:{subject}"
    raise HTTPException(403, "A synthetic check change needs an attributable caller")


def _require_checks_on() -> None:
    if not synthetic.enabled():
        raise HTTPException(409, "Synthetic checks are off in this deployment")


@router.get("/checks")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="observability.checks.sensitive.read",
    rate_limit="standard",
    idempotency="idempotent-read",
    audit_event="observability.checks.list",
)
async def list_checks(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """The tenant's synthetic checks with their last result, and whether the scheduled sweep is on."""
    checks = await synthetic.list_checks(uuid.UUID(tenant_id))
    return {
        "enabled": synthetic.enabled(),
        "kinds": list(synthetic.KINDS),
        "limit": synthetic.MAX_CHECKS,
        "checks": [check.to_dict() for check in checks],
    }


@router.post("/checks", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="observability.checks.sensitive.write",
    rate_limit="security-admin-write",
    idempotency="non-idempotent-create",
    audit_event="observability.checks.create",
)
async def create_check(
    body: CheckIn,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> dict[str, Any]:
    actor_id = _actor(request, caller)
    _require_checks_on()
    try:
        check = await synthetic.create_check(uuid.UUID(tenant_id), actor_id=actor_id, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return check.to_dict()


@router.patch("/checks/{check_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="observability.checks.sensitive.write",
    rate_limit="security-admin-write",
    idempotency="idempotent-update",
    audit_event="observability.checks.update",
)
async def update_check(
    check_id: uuid.UUID,
    body: CheckUpdate,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> dict[str, Any]:
    actor_id = _actor(request, caller)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "nothing to change")
    try:
        check = await synthetic.update_check(uuid.UUID(tenant_id), check_id, actor_id=actor_id, changes=changes)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if check is None:
        raise HTTPException(404, "Synthetic check not found")
    return check.to_dict()


@router.delete("/checks/{check_id}", status_code=204)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="observability.checks.sensitive.write",
    rate_limit="security-admin-write",
    idempotency="idempotent-delete",
    audit_event="observability.checks.delete",
)
async def delete_check(
    check_id: uuid.UUID,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> Response:
    _actor(request, caller)
    if not await synthetic.delete_check(uuid.UUID(tenant_id), check_id):
        raise HTTPException(404, "Synthetic check not found")
    return Response(status_code=204)


@router.post("/checks/{check_id}/run")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="observability.checks.sensitive.write",
    rate_limit="security-admin-write",
    idempotency="non-idempotent-create",
    audit_event="observability.checks.run",
)
async def run_check(
    check_id: uuid.UUID,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> dict[str, Any]:
    """Run one check now and store its result; refused while synthetic checks are off or the check is running."""
    _actor(request, caller)
    _require_checks_on()
    check = await synthetic.get_check(uuid.UUID(tenant_id), check_id)
    if check is None:
        raise HTTPException(404, "Synthetic check not found")
    result = await synthetic.run_check(check, trigger="manual")
    if result is None:
        raise HTTPException(409, "The check is already running")
    return result.to_dict()


@router.get("/checks/{check_id}/results")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="observability.checks.sensitive.read",
    rate_limit="standard",
    idempotency="idempotent-read",
    audit_event="observability.checks.results",
)
async def list_check_results(
    check_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The check's newest results: status, latency, reasons and counts."""
    tid = uuid.UUID(tenant_id)
    if await synthetic.get_check(tid, check_id) is None:
        raise HTTPException(404, "Synthetic check not found")
    found = await synthetic.results(tid, check_id, limit=limit)
    return {"results": [result.to_dict() for result in found]}
