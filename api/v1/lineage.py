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

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from core.lineage import provenance
from core.lineage.provenance import LineageError

router = APIRouter(prefix="/lineage", tags=["Lineage"])


class ChainIn(BaseModel):
    model_config = {"extra": "forbid"}

    nodes: list[dict[str, Any]] = Field(..., min_length=1, max_length=provenance.MAX_CHAIN_NODES)
    steps: list[dict[str, Any]] = Field(default_factory=list, max_length=provenance.MAX_CHAIN_STEPS)


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={"error": "lineage_disabled", "message": "Lineage is off (AGENTICORG_LINEAGE_ENABLED)."},
    )


def _refused(exc: LineageError) -> HTTPException:
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
