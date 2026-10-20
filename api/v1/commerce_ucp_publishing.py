# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Operator actions that publish a merchant's UCP business profile.

All routes need the merchant-config write scope (``commerce.merchant_config.write``
or tenant admin) and write the commerce ledger. The flow:

1. ``POST .../ucp/serving-host`` with ``{"host": "shop.example.com"}`` returns a
   DNS TXT challenge (``_agenticorg-ucp.<host>`` =
   ``agenticorg-ucp-verify=<value>``).
2. The merchant publishes the TXT record; ``POST .../ucp/serving-host/verify``
   looks it up (async DNS) and records the attestation (method, record, when,
   by whom) in the merchant config, then indexes the host for public lookup.
3. ``POST .../ucp/keys`` provisions the merchant's ES256 signing key when it has
   none (merchants onboarded before the profile flag); ``.../keys/rotate`` rotates.
4. ``POST .../ucp/publish`` validates the merchant namespace against the
   attested host (authority binding) and every vendor-namespaced entry the
   profile would carry, then records the merchant's public-discovery opt-in.
5. The next Grantex authority request carries ``public_discovery_requested``,
   ``serving_host`` and ``namespace_attestation_ref``; the profile is served
   once the verified ``public_discovery_state`` artifact Grantex issues allows
   publication for that host (refresh the artifacts after publishing).
   ``POST .../ucp/unpublish`` withdraws it.

The GET status view never includes private-key ciphertext.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from api.v1.commerce_runtime import require_merchant_commerce_config_write
from core.commerce.ledger import record_commerce_event, record_commerce_event_in_own_transaction
from core.commerce.merchant_keys import active_key_record, add_active_key, new_merchant_key_record
from core.commerce.oacp_merchant_config import merchant_config_id
from core.commerce.ucp.authority import (
    challenge_value_digest,
    dns_challenge_satisfied,
    is_ucp_governed,
    merchant_namespace_for_host,
    name_bound_to_host,
    new_dns_challenge,
    normalize_host,
)
from core.commerce.ucp.profile import merchant_serve_state
from core.commerce.ucp.registry import ucp_registry
from core.commerce.ucp.state import (
    attested_host,
    public_discovery_opt_in,
    public_ucp_state_view,
    ucp_state,
    with_ucp_state,
)
from core.commerce.ucp.store import host_claimed_by_other_tenant, write_host_index
from core.commerce.ucp.versions import configured_ucp_versions
from core.database import get_tenant_session
from core.models.commerce_c6z_runtime import C6ZMerchantCommerceConfigRow

router = APIRouter(
    prefix="/commerce/runtime",
    tags=["Commerce UCP"],
    dependencies=[Depends(require_merchant_commerce_config_write)],
)


class UcpScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seller_agent_id: str | None = Field(default=None, min_length=1, max_length=160)


class ServingHostRequest(UcpScope):
    host: str = Field(min_length=3, max_length=253)


class PublishRequest(UcpScope):
    namespace: str | None = Field(default=None, min_length=3, max_length=200)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _actor(request: Request) -> dict[str, str]:
    """Who performed the action, as the commerce ledger records it (never a credential value)."""
    state = request.state
    agent_id = getattr(state, "agent_id", None)
    if agent_id:
        return {"actor_type": "agent", "actor_id": str(agent_id)}
    if getattr(state, "auth_mode", None) == "api_key":
        # The middleware sets user_sub to "apikey:<prefix>" for API keys; the key's name is never read here.
        return {"actor_type": "api_key", "actor_id": str(getattr(state, "user_sub", None) or "api_key")}
    return {"actor_type": "user", "actor_id": str(getattr(state, "user_sub", None) or "unknown")}


async def _locked_row(
    session: Any,
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str | None,
    lock: bool = True,
) -> C6ZMerchantCommerceConfigRow:
    query = select(C6ZMerchantCommerceConfigRow).where(
        C6ZMerchantCommerceConfigRow.tenant_id == tenant_id,
        C6ZMerchantCommerceConfigRow.merchant_id == merchant_id,
    )
    if seller_agent_id:
        query = query.where(
            C6ZMerchantCommerceConfigRow.config_id == merchant_config_id(tenant_id, merchant_id, seller_agent_id)
        )
    query = query.order_by(C6ZMerchantCommerceConfigRow.created_at.desc())
    if lock:
        query = query.with_for_update()
    row = (await session.scalars(query)).first()
    if row is None:
        raise HTTPException(404, "Merchant commerce config not found")
    return row


