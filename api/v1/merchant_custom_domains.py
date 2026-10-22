# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Merchant custom domains: add a host, publish DNS, verify, follow the certificate to live.

Merchant routes (``commerce.merchant_config.write`` or tenant admin, under
``/api/v1/commerce/runtime/merchant-configs/{merchant_id}/custom-domain``):

* ``GET`` - the merchant's domain, the records to publish and where it stands.
* ``POST`` ``{"host": "agent.merchant.example"}`` - add the host: a zone apex is
  refused (a CNAME cannot sit there; see the apex guide), the UCP serving-host
  TXT challenge is issued (``api.v1.commerce_ucp_publishing``) and the CNAME
  and TXT records are returned.
* ``POST .../check`` ``{"search_console_owner_added": true}`` - verify the
  records (CNAME target, TXT ownership attestation) and, once both hold, put the
  domain in the operator queue; later calls re-check the mapping and
  certificate, and a failed domain is queued again (the retry).
* ``DELETE`` - disconnect the host (the serving-host index entry and the
  publication are withdrawn).

Operator queue (tenant administrators of ``AGENTICORG_CUSTOM_DOMAIN_OPERATOR_TENANT_ID``
only; 404 for everyone else), under ``/api/v1/commerce/custom-domains/operator-queue``:
list every tenant's domains waiting on the one manual step with the exact
``gcloud`` command, mark one mapped or failed.

``GET /.well-known/agenticorg-host`` (public, any host) answers the host it was
reached on, so the certificate probe can tell that a host reaches this service.
Every route answers 404 while ``AGENTICORG_CUSTOM_DOMAINS_ENABLED`` is off.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from api.v1.commerce_runtime import require_merchant_commerce_config_write
from api.v1.commerce_ucp_publishing import (
    _actor,
    _locked_row,
    issue_serving_host_challenge,
    verify_and_attest_serving_host,
)
from core.commerce import custom_domains as cd
from core.commerce.ledger import record_commerce_event
from core.commerce.ucp.authority import normalize_host
from core.commerce.ucp.state import attested_host, ucp_state, with_ucp_state
from core.commerce.ucp.store import write_host_index
from core.config import settings
from core.database import get_platform_session, get_tenant_session
from core.models.merchant_custom_domain import CLAIMED_STATUSES, INACTIVE_STATUSES, MerchantCustomDomainRow

logger = structlog.get_logger()

router = APIRouter(
    prefix="/commerce/runtime",
    tags=["Commerce Custom Domains"],
    dependencies=[Depends(require_merchant_commerce_config_write)],
)
operator_router = APIRouter(
    prefix="/commerce/custom-domains/operator-queue",
    tags=["Commerce Custom Domains"],
    dependencies=[require_tenant_admin],
)
host_check_router = APIRouter(tags=["Commerce Custom Domains"])

APEX_GUIDE = "docs/oacp/merchant-apex-domain.md"
# Waiting on the domain mapping (create) and on its deletion after a removal (unmap).
MAPPING_QUEUE_STATUSES: tuple[str, ...] = ("pending_mapping", "pending_certificate", "failed")
OPERATOR_QUEUE_STATUSES: tuple[str, ...] = (*MAPPING_QUEUE_STATUSES, "pending_unmap")
CERTIFICATE_TIMEOUT = timedelta(hours=48)
STEP_ORDER: tuple[str, ...] = ("dns", "ownership", "mapping", "certificate", "live")


class DomainScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seller_agent_id: str | None = Field(default=None, min_length=1, max_length=160)


class AddDomainRequest(DomainScope):
    host: str = Field(min_length=3, max_length=253)


class CheckDomainRequest(DomainScope):
    search_console_owner_added: bool = False


class OperatorFailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=400)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _require_enabled() -> None:
    if not cd.custom_domains_enabled():
        raise HTTPException(404, "Not Found")


def _actor_label(actor: dict[str, str]) -> str:
    return f"{actor.get('actor_type', 'user')}:{actor.get('actor_id', 'unknown')}"[:255]


# --- views --------------------------------------------------------------------


_DNS_ERRORS = frozenset({"cname_missing", "cname_wrong_target", "dns_lookup_failed"})
_OWNERSHIP_ERRORS = frozenset({"ownership_txt_missing", "host_bound_to_another_tenant", "host_in_use"})


def error_codes(row: MerchantCustomDomainRow) -> list[str]:
    """The recorded error code first, then the further codes a DNS check listed (``(also: a, b)``)."""
    if not row.last_error_code:
        return []
    codes = [row.last_error_code]
    detail = row.last_error_detail or ""
    if detail.endswith(")") and "(also: " in detail:
        codes.extend(code.strip() for code in detail.rsplit("(also: ", 1)[1][:-1].split(","))
    return codes


