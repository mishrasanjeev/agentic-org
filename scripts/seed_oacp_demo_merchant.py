#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Create, converge or tear down the one synthetic OACP demo merchant, "Nilgiri Tea House".

Runs inside the production API image as a one-off Cloud Run job (see
docs/runbooks/oacp-demo-merchant.md) with the API's environment
(``AGENTICORG_DB_URL``, the tenant KEK, ``GRANTEX_COMMERCE_BASE_URL``,
``GRANTEX_COMMERCE_INTERNAL_TOKEN``) and ``AGENTICORG_OACP_DEMO_MERCHANT_ENABLED=true``.
Dry run by default: it reads, prints what it would do and writes nothing.

    python scripts/seed_oacp_demo_merchant.py                      # dry run
    python scripts/seed_oacp_demo_merchant.py --apply              # create or converge
    python scripts/seed_oacp_demo_merchant.py --apply --serving-host nilgiritea.agenticorg.ai  # prints the TXT
    python scripts/seed_oacp_demo_merchant.py --apply --verify-host  # after DNS propagates: attest, publish
    python scripts/seed_oacp_demo_merchant.py --teardown           # what a teardown would delete
    python scripts/seed_oacp_demo_merchant.py --teardown --apply

What ``--apply`` converges (each step is skipped when already in place, so a
rerun never duplicates anything):

1. the demo tenant "Nilgiri Tea House (OACP demo)" (fixed id, slug ``nilgiri-tea-house-demo``,
   marked synthetic in ``tenants.settings``), refusing a slug or id it did not
   create;
2. one admin user ``demo-admin@demo.agenticorg.example``; it gets a password
   only from ``AGENTICORG_OACP_DEMO_ADMIN_PASSWORD`` (mount it from Secret
   Manager), which is never printed;
3. the merchant commerce config (``PUT .../merchant-configs/{id}`` handler: the
   ``synthetic_demo`` source, 10 teas and accessories in INR, one Ooty store with Asia/Kolkata hours, a
   returns policy, INR, no payment executor) and its onboarding packet;
4. the merchant's UCP signing key (``POST .../ucp/keys`` handler);
5. with ``--serving-host``: the DNS TXT challenge, printed as the exact record to
   create (no DNS is invented); with ``--verify-host``, in a later run once the
   record resolves: the attestation, the UCP publish and fresh artifacts;
6. the synthetic catalog evidence, the Grantex authority request and the
   verified caching of the issued artifacts (``POST .../authority/grantex/request``
   and ``POST .../artifacts/cache`` handlers), whenever a required artifact
   family has no usable verified record.

Every write goes through the same handler the HTTP route uses, in a
tenant-bound session, and records the route's commerce ledger event (the script
checks it was written). Exit codes: 0 done, 1 failed, 2 refused before any
write, 3 Grantex does not allow the demo tenant (set its allowlist),
4 Grantex does not accept the synthetic demo source yet.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2
EXIT_GRANTEX_TENANT_NOT_ALLOWED = 3
EXIT_GRANTEX_DEMO_SOURCE_UNSUPPORTED = 4

ADMIN_LOGIN_ENV = "AGENTICORG_OACP_DEMO_ADMIN_PASSWORD"
MIN_ADMIN_LOGIN_LENGTH = 16
OPERATOR_ACTOR_ID = "scripts/seed_oacp_demo_merchant.py"
GRANTEX_ENV_VARS = ("GRANTEX_COMMERCE_BASE_URL", "GRANTEX_COMMERCE_INTERNAL_TOKEN")
# Every artifact type the Grantex C6Z issuance produces for a scope.
ISSUED_ARTIFACT_TYPES: tuple[str, ...] = (
    "merchant_capability",
    "seller_agent_capability",
    "catalog_snapshot",
    "price",
    "inventory",
    "policy",
    "public_discovery",
    "mandate_capability",
    "protocol_adapter",
)
CONFIG_FIELDS: tuple[str, ...] = (
    "merchant_display_name",
    "public_brand_profile",
    "commerce_categories",
    "source_connectors",
    "buyer_channels",
    "payment_providers",
    "offline_pos_stores",
    "source_freshness_policy",
    "provider_policy",
    "status",
)


