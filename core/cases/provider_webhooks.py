# SPDX-License-Identifier: Apache-2.0
"""Inbound provider webhooks: verify, record, and use only as a trigger to re-query.

A provider webhook never changes a case directly, and only a delivery whose authenticity is proven
is allowed to trigger anything:

- the delivery must arrive at the tenant's own inbox path, which carries an unguessable per-tenant
  token (:func:`webhook_path_token`). A wrong or missing token is counted and dropped before any
  database work, so an event genuinely signed for one tenant cannot be replayed at another
  tenant's inbox;
- ``VerificationProvider.verify_webhook`` must return an event whose provider matches the path.
  **verified, new event id** (``accepted``) makes the event id single-use and re-queries every case
  of this tenant that is awaiting a decision on the event's subject; **verified, event id seen
  before** (``duplicate``) is recorded and nothing else happens;
- **not verifiable** (``unverified``: forged, stale, tampered, unsigned) is recorded and counted,
  and nothing else happens. Nothing is read out of the body, no case is looked up and no
  investigation runs: an unsigned body is evidence of an attempt, never an instruction.

The body itself is never stored or trusted: a re-investigation reads everything from the provider
again through the tool gateway. A re-investigation started this way records the event that caused
it, and leaves the case where it was if the provider cannot be reached (``core.cases.runtime``).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from prometheus_client import Counter
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from core.cases.runtime import CaseRuntime
from core.cases.states import CaseError, CaseState
from core.models.case_push import ProviderWebhookReceipt
from core.models.governed_case import GovernedCase

logger = structlog.get_logger()

MAX_BODY_BYTES = 256 * 1024
#: How many cases one verified event may re-investigate, so a single delivery cannot start
#: unbounded work.
MAX_REQUERY_CASES = 25
PATH_TOKEN_BYTES = 16
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_EVENT_ID_SAFE = re.compile(r"[^A-Za-z0-9._:\-]")

webhook_receipts_total = Counter(
    "agenticorg_provider_webhook_receipts_total",
    "Inbound provider webhooks, by outcome (accepted, duplicate, unverified, unbound)",
    ["outcome"],
)


@dataclass(frozen=True)
class RequeryRequest:
    """A re-investigation a verified event asks for; ``reason`` is recorded on the transition."""

    tenant_id: uuid.UUID
    case_ref: str
    actor: str
    reason: str


Requery = Callable[["RequeryRequest"], Awaitable[None]]


@dataclass(frozen=True)
class WebhookReceipt:
    outcome: str
    requeried: tuple[str, ...]


def requery_actor(provider: str) -> str:
    return f"provider_event:{provider}"


def webhook_path_token(tenant_id: uuid.UUID | str, provider: str) -> str:
    """The unguessable token in a tenant's inbox path, derived from the application secret.

    Derived rather than stored, so it needs no extra secret at rest; rotating the application
    secret key rotates every tenant's inbox path (see the case hand-off guide).
    """
    from core.config import settings

    message = f"provider-webhook:v1:{tenant_id}:{provider}".encode()
    digest = hmac.new(settings.secret_key.encode("utf-8"), message, hashlib.sha256).digest()
    return digest[:PATH_TOKEN_BYTES].hex()


def webhook_path(tenant_id: uuid.UUID | str, provider: str) -> str:
    """The path a provider should be configured to deliver this tenant's events to."""
    return f"/api/v1/webhooks/providers/{tenant_id}/{provider}/{webhook_path_token(tenant_id, provider)}"


def path_token_matches(tenant_id: uuid.UUID | str, provider: str, token: str) -> bool:
    if not isinstance(token, str) or not _TOKEN_RE.match(token):
        return False
    return hmac.compare_digest(token, webhook_path_token(tenant_id, provider))


def _event_reason(event: Any) -> str:
    event_id = _EVENT_ID_SAFE.sub("", str(getattr(event, "event_id", "") or ""))[:48]
    return f"provider_event:{event.event_type.value}:{event_id}"[:128]


