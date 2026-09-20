# SPDX-License-Identifier: Apache-2.0
"""Portable case hand-off: a signed webhook backed by an outbox, plus REST retrieval.

**Outbox.** When a case reaches ``awaiting_decision`` (``case.completed``, or ``case.updated`` on
re-investigation), ``decided`` (``case.decided``) or changes in a way a receiver should know
(``case.updated``), a ``case_push`` document is written to ``case_push_outbox`` in the same
transaction as the change. Delivery happens afterwards, so an unreachable receiver never loses or
blocks the case. A payload that does not validate against ``case_push`` is recorded dead-lettered
(``payload_invalid``) rather than stopping the case.

**Delivery.** :class:`CasePushDispatcher` leases due rows (``FOR UPDATE SKIP LOCKED``), posts each
with no redirects and a timeout, and records the outcome: 2xx delivered; 408, 425, 429, 5xx and
transport errors retried with exponential backoff (2 s doubling to 15 min, deterministic jitter);
any other 4xx, or ``max_attempts`` attempts, dead-lettered. A crashed worker's lease expires and the
row is retried: delivery is at least once, and receivers de-duplicate on the event id.

**Signing.** Every delivery carries::

    AgenticOrg-Event-Id: <uuid>
    AgenticOrg-Event-Type: case.completed
    AgenticOrg-Timestamp: <unix seconds>
    AgenticOrg-Signature: v1=<key_id>:<hex HMAC-SHA256>[, v1=<key_id>:<hex>]

over ``"<event id>.<timestamp>." + body``. It is signed with every key on the endpoint (the active
key first), so a receiver can move to a new key before the old one is retired. See
:func:`verify_signature` and ``docs/governance/case-hand-off.md``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.cases.states import CaseError, CaseState
from core.domain_schemas import DomainSchemaError, validate
from core.models.case_push import CasePushEndpoint, CasePushOutbox
from core.models.governed_case import GovernedCase

logger = structlog.get_logger()

EVENT_ID_HEADER = "AgenticOrg-Event-Id"
EVENT_TYPE_HEADER = "AgenticOrg-Event-Type"
TIMESTAMP_HEADER = "AgenticOrg-Timestamp"
SIGNATURE_HEADER = "AgenticOrg-Signature"
SIGNATURE_SCHEME = "v1"
DEFAULT_TOLERANCE_SECONDS = 300
MAX_KEYS = 2
#: Namespace for deterministic event ids: the same case version and event type always has the same id.
EVENT_NAMESPACE = uuid.UUID("5d0f6a0e-3c1b-4f7e-9a53-2b8f4c6d1e70")
RETRYABLE_STATUS = frozenset({408, 425, 429})

push_enqueued_total = Counter(
    "agenticorg_case_push_enqueued_total", "Case push events written to the outbox, by event type", ["event_type"]
)
push_deliveries_total = Counter(
    "agenticorg_case_push_deliveries_total",
    "Case push delivery attempts, by outcome (delivered, retry_scheduled, dead_lettered)",
    ["outcome"],
)
push_dead_letters_total = Counter(
    "agenticorg_case_push_dead_letters_total", "Case push events dead-lettered, by reason", ["reason"]
)
push_dead_letter_backlog = Gauge(
    "agenticorg_case_push_dead_letter_backlog", "Dead-lettered case push events seen by the last dispatcher sweep"
)
push_attempt_seconds = Histogram("agenticorg_case_push_attempt_duration_seconds", "Case push delivery attempt latency")


def _utc_now() -> datetime:
    return datetime.now(UTC)


# --- signing ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SigningKey:
    key_id: str
    secret: str = field(repr=False)
    created_at: str = ""


def _digest(secret: str, event_id: str, timestamp: int, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), f"{event_id}.{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def sign(body: bytes, *, event_id: str, event_type: str, timestamp: int, keys: Sequence[SigningKey]) -> dict[str, str]:
    if not keys:
        raise ValueError("at least one signing key is required")
    signatures = ", ".join(
        f"{SIGNATURE_SCHEME}={key.key_id}:{_digest(key.secret, event_id, timestamp, body)}" for key in keys
    )
    return {
        EVENT_ID_HEADER: event_id,
        EVENT_TYPE_HEADER: event_type,
        TIMESTAMP_HEADER: str(timestamp),
        SIGNATURE_HEADER: signatures,
        "Content-Type": "application/json",
    }


def verify_signature(
    headers: Mapping[str, str],
    body: bytes,
    *,
    keys: Mapping[str, str],
    now: int,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
) -> str:
    """Receiver-side check. Returns ``""`` when valid, otherwise a reason code; never raises.

    Reasons: ``signature_missing``, ``timestamp_invalid``, ``timestamp_outside_tolerance``,
    ``signature_mismatch``. ``keys`` maps key id to secret. De-duplicate on the event id separately.
    """
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    event_id = lowered.get(EVENT_ID_HEADER.lower(), "")
    raw_timestamp = lowered.get(TIMESTAMP_HEADER.lower(), "")
    raw_signature = lowered.get(SIGNATURE_HEADER.lower(), "")
    if not event_id or not raw_signature:
        return "signature_missing"
    if not (raw_timestamp.isascii() and raw_timestamp.isdigit()) or len(raw_timestamp) > 12:
        return "timestamp_invalid"
    timestamp = int(raw_timestamp)
    if abs(now - timestamp) > tolerance_seconds:
        return "timestamp_outside_tolerance"
    for part in raw_signature.split(","):
        scheme, _, rest = part.strip().partition("=")
        key_id, _, offered = rest.partition(":")
        secret = keys.get(key_id)
        if scheme != SIGNATURE_SCHEME or not secret or not offered:
            continue
        if hmac.compare_digest(offered.encode(), _digest(secret, event_id, timestamp, bytes(body)).encode()):
            return ""
    return "signature_mismatch"


def payload_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# --- payload ------------------------------------------------------------------------------------


class PushPayloadError(ValueError):
    pass


def event_id_for(case: GovernedCase, event_type: str) -> uuid.UUID:
    return uuid.uuid5(EVENT_NAMESPACE, f"{case.id}:{case.version}:{event_type}")


def event_type_for(case: GovernedCase, target: CaseState) -> str | None:
    if target is CaseState.AWAITING_DECISION:
        investigations = sum(1 for r in case.agent_records or [] if r.get("agent") == "business_underwriter")
        return "case.completed" if investigations <= 1 else "case.updated"
    if target is CaseState.DECIDED:
        return "case.decided"
    if target in (CaseState.WITHDRAWN, CaseState.FAILED):
        return "case.updated"
    return None


def build_case_push(
    case: GovernedCase, *, event_type: str, event_id: uuid.UUID, occurred_at: datetime
) -> dict[str, Any]:
    """The ``case_push`` document for the case as it is now; :class:`PushPayloadError` if it does not validate."""
    from core.cases.store import business_case_document

    document = {
        "schema_version": "1.0.0",
        "event_id": str(event_id),
        "event_type": event_type,
        "occurred_at": occurred_at.isoformat(),
        "case": business_case_document(case),
        "memo": case.memo,
        "screening_results": list(case.screening_results or []),
        "screening_dispositions": list(case.screening_dispositions or []),
        "ownership_graph": case.ownership_graph,
    }
    try:
        validate("case_push", document)
    except DomainSchemaError as exc:
        raise PushPayloadError("; ".join(exc.errors[:3])) from exc
    return document


def snapshot_event_type(case: GovernedCase) -> str:
    if case.state == CaseState.DECIDED:
        return "case.decided"
    if case.state == CaseState.AWAITING_DECISION:
        return event_type_for(case, CaseState.AWAITING_DECISION) or "case.updated"
    return "case.updated"


# --- outbox -------------------------------------------------------------------------------------


async def _endpoint(session: AsyncSession, tenant_id: uuid.UUID) -> CasePushEndpoint | None:
    return (
        await session.execute(select(CasePushEndpoint).where(CasePushEndpoint.tenant_id == tenant_id))
    ).scalar_one_or_none()


async def enqueue(
    session: AsyncSession, case: GovernedCase, event_type: str, *, now: datetime
) -> CasePushOutbox | None:
    """Write the event to the outbox when the tenant has an enabled endpoint. Same transaction as the change."""
    endpoint = await _endpoint(session, case.tenant_id)
    if endpoint is None or not endpoint.enabled:
        return None
    event_id = event_id_for(case, event_type)
    existing = (
        await session.execute(
            select(CasePushOutbox).where(
                CasePushOutbox.tenant_id == case.tenant_id, CasePushOutbox.event_id == event_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = CasePushOutbox(
        id=uuid.uuid4(),
        tenant_id=case.tenant_id,
        case_id=case.id,
        event_id=event_id,
        event_type=event_type,
        status="pending",
        attempts=0,
        next_attempt_at=now,
        replay_count=0,
        created_at=now,
        updated_at=now,
    )
    try:
        row.payload = build_case_push(case, event_type=event_type, event_id=event_id, occurred_at=now)
    except PushPayloadError as exc:
        logger.error("case_push_payload_invalid", case_ref=case.case_ref, event_type=event_type, detail=str(exc)[:300])
        row.payload = {"event_id": str(event_id), "event_type": event_type, "case_id": case.case_ref}
        row.status = "dead_lettered"
        row.last_error = "payload_invalid"
        row.dead_lettered_at = now
        push_dead_letters_total.labels(reason="payload_invalid").inc()
    row.payload_sha256 = "sha256:" + hashlib.sha256(payload_bytes(row.payload)).hexdigest()
    session.add(row)
    push_enqueued_total.labels(event_type=event_type).inc()
    return row


async def enqueue_for_transition(session: AsyncSession, case: GovernedCase, target: CaseState, at: datetime) -> None:
    event_type = event_type_for(case, target)
    if event_type is not None:
        await enqueue(session, case, event_type, now=at)


# --- endpoint configuration ----------------------------------------------------------------------


def validate_endpoint_url(url: str) -> str:
    """HTTPS to a public host in strict runtimes; relaxed runtimes also allow http and skip DNS checks."""
    from core.config import is_strict_runtime_env, settings
    from core.security.egress import EgressValidationError, validate_public_url

    strict = is_strict_runtime_env(settings.env)
    try:
        validated = validate_public_url(
            url, allowed_schemes=("https",) if strict else ("https", "http"), require_dns=strict
        )
    except EgressValidationError as exc:
        raise CaseError("endpoint_url_invalid", exc.reason, status=422) from exc
    return validated.url


def _new_key(now: datetime) -> SigningKey:
    return SigningKey(key_id=f"k_{secrets.token_hex(8)}", secret=secrets.token_urlsafe(32), created_at=now.isoformat())


async def _encrypt_keys(tenant_id: uuid.UUID, keys: Sequence[SigningKey]) -> dict[str, str]:
    from core.crypto.tenant_secrets import encrypt_with_kek, resolve_tenant_kek

    plaintext = json.dumps([{"key_id": k.key_id, "secret": k.secret, "created_at": k.created_at} for k in keys])
    kek = await resolve_tenant_kek(tenant_id)
    return {"_encrypted": await asyncio.to_thread(encrypt_with_kek, plaintext, kek)}


async def decrypt_keys(endpoint: CasePushEndpoint) -> list[SigningKey]:
    from core.crypto.tenant_secrets import decrypt_for_tenant

    stored = (endpoint.signing_keys_encrypted or {}).get("_encrypted")
    if not isinstance(stored, str) or not stored:
        raise CaseError("signing_keys_unreadable", status=500)
    try:
        items = json.loads(await asyncio.to_thread(decrypt_for_tenant, stored))
        keys = [SigningKey(str(i["key_id"]), str(i["secret"]), str(i.get("created_at") or "")) for i in items]
    except (ValueError, KeyError, TypeError) as exc:
        raise CaseError("signing_keys_unreadable", status=500) from exc
    if not keys:
        raise CaseError("signing_keys_unreadable", status=500)
    return keys


def endpoint_view(endpoint: CasePushEndpoint | None, key_ids: Sequence[str] = ()) -> dict[str, Any]:
    if endpoint is None:
        return {"configured": False}
    return {
        "configured": True,
        "url": endpoint.url,
        "enabled": endpoint.enabled,
        "active_key_id": endpoint.active_key_id,
        "key_ids": list(key_ids),
    }


async def configure_endpoint(
    session: AsyncSession, tenant_id: uuid.UUID, *, url: str, enabled: bool, now: datetime
) -> tuple[CasePushEndpoint, SigningKey | None]:
    """Create or update the tenant's endpoint. A new endpoint gets a signing key, returned once."""
    checked = validate_endpoint_url(url)
    endpoint = await _endpoint(session, tenant_id)
    if endpoint is not None:
        endpoint.url = checked
        endpoint.enabled = enabled
        endpoint.updated_at = now
        return endpoint, None
    key = _new_key(now)
    endpoint = CasePushEndpoint(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        url=checked,
        enabled=enabled,
        signing_keys_encrypted=await _encrypt_keys(tenant_id, [key]),
        active_key_id=key.key_id,
        created_at=now,
        updated_at=now,
    )
    session.add(endpoint)
    return endpoint, key