def _status(row: C6ZMerchantCommerceConfigRow) -> dict[str, Any]:
    state = ucp_state(row.public_publishing)
    serve = None
    host = attested_host(state)
    if host is not None:
        # Local readiness only; the public route also needs the verified public_discovery_state artifact.
        serve = merchant_serve_state(row, host=host, now=_now(), live_public_discovery_ids=None)
    discovery = state.get("verified_public_discovery")
    capabilities: list[str] = []
    if serve is not None:
        for version in configured_ucp_versions():
            capabilities.extend(sorted(ucp_registry.served_capability_names(serve.context(version))))
    return {
        "merchant_id": row.merchant_id,
        "seller_agent_id": row.seller_agent_id,
        "ucp": public_ucp_state_view(state),
        "ready_to_serve": serve is not None,
        "public_discovery_opt_in": public_discovery_opt_in(state) is not None,
        "public_discovery_allowed": isinstance(discovery, dict)
        and discovery.get("publication_allowed") is True
        and discovery.get("serving_host") == host,
        "versions": list(configured_ucp_versions()),
        "served_capabilities": sorted(set(capabilities)),
    }


@router.get("/merchant-configs/{merchant_id}/ucp")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.ucp.status.read",
)
async def get_ucp_publishing_status(
    merchant_id: str,
    seller_agent_id: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = await _locked_row(
            session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=seller_agent_id, lock=False
        )
        return _status(row)


@router.post("/merchant-configs/{merchant_id}/ucp/serving-host")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="commerce-runtime-write",
    idempotency="replaces-pending-challenge",
    audit_event="commerce.ucp.serving_host.challenge",
)
async def request_serving_host_challenge(
    merchant_id: str,
    body: ServingHostRequest,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    host = normalize_host(body.host)
    if host is None:
        raise HTTPException(422, "host must be a registered domain name (no IP literal, port or path)")
    challenge = {**new_dns_challenge(host), "created_at": _iso(_now()), "created_by": _actor(request)}
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = await _locked_row(
            session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=body.seller_agent_id
        )
        state = ucp_state(row.public_publishing)
        state["challenge"] = challenge
        row.public_publishing = with_ucp_state(row.public_publishing, state)
        await record_commerce_event(
            session,
            tenant_id=tenant_id,
            event="commerce.ucp.serving_host.challenge",
            outcome="success",
            merchant_id=row.merchant_id,
            seller_agent_id=row.seller_agent_id,
            buyer_agent_id=None,
            payload={"host": host, "record_name": challenge["record_name"]},
            resource_id=row.config_id,
        )
    return {
        "host": host,
        "dns_record": {
            "name": challenge["record_name"],
            "type": "TXT",
            "value": challenge["record_value"],
        },
        "next": "Publish the TXT record, then POST .../ucp/serving-host/verify.",
    }


@router.post("/merchant-configs/{merchant_id}/ucp/serving-host/verify")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="commerce-runtime-external",
    idempotency="re-verifies-pending-challenge",
    audit_event="commerce.ucp.serving_host.verify",
)
async def verify_serving_host(
    merchant_id: str,
    body: UcpScope,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = await _locked_row(
            session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=body.seller_agent_id
        )
        challenge = ucp_state(row.public_publishing).get("challenge")
        scope = (row.merchant_id, row.seller_agent_id, row.config_id)
    if not isinstance(challenge, dict) or not isinstance(challenge.get("host"), str):
        raise HTTPException(409, "No pending serving-host challenge; request one first")
    host = challenge["host"]
    # DNS is looked up outside the database session (no row lock held across network I/O).
    satisfied = await dns_challenge_satisfied(challenge)
    if satisfied and await host_claimed_by_other_tenant(host, tenant_id):
        satisfied = False
        reason = "host_bound_to_another_tenant"
    else:
        reason = "dns_txt_record_not_found"
    if not satisfied:
        await record_commerce_event_in_own_transaction(
            tenant_id=tenant_id,
            event="commerce.ucp.serving_host.verify",
            outcome="refused",
            merchant_id=scope[0],
            seller_agent_id=scope[1],
            buyer_agent_id=None,
            payload={"host": host, "reason": reason},
            resource_id=scope[2],
        )
        raise HTTPException(409, {"status": "not_verified", "reason": reason, "record_name": challenge["record_name"]})
    verified_at = _iso(_now())
    value_digest = challenge_value_digest(str(challenge["record_value"]))
    attestation = {
        "method": challenge.get("method"),
        "host": host,
        "record_name": challenge["record_name"],
        "record_value_sha256": value_digest,
        "verified_at": verified_at,
        "verified_by": _actor(request),
        # Opaque reference sent to Grantex as namespace_attestation_ref with the public-discovery opt-in.
        "ref": "agenticorg:ucp-attestation:" + challenge_value_digest(f"{host}|{value_digest}|{verified_at}")[:32],
    }
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = await _locked_row(session, tenant_id=tenant_id, merchant_id=scope[0], seller_agent_id=scope[1])
        state = ucp_state(row.public_publishing)
        if state.get("challenge") != challenge:
            raise HTTPException(409, "The challenge changed while it was being verified; verify again")
        previous_host = state.get("serving_host") if isinstance(state.get("serving_host"), str) else None
        state["serving_host"] = host
        state["attestation"] = attestation
        state.pop("challenge", None)
        if previous_host != host:
            # A new host is not published until the operator publishes it again.
            state["published"] = None
            state.pop("namespace", None)
        row.public_publishing = with_ucp_state(row.public_publishing, state)
        await write_host_index(
            session,
            tenant_id=tenant_id,
            host=host,
            merchant_id=row.merchant_id,
            seller_agent_id=row.seller_agent_id,
            previous_host=previous_host,
        )
        await record_commerce_event(
            session,
            tenant_id=tenant_id,
            event="commerce.ucp.serving_host.verify",
            outcome="success",
            merchant_id=row.merchant_id,
            seller_agent_id=row.seller_agent_id,
            buyer_agent_id=None,
            payload={"host": host, "attestation": attestation},
            resource_id=row.config_id,
        )
        return _status(row)


async def _provision_or_rotate(
    *,
    merchant_id: str,
    body: UcpScope,
    tenant_id: str,
    rotate: bool,
    event: str,
) -> dict[str, Any]:
    now = _now()
    # Generated (and encrypted for the tenant) before the row is locked: the KEK
    # lookup opens its own database session.
    new_record = await new_merchant_key_record(tenant_id, now=now)
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = await _locked_row(
            session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=body.seller_agent_id
        )
        state = ucp_state(row.public_publishing)
        keys = state.get("signing_keys") or []
        changed = rotate or active_key_record(keys) is None
        if changed:
            state["signing_keys"] = add_active_key(keys, new_record, now=now)
            row.public_publishing = with_ucp_state(row.public_publishing, state)
        active = active_key_record(state.get("signing_keys") or [])
        await record_commerce_event(
            session,
            tenant_id=tenant_id,
            event=event,
            outcome="success",
            merchant_id=row.merchant_id,
            seller_agent_id=row.seller_agent_id,
            buyer_agent_id=None,
            payload={"active_kid": (active or {}).get("kid"), "changed": changed},
            resource_id=row.config_id,
        )
        return {**_status(row), "changed": changed}


@router.post("/merchant-configs/{merchant_id}/ucp/keys")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="credential-write",
    idempotency="no-op-when-an-active-key-exists",
    audit_event="commerce.ucp.keys.provision",
)
async def provision_ucp_signing_key(
    merchant_id: str,
    body: UcpScope,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    return await _provision_or_rotate(
        merchant_id=merchant_id, body=body, tenant_id=tenant_id, rotate=False, event="commerce.ucp.keys.provision"
    )


@router.post("/merchant-configs/{merchant_id}/ucp/keys/rotate")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="credential-write",
    idempotency="each-call-rotates",
    audit_event="commerce.ucp.keys.rotate",
)
async def rotate_ucp_signing_key(
    merchant_id: str,
    body: UcpScope,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    return await _provision_or_rotate(
        merchant_id=merchant_id, body=body, tenant_id=tenant_id, rotate=True, event="commerce.ucp.keys.rotate"
    )


def publish_refusal(row: C6ZMerchantCommerceConfigRow, requested_namespace: str | None) -> tuple[str | None, str]:
    """``(refusal_code, namespace)``: refusal_code is None when the merchant may publish.

    Requires an attested serving host and an active signing key; the namespace
    must be authority-bound to the attested host (UCP overview, Authority
    Binding), and every registered vendor capability, service or payment
    handler that claims the host's authority must sit inside that namespace.
    """
    state = ucp_state(row.public_publishing)
    host = attested_host(state)
    if host is None:
        return "serving_host_not_attested", ""
    if active_key_record(state.get("signing_keys") or []) is None:
        return "signing_key_missing", ""
    try:
        namespace = merchant_namespace_for_host(host, requested_namespace)
    except ValueError:
        return "namespace_not_authority_bound", ""
    for name in _vendor_registrations_under_host(host):
        if not (name == namespace or name.startswith(namespace + ".")):
            return "vendor_entry_outside_namespace", namespace
    return None, namespace


def _vendor_registrations_under_host(host: str) -> list[str]:
    """Registered vendor entries that claim the merchant host's authority (they need its attestation)."""
    names = (
        set(ucp_registry.capabilities)
        | set(ucp_registry.payment_handlers)
        | {name for name, _transport in ucp_registry.services}
    )
    return sorted(name for name in names if not is_ucp_governed(name) and name_bound_to_host(name, host))


@router.post("/merchant-configs/{merchant_id}/ucp/publish")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="commerce-runtime-write",
    idempotency="republish-overwrites-published-state",
    audit_event="commerce.ucp.publish",
)
async def publish_ucp_profile(
    merchant_id: str,
    body: PublishRequest,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = await _locked_row(
            session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=body.seller_agent_id
        )
        refusal, namespace = publish_refusal(row, body.namespace)
        if refusal is not None:
            await record_commerce_event(
                session,
                tenant_id=tenant_id,
                event="commerce.ucp.publish",
                outcome="refused",
                merchant_id=row.merchant_id,
                seller_agent_id=row.seller_agent_id,
                buyer_agent_id=None,
                payload={"reason": refusal, "namespace": body.namespace},
                resource_id=row.config_id,
            )
            return {**_status(row), "published": False, "refusal": refusal}
        state = ucp_state(row.public_publishing)
        state["namespace"] = namespace
        state["published"] = {"published_at": _iso(_now()), "published_by": _actor(request), "namespace": namespace}
        row.public_publishing = with_ucp_state(row.public_publishing, state)
        await record_commerce_event(
            session,
            tenant_id=tenant_id,
            event="commerce.ucp.publish",
            outcome="success",
            merchant_id=row.merchant_id,
            seller_agent_id=row.seller_agent_id,
            buyer_agent_id=None,
            payload={"host": attested_host(state), "namespace": namespace},
            resource_id=row.config_id,
        )
        return {**_status(row), "published": True}


@router.post("/merchant-configs/{merchant_id}/ucp/unpublish")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="commerce-runtime-write",
    idempotency="no-op-when-unpublished",
    audit_event="commerce.ucp.unpublish",
)
async def unpublish_ucp_profile(
    merchant_id: str,
    body: UcpScope,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = await _locked_row(
            session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=body.seller_agent_id
        )
        state = ucp_state(row.public_publishing)
        state["published"] = None
        row.public_publishing = with_ucp_state(row.public_publishing, state)
        await record_commerce_event(
            session,
            tenant_id=tenant_id,
            event="commerce.ucp.unpublish",
            outcome="success",
            merchant_id=row.merchant_id,
            seller_agent_id=row.seller_agent_id,
            buyer_agent_id=None,
            payload={"host": state.get("serving_host")},
            resource_id=row.config_id,
        )
        return {**_status(row), "published": False}