async def receive_provider_webhook(
    *,
    tenant_id: uuid.UUID,
    provider_name: str,
    path_token: str,
    headers: Mapping[str, str],
    body: bytes,
    runtime: CaseRuntime,
    requery: Requery,
    now: datetime,
) -> WebhookReceipt:
    if not path_token_matches(tenant_id, provider_name, path_token):
        webhook_receipts_total.labels(outcome="unbound").inc()
        logger.warning("provider_webhook_not_bound", provider=provider_name)
        raise CaseError("webhook_not_bound", status=404)
    if len(body) > MAX_BODY_BYTES:
        raise CaseError("webhook_body_too_large", status=413)
    await runtime.require_enabled(tenant_id)
    try:
        provider = runtime.provider_factory(provider_name)
    # enterprise-gate: broad-except-ok reason=unknown-or-unavailable-provider-fails-closed-as-not-found
    except Exception as exc:
        logger.warning("provider_webhook_provider_unavailable", provider=provider_name, error=type(exc).__name__)
        raise CaseError("provider_unknown", status=404) from exc
    try:
        event = provider.verify_webhook(headers, body)
    # enterprise-gate: broad-except-ok reason=verification-error-fails-closed-as-unverified
    except Exception as exc:
        logger.error("provider_webhook_verify_raised", provider=provider_name, error=type(exc).__name__)
        event = None
    if event is not None and event.provider != provider_name:
        logger.warning("provider_webhook_provider_mismatch", provider=provider_name)
        event = None
    body_sha256 = "sha256:" + hashlib.sha256(body).hexdigest()

    requests: list[RequeryRequest] = []
    async with runtime.session_factory(tenant_id) as session:
        if event is None:
            session.add(
                ProviderWebhookReceipt(
                    id=uuid.uuid4(), tenant_id=tenant_id, provider=provider_name, verified=False, outcome="unverified",
                    event_id=None, event_type=None, body_sha256=body_sha256, requeried_cases=0, received_at=now,
                )
            )  # fmt: skip
            outcome = "unverified"
        else:
            inserted = await session.execute(
                insert(ProviderWebhookReceipt)
                .values(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    provider=provider_name,
                    verified=True,
                    outcome="accepted",
                    event_id=event.event_id,
                    event_type=event.event_type.value,
                    body_sha256=body_sha256,
                    requeried_cases=0,
                    received_at=now,
                )  # fmt: skip
                .on_conflict_do_nothing(
                    index_elements=["tenant_id", "provider", "event_id"],
                    index_where=ProviderWebhookReceipt.outcome == "accepted",
                )
                .returning(ProviderWebhookReceipt.id)
            )
            receipt_id = inserted.scalar_one_or_none()
            if receipt_id is None:
                session.add(
                    ProviderWebhookReceipt(
                        id=uuid.uuid4(), tenant_id=tenant_id, provider=provider_name, verified=True,
                        outcome="duplicate", event_id=event.event_id, event_type=event.event_type.value,
                        body_sha256=body_sha256, requeried_cases=0, received_at=now,
                    )
                )  # fmt: skip
                outcome = "duplicate"
            else:
                outcome = "accepted"
                requests = await _cases_to_requery(session, tenant_id, provider_name, event, receipt_id)

    webhook_receipts_total.labels(outcome=outcome).inc()
    logger.info(
        "provider_webhook_received", tenant_id=str(tenant_id), provider=provider_name, outcome=outcome,
        requeried=len(requests),
    )  # fmt: skip
    for request in requests:
        await requery(request)
    return WebhookReceipt(outcome=outcome, requeried=tuple(r.case_ref for r in requests))


async def _cases_to_requery(
    session: Any, tenant_id: uuid.UUID, provider_name: str, event: Any, receipt_id: uuid.UUID
) -> list[RequeryRequest]:
    """Cases of this tenant awaiting a decision on the verified event's subject."""
    ref = event.subject.provider_ref if event.subject is not None else None
    if not isinstance(ref, str) or not ref:
        return []
    rows = (
        await session.execute(
            select(GovernedCase.case_ref)
            .where(
                GovernedCase.tenant_id == tenant_id,
                GovernedCase.provider == provider_name,
                GovernedCase.state == CaseState.AWAITING_DECISION.value,
                GovernedCase.subject["provider_ref"].astext == ref,
            )
            .order_by(GovernedCase.case_ref)
            .limit(MAX_REQUERY_CASES)
        )
    ).all()
    if not rows:
        return []
    receipt = (
        await session.execute(
            select(ProviderWebhookReceipt).where(
                ProviderWebhookReceipt.tenant_id == tenant_id, ProviderWebhookReceipt.id == receipt_id
            )
        )
    ).scalar_one()
    receipt.requeried_cases = len(rows)
    reason = _event_reason(event)
    return [
        RequeryRequest(tenant_id=tenant_id, case_ref=case_ref, actor=requery_actor(provider_name), reason=reason)
        for (case_ref,) in rows
    ]