async def rotate_signing_key(session: AsyncSession, tenant_id: uuid.UUID, *, now: datetime) -> SigningKey:
    """Add a new active key; the previous one keeps signing until retired. At most two keys are kept."""
    endpoint = await _endpoint(session, tenant_id)
    if endpoint is None:
        raise CaseError("endpoint_not_configured", status=404)
    keys = await decrypt_keys(endpoint)
    key = _new_key(now)
    endpoint.signing_keys_encrypted = await _encrypt_keys(tenant_id, [key, *keys][:MAX_KEYS])
    endpoint.active_key_id = key.key_id
    endpoint.updated_at = now
    return key


async def retire_previous_keys(session: AsyncSession, tenant_id: uuid.UUID, *, now: datetime) -> list[str]:
    endpoint = await _endpoint(session, tenant_id)
    if endpoint is None:
        raise CaseError("endpoint_not_configured", status=404)
    keys = [k for k in await decrypt_keys(endpoint) if k.key_id == endpoint.active_key_id]
    if not keys:
        raise CaseError("signing_keys_unreadable", status=500)
    endpoint.signing_keys_encrypted = await _encrypt_keys(tenant_id, keys)
    endpoint.updated_at = now
    return [k.key_id for k in keys]


# --- dead letters -------------------------------------------------------------------------------


