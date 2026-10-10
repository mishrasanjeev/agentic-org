# SPDX-License-Identifier: Apache-2.0
"""AI spend intelligence: reference data, rupee pricing, usage records and their coverage.

``/spend/org-nodes`` keeps the organisation tree spend rolls up through,
``/spend/mappings`` ties agents, workflows, applications and legacy labels
to it, ``/spend/model-aliases`` maps called model names to SKUs,
``/spend/rate-cards`` keeps effective-dated prices (with ``/correct`` for a
wrong price), ``/spend/commitments`` committed volume and ``/spend/fx-rates``
the reference rates to INR. ``GET /spend/price`` prices a usage at a date
the way metering will. Each kind of reference data also takes a bounded
CSV or JSON import with a dry run.

``/spend/usage`` lists usage records, ``/spend/rollups`` sums them per day
and dimension, ``/spend/coverage`` reports the attributed and unattributed
shares (the Gate 1 attribution measure), ``/spend/coverage/ledgers`` sets
them beside the existing cost ledgers and ``/spend/gaps`` counts what could
not be metered. Rebuilds, backfills, restatements, re-attribution, FX
settlement and commitment recomputes are jobs (202 with a job id) read
through ``/spend/jobs``.

Reads need ``audit:read``; rate cards, commitments and prices are for a
human administrator or auditor only. Usage records, rollups (every
grouping) and the ledger comparison apply the caller's agent visibility;
coverage, gaps and jobs, which sum every agent's usage, are for an
administrator or auditor only. Every write needs a tenant
administrator signed in as a person and is audited. Off
(``spend_intelligence_enabled``), the status route says so and every other
route is not found.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from api.deps import ActiveHumanAdmin, get_active_human_admin, get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core import spend
from core.config import settings
from core.file_ingestion.limits import cleanup_tempfile, stream_to_tempfile
from core.ownership import Caller, caller_from_request
from core.spend import (
    access,
    clock,
    commitments,
    fx,
    imports,
    jobs,
    ledgers,
    maintenance,
    mappings,
    org,
    partitions,
    pricing,
    rates,
    rollups,
    vocab,
)
from core.spend.errors import SpendError

router = APIRouter(prefix="/spend", tags=["Spend"])

NodeKind = Literal["group", "business_unit", "department", "team", "cost_centre"]
SourceType = Literal["agent", "application", "workflow", "cost_center", "department"]
CardSource = Literal["list", "contract"]
TierMode = Literal["graduated", "all_units"]
FxSource = Literal["reference", "manual", "import"]
CommitmentKind = Literal["quantity", "money"]

Price = Annotated[Decimal, Field(ge=0, le=vocab.MAX_UNIT_PRICE, decimal_places=vocab.PRICE_PLACES)]
Percent = Annotated[Decimal, Field(ge=0, le=100, decimal_places=vocab.PCT_PLACES)]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]


class TierIn(BaseModel):
    model_config = {"extra": "forbid"}

    from_quantity: Decimal = Field(..., ge=0, le=vocab.MAX_TIER_QUANTITY, decimal_places=vocab.QUANTITY_PLACES)
    unit_price: Price


class OrgNodeIn(BaseModel):
    model_config = {"extra": "forbid"}

    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=org.MAX_NAME)
    kind: NodeKind
    parent_code: str | None = Field(None, max_length=64)
    owner_user_id: uuid.UUID | None = None


class OrgNodePatch(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = Field(None, min_length=1, max_length=org.MAX_NAME)
    parent_code: str | None = Field(None, max_length=64)
    clear_parent: bool = False
    owner_user_id: uuid.UUID | None = None
    active: bool | None = None


class MappingIn(BaseModel):
    model_config = {"extra": "forbid"}

    source_type: SourceType
    source_ref: str = Field(..., min_length=1, max_length=128)
    org_node_code: str | None = Field(None, max_length=64)
    product_line: str | None = Field(None, max_length=64)
    use_case: str | None = Field(None, max_length=64)
    active: bool = True


class AliasIn(BaseModel):
    model_config = {"extra": "forbid"}

    provider: str = Field(..., min_length=1, max_length=64)
    alias: str = Field(..., min_length=1, max_length=128)
    model_sku: str = Field(..., min_length=1, max_length=128)


class RateCardIn(BaseModel):
    model_config = {"extra": "forbid"}

    provider: str = Field(..., min_length=1, max_length=64)
    usage_type: str = Field(..., min_length=1, max_length=32)
    model_sku: str = Field("", max_length=128)
    unit: str = Field(..., min_length=1, max_length=32)
    unit_price: Price
    currency: Currency
    effective_from: date
    effective_to: date | None = None
    source: CardSource
    cached_unit_price: Price | None = None
    batch_discount_pct: Percent = Decimal("0")
    volume_tiers: list[TierIn] = Field(default_factory=list, max_length=vocab.MAX_TIERS)
    tier_mode: TierMode = "graduated"
    reference: str = Field("", max_length=200)
    supersede: bool = False
    restate: bool = False


class RateCardPatch(BaseModel):
    model_config = {"extra": "forbid"}

    effective_to: date | None = None
    status: Literal["retired"] | None = None
    reference: str | None = Field(None, max_length=200)
    unit_price: Price | None = None
    cached_unit_price: Price | None = None
    batch_discount_pct: Percent | None = None
    volume_tiers: list[TierIn] | None = Field(None, max_length=vocab.MAX_TIERS)
    tier_mode: TierMode | None = None
    restate: bool = False


class CorrectIn(BaseModel):
    model_config = {"extra": "forbid"}

    unit_price: Price | None = None
    cached_unit_price: Price | None = None
    batch_discount_pct: Percent | None = None
    volume_tiers: list[TierIn] | None = Field(None, max_length=vocab.MAX_TIERS)
    tier_mode: TierMode | None = None
    currency: Currency | None = None
    effective_to: date | None = None
    reason: str = Field(..., min_length=rates.REASON_MIN, max_length=rates.REASON_MAX)


class CommitmentIn(BaseModel):
    model_config = {"extra": "forbid"}

    provider: str = Field(..., min_length=1, max_length=64)
    kind: CommitmentKind
    usage_type: str | None = Field(None, max_length=32)
    model_sku: str = Field("", max_length=128)
    unit: str | None = Field(None, max_length=32)
    committed_quantity: Decimal | None = Field(
        None, gt=0, le=vocab.MAX_COMMITTED_QUANTITY, decimal_places=vocab.QUANTITY_PLACES
    )
    committed_amount: Decimal | None = Field(
        None, gt=0, le=vocab.MAX_COMMITTED_AMOUNT, decimal_places=vocab.PRICE_PLACES
    )
    currency: Currency | None = None
    period_start: date
    period_end: date
    overage_unit_price: Price | None = None
    overage_currency: Currency | None = None
    reference: str = Field("", max_length=200)


class CommitmentPatch(BaseModel):
    model_config = {"extra": "forbid"}

    period_end: date | None = None
    status: Literal["closed"] | None = None
    reference: str | None = Field(None, max_length=200)


class FxRateIn(BaseModel):
    model_config = {"extra": "forbid"}

    rate_date: date
    currency: Currency
    rate_to_inr: Decimal = Field(..., gt=0, le=vocab.MAX_FX_RATE, decimal_places=vocab.FX_PLACES)
    source: FxSource = "manual"
    restate: bool = False


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "spend_disabled",
            "message": "AI spend intelligence is off (AGENTICORG_SPEND_INTELLIGENCE_ENABLED).",
        },
    )


def spend_on() -> None:
    """404 ``spend_disabled`` while the feature is off (the request gate answers first in a running app)."""
    if not spend.enabled():
        raise _off()


async def spend_admin(request: Request) -> ActiveHumanAdmin:
    """The acting administrator: the flag first (404 while off, before any database read), then a person
    who is an active administrator of the tenant (API keys and agent tokens are refused)."""
    spend_on()
    return await get_active_human_admin(request)


def _refused(exc: SpendError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.code, "message": exc.message})


def _tenant(tenant_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(tenant_id))
    except ValueError:
        raise HTTPException(401, "Invalid tenant context") from None


async def _upload_rows(
    file: UploadFile, *, required: tuple[str, ...], optional: tuple[str, ...]
) -> tuple[list[dict[str, str]], str]:
    """The rows of an uploaded import file and its sha256; the file is bounded while it streams."""
    try:
        path, _size = await stream_to_tempfile(file, max_bytes=imports.MAX_IMPORT_BYTES)
    except HTTPException as exc:
        if exc.status_code == 413:
            raise HTTPException(
                413,
                detail={
                    "error": "import_too_large",
                    "message": f"an import is at most {imports.MAX_IMPORT_BYTES} bytes",
                },
            ) from None
        raise
    try:
        rows = await asyncio.to_thread(
            imports.parse_rows,
            path,
            filename=file.filename or "",
            content_type=file.content_type or "",
            required=required,
            optional=optional,
        )
        digest = await asyncio.to_thread(imports.file_sha256, path)
    finally:
        cleanup_tempfile(path)
    return rows, digest


# ---------------------------------------------------------------- status


def _writer_state() -> dict[str, Any]:
    """This process's usage writer: started or not, and events pending (no import while it never started)."""
    import sys

    module = sys.modules.get("core.spend.writer")
    if module is None:
        return {"started": False, "pending": 0}
    return {"started": bool(module.started()), "pending": int(module.pending())}


