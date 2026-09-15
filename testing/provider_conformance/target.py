# SPDX-License-Identifier: Apache-2.0
"""What an implementation hands the conformance suite: how to build it and what to ask it."""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from connectors.framework.verification_provider import (
    BusinessQuery,
    BusinessRef,
    BusinessSubject,
    Capability,
    MonitorHandle,
    PersonSubject,
    VerificationProvider,
)

FaultKindName = Literal["unavailable", "rate_limited", "hang", "slow"]


@dataclass(frozen=True)
class WebhookSample:
    headers: Mapping[str, str]
    body: bytes
    description: str = ""


ProviderFactory = Callable[[], VerificationProvider | Awaitable[VerificationProvider]]
#: Make the next call to ``capability`` (or any call) on this provider fail, hang or answer after ``delay_seconds``.
FaultInjector = Callable[[VerificationProvider, FaultKindName, Capability | None, float], Awaitable[None] | None]
WebhookSource = Callable[[VerificationProvider], Awaitable[Sequence[WebhookSample]] | Sequence[WebhookSample]]
#: Cause at least two alerts on this monitor (for example by triggering sandbox events).
AlertPreparer = Callable[[VerificationProvider, MonitorHandle], Awaitable[None] | None]


@dataclass(frozen=True)
class ConformanceTarget:
    """Everything the suite needs to exercise one implementation.

    ``factory`` is called inside the event loop of each check, so the provider may hold loop-bound
    resources; ``close`` (optional) releases them. Inputs must reference real data at the provider:
    ``known_business`` must exist and ``resolvable_query`` must return at least two candidates so
    paging can be checked; ``unknown_business`` must be well formed but absent.

    ``genuine_webhooks`` returns deliveries the provider must accept (``None`` if it never verifies
    webhooks). ``forged_webhooks`` returns deliveries it must reject; the suite adds its own
    tampered and malformed variants. ``stale_webhooks`` returns deliveries that are correctly signed
    but outside the provider's accepted time window, which it must also reject.
    ``fault_injector`` enables the checks that need a slow, hanging or failing provider, and
    ``prepare_monitor_alerts`` makes at least two alerts exist on a monitor so its pages can be
    checked. Without them those checks are skipped with the reason shown - unless ``strict`` is
    true, in which case a skipped check fails. Use ``strict`` for any provider you publish.
    """

    factory: ProviderFactory
    known_business: BusinessRef
    resolvable_query: BusinessQuery
    unknown_business: BusinessRef
    person: PersonSubject
    business: BusinessSubject
    genuine_webhooks: WebhookSource | None = None
    forged_webhooks: WebhookSource | None = None
    stale_webhooks: WebhookSource | None = None
    fault_injector: FaultInjector | None = None
    prepare_monitor_alerts: AlertPreparer | None = None
    strict: bool = False
    expects_pending: bool = False
    poll_timeout_seconds: float = 30.0
    grace_seconds: float = 1.0
    close: Callable[[VerificationProvider], Awaitable[None]] | None = None
    #: Makes idempotency keys unique per run, so a shared, stateful provider can be checked repeatedly.
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


async def resolve_maybe_awaitable[T](value: T | Awaitable[T]) -> T:
    if inspect.isawaitable(value):
        return await value
    return value