async def list_dead_letters(session: AsyncSession, tenant_id: uuid.UUID, *, limit: int = 100) -> list[CasePushOutbox]:
    rows = await session.execute(
        select(CasePushOutbox)
        .where(CasePushOutbox.tenant_id == tenant_id, CasePushOutbox.status == "dead_lettered")
        .order_by(CasePushOutbox.dead_lettered_at.desc())
        .limit(max(1, min(limit, 500)))
    )
    return list(rows.scalars())


async def replay_dead_letter(
    session: AsyncSession, tenant_id: uuid.UUID, outbox_id: uuid.UUID, *, actor: str, now: datetime
) -> CasePushOutbox:
    """Queue a dead-lettered event again with the same event id, so receivers still de-duplicate it."""
    row = (
        await session.execute(
            select(CasePushOutbox)
            .where(CasePushOutbox.tenant_id == tenant_id, CasePushOutbox.id == outbox_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise CaseError("dead_letter_not_found", status=404)
    if row.status != "dead_lettered":
        raise CaseError("dead_letter_not_replayable", row.status)
    if row.last_error == "payload_invalid":
        case = (
            await session.execute(
                select(GovernedCase).where(GovernedCase.tenant_id == tenant_id, GovernedCase.id == row.case_id)
            )
        ).scalar_one()
        try:
            row.payload = build_case_push(case, event_type=row.event_type, event_id=row.event_id, occurred_at=now)
        except PushPayloadError as exc:
            raise CaseError("payload_invalid", status=422) from exc
        row.payload_sha256 = "sha256:" + hashlib.sha256(payload_bytes(row.payload)).hexdigest()
    row.status = "pending"
    row.attempts = 0
    row.next_attempt_at = now
    row.last_error = None
    row.last_status_code = None
    row.dead_lettered_at = None
    row.replay_count = row.replay_count + 1
    row.updated_at = now
    logger.info("case_push_dead_letter_replayed", outbox_id=str(outbox_id), actor=actor, replay_count=row.replay_count)
    return row


# --- dispatcher ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class PushSettings:
    max_attempts: int = 10
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 900.0
    timeout_seconds: float = 10.0
    lease_seconds: float = 120.0
    batch_size: int = 50


def retry_delay(attempt: int, event_id: uuid.UUID, settings: PushSettings) -> float:
    """Exponential backoff for the attempt that just failed (1-based), with deterministic +/-20% jitter."""
    base = min(settings.base_delay_seconds * (2 ** max(0, attempt - 1)), settings.max_delay_seconds)
    draw = int.from_bytes(hashlib.sha256(f"{event_id}:{attempt}".encode()).digest()[:4], "big") / 2**32
    return base * (0.8 + 0.4 * draw)


@dataclass
class DispatchReport:
    delivered: int = 0
    retried: int = 0
    dead_lettered: int = 0


SessionFactory = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


def _default_session_factory(tenant_id: uuid.UUID) -> AbstractAsyncContextManager[AsyncSession]:
    from core.database import get_tenant_session

    return get_tenant_session(tenant_id)


async def _all_tenant_ids() -> list[uuid.UUID]:
    from sqlalchemy import text

    from core.database import async_session_factory
    from core.models.tenant import Tenant

    async with async_session_factory() as session:
        # A maintenance role that cannot bypass tenant RLS must fail loudly, not enumerate nothing.
        await session.execute(text("SET LOCAL row_security = off"))
        return list((await session.scalars(select(Tenant.id))).all())


def _default_transport() -> httpx.AsyncBaseTransport:
    from core.security.egress import build_pinned_async_transport, egress_dns_validation_required

    return build_pinned_async_transport(require_dns=egress_dns_validation_required())


@dataclass
class CasePushDispatcher:
    session_factory: SessionFactory = _default_session_factory
    transport_factory: Callable[[], httpx.AsyncBaseTransport] = _default_transport
    clock: Callable[[], datetime] = _utc_now
    settings: PushSettings = field(default_factory=PushSettings)
    tenant_ids: Callable[[], Awaitable[list[uuid.UUID]]] = _all_tenant_ids

    async def dispatch_all(self) -> DispatchReport:
        total = DispatchReport()
        backlog = 0
        for tenant_id in await self.tenant_ids():
            report, dead = await self._dispatch_tenant(tenant_id)
            total.delivered += report.delivered
            total.retried += report.retried
            total.dead_lettered += report.dead_lettered
            backlog += dead
        push_dead_letter_backlog.set(backlog)
        return total

    async def dispatch_tenant(self, tenant_id: uuid.UUID) -> DispatchReport:
        report, _ = await self._dispatch_tenant(tenant_id)
        return report

    async def _dispatch_tenant(self, tenant_id: uuid.UUID) -> tuple[DispatchReport, int]:
        report = DispatchReport()
        now = self.clock()
        async with self.session_factory(tenant_id) as session:
            endpoint = await _endpoint(session, tenant_id)
            dead = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(CasePushOutbox)
                        .where(CasePushOutbox.tenant_id == tenant_id, CasePushOutbox.status == "dead_lettered")
                    )
                ).scalar_one()
            )
            if endpoint is None or not endpoint.enabled:
                return report, dead
            keys = await decrypt_keys(endpoint)
            keys.sort(key=lambda k: k.key_id != endpoint.active_key_id)
            url = endpoint.url
            rows = list(
                (
                    await session.execute(
                        select(CasePushOutbox)
                        .where(
                            CasePushOutbox.tenant_id == tenant_id,
                            CasePushOutbox.status == "pending",
                            CasePushOutbox.next_attempt_at <= now,
                        )
                        .order_by(CasePushOutbox.next_attempt_at)
                        .limit(self.settings.batch_size)
                        .with_for_update(skip_locked=True)
                    )
                ).scalars()
            )
            leased: list[tuple[uuid.UUID, uuid.UUID, str, dict[str, Any], int]] = []
            for row in rows:
                row.attempts = row.attempts + 1
                row.next_attempt_at = now + timedelta(seconds=self.settings.lease_seconds)
                row.updated_at = now
                leased.append((row.id, row.event_id, row.event_type, dict(row.payload), row.attempts))
        if not leased:
            return report, dead

        results: list[tuple[uuid.UUID, uuid.UUID, int, str, int | None]] = []
        async with httpx.AsyncClient(
            transport=self.transport_factory(), timeout=self.settings.timeout_seconds, follow_redirects=False
        ) as client:
            for row_id, event_id, event_type, payload, attempts in leased:
                body = payload_bytes(payload)
                headers = sign(
                    body,
                    event_id=str(event_id),
                    event_type=event_type,
                    timestamp=int(self.clock().timestamp()),
                    keys=keys,
                )
                started = asyncio.get_running_loop().time()
                try:
                    response = await client.post(url, content=body, headers=headers)
                    status_code: int | None = response.status_code
                    error = "" if 200 <= response.status_code < 300 else f"http_{response.status_code}"
                except httpx.TimeoutException:
                    status_code, error = None, "timeout"
                except httpx.HTTPError as exc:
                    status_code, error = None, f"transport_{type(exc).__name__}"[:64]
                push_attempt_seconds.observe(asyncio.get_running_loop().time() - started)
                results.append((row_id, event_id, attempts, error, status_code))

        finished = self.clock()
        async with self.session_factory(tenant_id) as session:
            for row_id, event_id, attempts, error, status_code in results:
                row = (
                    await session.execute(
                        select(CasePushOutbox).where(CasePushOutbox.tenant_id == tenant_id, CasePushOutbox.id == row_id)
                    )
                ).scalar_one()
                row.last_status_code = status_code
                row.updated_at = finished
                if not error:
                    row.status, row.delivered_at, row.last_error = "delivered", finished, None
                    report.delivered += 1
                    push_deliveries_total.labels(outcome="delivered").inc()
                    continue
                row.last_error = error
                retryable = status_code is None or status_code in RETRYABLE_STATUS or status_code >= 500
                if retryable and attempts < self.settings.max_attempts:
                    row.next_attempt_at = finished + timedelta(seconds=retry_delay(attempts, event_id, self.settings))
                    report.retried += 1
                    push_deliveries_total.labels(outcome="retry_scheduled").inc()
                else:
                    reason = "max_attempts_exceeded" if retryable else "endpoint_rejected"
                    row.status, row.dead_lettered_at, row.last_error = (
                        "dead_lettered",
                        finished,
                        f"{reason}:{error}"[:64],
                    )
                    report.dead_lettered += 1
                    dead += 1
                    push_deliveries_total.labels(outcome="dead_lettered").inc()
                    push_dead_letters_total.labels(reason=reason).inc()
                    logger.warning(
                        "case_push_dead_lettered", tenant_id=str(tenant_id), outbox_id=str(row_id), reason=reason
                    )
        return report, dead


def _send_kick(tenant_id: uuid.UUID) -> None:
    try:
        from core.tasks.case_push_tasks import dispatch_case_pushes

        dispatch_case_pushes.delay(str(tenant_id))
    # enterprise-gate: broad-except-ok reason=broker-failure-falls-back-to-the-periodic-sweep-durable-outbox
    except Exception as exc:
        logger.warning("case_push_kick_failed", tenant_id=str(tenant_id), error=type(exc).__name__)


def kick_dispatch(tenant_id: uuid.UUID) -> None:
    """Ask a worker to deliver now; the periodic sweep delivers anyway if the broker is unreachable.

    Publishing to the broker is blocking I/O, so inside an event loop it runs in a worker thread
    and never holds up the request that changed the case.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _send_kick(tenant_id)
        return
    loop.run_in_executor(None, _send_kick, tenant_id)
