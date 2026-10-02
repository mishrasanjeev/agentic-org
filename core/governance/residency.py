# SPDX-License-Identifier: Apache-2.0
"""Residency enforcement: keep processing inside the configured data region.

A tenant's ``data_region`` (governance config: IN, EU or US) selects where its data
may be processed. With enforcement on, the platform refuses any AI or integration
provider that has no active residency attestation for that region, where an
attestation is an administrator's record (``core.models.provider_attestation``)
that the provider processes inside the region and has committed not to train on
the institution's data. Providers that run inside the deployment itself (local
inference, local embeddings, local speech engines) need no attestation.

Enforcement points: the AI credential resolver (every LLM, embedding, retrieval,
speech credential passes through it), the managed retrieval service upload, the
third-party tool hub adapter, and tracing export. The compliance report gains a
``data_residency`` section: region, storage region conformance, tenancy profile,
disaster-recovery profile and the active attestations.

Behaviour is off until ``residency.enforce`` is on for the tenant (an authority
flag, operator-managed) or ``AGENTICORG_RESIDENCY_ENFORCE`` is set. With it off
``check_provider`` always allows and reads nothing.

Fail-closed rules with enforcement on: a tenant region or attestation list that
cannot be read blocks the provider in a strict runtime and allows it (logged) in
a relaxed one.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from core.config import is_strict_runtime_env, settings

logger = structlog.get_logger()

FLAG_KEY = "residency.enforce"
DATA_REGIONS: tuple[str, ...] = ("IN", "EU", "US")

# Cloud regions that sit inside each data region. Used to judge whether the
# configured storage region (and a disaster-recovery standby) conform.
REGION_CLOUD_REGIONS: dict[str, frozenset[str]] = {
    "IN": frozenset(
        {"asia-south1", "asia-south2", "ap-south-1", "ap-south-2", "centralindia", "southindia", "westindia"}
    ),
    "EU": frozenset(
        {
            "europe-west1",
            "europe-west2",
            "europe-west3",
            "europe-west4",
            "europe-west6",
            "europe-west8",
            "europe-west9",
            "europe-north1",
            "europe-central2",
            "europe-southwest1",
            "eu-west-1",
            "eu-west-2",
            "eu-west-3",
            "eu-central-1",
            "eu-central-2",
            "eu-north-1",
            "eu-south-1",
            "eu-south-2",
            "westeurope",
            "northeurope",
            "francecentral",
            "germanywestcentral",
            "swedencentral",
        }
    ),
    "US": frozenset(
        {
            "us-central1",
            "us-east1",
            "us-east4",
            "us-east5",
            "us-west1",
            "us-west2",
            "us-west3",
            "us-west4",
            "us-south1",
            "us-east-1",
            "us-east-2",
            "us-west-1",
            "us-west-2",
            "eastus",
            "eastus2",
            "westus",
            "westus2",
            "westus3",
            "centralus",
            "southcentralus",
            "northcentralus",
        }
    ),
}

# Providers that run inside the deployment: their region is the deployment's.
LOCAL_PROVIDERS: frozenset[str] = frozenset(
    {"ollama", "vllm", "local", "local_embeddings", "tei", "faster_whisper", "whisper_local", "piper", "piper_local"}
)

_REGION_CACHE_TTL_S = 60.0
_region_cache: dict[str, tuple[str, float]] = {}
_attestation_cache: dict[str, tuple[list[Attestation], float]] = {}


class ResidencyBlocked(RuntimeError):  # noqa: N818 - surface name used in error payloads
    """Raised where a provider or destination outside the data region is refused."""

    def __init__(self, decision: ResidencyDecision) -> None:
        self.decision = decision
        super().__init__(decision.reason)


@dataclass(frozen=True)
class Attestation:
    id: str
    provider: str
    data_region: str
    in_region: bool
    no_training: bool
    evidence_ref: str
    attested_by: str
    expires_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResidencyDecision:
    blocked: bool
    reason: str = ""
    provider: str = ""
    data_region: str = ""
    attestation: Attestation | None = None

    def to_error(self) -> dict[str, Any]:
        return {
            "error": {"code": "E4006", "message": self.reason},
            "residency": {"provider": self.provider, "data_region": self.data_region},
        }


ALLOWED = ResidencyDecision(blocked=False)


def _as_uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    if value in (None, "", "__platform__"):
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def normalise_region(value: str | None) -> str:
    return (value or "").strip().upper()


def cloud_region_conforms(data_region: str, cloud_region: str | None) -> bool | None:
    """Whether ``cloud_region`` lies inside ``data_region``; None when unknown to the table."""
    region = normalise_region(data_region)
    cloud = (cloud_region or "").strip().lower()
    if not cloud or region not in REGION_CLOUD_REGIONS:
        return None
    for known, members in REGION_CLOUD_REGIONS.items():
        if cloud in members:
            return known == region
    return None


async def enabled(tenant_id: uuid.UUID | str | None) -> bool:
    """Whether enforcement is on for ``tenant_id`` (settings switch or authority flag)."""
    if settings.residency_enforce:
        return True
    tid = _as_uuid(tenant_id)
    if tid is None:
        return False
    from core.feature_flags import is_enabled

    return await is_enabled(FLAG_KEY, tenant_id=tid, default=False)


async def _load_region(tenant_id: uuid.UUID) -> str:
    from core.database import get_tenant_session
    from core.models.governance_config import GovernanceConfig

    async with get_tenant_session(tenant_id) as session:
        row = await session.get(GovernanceConfig, tenant_id)
        return normalise_region(row.data_region if row is not None else settings.data_region)


async def tenant_data_region(tenant_id: uuid.UUID | str | None) -> str:
    """The tenant's data region (governance config), cached; the platform default without a tenant."""
    tid = _as_uuid(tenant_id)
    if tid is None:
        return normalise_region(settings.data_region)
    key = str(tid)
    cached = _region_cache.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[1] < _REGION_CACHE_TTL_S:
        return cached[0]
    region = await _load_region(tid)
    _region_cache[key] = (region, now)
    return region