class SeedError(RuntimeError):
    def __init__(self, exit_code: int, message: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class DemoState:
    """What exists now for the demo tenant (read without writing)."""

    tenant_exists: bool = False
    tenant_marked: bool = False
    tenant_settings: dict[str, Any] = field(default_factory=dict)
    admin_exists: bool = False
    admin_can_sign_in: bool = False
    config_exists: bool = False
    config_converged: bool = False
    packet_exists: bool = False
    active_kid: str | None = None
    evidence_observed_at: str | None = None
    usable_types: dict[str, int] = field(default_factory=dict)
    catalog_available: bool = False
    ucp: dict[str, Any] = field(default_factory=dict)


@dataclass
class Report:
    mode: str
    actions: list[str] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    dns_record: dict[str, str] | None = None
    admin_login: str = "not_set"
    plural_live: bool = False


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _operator_request() -> Any:
    """What the serving-host and publish handlers read from a request: who acted (never a credential)."""
    return SimpleNamespace(
        state=SimpleNamespace(agent_id=None, auth_mode="operator", user_sub=f"operator:{OPERATOR_ACTOR_ID}")
    )


async def _ledgered(event: str, call: Callable[[], Awaitable[Any]]) -> Any:
    """Run one route handler as the operator and require the ledger event the route declares."""
    from core.commerce.ledger import close_request_scope, open_request_scope

    scope, token = open_request_scope(declared_event=event, actor_type="operator", actor_id=OPERATOR_ACTOR_ID)
    try:
        result = await call()
    finally:
        close_request_scope(token)
    if event not in scope.recorded:
        raise SeedError(EXIT_FAILED, f"{event} returned without writing its commerce ledger event")
    return result


def check_preconditions(*, apply: bool, teardown: bool, environ: Mapping[str, str]) -> list[str]:
    """Reasons the run must stop before touching the database (empty: go)."""
    from core.commerce.oacp_demo_merchant import demo_merchant_enabled

    blockers: list[str] = []
    if not demo_merchant_enabled():
        blockers.append("AGENTICORG_OACP_DEMO_MERCHANT_ENABLED is not true in this environment")
    if not str(environ.get("AGENTICORG_DB_URL", "")).strip():
        blockers.append("AGENTICORG_DB_URL is not set")
    if apply and not teardown:
        missing = [name for name in GRANTEX_ENV_VARS if not str(environ.get(name, "")).strip()]
        if missing:
            blockers.append("missing Grantex settings: " + ", ".join(missing))
        if _truthy(environ.get("PLURAL_PINE_LIVE_EXECUTION_ENABLED")):
            blockers.append("PLURAL_PINE_LIVE_EXECUTION_ENABLED is on in this job; the demo runs with Plural live off")
        if 0 < len(environ.get(ADMIN_LOGIN_ENV) or "") < MIN_ADMIN_LOGIN_LENGTH:
            blockers.append(f"{ADMIN_LOGIN_ENV} must be at least {MIN_ADMIN_LOGIN_LENGTH} characters")
    return blockers


# --- reading --------------------------------------------------------------------------------


async def read_state() -> DemoState:
    """The demo tenant's current state; raises SeedError (refused) when a name is taken by someone else."""
    from sqlalchemy import func, select

    from api.v1 import commerce_runtime as runtime
    from core.commerce.merchant_keys import active_key_record
    from core.commerce.oacp_artifacts import DurableOacpArtifactCacheRepository, OacpArtifactCacheRepositoryQuery
    from core.commerce.oacp_demo_merchant import (
        DEMO_ADMIN_EMAIL,
        DEMO_ADMIN_USER_ID,
        DEMO_MERCHANT_ID,
        DEMO_SELLER_AGENT_ID,
        DEMO_TENANT_ID,
        DEMO_TENANT_SLUG,
        is_demo_tenant_settings,
    )
    from core.commerce.oacp_merchant_config import merchant_config_id
    from core.commerce.ucp.catalog import usable_artifact_ids, verified_catalog_source
    from core.commerce.ucp.state import attested_host, ucp_state
    from core.database import get_platform_session, get_tenant_session
    from core.models.commerce_c6z_runtime import (
        C6ZConnectorEvidenceRow,
        C6ZMerchantCommerceConfigRow,
        C6ZSellerOnboardingPacketRow,
    )
    from core.models.tenant import Tenant
    from core.models.user import User

    state = DemoState()
    tenant_uuid = uuid.UUID(DEMO_TENANT_ID)
    # Tenant creation and the cross-tenant name checks are the documented platform-context flows.
    async with get_platform_session() as session:
        by_id = await session.get(Tenant, tenant_uuid)
        by_slug = (await session.execute(select(Tenant.id).where(Tenant.slug == DEMO_TENANT_SLUG))).scalar()
        email_tenants = {
            str(row[0])
            for row in (
                await session.execute(select(User.tenant_id).where(func.lower(User.email) == DEMO_ADMIN_EMAIL))
            ).all()
        }
        settings_value = dict(by_id.settings or {}) if by_id is not None else {}
    if by_slug is not None and str(by_slug) != DEMO_TENANT_ID:
        raise SeedError(EXIT_REFUSED, f"tenant slug {DEMO_TENANT_SLUG!r} belongs to tenant {by_slug}, not this seed's")
    if email_tenants - {DEMO_TENANT_ID}:
        raise SeedError(EXIT_REFUSED, f"{DEMO_ADMIN_EMAIL} already exists in another tenant")
    if by_id is None:
        return state
    state.tenant_exists = True
    state.tenant_settings = settings_value
    state.tenant_marked = is_demo_tenant_settings(settings_value)
    if not state.tenant_marked:
        raise SeedError(EXIT_REFUSED, f"tenant {DEMO_TENANT_ID} exists without the demo marker; refusing to touch it")
    desired = desired_config()
    packet_id = desired_packet_id(desired)
    async with get_tenant_session(tenant_uuid) as session:
        user = await session.get(User, uuid.UUID(DEMO_ADMIN_USER_ID))
        state.admin_exists = user is not None
        state.admin_can_sign_in = bool(user is not None and user.password_hash)
        row = await session.get(
            C6ZMerchantCommerceConfigRow, merchant_config_id(DEMO_TENANT_ID, DEMO_MERCHANT_ID, DEMO_SELLER_AGENT_ID)
        )
        state.packet_exists = await session.get(C6ZSellerOnboardingPacketRow, packet_id) is not None
        evidence_row = (
            await session.scalars(
                select(C6ZConnectorEvidenceRow)
                .where(
                    C6ZConnectorEvidenceRow.tenant_id == DEMO_TENANT_ID,
                    C6ZConnectorEvidenceRow.merchant_id == DEMO_MERCHANT_ID,
                )
                .order_by(C6ZConnectorEvidenceRow.synced_at.desc())
            )
        ).first()
        state.evidence_observed_at = (
            None if evidence_row is None else _iso(evidence_row.source_observed_at.astimezone(UTC))
        )
        records = await DurableOacpArtifactCacheRepository(session).list_for_scope(
            OacpArtifactCacheRepositoryQuery(
                tenant_id=DEMO_TENANT_ID,
                merchant_id=DEMO_MERCHANT_ID,
                seller_agent_id=DEMO_SELLER_AGENT_ID,
                buyer_agent_id=None,
                shared_only=True,
            )
        )
        if row is not None:
            state.config_exists = True
            stored = runtime._row_to_merchant_config(row)
            state.config_converged = all(
                _canonical(stored.get(name)) == _canonical(desired.get(name)) for name in CONFIG_FIELDS
            ) and _canonical(_without_ucp(stored.get("public_publishing"))) == _canonical(
                _without_ucp(desired.get("public_publishing"))
            )
            ucp = ucp_state(row.public_publishing)
            active = active_key_record(ucp.get("signing_keys") or [])
            state.active_kid = None if active is None else str(active.get("kid"))
            discovery = (
                ucp.get("verified_public_discovery") if isinstance(ucp.get("verified_public_discovery"), dict) else {}
            )
            state.ucp = {
                "serving_host": attested_host(ucp),
                "pending_challenge": dict(ucp["challenge"]) if isinstance(ucp.get("challenge"), dict) else None,
                "published": bool(ucp.get("published")),
                "namespace": ucp.get("namespace"),
                "public_discovery_allowed": discovery.get("publication_allowed") is True
                and discovery.get("serving_host") == attested_host(ucp),
            }
    now_iso = _iso(_now())
    for artifact_type in ISSUED_ARTIFACT_TYPES:
        ids = usable_artifact_ids(cache_records=records, now_iso=now_iso, artifact_type=artifact_type)
        if ids:
            state.usable_types[artifact_type] = len(ids)
    state.catalog_available = (
        verified_catalog_source(cache_records=records, products=[], now_iso=now_iso).unavailable_reason is None
    )
    return state


def desired_config() -> dict[str, Any]:
    from api.v1 import commerce_runtime as runtime
    from core.commerce.oacp_demo_merchant import DEMO_MERCHANT_ID, DEMO_TENANT_ID, demo_merchant_config_body

    body = runtime.MerchantCommerceConfigUpsert(**demo_merchant_config_body())
    return runtime._normalize_merchant_config_from_body(
        tenant_id=DEMO_TENANT_ID, merchant_id=DEMO_MERCHANT_ID, body=body
    )


def desired_packet_id(config: Mapping[str, Any]) -> str:
    from api.v1 import commerce_runtime as runtime

    return str(runtime._build_packet_from_merchant_config(config)["packet_id"])


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _without_ucp(value: Any) -> dict[str, Any]:
    return {k: v for k, v in (value or {}).items() if k != "ucp"} if isinstance(value, Mapping) else {}


def needs_issuance(state: DemoState, *, refresh: bool) -> bool:
    return refresh or any(artifact_type not in state.usable_types for artifact_type in ISSUED_ARTIFACT_TYPES)


# --- writing --------------------------------------------------------------------------------


async def ensure_tenant(state: DemoState, report: Report) -> None:
    from core.commerce.oacp_demo_merchant import (
        DEMO_TENANT_ID,
        DEMO_TENANT_NAME,
        DEMO_TENANT_SLUG,
        demo_tenant_settings,
    )
    from core.database import get_platform_session
    from core.models.tenant import Tenant

    now_iso = _iso(_now())
    async with get_platform_session() as session:
        tenant = await session.get(Tenant, uuid.UUID(DEMO_TENANT_ID))
        if tenant is None:
            session.add(
                Tenant(
                    id=uuid.UUID(DEMO_TENANT_ID),
                    name=DEMO_TENANT_NAME,
                    slug=DEMO_TENANT_SLUG,
                    plan="free",
                    data_region="IN",
                    settings=demo_tenant_settings(None, now_iso=now_iso),
                )
            )
            report.actions.append("tenant: created")
            return
        desired = demo_tenant_settings(tenant.settings, now_iso=now_iso)
        if _canonical(desired) != _canonical(tenant.settings or {}) or tenant.name != DEMO_TENANT_NAME:
            tenant.name = DEMO_TENANT_NAME
            tenant.settings = desired
            report.actions.append("tenant: updated")
        else:
            report.actions.append("tenant: unchanged")


async def ensure_admin_user(report: Report, environ: Mapping[str, str]) -> None:
    import bcrypt

    from core.commerce.oacp_demo_merchant import DEMO_ADMIN_EMAIL, DEMO_ADMIN_NAME, DEMO_ADMIN_USER_ID, DEMO_TENANT_ID
    from core.database import get_tenant_session
    from core.models.user import User

    password = environ.get(ADMIN_LOGIN_ENV) or ""
    async with get_tenant_session(uuid.UUID(DEMO_TENANT_ID)) as session:
        user = await session.get(User, uuid.UUID(DEMO_ADMIN_USER_ID))
        if user is None:
            user = User(
                id=uuid.UUID(DEMO_ADMIN_USER_ID),
                tenant_id=uuid.UUID(DEMO_TENANT_ID),
                email=DEMO_ADMIN_EMAIL,
                role="admin",
            )
            session.add(user)
            report.actions.append("admin user: created")
        else:
            report.actions.append("admin user: present")
        user.email, user.name, user.role, user.status = DEMO_ADMIN_EMAIL, DEMO_ADMIN_NAME, "admin", "active"
        if password:
            matches = bool(user.password_hash) and await asyncio.to_thread(
                bcrypt.checkpw, password.encode("utf-8"), str(user.password_hash).encode("utf-8")
            )
            if matches:
                report.admin_login = "unchanged (matches the secret)"
            else:
                user.password_hash = await asyncio.to_thread(
                    lambda: bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")
                )
                report.admin_login = f"set from {ADMIN_LOGIN_ENV} (not shown)"
        else:
            report.admin_login = (
                "unchanged" if user.password_hash else f"not set: login disabled until {ADMIN_LOGIN_ENV} is provided"
            )


async def ensure_merchant_config(state: DemoState, report: Report) -> None:
    from api.v1 import commerce_runtime as runtime
    from core.commerce.oacp_demo_merchant import DEMO_MERCHANT_ID, DEMO_TENANT_ID, demo_merchant_config_body

    if state.config_exists and state.config_converged and state.packet_exists:
        report.actions.append("merchant config: unchanged")
        return
    body = runtime.MerchantCommerceConfigUpsert(**demo_merchant_config_body())
    result = await _ledgered(
        "commerce.runtime.merchant_config.upsert",
        lambda: runtime.upsert_merchant_commerce_config(DEMO_MERCHANT_ID, body, tenant_id=DEMO_TENANT_ID),
    )
    if not result.get("synced_onboarding_packet"):
        raise SeedError(EXIT_FAILED, "the merchant config was saved without its demo onboarding packet")
    report.actions.append("merchant config: saved" if state.config_exists else "merchant config: created")


async def ensure_signing_key(report: Report) -> None:
    from api.v1 import commerce_ucp_publishing as publishing
    from core.commerce.oacp_demo_merchant import DEMO_MERCHANT_ID, DEMO_SELLER_AGENT_ID, DEMO_TENANT_ID

    current = await read_state()
    if current.active_kid:
        report.actions.append("ucp signing key: present")
        return
    await _ledgered(
        "commerce.ucp.keys.provision",
        lambda: publishing.provision_ucp_signing_key(
            DEMO_MERCHANT_ID, publishing.UcpScope(seller_agent_id=DEMO_SELLER_AGENT_ID), tenant_id=DEMO_TENANT_ID
        ),
    )
    report.actions.append("ucp signing key: provisioned")


def dns_record(challenge: Mapping[str, Any]) -> dict[str, str]:
    """The TXT record the operator creates, also as the host label inside the parent zone."""
    name = str(challenge["record_name"])
    labels = name.split(".")
    zone = ".".join(labels[-2:])
    return {
        "name": name,
        "type": "TXT",
        "value": str(challenge["record_value"]),
        "zone": zone,
        "host_in_zone": ".".join(labels[:-2]),
    }


async def request_serving_host(host: str, report: Report) -> None:
    """Step 1 of the public profile: the DNS TXT challenge for an operator-controlled host, printed.

    Reuses a pending challenge for the same host (the record value never changes
    between runs). Nothing is verified here: run ``--verify-host`` once the
    record resolves.
    """
    from api.v1 import commerce_ucp_publishing as publishing
    from core.commerce.oacp_demo_merchant import DEMO_MERCHANT_ID, DEMO_SELLER_AGENT_ID, DEMO_TENANT_ID
    from core.commerce.ucp.authority import normalize_host

    wanted = normalize_host(host)
    if wanted is None:
        raise SeedError(EXIT_REFUSED, "--serving-host must be a registered DNS name (no IP literal, port or path)")
    ucp = (await read_state()).ucp
    if ucp.get("serving_host") == wanted:
        report.actions.append(f"serving host {wanted}: already attested")
        return
    challenge = ucp.get("pending_challenge")
    if challenge and challenge.get("host") == wanted:
        report.dns_record = dns_record(challenge)
        report.actions.append(
            f"serving host {wanted}: DNS challenge pending; create the TXT record, then --verify-host"
        )
        return
    created = await _ledgered(
        "commerce.ucp.serving_host.challenge",
        lambda: publishing.request_serving_host_challenge(
            DEMO_MERCHANT_ID,
            publishing.ServingHostRequest(host=wanted, seller_agent_id=DEMO_SELLER_AGENT_ID),
            _operator_request(),
            tenant_id=DEMO_TENANT_ID,
        ),
    )
    record = created["dns_record"]
    report.dns_record = dns_record({"record_name": record["name"], "record_value": record["value"]})
    report.actions.append(f"serving host {wanted}: DNS challenge created; create the TXT record, then --verify-host")


async def verify_host_and_publish(report: Report) -> bool:
    """Step 2: verify the pending challenge (async DNS lookup), then publish the UCP profile.

    Returns whether the merchant is published on an attested host. A record that
    does not resolve yet is reported, not an error: rerun after propagation.
    """
    from fastapi import HTTPException

    from api.v1 import commerce_ucp_publishing as publishing
    from core.commerce.oacp_demo_merchant import DEMO_MERCHANT_ID, DEMO_SELLER_AGENT_ID, DEMO_TENANT_ID

    state = await read_state()
    if not state.ucp.get("serving_host"):
        challenge = state.ucp.get("pending_challenge")
        if not challenge:
            raise SeedError(EXIT_REFUSED, "--verify-host needs a pending challenge: run --serving-host <host> first")
        report.dns_record = dns_record(challenge)
        try:
            await _ledgered(
                "commerce.ucp.serving_host.verify",
                lambda: publishing.verify_serving_host(
                    DEMO_MERCHANT_ID,
                    publishing.UcpScope(seller_agent_id=DEMO_SELLER_AGENT_ID),
                    _operator_request(),
                    tenant_id=DEMO_TENANT_ID,
                ),
            )
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            reason = exc.detail.get("reason") if isinstance(exc.detail, Mapping) else str(exc.detail)
            report.actions.append(
                f"serving host {challenge.get('host')}: not verified ({reason}); rerun after DNS propagates"
            )
            return False
        report.actions.append(f"serving host {challenge.get('host')}: attested")
        state = await read_state()
    host = state.ucp.get("serving_host")
    if state.ucp.get("published"):
        report.actions.append(f"ucp profile on {host}: already published")
        return True
    result = await _ledgered(
        "commerce.ucp.publish",
        lambda: publishing.publish_ucp_profile(
            DEMO_MERCHANT_ID,
            publishing.PublishRequest(seller_agent_id=DEMO_SELLER_AGENT_ID),
            _operator_request(),
            tenant_id=DEMO_TENANT_ID,
        ),
    )
    if not result.get("published"):
        raise SeedError(EXIT_FAILED, f"UCP publish refused: {result.get('refusal')}")
    report.actions.append(f"ucp profile on {host}: published (namespace {(result.get('ucp') or {}).get('namespace')})")
    return True


def _grantex_error_code(response: Any) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, Mapping):
        return None
    error = body.get("error")
    if isinstance(error, Mapping) and isinstance(error.get("code"), str):
        return str(error["code"])
    for key in ("refusal_code", "code"):
        if isinstance(body.get(key), str):
            return str(body[key])
    return None


