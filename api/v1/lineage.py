# SPDX-License-Identifier: Apache-2.0
"""Provenance and lineage: what a kept thing came from, which version it is, and what was done to it.

``POST /lineage`` notes a chain of nodes and the steps between them, for
an acquisition the platform did not perform itself (a connector sync, a
feed). ``GET /lineage/nodes/{kind}/{ref}`` describes one thing: its
versions, its sources and the processing history back to them.
``GET /lineage/trace/{kind}/{ref}`` walks the graph upstream, downstream
or both. Off, the status route says so and the rest is not found.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, Field

from api.deps import get_current_tenant, get_current_user
from api.route_metadata import route_meta
from api.v1.agents import _user_uuid_from_claims
from core.lineage import provenance, sync
from core.lineage.provenance import LineageError
from core.lineage.sync import SyncError

router = APIRouter(prefix="/lineage", tags=["Lineage"])


class ChainIn(BaseModel):
    model_config = {"extra": "forbid"}

    nodes: list[dict[str, Any]] = Field(..., min_length=1, max_length=provenance.MAX_CHAIN_NODES)
    steps: list[dict[str, Any]] = Field(default_factory=list, max_length=provenance.MAX_CHAIN_STEPS)


class SourceIn(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=1, max_length=100)
    kind: str = Field("feed", max_length=16)
    url: str = Field(..., min_length=1, max_length=500)
    item_kind: str = Field("document", pattern="^(document|record)$")
    interval_minutes: int = Field(60, ge=sync.MIN_INTERVAL, le=sync.MAX_INTERVAL)
    enabled: bool = True
    token: str | None = Field(None, max_length=2000)
    config: dict[str, Any] = Field(default_factory=dict)


class SourcePatch(BaseModel):
    model_config = {"extra": "forbid"}

    url: str | None = Field(None, min_length=1, max_length=500)
    item_kind: str | None = Field(None, pattern="^(document|record)$")
    interval_minutes: int | None = Field(None, ge=sync.MIN_INTERVAL, le=sync.MAX_INTERVAL)
    enabled: bool | None = None
    token: str | None = Field(None, max_length=2000)
    config: dict[str, Any] | None = None
    reset_cursor: bool = False


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={"error": "lineage_disabled", "message": "Lineage is off (AGENTICORG_LINEAGE_ENABLED)."},
    )


def _refused(exc: LineageError | SyncError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.code, "message": exc.message})


@router.get("/status")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="lineage.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="lineage.status",
)
async def status(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Whether lineage is on, the kinds of nodes and steps, and the bounds."""
    return {
        "enabled": provenance.enabled(),
        "kinds": list(provenance.KINDS),
        "steps": list(provenance.STEPS),
        "limits": {
            "hops": provenance.MAX_HOPS,
            "nodes": provenance.MAX_NODES,
            "chain_nodes": provenance.MAX_CHAIN_NODES,
            "chain_steps": provenance.MAX_CHAIN_STEPS,
        },
    }