async def _load_attestations(tenant_id: uuid.UUID) -> list[Attestation]:
    from sqlalchemy import or_, select

    from core.database import get_tenant_session
    from core.models.provider_attestation import ProviderAttestation

    now = datetime.now(UTC)
    async with get_tenant_session(tenant_id) as session:
        rows = (
            await session.execute(
                select(ProviderAttestation).where(
                    ProviderAttestation.tenant_id == tenant_id,
                    ProviderAttestation.revoked_at.is_(None),
                    or_(ProviderAttestation.expires_at.is_(None), ProviderAttestation.expires_at > now),
                )
            )
        ).scalars()
        return [_attestation(row) for row in rows]


def _attestation(row: Any) -> Attestation:
    return Attestation(
        id=str(row.id),
        provider=row.provider,
        data_region=normalise_region(row.data_region),
        in_region=bool(row.in_region),
        no_training=bool(row.no_training),
        evidence_ref=row.evidence_ref or "",
        attested_by=row.attested_by,
        expires_at=row.expires_at.isoformat() if row.expires_at else None,
    )


async def active_attestations(tenant_id: uuid.UUID) -> list[Attestation]:
    key = str(tenant_id)
    cached = _attestation_cache.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[1] < _REGION_CACHE_TTL_S:
        return cached[0]
    rows = await _load_attestations(tenant_id)
    _attestation_cache[key] = (rows, now)
    return rows


def invalidate(tenant_id: uuid.UUID | None = None) -> None:
    """Drop cached regions and attestations (all tenants when ``tenant_id`` is None)."""
    if tenant_id is None:
        _region_cache.clear()
        _attestation_cache.clear()
        return
    _region_cache.pop(str(tenant_id), None)
    _attestation_cache.pop(str(tenant_id), None)


def is_local_provider(provider: str) -> bool:
    key = (provider or "").strip().lower()
    return key in LOCAL_PROVIDERS or key.startswith(("ollama", "vllm", "local"))


def _fail(provider: str, region: str, reason: str) -> ResidencyDecision:
    _meter(provider, "unavailable")
    return ResidencyDecision(blocked=True, reason=reason, provider=provider, data_region=region)