def _grantex_refusal(status_code: int, code: str | None) -> SeedError:
    from core.commerce.oacp_demo_merchant import DEMO_TENANT_ID

    if code == "service_tenant_not_allowed":
        return SeedError(
            EXIT_GRANTEX_TENANT_NOT_ALLOWED,
            "Grantex refused the AgenticOrg service token for the demo tenant (service_tenant_not_allowed). "
            f"Add {DEMO_TENANT_ID} to COMMERCE_C6Z_AUTHORITY_SERVICE_TENANTS on Grantex, then rerun.",
        )
    if code == "unsupported_or_enabling_authority_request":
        return SeedError(
            EXIT_GRANTEX_DEMO_SOURCE_UNSUPPORTED,
            "Grantex refused the synthetic_demo connector source (unsupported_or_enabling_authority_request): "
            "this Grantex release issues artifacts for Shopify read-only sources only. Deploy the Grantex "
            "release that accepts the synthetic demo source for the demo tenant, then rerun. Nothing was "
            "presented to Grantex as Shopify.",
        )
    return SeedError(EXIT_FAILED, f"Grantex refused the authority request: HTTP {status_code} {code or 'unknown'}")


async def issue_and_cache_artifacts(report: Report) -> None:
    """Refresh the synthetic catalog evidence, request Grantex authority and cache the verified artifacts."""
    import httpx
    from fastapi import HTTPException

    from api.v1 import commerce_runtime as runtime
    from core.commerce.c6z_runtime_vertical import C6ZRuntimeValidationError
    from core.commerce.artifact_freshness import Scope, prune_scope
    from core.commerce.oacp_demo_merchant import DEMO_MERCHANT_ID, DEMO_SELLER_AGENT_ID, DEMO_TENANT_ID

    packet_id = desired_packet_id(desired_config())
    synced = await _ledgered(
        "commerce.runtime.demo_catalog.sync",
        lambda: runtime.sync_demo_catalog(tenant_id=DEMO_TENANT_ID, packet_id=packet_id),
    )
    try:
        response = await _ledgered(
            "commerce.runtime.grantex.authority_request",
            lambda: runtime.request_grantex_authority_artifacts(
                runtime.GrantexAuthorityRequest(packet_id=packet_id, evidence_id=synced["evidence_id"]),
                tenant_id=DEMO_TENANT_ID,
            ),
        )
    except httpx.HTTPStatusError as exc:
        raise _grantex_refusal(exc.response.status_code, _grantex_error_code(exc.response)) from exc
    except (httpx.HTTPError, C6ZRuntimeValidationError) as exc:
        raise SeedError(EXIT_FAILED, f"Grantex authority request failed: {type(exc).__name__}") from exc
    if response.get("status") == "blocked_missing_grantex_env":
        raise SeedError(EXIT_REFUSED, "missing Grantex settings: " + ", ".join(response.get("missing_env_vars") or []))
    artifacts = response.get("artifacts") or []
    if response.get("status") != "artifact_issuance_ready" or not artifacts:
        code = response.get("refusal_code")
        raise _grantex_refusal(202, str(code) if code else str(response.get("status")))
    try:
        cached = await _ledgered(
            "commerce.runtime.artifacts.cache",
            lambda: runtime.cache_grantex_artifacts(
                runtime.CacheArtifactsRequest(artifacts=artifacts), tenant_id=DEMO_TENANT_ID
            ),
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, Mapping) else {"status": str(exc.detail)}
        codes = sorted(
            {str(item.get("refusal_code")) for item in detail.get("refusals") or [] if isinstance(item, Mapping)}
        )
        raise SeedError(
            EXIT_FAILED,
            f"artifact caching refused: {detail.get('status')} {detail.get('refusal_code') or ''} {codes or ''}".strip(),
        ) from exc
    pruned = await prune_scope(Scope(DEMO_TENANT_ID, DEMO_MERCHANT_ID, DEMO_SELLER_AGENT_ID))
    verification = cached.get("verification") or {}
    report.artifacts = {
        "issued": len(artifacts),
        "stored": int(cached.get("records_stored") or 0),
        "verified": int(verification.get("verified") or 0),
        "verification_mode": verification.get("mode"),
        "refused": len(verification.get("refused") or []),
        "cache_records_pruned": pruned,
        "public_discovery_enabled_by_grantex": response.get("no_public_discovery_enablement") is False,
    }
    report.actions.append(f"artifacts: {len(artifacts)} issued, {report.artifacts['verified']} verified and cached")


