# SPDX-License-Identifier: Apache-2.0
"""Observability endpoints: run timelines for the console's waterfall and the live workload.

Admin-only reads. The timeline answers only what ``run_spans`` holds: with
``AGENTICORG_TRACING_TIMELINE_ENABLED`` off (the default) the list is empty
and says so (``enabled``), so a console never mistakes an unrecorded run for
a missing one.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core.config import settings
from observability import timeline, tracing, workload

router = APIRouter(prefix="/observability", tags=["Observability"], dependencies=[require_tenant_admin])


class TraceSummary(BaseModel):
    trace_id: str
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


class TracesOut(BaseModel):
    enabled: bool
    tracing: bool
    traces: list[TraceSummary]


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


class TraceOut(BaseModel):
    trace_id: str
    started_at: str | None = None
    duration_ms: int
    spans: list[SpanOut]


@router.get("/traces", response_model=TracesOut)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="observability.traces.sensitive.read",
    rate_limit="standard",
    idempotency="idempotent-read",
    audit_event="observability.traces.list",
)
async def list_traces(
    agent_id: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    tenant_id: str = Depends(get_current_tenant),
) -> TracesOut:
    """The newest stored runs, one entry per run, and whether runs are being recorded at all."""
    rows = await timeline.recent_traces(uuid.UUID(tenant_id), agent_id=agent_id, limit=limit)
    return TracesOut(
        enabled=bool(settings.tracing_timeline_enabled) and timeline.enabled(),
        tracing=tracing.enabled(),
        traces=[TraceSummary(**row) for row in rows],
    )


@router.get("/traces/{trace_id}", response_model=TraceOut)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="observability.traces.sensitive.read",
    rate_limit="standard",
    idempotency="idempotent-read",
    audit_event="observability.traces.read",
)
async def get_trace(
    trace_id: Annotated[str, Path(pattern="^[0-9a-f]{32}$")],
    tenant_id: str = Depends(get_current_tenant),
) -> TraceOut:
    """Every stored span of one run with its offset from the run's start: the waterfall."""
    detail = await timeline.trace_detail(uuid.UUID(tenant_id), trace_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return TraceOut(**detail)


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
    """Queue depths, review deadlines and the last hour's run, model-call and guardrail outcomes."""
    return await workload.workload(uuid.UUID(tenant_id))
