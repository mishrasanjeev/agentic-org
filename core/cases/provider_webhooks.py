# SPDX-License-Identifier: Apache-2.0
"""Inbound provider webhooks: verify, record, and use only as a trigger to re-query.

A provider webhook never changes a case directly. ``VerificationProvider.verify_webhook`` decides
whether the delivery is authentic:

- **verified, new event id** (``accepted``): the event id becomes single-use and every case of
  this tenant awaiting a decision on the event's subject is re-investigated from the provider;
- **verified, event id seen before** (``duplicate``, a replay): recorded, nothing else happens;
- **not verifiable** (``unverified``: forged, stale, tampered, unsigned): recorded and counted.
  Its body is an untrusted trigger - only a subject reference is read from it, only to find cases
  to re-query, and a case is re-queried this way at most once per ``UNVERIFIED_REQUERY_WINDOW``.

The body itself is never stored or trusted: a re-investigation reads everything from the
provider again through the tool gateway.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog
from prometheus_client import Counter
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from core.cases.runtime import CaseRuntime
from core.cases.states import CaseError, CaseState
from core.models.case_push import ProviderWebhookReceipt
from core.models.governed_case import GovernedCase, GovernedCaseTransition

logger = structlog.get_logger()

MAX_BODY_BYTES = 256 * 1024
UNVERIFIED_REQUERY_WINDOW = timedelta(minutes=10)
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}$")

webhook_receipts_total = Counter(
    "agenticorg_provider_webhook_receipts_total",
    "Inbound provider webhooks, by outcome (accepted, duplicate, unverified)",
    ["outcome"],
)

Requery = Callable[[uuid.UUID, str, str], Awaitable[None]]


@dataclass(frozen=True)
class WebhookReceipt:
    outcome: str
    requeried: tuple[str, ...]


def requery_actor(provider: str) -> str:
    return f"provider_event:{provider}"


def _subject_hint(body: bytes) -> str | None:
    """The only thing read from an unverified body: a subject reference to look cases up by."""
    try:
        parsed = json.loads(body)
    except (ValueError, RecursionError):
        return None
    subject = parsed.get("subject") if isinstance(parsed, Mapping) else None
    ref = subject.get("provider_ref") if isinstance(subject, Mapping) else None
    return ref if isinstance(ref, str) and _REF.match(ref) else None


async def receive_provider_webhook(
    *,
    tenant_id: uuid.UUID,
    provider_name: str,
    headers: Mapping[str, str],
    body: bytes,
    runtime: CaseRuntime,
    requery: Requery,
    now: datetime,
) -> WebhookReceipt:
    await runtime.require_enabled(tenant_id)
    if len(body) > MAX_BODY_BYTES:
        raise CaseError("webhook_body_too_large", status=413)
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
    body_sha256 = "sha256:" + hashlib.sha256(body).hexdigest()

    async with runtime.session_factory(tenant_id) as session:
        if event is not None and event.provider == provider_name:
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
                outcome, ref, debounce = "duplicate", None, False
            else:
                outcome = "accepted"
                ref = event.subject.provider_ref if event.subject is not None else None
                debounce = False
        else:
            receipt_id = uuid.uuid4()
            session.add(
                ProviderWebhookReceipt(
                    id=receipt_id, tenant_id=tenant_id, provider=provider_name, verified=False, outcome="unverified",
                    event_id=None, event_type=None, body_sha256=body_sha256, requeried_cases=0, received_at=now,
                )
            )  # fmt: skip
            outcome, ref, debounce = "unverified", _subject_hint(body), True
            await session.flush()

        case_refs: list[str] = []
        if ref is not None:
            candidates = (
                await session.execute(
                    select(GovernedCase.id, GovernedCase.case_ref).where(
                        GovernedCase.tenant_id == tenant_id,
                        GovernedCase.provider == provider_name,
                        GovernedCase.state == CaseState.AWAITING_DECISION.value,
                        GovernedCase.subject["provider_ref"].astext == ref,
                    )
                )
            ).all()
            for case_id, case_ref in candidates:
                if debounce and await _recently_requeried(session, tenant_id, case_id, provider_name, now):
                    continue
                case_refs.append(case_ref)
            if receipt_id is not None and case_refs:
                receipt = (
                    await session.execute(
                        select(ProviderWebhookReceipt).where(
                            ProviderWebhookReceipt.tenant_id == tenant_id, ProviderWebhookReceipt.id == receipt_id
                        )
                    )
                ).scalar_one()
                receipt.requeried_cases = len(case_refs)

    webhook_receipts_total.labels(outcome=outcome).inc()
    logger.info(
        "provider_webhook_received", tenant_id=str(tenant_id), provider=provider_name, outcome=outcome,
        requeried=len(case_refs),
    )  # fmt: skip
    for case_ref in case_refs:
        await requery(tenant_id, case_ref, requery_actor(provider_name))
    return WebhookReceipt(outcome=outcome, requeried=tuple(case_refs))


async def _recently_requeried(
    session: Any, tenant_id: uuid.UUID, case_id: uuid.UUID, provider: str, now: datetime
) -> bool:
    recent = await session.execute(
        select(GovernedCaseTransition.id)
        .where(
            GovernedCaseTransition.tenant_id == tenant_id,
            GovernedCaseTransition.case_id == case_id,
            GovernedCaseTransition.actor == requery_actor(provider),
            GovernedCaseTransition.created_at >= now - UNVERIFIED_REQUERY_WINDOW,
        )
        .limit(1)
    )
    return recent.first() is not None