# --- teardown --------------------------------------------------------------------------------


async def teardown(*, apply: bool, report: Report) -> dict[str, int]:
    from sqlalchemy import text

    from core.commerce.ledger import record_commerce_event
    from core.commerce.oacp_demo_merchant import (
        DEMO_ADMIN_USER_ID,
        DEMO_MERCHANT_ID,
        DEMO_SELLER_AGENT_ID,
        DEMO_TENANT_ID,
        DEMO_TENANT_MARKER,
        TEARDOWN_TABLES,
        delete_demo_commerce_rows,
        demo_tenant_marked,
    )
    from core.commerce.ucp.store import HOST_INDEX_KEY
    from core.database import get_platform_session, get_tenant_session
    from core.models.tenant import Tenant
    from core.models.user import User

    tenant_uuid = uuid.UUID(DEMO_TENANT_ID)
    async with get_platform_session() as session:
        tenant = await session.get(Tenant, tenant_uuid)
        if tenant is None:
            report.actions.append("teardown: no demo tenant; nothing to do")
            return {}
    async with get_tenant_session(tenant_uuid) as session:
        if not await demo_tenant_marked(session, DEMO_TENANT_ID):
            raise SeedError(EXIT_REFUSED, f"tenant {DEMO_TENANT_ID} has no demo marker; refusing to tear it down")
        if not apply:
            counts = {}
            for table in TEARDOWN_TABLES:
                result = await session.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id::text = :tenant_id"),  # noqa: S608 - constant names
                    {"tenant_id": DEMO_TENANT_ID},
                )
                counts[table] = int(result.scalar() or 0)
            report.actions.append("teardown (dry run): rows that would be deleted listed under deleted_rows")
            return counts
        counts = await delete_demo_commerce_rows(session, DEMO_TENANT_ID)
        user = await session.get(User, uuid.UUID(DEMO_ADMIN_USER_ID))
        if user is not None:
            user.status = "inactive"
            user.sessions_invalid_before = _now()
        await record_commerce_event(
            session,
            tenant_id=DEMO_TENANT_ID,
            event="commerce.runtime.demo_merchant.teardown",
            outcome="success",
            merchant_id=DEMO_MERCHANT_ID,
            seller_agent_id=DEMO_SELLER_AGENT_ID,
            buyer_agent_id=None,
            payload={"deleted_rows": counts},
            actor_type="operator",
            actor_id=OPERATOR_ACTOR_ID,
        )
    async with get_platform_session() as session:
        tenant = await session.get(Tenant, tenant_uuid)
        if tenant is not None:
            settings_value = dict(tenant.settings or {})
            settings_value.pop(HOST_INDEX_KEY, None)
            marker = dict(settings_value.get(DEMO_TENANT_MARKER) or {})
            marker["torn_down_at"] = _iso(_now())
            settings_value[DEMO_TENANT_MARKER] = marker
            tenant.settings = settings_value
    report.actions.append("teardown: commerce rows deleted, serving host unindexed, admin user deactivated")
    return counts