async def check_provider(tenant_id: uuid.UUID | str | None, provider: str, *, kind: str = "llm") -> ResidencyDecision:
    """Decide whether ``provider`` may process the tenant's data under residency enforcement.

    Allowed when enforcement is off, when the provider runs inside the deployment,
    or when an active attestation for the tenant's region says the provider stays
    in region and does not train on the data. Blocked otherwise.
    """
    key = (provider or "").strip().lower()
    if not key:
        return ALLOWED
    tid = _as_uuid(tenant_id)
    if not await enabled(tid):
        return ALLOWED
    if is_local_provider(key):
        return ALLOWED
    strict = is_strict_runtime_env(settings.env)
    try:
        region = await tenant_data_region(tid)
    # enterprise-gate: broad-except-ok reason=region-read-failure-fails-closed-in-strict-runtime
    except Exception as exc:
        logger.error("residency_region_read_failed", provider=key, error_type=type(exc).__name__)
        if strict:
            return _fail(key, "", "Residency: the tenant's data region could not be read; refusing the provider.")
        return ALLOWED
    if tid is None:
        attestations: list[Attestation] = []
    else:
        try:
            attestations = await active_attestations(tid)
        # enterprise-gate: broad-except-ok reason=attestation-read-failure-fails-closed-in-strict-runtime
        except Exception as exc:
            logger.error("residency_attestations_read_failed", provider=key, error_type=type(exc).__name__)
            if strict:
                return _fail(key, region, "Residency: provider attestations could not be read; refusing the provider.")
            return ALLOWED
    for a in attestations:
        if a.provider.lower() == key and a.data_region == region and a.in_region and a.no_training:
            return ResidencyDecision(blocked=False, provider=key, data_region=region, attestation=a)
    partial = [a for a in attestations if a.provider.lower() == key and a.data_region == region]
    if partial:
        missing = []
        if not partial[0].in_region:
            missing.append("in-region processing")
        if not partial[0].no_training:
            missing.append("a no-training commitment")
        reason = f"Residency: provider {key} ({kind}) is attested for {region} without {' or '.join(missing)}."
    else:
        reason = f"Residency: provider {key} ({kind}) has no active attestation for data region {region}."
    _meter(key, "no_attestation")
    logger.warning("residency_provider_refused", provider=key, kind=kind, data_region=region)
    return ResidencyDecision(blocked=True, reason=reason, provider=key, data_region=region)


async def assert_provider_allowed(tenant_id: uuid.UUID | str | None, provider: str, *, kind: str = "llm") -> None:
    decision = await check_provider(tenant_id, provider, kind=kind)
    if decision.blocked:
        raise ResidencyBlocked(decision)


def _meter(provider: str, reason: str) -> None:
    try:
        from observability.metrics import residency_refusals_total

        residency_refusals_total.labels(reason=reason).inc()
    # enterprise-gate: broad-except-ok reason=metrics-never-change-an-enforcement-decision
    except Exception:
        logger.debug("residency_metric_unavailable", provider=provider)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def deployment_profile() -> dict[str, Any]:
    """Deployment facts the operator records in settings: storage, tenancy and disaster recovery."""
    region = normalise_region(settings.data_region)
    return {
        "platform_data_region": region,
        "storage_region": settings.storage_region,
        "storage_region_conforms": cloud_region_conforms(region, settings.storage_region),
        "tenancy_profile": settings.tenancy_profile,
        "disaster_recovery": {
            "standby_region": settings.dr_standby_region,
            "standby_conforms": cloud_region_conforms(region, settings.dr_standby_region),
            "last_drill_at": settings.dr_last_drill_at,
            "status": "active" if settings.dr_standby_region else "not_configured",
        },
    }