@router.get("/status")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="spend.status",
)
async def spend_status(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Whether spend intelligence is on, the reporting currency and calendar, the vocabularies and the bounds."""
    return {
        "enabled": spend.enabled(),
        "reporting_currency": vocab.REPORTING_CURRENCY,
        "reporting_timezone": settings.spend_reporting_timezone,
        "usage_types": list(vocab.USAGE_TYPES),
        "record_units": {k: list(v) for k, v in vocab.RECORD_UNITS.items()},
        "card_units": {k: list(v) for k, v in vocab.CARD_UNITS.items()},
        "applications": list(vocab.APPLICATIONS),
        "node_kinds": list(vocab.NODE_KINDS),
        "source_types": list(vocab.SOURCE_TYPES),
        "price_sources": list(vocab.PRICE_SOURCES),
        "line_kinds": list(vocab.LINE_KINDS),
        "limits": {
            "import_rows": imports.MAX_IMPORT_ROWS,
            "import_bytes": imports.MAX_IMPORT_BYTES,
            "usage_window_days": rollups.MAX_USAGE_DAYS,
            "rebuild_days": rollups.MAX_REBUILD_DAYS,
            "restate_days": maintenance.MAX_DAYS,
        },
        "backfill_source": ledgers.backfill_source(),
        "partition_horizon": await partitions.horizon(clock.now_utc()),
        "writer": _writer_state(),
    }


# ---------------------------------------------------------------- organisation tree


@router.get("/org-nodes")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.org.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="spend.org_nodes.list",
)
async def list_org_nodes(
    active: bool | None = None,
    kind: Annotated[str | None, Query(max_length=16)] = None,
    limit: Limit = 500,
    offset: Offset = 0,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The organisation tree's nodes by code, with each parent's code."""
    spend_on()
    try:
        return await org.list_nodes(_tenant(tenant_id), active=active, kind=kind, limit=limit, offset=offset)
    except SpendError as exc:
        raise _refused(exc) from None


@router.post("/org-nodes/import", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.org.sensitive.write",
    rate_limit="bulk-import",
    idempotency="upsert-by-code",
    audit_event="spend.org_nodes.import",
)
async def import_org_nodes(
    file: UploadFile,
    dry_run: bool = False,
    admin: ActiveHumanAdmin = Depends(spend_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Upsert nodes by code from CSV or JSON (code, name, kind; parent_code, owner_user_id, active)."""
    spend_on()
    try:
        rows, digest = await _upload_rows(file, required=org.IMPORT_REQUIRED, optional=org.IMPORT_OPTIONAL)
        return await org.import_nodes(
            _tenant(tenant_id), rows, actor=str(admin.user_id), dry_run=dry_run, file_sha256=digest
        )
    except SpendError as exc:
        raise _refused(exc) from None


@router.get("/org-nodes/{node_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.org.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="spend.org_nodes.get",
)
async def get_org_node(node_id: uuid.UUID, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """A node with its ancestors and its business unit."""
    spend_on()
    try:
        return await org.get_node(_tenant(tenant_id), node_id)
    except SpendError as exc:
        raise _refused(exc) from None


@router.post("/org-nodes", status_code=201, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.org.sensitive.write",
    rate_limit="standard",
    idempotency="not-idempotent-create",
    audit_event="spend.org_nodes.create",
)
async def create_org_node(
    body: OrgNodeIn,
    admin: ActiveHumanAdmin = Depends(spend_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """A new node under its parent (by code)."""
    spend_on()
    try:
        return await org.create_node(_tenant(tenant_id), body.model_dump(), actor=str(admin.user_id))
    except SpendError as exc:
        raise _refused(exc) from None


@router.patch("/org-nodes/{node_id}", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.org.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-update",
    audit_event="spend.org_nodes.update",
)
async def update_org_node(
    node_id: uuid.UUID,
    body: OrgNodePatch,
    admin: ActiveHumanAdmin = Depends(spend_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Rename, move (``parent_code`` or ``clear_parent``), change the owner, or deactivate a node."""
    spend_on()
    try:
        return await org.update_node(
            _tenant(tenant_id), node_id, body.model_dump(exclude_unset=True), actor=str(admin.user_id)
        )
    except SpendError as exc:
        raise _refused(exc) from None


# ---------------------------------------------------------------- mappings and aliases


@router.get("/mappings")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.mappings.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="spend.mappings.list",
)
async def list_mappings(
    source_type: Annotated[str | None, Query(max_length=16)] = None,
    active: bool | None = None,
    limit: Limit = 500,
    offset: Offset = 0,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Source mappings with each node's code."""
    spend_on()
    try:
        return await mappings.list_mappings(
            _tenant(tenant_id), source_type=source_type, active=active, limit=limit, offset=offset
        )
    except SpendError as exc:
        raise _refused(exc) from None


@router.put("/mappings", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.mappings.sensitive.write",
    rate_limit="standard",
    idempotency="upsert-by-key",
    audit_event="spend.mappings.put",
)
async def put_mapping(
    body: MappingIn,
    admin: ActiveHumanAdmin = Depends(spend_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Upsert the mapping of a source (agent, workflow, application, cost-centre or department id)."""
    spend_on()
    try:
        return await mappings.put_mapping(_tenant(tenant_id), body.model_dump(), actor=str(admin.user_id))
    except SpendError as exc:
        raise _refused(exc) from None


@router.post("/mappings/import", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.mappings.sensitive.write",
    rate_limit="bulk-import",
    idempotency="upsert-by-key",
    audit_event="spend.mappings.import",
)
async def import_mappings(
    file: UploadFile,
    dry_run: bool = False,
    admin: ActiveHumanAdmin = Depends(spend_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Upsert mappings from CSV or JSON (source_type, source_ref; org_node_code, product_line, use_case, active)."""
    spend_on()
    try:
        rows, digest = await _upload_rows(file, required=mappings.IMPORT_REQUIRED, optional=mappings.IMPORT_OPTIONAL)
        return await mappings.import_mappings(
            _tenant(tenant_id), rows, actor=str(admin.user_id), dry_run=dry_run, file_sha256=digest
        )
    except SpendError as exc:
        raise _refused(exc) from None


@router.get("/model-aliases")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.mappings.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="spend.model_aliases.list",
)
async def list_model_aliases(
    provider: Annotated[str | None, Query(max_length=64)] = None,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Model names as called and the SKUs they price as."""
    spend_on()
    try:
        return await mappings.list_aliases(_tenant(tenant_id), provider=provider)
    except SpendError as exc:
        raise _refused(exc) from None


@router.put("/model-aliases", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.mappings.sensitive.write",
    rate_limit="standard",
    idempotency="upsert-by-key",
    audit_event="spend.model_aliases.put",
)
async def put_model_alias(
    body: AliasIn,
    admin: ActiveHumanAdmin = Depends(spend_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Upsert the SKU a called model name prices and reconciles as."""
    spend_on()
    try:
        return await mappings.put_alias(_tenant(tenant_id), body.model_dump(), actor=str(admin.user_id))
    except SpendError as exc:
        raise _refused(exc) from None


# ---------------------------------------------------------------- rate cards


@router.get("/rate-cards")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.rate_cards.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="spend.rate_cards.list",
)
async def list_rate_cards(
    provider: Annotated[str | None, Query(max_length=64)] = None,
    usage_type: Annotated[str | None, Query(max_length=32)] = None,
    as_of: date | None = None,
    status: Annotated[str | None, Query(max_length=16)] = None,
    limit: Limit = 500,
    offset: Offset = 0,
    caller: Caller = Depends(caller_from_request),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Rate cards (an administrator or auditor only); ``as_of`` keeps the active cards in force on a billing date.

    With ``as_of``, ``status=retired`` lists the retired cards whose dates cover it.
    """
    spend_on()
    try:
        access.require_commercial(caller)
        return await rates.list_cards(
            _tenant(tenant_id),
            provider=provider,
            usage_type=usage_type,
            as_of=as_of,
            status=status,
            limit=limit,
            offset=offset,
        )
    except SpendError as exc:
        raise _refused(exc) from None


@router.post("/rate-cards/import", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.rate_cards.sensitive.write",
    rate_limit="bulk-import",
    idempotency="upsert-by-key",
    audit_event="spend.rate_cards.import",
)
async def import_rate_cards(
    file: UploadFile,
    dry_run: bool = False,
    admin: ActiveHumanAdmin = Depends(spend_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Upsert cards from CSV or JSON by key and start date, under the same rules as single writes."""
    spend_on()
    try:
        rows, digest = await _upload_rows(file, required=rates.IMPORT_REQUIRED, optional=rates.IMPORT_OPTIONAL)
        return await rates.import_cards(
            _tenant(tenant_id), rows, actor=str(admin.user_id), dry_run=dry_run, file_sha256=digest
        )
    except SpendError as exc:
        raise _refused(exc) from None


@router.post("/rate-cards", status_code=201, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.rate_cards.sensitive.write",
    rate_limit="standard",
    idempotency="not-idempotent-create",
    audit_event="spend.rate_cards.create",
)
async def create_rate_card(
    body: RateCardIn,
    admin: ActiveHumanAdmin = Depends(spend_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """A new card; refused when it overlaps an active card of the same key."""
    spend_on()
    try:
        return await rates.create_card(_tenant(tenant_id), body.model_dump(), actor=str(admin.user_id))
    except SpendError as exc:
        raise _refused(exc) from None


@router.patch("/rate-cards/{card_id}", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.rate_cards.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-update",
    audit_event="spend.rate_cards.update",
)
async def update_rate_card(
    card_id: uuid.UUID,
    body: RateCardPatch,
    admin: ActiveHumanAdmin = Depends(spend_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Change a card's reference or end, retire an unused card, or edit prices before the card starts."""
    spend_on()
    try:
        return await rates.update_card(
            _tenant(tenant_id), card_id, body.model_dump(exclude_unset=True), actor=str(admin.user_id)
        )
    except SpendError as exc:
        raise _refused(exc) from None


@router.post("/rate-cards/{card_id}/correct", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.rate_cards.sensitive.write",
    rate_limit="standard",
    idempotency="not-idempotent-create",
    audit_event="spend.rate_cards.correct",
)
async def correct_rate_card(
    card_id: uuid.UUID,
    body: CorrectIn,
    admin: ActiveHumanAdmin = Depends(spend_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Retire a card with a wrong price and insert its corrected replacement, with a reason."""
    spend_on()
    fields = body.model_dump(exclude_unset=True)
    reason = fields.pop("reason", "")
    try:
        return await rates.correct_card(_tenant(tenant_id), card_id, fields, reason=reason, actor=str(admin.user_id))
    except SpendError as exc:
        raise _refused(exc) from None


# ---------------------------------------------------------------- commitments


@router.get("/commitments")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.commitments.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="spend.commitments.list",
)
async def list_commitments(
    provider: Annotated[str | None, Query(max_length=64)] = None,
    active: bool | None = None,
    limit: Limit = 500,
    offset: Offset = 0,
    caller: Caller = Depends(caller_from_request),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Commitments with their drawdown and what remains (an administrator or auditor only)."""
    spend_on()
    try:
        access.require_commercial(caller)
        return await commitments.list_commitments(
            _tenant(tenant_id), provider=provider, active=active, limit=limit, offset=offset
        )
    except SpendError as exc:
        raise _refused(exc) from None


@router.post("/commitments", status_code=201, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.commitments.sensitive.write",
    rate_limit="standard",
    idempotency="not-idempotent-create",
    audit_event="spend.commitments.create",
)
async def create_commitment(
    body: CommitmentIn,
    admin: ActiveHumanAdmin = Depends(spend_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """A new committed quantity or amount over a billing period."""
    spend_on()
    try:
        return await commitments.create_commitment(_tenant(tenant_id), body.model_dump(), actor=str(admin.user_id))
    except SpendError as exc:
        raise _refused(exc) from None


@router.patch("/commitments/{commitment_id}", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.commitments.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-update",
    audit_event="spend.commitments.update",
)
async def update_commitment(
    commitment_id: uuid.UUID,
    body: CommitmentPatch,
    admin: ActiveHumanAdmin = Depends(spend_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Change a commitment's end, close it, or change its reference."""
    spend_on()
    try:
        return await commitments.update_commitment(
            _tenant(tenant_id), commitment_id, body.model_dump(exclude_unset=True), actor=str(admin.user_id)
        )
    except SpendError as exc:
        raise _refused(exc) from None


# ---------------------------------------------------------------- FX rates


@router.get("/fx-rates")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.fx.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="spend.fx_rates.list",
)
async def list_fx_rates(
    currency: Annotated[str | None, Query(max_length=3)] = None,
    start: date | None = None,
    end: date | None = None,
    limit: Limit = 500,
    offset: Offset = 0,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Reference rates to INR, newest first."""
    spend_on()
    try:
        return await fx.list_rates(
            _tenant(tenant_id), currency=currency, start=start, end=end, limit=limit, offset=offset
        )
    except SpendError as exc:
        raise _refused(exc) from None


@router.put("/fx-rates", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.fx.sensitive.write",
    rate_limit="standard",
    idempotency="upsert-by-key",
    audit_event="spend.fx_rates.put",
)
async def put_fx_rate(
    body: FxRateIn,
    admin: ActiveHumanAdmin = Depends(spend_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Upsert the rate of a currency to INR on a reporting date; answers the rate it replaced."""
    spend_on()
    try:
        return await fx.put_rate(_tenant(tenant_id), body.model_dump(), actor=str(admin.user_id))
    except SpendError as exc:
        raise _refused(exc) from None


@router.post("/fx-rates/import", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.fx.sensitive.write",
    rate_limit="bulk-import",
    idempotency="upsert-by-key",
    audit_event="spend.fx_rates.import",
)
async def import_fx_rates(
    file: UploadFile,
    dry_run: bool = False,
    admin: ActiveHumanAdmin = Depends(spend_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Upsert rates from CSV or JSON (rate_date, currency, rate_to_inr; source)."""
    spend_on()
    try:
        rows, digest = await _upload_rows(file, required=fx.IMPORT_REQUIRED, optional=fx.IMPORT_OPTIONAL)
        return await fx.import_rates(
            _tenant(tenant_id), rows, actor=str(admin.user_id), dry_run=dry_run, file_sha256=digest
        )
    except SpendError as exc:
        raise _refused(exc) from None


# ---------------------------------------------------------------- price quote


@router.get("/price")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="spend.price.quote",
)
async def price_quote(
    provider: Annotated[str, Query(min_length=1, max_length=64)],
    usage_type: Annotated[str, Query(min_length=1, max_length=32)],
    unit: Annotated[str, Query(min_length=1, max_length=32)],
    quantity: Annotated[str, Query(min_length=1, max_length=40)],
    on: date,
    model: Annotated[str, Query(max_length=128)] = "",
    fx_on: date | None = None,
    caller: Caller = Depends(caller_from_request),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The price of a usage on a billing date (FX on ``fx_on``, else the same date), as metering would price it."""
    spend_on()
    try:
        access.require_commercial(caller)
        return await pricing.quote(
            _tenant(tenant_id),
            provider=provider,
            usage_type=usage_type,
            unit=unit,
            quantity=quantity,
            model=model,
            on=on,
            fx_on=fx_on,
        )
    except SpendError as exc:
        raise _refused(exc) from None


# ---------------------------------------------------------------- usage, rollups, coverage (PR B)

JOB_RATE_LIMIT = "bulk-import"
JOB_IDEMPOTENCY = "single-active-job-per-kind"
JobKindQuery = Literal["rebuild", "backfill", "restate", "settle_fx", "reattribute", "recompute_commitments"]
GroupBy = Literal[
    "day",
    "billing_date",
    "org_node_id",
    "business_unit_node_id",
    "attribution_path",
    "unattributed_reason",
    "product_line",
    "use_case",
    "application",
    "agent_id",
    "provider",
    "model",
    "usage_type",
    "unit",
    "currency",
    "rate_card_id",
    "price_source",
    "commitment_id",
    "billing_account",
    "region",
    "environment",
    "risk_tier",
]


class RangeIn(BaseModel):
    model_config = {"extra": "forbid"}

    start: date
    end: date


class RebuildIn(RangeIn):
    pass


class BackfillIn(RangeIn):
    pass


class ReattributeIn(RangeIn):
    pass


class SettleIn(RangeIn):
    pass


class RestateIn(BaseModel):
    model_config = {"extra": "forbid"}

    provider: str = Field(..., min_length=1, max_length=64)
    start: date
    end: date
    card_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    include_unpriced: bool = True
    reason: str = Field(..., min_length=10, max_length=500)


class RecomputeIn(BaseModel):
    model_config = {"extra": "forbid"}

    provider: str | None = Field(None, min_length=1, max_length=64)


def _job_accepted(out: dict[str, Any]) -> dict[str, Any]:
    return {"job_id": out["job_id"], "status": out["status"]}


@router.get("/usage")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.usage.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="spend.usage.list",
)
async def list_usage(
    start: date,
    end: date,
    usage_type: Annotated[str | None, Query(max_length=32)] = None,
    provider: Annotated[str | None, Query(max_length=64)] = None,
    org_node_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    unattributed: bool | None = None,
    unpriced: bool | None = None,
    limit: Limit = 100,
    cursor: Annotated[str | None, Query(max_length=120)] = None,
    caller: Caller = Depends(caller_from_request),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Usage records of reporting days (at most 31), a page at a time; other people's personal agents are hidden."""
    spend_on()
    try:
        return await rollups.list_records(
            _tenant(tenant_id),
            start=start,
            end=end,
            view=access.read_view(caller),
            usage_type=usage_type,
            provider=provider,
            org_node_id=org_node_id,
            agent_id=agent_id,
            unattributed=unattributed,
            unpriced=unpriced,
            limit=limit,
            cursor=cursor,
        )
    except SpendError as exc:
        raise _refused(exc) from None


@router.get("/rollups")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.usage.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="spend.rollups.list",
)
async def list_rollups(
    start: date,
    end: date,
    group_by: GroupBy = "day",
    provider: Annotated[str | None, Query(max_length=64)] = None,
    usage_type: Annotated[str | None, Query(max_length=32)] = None,
    org_node_id: uuid.UUID | None = None,
    application: Annotated[str | None, Query(max_length=16)] = None,
    billing_account: Annotated[str | None, Query(max_length=16)] = None,
    caller: Caller = Depends(caller_from_request),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Daily rollups summed per a dimension or per day (at most 366 days); amounts per currency and in INR.

    Every grouping applies the caller's agent visibility, as ``GET /spend/usage`` does."""
    spend_on()
    try:
        filters = {
            "provider": vocab.norm_provider(provider) if provider else None,
            "usage_type": usage_type,
            "org_node_id": org_node_id,
            "application": application,
            "billing_account": billing_account,
        }
        return await rollups.query(
            _tenant(tenant_id),
            start=start,
            end=end,
            group_by=group_by,
            filters=filters,
            view=access.read_view(caller),
        )
    except SpendError as exc:
        raise _refused(exc) from None


@router.get("/coverage")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.usage.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="spend.coverage.get",
)
async def spend_coverage(
    start: date,
    end: date,
    caller: Caller = Depends(caller_from_request),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Attribution coverage per day and for the period (at most 366 days): the Gate 1 attribution measure.

    A tenant-wide figure (every agent's usage, and gaps that carry no agent): an administrator or auditor
    only, 403 ``tenant_wide_read_refused`` otherwise."""
    spend_on()
    try:
        access.require_tenant_wide(caller)
        return await rollups.coverage(_tenant(tenant_id), start=start, end=end, now=clock.now_utc())
    except SpendError as exc:
        raise _refused(exc) from None


@router.get("/coverage/ledgers")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.usage.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="spend.coverage.ledgers",
)
async def ledger_comparison(
    start: date,
    end: date,
    caller: Caller = Depends(caller_from_request),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Usage tokens and amounts per day beside the existing cost ledgers (at most 31 days), with the expected
    differences. Every source carries an agent id, so each is filtered by the caller's agent visibility."""
    spend_on()
    try:
        return await ledgers.compare(_tenant(tenant_id), start=start, end=end, view=access.read_view(caller))
    except SpendError as exc:
        raise _refused(exc) from None


@router.get("/gaps")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.usage.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="spend.gaps.list",
)
async def list_gaps(
    start: date,
    end: date,
    caller: Caller = Depends(caller_from_request),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Usage that could not be metered (at most 92 days), by day, usage type, reason and detail.

    Gaps carry no agent, so they cannot be filtered by agent visibility: an administrator or auditor only,
    403 ``tenant_wide_read_refused`` otherwise."""
    spend_on()
    try:
        access.require_tenant_wide(caller)
        return await rollups.list_gaps(_tenant(tenant_id), start=start, end=end)
    except SpendError as exc:
        raise _refused(exc) from None


@router.post("/rollups/rebuild", status_code=202, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.rollups.sensitive.write",
    rate_limit=JOB_RATE_LIMIT,
    idempotency=JOB_IDEMPOTENCY,
    audit_event="spend.rollups.rebuild",
)
async def rebuild_rollups(
    body: RebuildIn,
    admin: ActiveHumanAdmin = Depends(spend_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Queue a rebuild of the daily rollups of reporting days (at most 31) from the usage records."""
    spend_on()
    try:
        rollups.check_range(body.start, body.end, max_days=rollups.MAX_REBUILD_DAYS)
        out = await jobs.enqueue(
            _tenant(tenant_id),
            kind="rebuild",
            params={"start": body.start, "end": body.end},
            actor=str(admin.user_id),
        )
        return _job_accepted(out)
    except SpendError as exc:
        raise _refused(exc) from None


@router.post("/usage/backfill", status_code=202, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.usage.sensitive.write",
    rate_limit=JOB_RATE_LIMIT,
    idempotency=JOB_IDEMPOTENCY,
    audit_event="spend.usage.backfill",
)
async def backfill_usage(
    body: BackfillIn,
    admin: ActiveHumanAdmin = Depends(spend_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Queue a backfill of model-call records from the gateway rows of reporting days (at most 31)."""
    spend_on()
    try:
        rollups.check_range(body.start, body.end, max_days=ledgers.MAX_DAYS)
        out = await jobs.enqueue(
            _tenant(tenant_id),
            kind="backfill",
            params={"start": body.start, "end": body.end},
            actor=str(admin.user_id),
        )
        return _job_accepted(out)
    except SpendError as exc:
        raise _refused(exc) from None


@router.post("/usage/restate", status_code=202, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.usage.sensitive.write",
    rate_limit=JOB_RATE_LIMIT,
    idempotency=JOB_IDEMPOTENCY,
    audit_event="spend.usage.restate",
)
async def restate_usage(
    body: RestateIn,
    admin: ActiveHumanAdmin = Depends(spend_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Queue a restatement of a provider's records of billing days (at most 92) with the cards as known now."""
    spend_on()
    try:
        rollups.check_range(body.start, body.end, max_days=maintenance.MAX_DAYS)
        reason = maintenance.check_reason(body.reason)
        out = await jobs.enqueue(
            _tenant(tenant_id),
            kind="restate",
            params={
                "provider": vocab.norm_provider(body.provider),
                "start": body.start,
                "end": body.end,
                "card_ids": [str(c) for c in body.card_ids],
                "include_unpriced": body.include_unpriced,
                "reason": reason,
            },
            actor=str(admin.user_id),
        )
        return _job_accepted(out)
    except SpendError as exc:
        raise _refused(exc) from None


@router.post("/usage/reattribute", status_code=202, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.usage.sensitive.write",
    rate_limit=JOB_RATE_LIMIT,
    idempotency=JOB_IDEMPOTENCY,
    audit_event="spend.usage.reattribute",
)
async def reattribute_usage(
    body: ReattributeIn,
    admin: ActiveHumanAdmin = Depends(spend_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Queue a re-attribution of the still-unattributed records of reporting days (at most 92)."""
    spend_on()
    try:
        rollups.check_range(body.start, body.end, max_days=maintenance.MAX_DAYS)
        out = await jobs.enqueue(
            _tenant(tenant_id),
            kind="reattribute",
            params={"start": body.start, "end": body.end},
            actor=str(admin.user_id),
        )
        return _job_accepted(out)
    except SpendError as exc:
        raise _refused(exc) from None


@router.post("/fx-rates/settle", status_code=202, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.fx.sensitive.write",
    rate_limit=JOB_RATE_LIMIT,
    idempotency=JOB_IDEMPOTENCY,
    audit_event="spend.fx_rates.settle",
)
async def settle_fx_rates(
    body: SettleIn,
    admin: ActiveHumanAdmin = Depends(spend_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Queue an FX settlement of reporting days (at most 92): pending records converted with the rates known now."""
    spend_on()
    try:
        rollups.check_range(body.start, body.end, max_days=maintenance.MAX_DAYS)
        out = await jobs.enqueue(
            _tenant(tenant_id),
            kind="settle_fx",
            params={"start": body.start, "end": body.end, "force_dates": []},
            actor=str(admin.user_id),
        )
        return _job_accepted(out)
    except SpendError as exc:
        raise _refused(exc) from None


@router.post("/commitments/recompute", status_code=202, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.commitments.sensitive.write",
    rate_limit=JOB_RATE_LIMIT,
    idempotency=JOB_IDEMPOTENCY,
    audit_event="spend.commitments.recompute",
)
async def recompute_commitments(
    body: RecomputeIn,
    admin: ActiveHumanAdmin = Depends(spend_admin),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Queue a recompute of commitment drawdown and overage (one provider, or every provider with commitments)."""
    spend_on()
    try:
        params = {"provider": vocab.norm_provider(body.provider)} if body.provider else {}
        out = await jobs.enqueue(
            _tenant(tenant_id), kind="recompute_commitments", params=params, actor=str(admin.user_id)
        )
        return _job_accepted(out)
    except SpendError as exc:
        raise _refused(exc) from None


@router.get("/jobs")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.jobs.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="spend.jobs.list",
)
async def list_jobs(
    kind: JobKindQuery | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
    caller: Caller = Depends(caller_from_request),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Spend maintenance jobs, newest first.

    A job's result counts every agent's records and its parameters carry a correction's reason and card ids:
    an administrator or auditor only, 403 ``tenant_wide_read_refused`` otherwise."""
    spend_on()
    try:
        access.require_tenant_wide(caller)
        return await jobs.list_jobs(_tenant(tenant_id), kind=kind, limit=limit)
    except SpendError as exc:
        raise _refused(exc) from None


@router.get("/jobs/{job_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="spend.jobs.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="spend.jobs.get",
)
async def get_job(
    job_id: uuid.UUID,
    caller: Caller = Depends(caller_from_request),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """One spend maintenance job: its parameters, status, result and error code (an administrator or auditor
    only, refused before the job is looked up)."""
    spend_on()
    try:
        access.require_tenant_wide(caller)
        return await jobs.get_job(_tenant(tenant_id), job_id)
    except SpendError as exc:
        raise _refused(exc) from None