# --- orchestration ---------------------------------------------------------------------------


async def run(args: argparse.Namespace, environ: Mapping[str, str]) -> dict[str, Any]:
    report = Report(mode="apply" if args.apply else "dry_run")
    report.plural_live = _truthy(environ.get("PLURAL_PINE_LIVE_EXECUTION_ENABLED"))
    if args.teardown:
        deleted = await teardown(apply=args.apply, report=report)
        return summary(report, await _maybe_state(), extra={"deleted_rows": deleted, "teardown": True})
    state = await read_state()
    if not args.apply:
        report.actions.extend(plan(state, args))
        return summary(report, state)
    await ensure_tenant(state, report)
    await ensure_admin_user(report, environ)
    state = await read_state()
    await ensure_merchant_config(state, report)
    await ensure_signing_key(report)
    refresh = args.refresh_artifacts
    if args.serving_host:
        await request_serving_host(args.serving_host, report)
    if args.verify_host:
        published_before = bool((await read_state()).ucp.get("published"))
        # A newly published merchant needs new artifacts: the request now carries the opt-in.
        published = await verify_host_and_publish(report)
        refresh = refresh or (published and not published_before)
    state = await read_state()
    if needs_issuance(state, refresh=refresh):
        await issue_and_cache_artifacts(report)
    else:
        report.actions.append("artifacts: every family has a usable verified record; nothing issued")
    return summary(report, await read_state())