async def report_section(tenant_id: uuid.UUID) -> dict[str, Any]:
    """The ``data_residency`` section of the compliance report; never raises."""
    section: dict[str, Any] = {"control_id": "RES-1", "status": "collected", **deployment_profile()}
    try:
        section["enforcement"] = "on" if await enabled(tenant_id) else "off"
        region = await tenant_data_region(tenant_id)
        section["data_region"] = region
        section["storage_region_conforms"] = cloud_region_conforms(region, settings.storage_region)
        attestations = await active_attestations(tenant_id)
        section["provider_attestations"] = [a.to_dict() for a in attestations if a.data_region == region]
    # enterprise-gate: broad-except-ok reason=report-section-reports-unavailable-rather-than-failing-the-package
    except Exception as exc:
        logger.warning("residency_report_unavailable", error_type=type(exc).__name__)
        section["status"] = "unavailable"
    return section


# ---------------------------------------------------------------------------
# Changes (used by the admin API)
# ---------------------------------------------------------------------------


def _audit_entry(
    tenant_id: uuid.UUID, *, actor_id: str, action: str, attestation_id: str, details: dict[str, Any]
) -> Any:
    from core.models.audit import AuditLog
    from core.tool_gateway.audit_logger import sign_audit_record

    entry: dict[str, Any] = {
        "tenant_id": tenant_id,
        "event_type": f"residency_attestation.{action}",
        "actor_type": "user",
        "actor_id": actor_id,
        "agent_id": None,
        "workflow_run_id": None,
        "resource_type": "provider_attestation",
        "resource_id": attestation_id,
        "action": action,
        "outcome": "success",
        "details": details,
        "trace_id": "",
        "created_at": datetime.now(UTC),
    }
    entry["signature"] = sign_audit_record(entry, settings.secret_key.encode())
    return AuditLog(**entry)


async def set_attestation(
    tenant_id: uuid.UUID,
    *,
    provider: str,
    data_region: str,
    in_region: bool,
    no_training: bool,
    evidence_ref: str,
    actor_id: str,
    expires_at: datetime | None,
) -> Attestation:
    """Record an attestation and its audit row in the same transaction."""
    from core.database import get_tenant_session
    from core.models.provider_attestation import ProviderAttestation

    key = (provider or "").strip().lower()
    region = normalise_region(data_region)
    if not key:
        raise ValueError("provider is required")
    if region not in DATA_REGIONS:
        raise ValueError(f"data_region must be one of {', '.join(DATA_REGIONS)}")
    row = ProviderAttestation(
        tenant_id=tenant_id,
        provider=key,
        data_region=region,
        in_region=in_region,
        no_training=no_training,
        evidence_ref=(evidence_ref or "").strip(),
        attested_by=actor_id,
        expires_at=expires_at,
    )
    async with get_tenant_session(tenant_id) as session:
        session.add(row)
        await session.flush()
        session.add(
            _audit_entry(
                tenant_id,
                actor_id=actor_id,
                action="set",
                attestation_id=str(row.id),
                details={
                    "provider": key,
                    "data_region": region,
                    "in_region": in_region,
                    "no_training": no_training,
                    "evidence_ref": row.evidence_ref,
                    "expires_at": expires_at.isoformat() if expires_at else None,
                },
            )
        )
        attestation = _attestation(row)
    invalidate(tenant_id)
    logger.warning("residency_attestation_set", tenant_id=str(tenant_id), **attestation.to_dict(), actor_id=actor_id)
    return attestation


async def revoke_attestation(tenant_id: uuid.UUID, attestation_id: uuid.UUID, *, actor_id: str) -> Attestation | None:
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.provider_attestation import ProviderAttestation

    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(ProviderAttestation).where(
                    ProviderAttestation.id == attestation_id,
                    ProviderAttestation.tenant_id == tenant_id,
                    ProviderAttestation.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        row.revoked_at = datetime.now(UTC)
        row.revoked_by = actor_id
        session.add(
            _audit_entry(
                tenant_id,
                actor_id=actor_id,
                action="revoked",
                attestation_id=str(row.id),
                details={"provider": row.provider, "data_region": row.data_region},
            )
        )
        attestation = _attestation(row)
    invalidate(tenant_id)
    logger.warning(
        "residency_attestation_revoked", tenant_id=str(tenant_id), attestation_id=str(attestation_id), actor_id=actor_id
    )
    return attestation