def _steps(row: MerchantCustomDomainRow) -> list[dict[str, str]]:
    """``dns`` -> ``ownership`` -> ``mapping`` -> ``certificate`` -> ``live``, each done, current, error or pending."""
    states = dict.fromkeys(STEP_ORDER, "pending")
    codes = set(error_codes(row))
    if row.status == "live":
        states = dict.fromkeys(STEP_ORDER, "done")
    elif row.status == "pending_dns":
        states["dns"] = "error" if codes & _DNS_ERRORS else "current"
        if row.verified_at:
            states["ownership"] = "done"
        else:
            states["ownership"] = "error" if codes & _OWNERSHIP_ERRORS else "current"
    elif row.status in ("pending_mapping", "pending_certificate", "failed"):
        mapped = row.status == "pending_certificate" or (row.status == "failed" and row.mapping_created_at)
        states["dns"] = states["ownership"] = "done"
        current = "certificate" if mapped else "mapping"
        if mapped:
            states["mapping"] = "done"
        states[current] = "error" if row.status == "failed" else "current"
    return [{"key": key, "state": states[key]} for key in STEP_ORDER]


def domain_view(row: MerchantCustomDomainRow, ucp: dict[str, Any] | None = None) -> dict[str, Any]:
    """The merchant's view: records to publish, where the domain stands, the error and what to do next."""
    state = ucp or {}
    challenge = state.get("challenge") if isinstance(state.get("challenge"), dict) else None
    if challenge is not None and challenge.get("host") != row.host:
        challenge = None
    records = cd.dns_records(row.host, challenge, target=row.cname_target)
    attestation = state.get("attestation") if isinstance(state.get("attestation"), dict) else None
    ownership_verified = attested_host(state) == row.host
    if challenge is None and ownership_verified and attestation is not None:
        records.append(
            {
                "purpose": "ownership",
                "type": "TXT",
                "name": str(attestation.get("record_name") or ""),
                "value": "",
                "note": "Verified. Keep the TXT record you published while the domain is connected.",
            }
        )
    error = None
    if row.last_error_code:
        error = {
            "code": row.last_error_code,
            "message": cd.error_message(row.last_error_code) or row.last_error_code,
            "detail": row.last_error_detail,
            "codes": error_codes(row),
        }
    return {
        "id": str(row.id),
        "host": row.host,
        "merchant_id": row.merchant_id,
        "seller_agent_id": row.seller_agent_id or None,
        "status": row.status,
        "steps": _steps(row),
        "records": records,
        "ownership_verified": ownership_verified,
        "provider": row.provider or "cloud_run_mapping",
        "search_console": {
            **cd.search_console_step(row.host, provider=row.provider or "cloud_run_mapping"),
            "confirmed": row.search_console_owner_confirmed_at is not None,
        },
        "error": error,
        "can_retry": row.status in ("pending_dns", "failed", "pending_mapping", "pending_certificate"),
        "profile_url": f"https://{row.host}/.well-known/ucp",
        "apex_guide": APEX_GUIDE,
        "check_count": row.check_count,
        "last_checked_at": _iso(row.last_checked_at),
        "verified_at": _iso(row.verified_at),
        "mapping_requested_at": _iso(row.mapping_requested_at),
        "mapping_created_at": _iso(row.mapping_created_at),
        "live_at": _iso(row.live_at),
        "created_at": _iso(row.created_at),
    }


def operator_view(row: MerchantCustomDomainRow, *, mapping_in_use: bool = False) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "action": "delete_mapping" if row.status == "pending_unmap" else "create_mapping",
        # An unmap item whose host another active domain now holds: keep the mapping, just mark it done.
        "mapping_in_use": mapping_in_use,
        "tenant_id": str(row.tenant_id),
        "merchant_id": row.merchant_id,
        "seller_agent_id": row.seller_agent_id or None,
        "host": row.host,
        "status": row.status,
        "mapping_source": row.mapping_source,
        "search_console_owner_confirmed": row.search_console_owner_confirmed_at is not None,
        "mapping_account": cd.mapping_account(),
        "provider": row.provider or "cloud_run_mapping",
        # Firebase Hosting domains are connected and disconnected by AgenticOrg: no command to run.
        "commands": None if row.provider == "firebase_hosting" else cd.mapping_commands(row.host),
        "error": {"code": row.last_error_code, "detail": row.last_error_detail} if row.last_error_code else None,
        "mapping_requested_at": _iso(row.mapping_requested_at),
        "mapping_created_at": _iso(row.mapping_created_at),
        "last_checked_at": _iso(row.last_checked_at),
    }


# --- database helpers ---------------------------------------------------------


async def _active_row(session: Any, *, tenant_id: str, merchant_id: str, seller_agent_id: str | None) -> Any:
    """The scope's active domain. Without ``seller_agent_id`` it must be unambiguous: 409 when the
    merchant has active domains for more than one seller agent (never a guess)."""
    query = select(MerchantCustomDomainRow).where(
        MerchantCustomDomainRow.tenant_id == uuid.UUID(tenant_id),
        MerchantCustomDomainRow.merchant_id == merchant_id,
        MerchantCustomDomainRow.status.not_in(INACTIVE_STATUSES),
    )
    if seller_agent_id:
        query = query.where(MerchantCustomDomainRow.seller_agent_id == seller_agent_id)
    rows = (await session.scalars(query.order_by(MerchantCustomDomainRow.created_at.desc()))).all()
    if len(rows) > 1:
        raise HTTPException(
            409,
            {
                "code": "seller_agent_id_required",
                "message": "This merchant has domains for more than one seller agent; pass seller_agent_id.",
            },
        )
    return rows[0] if rows else None