@router.post("", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="lineage.write",
    rate_limit="standard",
    idempotency="idempotent-by-node-key-and-edge",
    audit_event="lineage.record",
)
async def record_chain(body: ChainIn, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Note a chain of nodes and the steps between them (positions into nodes); kept once under their keys."""
    if not provenance.enabled():
        raise _off()
    try:
        return await provenance.record_chain(uuid.UUID(tenant_id), body.nodes, body.steps)
    except LineageError as exc:
        raise _refused(exc) from None


@router.get("/nodes/{kind}/{ref:path}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="lineage.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="lineage.describe",
)
async def describe(
    kind: Annotated[str, Path(max_length=32)],
    ref: Annotated[str, Path(max_length=provenance.MAX_REF)],
    version: Annotated[str | None, Query(max_length=provenance.MAX_VERSION)] = None,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """One thing's provenance: its versions, its sources and the processing history back to them."""
    if not provenance.enabled():
        raise _off()
    try:
        return await provenance.describe(uuid.UUID(tenant_id), kind, ref, version=version)
    except LineageError as exc:
        raise _refused(exc) from None


@router.get("/trace/{kind}/{ref:path}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="lineage.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="lineage.trace",
)
async def trace(
    kind: Annotated[str, Path(max_length=32)],
    ref: Annotated[str, Path(max_length=provenance.MAX_REF)],
    direction: Annotated[str, Query(pattern="^(upstream|downstream|both)$")] = "upstream",
    hops: Annotated[int, Query(ge=1, le=provenance.MAX_HOPS)] = provenance.MAX_HOPS,
    version: Annotated[str | None, Query(max_length=provenance.MAX_VERSION)] = None,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The nodes and steps around one thing, bounded by hops and by the node count."""
    if not provenance.enabled():
        raise _off()
    try:
        return await provenance.trace(uuid.UUID(tenant_id), kind, ref, direction=direction, hops=hops, version=version)
    except LineageError as exc:
        raise _refused(exc) from None


# ---------------------------------------------------------------- incremental synchronisation


@router.get("/sync/sources")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="lineage.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="lineage.sync.sources.list",
)
async def list_sync_sources(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """The tenant's sync sources with their cursors, schedules and last outcome; the token is never returned."""
    if not provenance.enabled():
        raise _off()
    found = await sync.list_sources(uuid.UUID(tenant_id))
    return {"sources": found, "total": len(found), "kinds": sorted(set(sync.SOURCE_KINDS) | set(sync.FETCHERS))}


@router.post("/sync/sources", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="lineage.write",
    rate_limit="standard",
    idempotency="conflict-on-duplicate-name",
    audit_event="lineage.sync.sources.create",
)
async def create_sync_source(
    body: SourceIn, tenant_id: str = Depends(get_current_tenant), user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    """Set up a feed to poll: its URL (public HTTPS), its interval, an optional bearer token kept encrypted."""
    if not provenance.enabled():
        raise _off()
    actor = _user_uuid_from_claims(user)
    try:
        return await sync.create_source(uuid.UUID(tenant_id), body.model_dump(), user_id=str(actor or ""))
    except SyncError as exc:
        raise _refused(exc) from None


@router.patch("/sync/sources/{source_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="lineage.write",
    rate_limit="standard",
    idempotency="idempotent-update",
    audit_event="lineage.sync.sources.update",
)
async def update_sync_source(
    source_id: uuid.UUID, body: SourcePatch, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """Change a source; the cursor can be reset so the next run starts from the beginning."""
    if not provenance.enabled():
        raise _off()
    try:
        return await sync.update_source(uuid.UUID(tenant_id), source_id, body.model_dump(exclude_unset=True))
    except SyncError as exc:
        raise _refused(exc) from None


@router.delete("/sync/sources/{source_id}", status_code=204)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="lineage.write",
    rate_limit="standard",
    idempotency="idempotent-delete",
    audit_event="lineage.sync.sources.delete",
)
async def delete_sync_source(source_id: uuid.UUID, tenant_id: str = Depends(get_current_tenant)) -> Response:
    """Remove a source and its runs; what it ingested stays, with its provenance."""
    if not provenance.enabled():
        raise _off()
    try:
        await sync.delete_source(uuid.UUID(tenant_id), source_id)
    except SyncError as exc:
        raise _refused(exc) from None
    return Response(status_code=204)


@router.post("/sync/sources/{source_id}/run")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="lineage.write",
    rate_limit="approval-decision",
    idempotency="not_idempotent-each-call-is-a-run",
    audit_event="lineage.sync.sources.run",
)
async def run_sync_source(source_id: uuid.UUID, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Run a source now: fetch since its cursor, skip the unchanged, ingest the rest, record the run."""
    if not provenance.enabled():
        raise _off()
    try:
        return await sync.run_source(uuid.UUID(tenant_id), source_id, trigger="manual")
    except SyncError as exc:
        raise _refused(exc) from None


@router.get("/sync/sources/{source_id}/runs")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="lineage.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="lineage.sync.sources.runs",
)
async def list_sync_runs(
    source_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=sync.MAX_RUNS)] = 20,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The runs of a source, newest first: what each received, processed, skipped and failed."""
    if not provenance.enabled():
        raise _off()
    try:
        runs = await sync.list_runs(uuid.UUID(tenant_id), source_id, limit=limit)
    except SyncError as exc:
        raise _refused(exc) from None
    return {"runs": runs, "total": len(runs)}