async def _maybe_state() -> DemoState | None:
    try:
        return await read_state()
    except SeedError:
        return None


def plan(state: DemoState, args: argparse.Namespace) -> list[str]:
    steps = [
        "tenant: " + ("present" if state.tenant_exists else "would create"),
        "admin user: " + ("present" if state.admin_exists else "would create"),
        "merchant config: "
        + ("unchanged" if state.config_exists and state.config_converged and state.packet_exists else "would save"),
        "ucp signing key: " + ("present" if state.active_kid else "would provision"),
    ]
    if args.serving_host:
        steps.append(f"serving host {args.serving_host}: would request (or reprint) the DNS TXT challenge")
    if args.verify_host:
        steps.append("serving host: would verify the pending TXT challenge and publish the UCP profile")
    steps.append(
        "artifacts: "
        + (
            "would request and cache"
            if needs_issuance(state, refresh=args.refresh_artifacts)
            else "usable; nothing to issue"
        )
    )
    return [f"(dry run) {step}" for step in steps]


def summary(report: Report, state: DemoState | None, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from core.commerce.oacp_demo_merchant import (
        DEMO_ADMIN_EMAIL,
        DEMO_MERCHANT_ID,
        DEMO_SELLER_AGENT_ID,
        DEMO_TENANT_ID,
        DEMO_TENANT_SLUG,
    )
    from core.config import oacp_verify_strict_enabled, settings

    out: dict[str, Any] = {
        "mode": report.mode,
        "tenant_id": DEMO_TENANT_ID,
        "tenant_slug": DEMO_TENANT_SLUG,
        "merchant_id": DEMO_MERCHANT_ID,
        "seller_agent_id": DEMO_SELLER_AGENT_ID,
        "admin_user": {"email": DEMO_ADMIN_EMAIL, "login": report.admin_login},
        "grantex_allowlist": f"COMMERCE_C6Z_AUTHORITY_SERVICE_TENANTS must include {DEMO_TENANT_ID}",
        "actions": report.actions,
        "artifacts_this_run": report.artifacts,
        "synthetic_data": True,
        "enabled_in_this_job": {
            "AGENTICORG_OACP_DEMO_MERCHANT_ENABLED": bool(getattr(settings, "oacp_demo_merchant_enabled", False)),
            "AGENTICORG_UCP_PROFILE_ENABLED": bool(getattr(settings, "ucp_profile_enabled", False)),
            "AGENTICORG_OACP_VERIFY_STRICT": oacp_verify_strict_enabled(),
            "PLURAL_PINE_LIVE_EXECUTION_ENABLED": report.plural_live,
            "payment_executor_for_demo_merchant": None,
        },
    }
    if state is not None:
        out["state"] = {
            "tenant_exists": state.tenant_exists,
            "admin_user_exists": state.admin_exists,
            "merchant_config_exists": state.config_exists,
            "merchant_config_converged": state.config_converged,
            "onboarding_packet_exists": state.packet_exists,
            "merchant_signing_key_kid": state.active_kid,
            "catalog_evidence_observed_at": state.evidence_observed_at,
            "usable_verified_artifacts_now": state.usable_types,
            "catalog_available_now": state.catalog_available,
            "ucp": {k: v for k, v in state.ucp.items() if k != "pending_challenge"},
        }
    if report.dns_record:
        out["dns_record_to_create"] = report.dns_record
    if extra:
        out.update(extra)
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create, converge or tear down the synthetic OACP demo merchant.")
    parser.add_argument("--apply", action="store_true", help="write (without it the run is a dry run)")
    parser.add_argument("--teardown", action="store_true", help="delete the demo tenant's commerce data")
    parser.add_argument(
        "--serving-host", help="an operator-controlled DNS name to attest and publish the UCP profile on"
    )
    parser.add_argument(
        "--verify-host",
        action="store_true",
        help="verify the pending serving-host TXT challenge, publish the UCP profile and refresh the artifacts",
    )
    parser.add_argument(
        "--refresh-artifacts", action="store_true", help="request new artifacts even if usable ones exist"
    )
    args = parser.parse_args(argv)
    if args.teardown and (args.serving_host or args.verify_host or args.refresh_artifacts):
        parser.error("--teardown takes only --apply")
    return args


async def _main_async(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    from core.database import engine

    try:
        result = await run(args, environ)
    finally:
        await engine.dispose()
    # One JSON line: Cloud Run job logs keep it as one structured entry.
    print(json.dumps({"summary": result}, sort_keys=True, default=str), flush=True)
    return EXIT_OK


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    args = parse_args(argv)
    source = os.environ if environ is None else environ
    blockers = check_preconditions(apply=args.apply, teardown=args.teardown, environ=source)
    if blockers:
        print(json.dumps({"status": "refused", "reasons": blockers}), file=sys.stderr)
        return EXIT_REFUSED
    try:
        return asyncio.run(_main_async(args, source))
    except SeedError as exc:
        print(json.dumps({"status": "stopped", "exit_code": exc.exit_code, "reason": str(exc)}), file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