async def _ucp_state_for(tenant_id: str, merchant_id: str, seller_agent_id: str | None) -> dict[str, Any]:
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = await _locked_row(
            session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=seller_agent_id, lock=False
        )
        return ucp_state(row.public_publishing)


async def _ledger(session: Any, row: MerchantCustomDomainRow, event: str, outcome: str, **payload: Any) -> None:
    await record_commerce_event(
        session,
        tenant_id=str(row.tenant_id),
        event=event,
        outcome=outcome,
        merchant_id=row.merchant_id,
        seller_agent_id=row.seller_agent_id or None,
        buyer_agent_id=None,
        payload={"host": row.host, "status": row.status, **payload},
        resource_id=str(row.id),
    )


# --- merchant routes ------------------------------------------------------------


@router.get("/merchant-configs/{merchant_id}/custom-domain")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.custom_domain.read",
)
async def get_custom_domain(
    merchant_id: str,
    seller_agent_id: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    _require_enabled()
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = await _active_row(session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=seller_agent_id)
    scope_seller = (row.seller_agent_id or None) if row is not None else seller_agent_id
    ucp = await _ucp_state_for(tenant_id, merchant_id, scope_seller)
    return {
        "enabled": True,
        "cname_target": cd.cname_target(),
        "mapping_account": cd.mapping_account(),
        "apex_guide": APEX_GUIDE,
        "domain": domain_view(row, ucp) if row is not None else None,
    }


@router.post("/merchant-configs/{merchant_id}/custom-domain")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="commerce-runtime-external",
    idempotency="same-host-returns-the-existing-domain",
    audit_event="commerce.custom_domain.add",
)
async def add_custom_domain(
    merchant_id: str,
    body: AddDomainRequest,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    _require_enabled()
    host = cd.normalize_custom_host(body.host)
    if host is None:
        raise HTTPException(422, {"code": "invalid_host", "message": cd.error_message("invalid_host")})
    if await cd.is_zone_apex(host):
        raise HTTPException(
            422,
            {
                "code": "apex_domain_needs_subdomain",
                "message": cd.error_message("apex_domain_needs_subdomain"),
                "apex_guide": APEX_GUIDE,
            },
        )
    actor = _actor(request)
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        # The seller scope comes from the merchant config (the one the UCP routes use), never a
        # domain row picked across sellers.
        config = await _locked_row(
            session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=body.seller_agent_id, lock=False
        )
        seller = config.seller_agent_id or None
        ucp = ucp_state(config.public_publishing)
        existing = await _active_row(session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=seller)
    if existing is not None:
        if existing.host != host:
            raise HTTPException(
                409,
                {"code": "domain_exists", "message": f"Remove {existing.host} before adding another domain."},
            )
        async with get_tenant_session(uuid.UUID(tenant_id)) as session:
            row = await session.get(MerchantCustomDomainRow, existing.id)
            await _ledger(session, row, "commerce.custom_domain.add", "success", replay=True)
        return domain_view(existing, ucp)
    if attested_host(ucp) != host:
        await issue_serving_host_challenge(
            tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=seller, host=host, actor=actor
        )
        ucp = await _ucp_state_for(tenant_id, merchant_id, seller)
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = MerchantCustomDomainRow(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            merchant_id=config.merchant_id,
            seller_agent_id=config.seller_agent_id or "",
            host=host,
            status="pending_dns",
            provider=cd.current_provider(),
            cname_target=cd.cname_target(),
            created_by=_actor_label(actor),
            check_count=0,
            created_at=_now(),
            updated_at=_now(),
        )
        session.add(row)
        await _ledger(session, row, "commerce.custom_domain.add", "success")
    return domain_view(row, ucp)


async def _verify_dns(
    row: MerchantCustomDomainRow, *, tenant_id: str, actor: dict[str, str]
) -> tuple[list[tuple[str, str | None]], dict[str, Any]]:
    """Check the CNAME and the ownership TXT (recording the attestation); returns ``(errors, ucp_state)``."""
    errors: list[tuple[str, str | None]] = []
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        clash = (
            await session.scalars(
                select(MerchantCustomDomainRow.id).where(
                    MerchantCustomDomainRow.tenant_id == uuid.UUID(tenant_id),
                    MerchantCustomDomainRow.host == row.host,
                    MerchantCustomDomainRow.id != row.id,
                    MerchantCustomDomainRow.status.in_(CLAIMED_STATUSES),
                )
            )
        ).first()
    if clash is not None:
        # Attesting would repoint the host's public index at this merchant: refuse before it.
        return [("host_in_use", None)], await _ucp_state_for(tenant_id, row.merchant_id, row.seller_agent_id or None)
    ucp = await _ucp_state_for(tenant_id, row.merchant_id, row.seller_agent_id or None)
    if attested_host(ucp) != row.host:
        challenge = ucp.get("challenge") if isinstance(ucp.get("challenge"), dict) else None
        if challenge is None or challenge.get("host") != row.host:
            # The pending challenge was replaced (or never issued): issue one for this host again.
            await issue_serving_host_challenge(
                tenant_id=tenant_id,
                merchant_id=row.merchant_id,
                seller_agent_id=row.seller_agent_id or None,
                host=row.host,
                actor=actor,
            )
            errors.append(("ownership_txt_missing", "A new TXT value was issued; publish it and check again."))
        else:
            try:
                await verify_and_attest_serving_host(
                    tenant_id=tenant_id,
                    merchant_id=row.merchant_id,
                    seller_agent_id=row.seller_agent_id or None,
                    actor=actor,
                )
            except HTTPException as exc:
                detail: dict[str, Any] = exc.detail if isinstance(exc.detail, dict) else {}
                reason = detail.get("reason")
                if reason == "host_bound_to_another_tenant":
                    errors.append(("host_bound_to_another_tenant", None))
                elif reason == "dns_txt_record_not_found":
                    errors.append(("ownership_txt_missing", str(challenge.get("record_name"))))
                else:
                    errors.append(("ownership_txt_missing", "The challenge changed; check again."))
        ucp = await _ucp_state_for(tenant_id, row.merchant_id, row.seller_agent_id or None)
    ok, observed, code = await cd.check_routing(row.host, target=row.cname_target)
    if not ok and code is not None:
        detail = f"found {observed}, expected {row.cname_target}" if code == "cname_wrong_target" else None
        errors.append((code, detail))
    return errors, ucp


@router.post("/merchant-configs/{merchant_id}/custom-domain/check")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="commerce-runtime-external",
    idempotency="re-checks-current-state",
    audit_event="commerce.custom_domain.check",
)
async def check_custom_domain(
    merchant_id: str,
    body: CheckDomainRequest,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    _require_enabled()
    actor = _actor(request)
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = await _active_row(
            session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=body.seller_agent_id
        )
        if row is None:
            raise HTTPException(404, "No custom domain; add one first")
        domain_id = row.id
        if body.search_console_owner_added and row.search_console_owner_confirmed_at is None:
            row.search_console_owner_confirmed_at = _now()
        if row.status == "failed":
            # Retry: back to the step that failed.
            row.status = "pending_mapping" if row.verified_at else "pending_dns"
            row.mapping_requested_at = _now() if row.verified_at else None
            row.operator_notified_at = None
            row.mapping_source = None
            row.mapping_created_at = None
            row.last_error_code = None
            row.last_error_detail = None
        status = row.status
    if status == "pending_dns":
        return await _check_pending_dns(domain_id, tenant_id=tenant_id, actor=actor)
    if status in ("pending_mapping", "pending_certificate"):
        if await progress_domain(domain_id, tenant_id=tenant_id) == "pending_mapping":
            await notify_operator(domain_id, tenant_id=tenant_id)
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = await session.get(MerchantCustomDomainRow, domain_id)
        await _ledger(session, row, "commerce.custom_domain.check", "success")
    return domain_view(row, await _ucp_state_for(tenant_id, row.merchant_id, row.seller_agent_id or None))


async def _check_pending_dns(domain_id: uuid.UUID, *, tenant_id: str, actor: dict[str, str]) -> dict[str, Any]:
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        snapshot = await session.get(MerchantCustomDomainRow, domain_id)
    errors, ucp = await _verify_dns(snapshot, tenant_id=tenant_id, actor=actor)
    queued = False
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = await session.get(MerchantCustomDomainRow, domain_id, with_for_update=True)
        now = _now()
        row.check_count = (row.check_count or 0) + 1
        row.last_checked_at = now
        row.updated_at = now
        if attested_host(ucp) == row.host and row.verified_at is None:
            row.verified_at = now
        needs_owner = row.provider != "firebase_hosting"
        if not errors and needs_owner and row.search_console_owner_confirmed_at is None:
            errors.append(("search_console_owner_not_confirmed", None))
        if errors:
            row.last_error_code, row.last_error_detail = errors[0][0], (errors[0][1] or None)
            if len(errors) > 1:
                also = ", ".join(code for code, _ in errors[1:])
                row.last_error_detail = f"{row.last_error_detail or ''} (also: {also})".strip()[:500]
            await _ledger(session, row, "commerce.custom_domain.check", "refused", errors=[code for code, _ in errors])
        else:
            row.status = "pending_mapping"
            row.mapping_requested_at = now
            row.last_error_code = None
            row.last_error_detail = None
            await _ledger(session, row, "commerce.custom_domain.check", "success", queued=True)
            queued = True
        try:
            await session.flush()
        except IntegrityError as exc:
            # uq_merchant_custom_domains_claimed_host: another merchant holds this host.
            raise HTTPException(409, {"code": "host_in_use", "message": cd.error_message("host_in_use")}) from exc
        view = domain_view(row, ucp)
    if queued:
        await notify_operator(domain_id, tenant_id=tenant_id)
    return view


@router.delete("/merchant-configs/{merchant_id}/custom-domain")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="commerce-runtime-write",
    idempotency="no-op-when-no-domain",
    audit_event="commerce.custom_domain.remove",
)
async def remove_custom_domain(
    merchant_id: str,
    request: Request,
    seller_agent_id: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    _require_enabled()
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = await _active_row(session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=seller_agent_id)
        if row is None:
            raise HTTPException(404, "No custom domain")
        # Once verified, a mapping may exist (an operator can create it before marking it), so its
        # deletion becomes a durable queue item rather than an email alone.
        had_mapping = row.verified_at is not None and row.status != "pending_dns"
        config = await _locked_row(
            session, tenant_id=tenant_id, merchant_id=row.merchant_id, seller_agent_id=row.seller_agent_id or None
        )
        state = ucp_state(config.public_publishing)
        if attested_host(state) == row.host or state.get("serving_host") == row.host:
            # Disconnecting the host withdraws its publication and its public index entry.
            state["serving_host"] = None
            state.pop("attestation", None)
            state["published"] = None
            state.pop("namespace", None)
            await write_host_index(
                session,
                tenant_id=tenant_id,
                host=row.host,
                merchant_id=None,
                seller_agent_id=None,
            )
        challenge = state.get("challenge")
        if isinstance(challenge, dict) and challenge.get("host") == row.host:
            state.pop("challenge", None)
        config.public_publishing = with_ucp_state(config.public_publishing, state)
        row.status = "pending_unmap" if had_mapping else "removed"
        row.operator_notified_at = None
        row.last_error_code = None
        row.last_error_detail = None
        row.updated_at = _now()
        await _ledger(session, row, "commerce.custom_domain.remove", "success", had_mapping=had_mapping)
        host, domain_id = row.host, row.id
    if had_mapping:
        # The queue item is the durable record; the email is a courtesy the status task resends.
        await notify_operator(domain_id, tenant_id=tenant_id)
    logger.info("custom_domain_removed", had_mapping=had_mapping)
    return {"removed": True, "host": host, "mapping_delete_needed": had_mapping}


# --- progress (check endpoint and status task) ---------------------------------


async def progress_domain(domain_id: uuid.UUID, *, tenant_id: str) -> str:
    """Advance one ``pending_mapping``, ``pending_certificate`` or ``pending_unmap`` domain; returns its status.

    Network calls (Admin API, DNS, the HTTPS probe) run outside the row lock.
    """
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = await session.get(MerchantCustomDomainRow, domain_id)
        if row is None or row.status not in ("pending_mapping", "pending_certificate", "pending_unmap"):
            return row.status if row is not None else "missing"
        original, host, created_at, target = row.status, row.host, row.mapping_created_at, row.cname_target
        provider = row.provider or "cloud_run_mapping"
    if original == "pending_unmap":
        return await _progress_unmap(domain_id, tenant_id=tenant_id, host=host, provider=provider)
    status = original
    update: dict[str, Any] = {}
    now = _now()
    if status == "pending_mapping" and provider == "firebase_hosting":
        # Firebase Hosting: AgenticOrg connects the domain itself, no operator and no Search Console.
        try:
            hosted = await cd.create_firebase_domain(host)
        # enterprise-gate: broad-except-ok reason=hosting-api-failure-is-recorded-on-the-domain-and-retried
        except Exception as exc:
            logger.warning("custom_domain_firebase_create_error", error_type=type(exc).__name__)
            update = {
                "last_error_code": "mapping_failed",
                "last_error_detail": "Firebase Hosting unavailable; retrying.",
            }
        else:
            if hosted.failed:
                update = {"status": "failed", "last_error_code": "mapping_failed", "last_error_detail": hosted.message}
            else:
                update = {
                    "status": "pending_certificate",
                    "mapping_source": "firebase_hosting",
                    "mapping_created_at": now,
                    "last_error_code": None,
                    "last_error_detail": None,
                }
                status, created_at = "pending_certificate", now
    elif status == "pending_mapping" and cd.automap_enabled():
        try:
            mapping = await cd.create_domain_mapping(host)
        # enterprise-gate: broad-except-ok reason=admin-api-failure-is-recorded-on-the-domain-and-retried
        except Exception as exc:
            logger.warning("custom_domain_mapping_create_error", error_type=type(exc).__name__)
            mapping = cd.MappingState(exists=False, ready=False, failed=False, message=None)
            update = {"last_error_code": "mapping_failed", "last_error_detail": "Admin API unavailable; retrying."}
        else:
            if mapping.failed and not mapping.exists:
                update = {"status": "failed", "last_error_code": "mapping_failed", "last_error_detail": mapping.message}
            elif mapping.exists:
                update = {"status": "pending_certificate", "mapping_source": "cloud_run_api", "mapping_created_at": now}
                status, created_at = "pending_certificate", now
    if status in ("pending_mapping", "pending_certificate") and update.get("status") != "failed":
        update.update(
            await _certificate_update(host, target, status=status, created_at=created_at, now=now, provider=provider)
        )
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = await session.get(MerchantCustomDomainRow, domain_id, with_for_update=True)
        if row is None or row.status != original:
            # Changed meanwhile (operator action, removal, a concurrent check): leave it.
            return row.status if row is not None else "missing"
        previous = row.status
        for key, value in update.items():
            setattr(row, key, value)
        if row.status == "live":
            row.last_error_code = None
            row.last_error_detail = None
        row.last_checked_at = now
        row.updated_at = now
        row.check_count = (row.check_count or 0) + 1
        if row.status != previous:
            await _ledger(session, row, "commerce.custom_domain.status", "success", previous=previous)
        return row.status


async def _certificate_update(
    host: str,
    target: str,
    *,
    status: str,
    created_at: datetime | None,
    now: datetime,
    provider: str = "cloud_run_mapping",
) -> dict[str, Any]:
    ok, observed, code = await cd.check_routing(host, target=target)
    if not ok:
        detail = f"found {observed}" if code == "cname_wrong_target" else None
        return {"last_error_code": code, "last_error_detail": detail}
    timed_out = status == "pending_certificate" and created_at is not None and now - created_at > CERTIFICATE_TIMEOUT
    if provider == "firebase_hosting" and status == "pending_certificate":
        try:
            hosted = await cd.get_firebase_domain(host)
        # enterprise-gate: broad-except-ok reason=hosting-api-read-failure-is-recorded-and-retried
        except Exception as exc:
            logger.warning("custom_domain_firebase_read_error", error_type=type(exc).__name__)
            return {"last_error_code": "certificate_pending", "last_error_detail": "Firebase Hosting unavailable."}
        if hosted.failed:
            return {"status": "failed", "last_error_code": "mapping_failed", "last_error_detail": hosted.message}
        if not hosted.exists:
            # The domain left the site (deleted in the console): connect it again on the next check.
            return {"status": "pending_mapping", "mapping_source": None, "mapping_created_at": None}
        if not hosted.serving:
            if timed_out:
                return {
                    "status": "failed",
                    "last_error_code": "mapping_failed",
                    "last_error_detail": "No certificate after 48 hours; check that the CNAME is DNS only.",
                }
            detail = f"Firebase Hosting: {hosted.ownership or 'pending'}, {hosted.cert_state or 'pending'}"
            return {"last_error_code": "certificate_pending", "last_error_detail": detail}
    if cd.automap_enabled() and status == "pending_certificate" and provider != "firebase_hosting":
        try:
            mapping = await cd.get_domain_mapping(host)
        # enterprise-gate: broad-except-ok reason=admin-api-read-failure-falls-back-to-the-https-probe
        except Exception as exc:
            logger.warning("custom_domain_mapping_read_error", error_type=type(exc).__name__)
        else:
            if mapping.failed:
                return {"status": "failed", "last_error_code": "mapping_failed", "last_error_detail": mapping.message}
    probe = await cd.probe_host(host)
    if probe.state == "live":
        return {"status": "live", "live_at": now}
    if timed_out:
        return {
            "status": "failed",
            "last_error_code": "mapping_failed",
            "last_error_detail": "No certificate after 48 hours; check CAA records and that the CNAME is DNS only.",
        }
    if status == "pending_mapping":
        return {"last_error_code": None, "last_error_detail": None}
    return {"last_error_code": probe.state, "last_error_detail": probe.detail}


async def host_still_claimed(host: str, *, excluding: uuid.UUID) -> bool:
    """Whether another active domain row (any tenant) holds ``host``: its mapping must then be kept."""
    async with get_platform_session() as session:
        found = (
            await session.scalars(
                select(MerchantCustomDomainRow.id).where(
                    MerchantCustomDomainRow.host == host,
                    MerchantCustomDomainRow.id != excluding,
                    MerchantCustomDomainRow.status.not_in(INACTIVE_STATUSES),
                )
            )
        ).first()
    return found is not None


async def _finish_unmap(domain_id: uuid.UUID, *, tenant_id: str, **payload: Any) -> str:
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = await session.get(MerchantCustomDomainRow, domain_id, with_for_update=True)
        if row is None or row.status != "pending_unmap":
            return row.status if row is not None else "missing"
        row.status = "removed"
        row.last_error_code = None
        row.last_error_detail = None
        row.updated_at = _now()
        await _ledger(session, row, "commerce.custom_domain.status", "success", previous="pending_unmap", **payload)
        return row.status


async def _progress_unmap(
    domain_id: uuid.UUID, *, tenant_id: str, host: str, provider: str = "cloud_run_mapping"
) -> str:
    """Delete a removed domain's mapping (Firebase Hosting always; a Cloud Run mapping with automap on).

    The mapping is kept when another active row holds the host.
    """
    if await host_still_claimed(host, excluding=domain_id):
        return await _finish_unmap(domain_id, tenant_id=tenant_id, mapping_kept=True)
    firebase = provider == "firebase_hosting"
    if not firebase and not cd.automap_enabled():
        # The operator deletes it; note the check so the item does not keep the head of every batch.
        async with get_tenant_session(uuid.UUID(tenant_id)) as session:
            row = await session.get(MerchantCustomDomainRow, domain_id, with_for_update=True)
            if row is not None and row.status == "pending_unmap":
                row.last_checked_at = _now()
        return "pending_unmap"
    try:
        deleted = await (cd.delete_firebase_domain(host) if firebase else cd.delete_domain_mapping(host))
    # enterprise-gate: broad-except-ok reason=admin-api-failure-keeps-the-unmap-item-queued-for-retry
    except Exception as exc:
        logger.warning("custom_domain_mapping_delete_error", error_type=type(exc).__name__)
        deleted = False
    if not deleted:
        async with get_tenant_session(uuid.UUID(tenant_id)) as session:
            row = await session.get(MerchantCustomDomainRow, domain_id, with_for_update=True)
            if row is not None and row.status == "pending_unmap":
                row.last_error_code = "mapping_failed"
                row.last_error_detail = "The domain mapping could not be deleted; retrying."
                row.last_checked_at = _now()
                row.check_count = (row.check_count or 0) + 1
        return "pending_unmap"
    return await _finish_unmap(
        domain_id, tenant_id=tenant_id, mapping_source="firebase_hosting" if firebase else "cloud_run_api"
    )


# --- operator notification --------------------------------------------------------


def _send_operator_email(*, subject: str, lines: list[str]) -> bool:
    recipient = str(getattr(settings, "custom_domain_operator_email", "") or "").strip()
    if not recipient:
        return False
    import html  # noqa: PLC0415

    from core.email import send_email  # noqa: PLC0415

    body = "".join(
        f"<p><code>{html.escape(line)}</code></p>" if line.startswith("gcloud") else f"<p>{html.escape(line)}</p>"
        for line in lines
    )
    try:
        return send_email(recipient, subject, body)
    # enterprise-gate: broad-except-ok reason=operator-email-failure-is-logged-the-queue-stays-authoritative
    except Exception as exc:
        logger.warning("custom_domain_operator_email_failed", error_type=type(exc).__name__)
        return False


async def notify_operator(domain_id: uuid.UUID, *, tenant_id: str) -> bool:
    """Email the operator the queue item and its command once (no-op with automap on or no recipient)."""
    if cd.automap_enabled():
        return False
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = await session.get(MerchantCustomDomainRow, domain_id)
        if row is None or row.status not in ("pending_mapping", "pending_unmap") or row.operator_notified_at:
            return False
        if row.provider == "firebase_hosting":
            return False  # AgenticOrg connects and disconnects Firebase domains itself
        host, status = row.host, row.status
        confirmed = row.search_console_owner_confirmed_at is not None
    commands = cd.mapping_commands(host)
    if status == "pending_unmap":
        subject = f"Custom domain removed, delete its mapping: {host}"
        lines = [
            f"The merchant disconnected {host}. Delete its Cloud Run domain mapping, if it was created:",
            commands["delete"],
            "Then mark it deleted in the operator queue (Commerce > Custom domains).",
        ]
    else:
        subject = f"Custom domain waiting for a mapping: {host}"
        lines = [
            f"{host} passed DNS and ownership checks and waits for its Cloud Run domain mapping.",
            "Merchant confirmed the Search Console owner: " + ("yes" if confirmed else "no"),
            commands["create"],
            "Then mark it mapped in the operator queue (Commerce > Custom domains), or let the status check find it.",
        ]
    sent = await asyncio.to_thread(_send_operator_email, subject=subject, lines=lines)
    if sent:
        async with get_tenant_session(uuid.UUID(tenant_id)) as session:
            row = await session.get(MerchantCustomDomainRow, domain_id, with_for_update=True)
            if row is not None and row.operator_notified_at is None:
                row.operator_notified_at = _now()
    return sent


# --- operator queue ------------------------------------------------------------------


def _require_operator(tenant_id: str) -> None:
    _require_enabled()
    operator_tenant = str(getattr(settings, "custom_domain_operator_tenant_id", "") or "").strip()
    if not operator_tenant or operator_tenant != tenant_id:
        raise HTTPException(404, "Not Found")


async def _operator_row(domain_id: str, statuses: tuple[str, ...]) -> MerchantCustomDomainRow:
    try:
        key = uuid.UUID(domain_id)
    except ValueError as exc:
        raise HTTPException(404, "Not Found") from exc
    async with get_platform_session() as session:
        row = await session.get(MerchantCustomDomainRow, key)
    if row is None or row.status not in statuses:
        raise HTTPException(404, "Not Found")
    return row


@operator_router.get("")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.custom_domain.operator_queue.read",
)
async def list_operator_queue(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    _require_operator(tenant_id)
    async with get_platform_session() as session:
        rows = (
            await session.scalars(
                select(MerchantCustomDomainRow)
                .where(
                    MerchantCustomDomainRow.status.in_(OPERATOR_QUEUE_STATUSES),
                    # Firebase Hosting domains need an operator only when they fail.
                    or_(
                        MerchantCustomDomainRow.provider != "firebase_hosting",
                        MerchantCustomDomainRow.status == "failed",
                    ),
                )
                .order_by(MerchantCustomDomainRow.mapping_requested_at.asc().nulls_last())
                .limit(200)
            )
        ).all()
    active_hosts: dict[str, set[uuid.UUID]] = {}
    unmap_hosts = {row.host for row in rows if row.status == "pending_unmap"}
    if unmap_hosts:
        async with get_platform_session() as session:
            for domain_id, host in (
                await session.execute(
                    select(MerchantCustomDomainRow.id, MerchantCustomDomainRow.host).where(
                        MerchantCustomDomainRow.host.in_(unmap_hosts),
                        MerchantCustomDomainRow.status.not_in(INACTIVE_STATUSES),
                    )
                )
            ).all():
                active_hosts.setdefault(host, set()).add(domain_id)
    return {
        "automap_enabled": cd.automap_enabled(),
        "mapping_account": cd.mapping_account(),
        "items": [
            operator_view(row, mapping_in_use=row.status == "pending_unmap" and bool(active_hosts.get(row.host)))
            for row in rows
        ],
    }


@operator_router.post("/{domain_id}/mapped")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="commerce-runtime-write",
    idempotency="no-op-when-already-mapped",
    audit_event="commerce.custom_domain.operator.mapped",
)
async def mark_mapped(domain_id: str, request: Request, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    _require_operator(tenant_id)
    found = await _operator_row(domain_id, MAPPING_QUEUE_STATUSES)
    async with get_tenant_session(found.tenant_id) as session:
        row = await session.get(MerchantCustomDomainRow, found.id, with_for_update=True)
        if row is None or row.status not in MAPPING_QUEUE_STATUSES:
            raise HTTPException(404, "Not Found")
        if row.provider == "firebase_hosting":
            raise HTTPException(409, {"code": "connected_automatically", "message": "AgenticOrg connects this domain."})
        if row.status != "pending_certificate":
            row.status = "pending_certificate"
            row.mapping_source = "operator"
            row.mapping_created_at = _now()
            row.last_error_code = None
            row.last_error_detail = None
            row.updated_at = _now()
        await _ledger(session, row, "commerce.custom_domain.operator.mapped", "success", operator=_actor(request))
        return operator_view(row)


@operator_router.post("/{domain_id}/failed")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="commerce-runtime-write",
    idempotency="overwrites-failure-reason",
    audit_event="commerce.custom_domain.operator.failed",
)
async def mark_failed(
    domain_id: str,
    body: OperatorFailRequest,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    _require_operator(tenant_id)
    found = await _operator_row(domain_id, MAPPING_QUEUE_STATUSES)
    async with get_tenant_session(found.tenant_id) as session:
        row = await session.get(MerchantCustomDomainRow, found.id, with_for_update=True)
        if row is None or row.status not in MAPPING_QUEUE_STATUSES:
            raise HTTPException(404, "Not Found")
        row.status = "failed"
        row.last_error_code = "mapping_failed"
        row.last_error_detail = body.reason
        row.updated_at = _now()
        await _ledger(session, row, "commerce.custom_domain.operator.failed", "refused", operator=_actor(request))
        return operator_view(row)


@operator_router.post("/{domain_id}/unmapped")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="commerce-runtime-write",
    idempotency="no-op-when-already-removed",
    audit_event="commerce.custom_domain.operator.unmapped",
)
async def mark_unmapped(
    domain_id: str, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """The operator deleted the removed domain's mapping (or kept it because another domain holds the host)."""
    _require_operator(tenant_id)
    found = await _operator_row(domain_id, ("pending_unmap",))
    async with get_tenant_session(found.tenant_id) as session:
        row = await session.get(MerchantCustomDomainRow, found.id, with_for_update=True)
        if row is None or row.status != "pending_unmap":
            raise HTTPException(404, "Not Found")
        row.status = "removed"
        row.last_error_code = None
        row.last_error_detail = None
        row.updated_at = _now()
        await _ledger(session, row, "commerce.custom_domain.operator.unmapped", "success", operator=_actor(request))
        return operator_view(row)


# --- host check ------------------------------------------------------------------------


@host_check_router.get("/.well-known/agenticorg-host")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:custom_domain.host_check",
    rate_limit="ucp-discovery",
    idempotency="read-only",
    audit_event="none-public-custom-domain-host-check",
    public_reason="custom-domain-probe-echoes-only-the-request-host",
)
async def host_check(request: Request) -> JSONResponse:
    _require_enabled()
    host = normalize_host(request.headers.get("host"))
    return JSONResponse(
        content={"service": cd.HOST_CHECK_SERVICE, "host": host},
        headers={"Cache-Control": "no-store"},
    )
