# SPDX-License-Identifier: Apache-2.0
"""How a provider package runs the conformance suite against itself, using the mock as the provider.

This file is written from outside the repository's point of view: it imports the suite from its
published location, ``agenticorg.testing.provider_conformance``. It is not collected by the
repository's own test run; ``tests/contract/test_provider_conformance_published.py`` installs the
suite in its published layout and runs this file with pytest in a separate process. The
documentation's examples are extracted from it.
"""

from __future__ import annotations

# docs-snippet: start conformance-imports
import json
import time
from collections.abc import Iterator

import pytest
from agenticorg.testing.provider_conformance import ConformanceTarget, ProviderConformanceSuite, WebhookSample

from connectors.framework.verification_provider import (
    BusinessQuery,
    BusinessRef,
    BusinessSubject,
    Capability,
    Deadline,
    Identifier,
    MonitorHandle,
    PersonSubject,
    ProviderEventType,
    VerificationProvider,
)
from connectors.providers.mock import FaultKind, MockConfig, MockHttpProvider, MockProvider, webhooks
from connectors.providers.mock.service import serve_in_thread

# docs-snippet: end conformance-imports

# docs-snippet: start conformance-target
KNOWN = BusinessRef(
    provider="mock",
    provider_ref="mock-gb-00000001",
    jurisdiction="GB",
    identifiers=(Identifier(scheme="gb_company_number", value="00000001"),),
)


def genuine_webhooks(provider: VerificationProvider) -> list[WebhookSample]:
    # Ask the provider for a delivery it signed itself. A provider without a way to produce one
    # records genuine deliveries as fixtures and sets its clock to when they were signed.
    assert isinstance(provider, MockProvider)
    headers, body = provider.emit_event(KNOWN, ProviderEventType.BUSINESS_DISSOLVED)
    return [WebhookSample(headers=headers, body=body, description="a signed business.dissolved event")]


def forged_webhooks(provider: VerificationProvider) -> list[WebhookSample]:
    assert isinstance(provider, MockProvider)
    headers, body = provider.emit_event(KNOWN, ProviderEventType.BUSINESS_DISSOLVED)
    forged_payload = body.replace(b"00000001", b"00000002")
    return [WebhookSample(headers=headers, body=forged_payload, description="a genuine signature on another company")]


def stale_webhooks(provider: VerificationProvider) -> list[WebhookSample]:
    # A genuine event re-signed two hours ago: the signature is valid, the delivery is a replay.
    assert isinstance(provider, MockProvider)
    _, body = provider.emit_event(KNOWN, ProviderEventType.BUSINESS_DISSOLVED)
    event_id = json.loads(body)["event_id"]
    two_hours_ago = int(time.time()) - 7200
    headers = webhooks.sign(provider.config.webhook_secret, body, event_id=event_id, timestamp=two_hours_ago)
    return [WebhookSample(headers=headers, body=body, description="a correctly signed delivery from two hours ago")]


def prepare_monitor_alerts(provider: VerificationProvider, handle: MonitorHandle) -> None:
    assert isinstance(provider, MockProvider)
    for _ in range(3):
        provider.emit_event(handle.ref, ProviderEventType.OFFICERS_CHANGED)


def inject_fault(provider: VerificationProvider, kind: str, capability: Capability | None, delay: float) -> None:
    assert isinstance(provider, MockProvider)
    provider.inject_fault(FaultKind(kind), capability=capability, delay_seconds=delay)


def mock_target() -> ConformanceTarget:
    return ConformanceTarget(
        factory=lambda: MockProvider(MockConfig(polls_until_ready=2)),
        known_business=KNOWN,
        resolvable_query=BusinessQuery(
            identifiers=tuple(Identifier(scheme="gb_company_number", value=f"0000000{n}") for n in (1, 2, 3))
        ),
        unknown_business=KNOWN.model_copy(update={"provider_ref": "mock-gb-99999999"}),
        person=PersonSubject(full_name="Orla Venncastle", date_of_birth="1971-04"),
        business=BusinessSubject(legal_name="Brightwater Lantern Works Ltd", jurisdiction="GB"),
        genuine_webhooks=genuine_webhooks,
        forged_webhooks=forged_webhooks,
        stale_webhooks=stale_webhooks,
        fault_injector=inject_fault,
        prepare_monitor_alerts=prepare_monitor_alerts,
        expects_pending=True,
        strict=True,  # a skipped check fails
    )


class TestMockProviderConformance(ProviderConformanceSuite):
    @pytest.fixture
    def conformance_target(self) -> ConformanceTarget:
        return mock_target()


# docs-snippet: end conformance-target


# The same provider reached over HTTP: the service runs on a real socket and the client provider
# talks to it. Controls that the in-process provider offers directly go through the service.
@pytest.fixture(scope="module")
def mock_service() -> Iterator[str]:
    with serve_in_thread(MockProvider(MockConfig(polls_until_ready=2))) as base_url:
        yield base_url


class TestMockProviderOverHttpConformance(ProviderConformanceSuite):
    @pytest.fixture
    def conformance_target(self, mock_service: str) -> ConformanceTarget:
        async def genuine(provider: VerificationProvider) -> list[WebhookSample]:
            assert isinstance(provider, MockHttpProvider)
            headers, body = await provider.emit_event(
                KNOWN, ProviderEventType.BUSINESS_DISSOLVED, deadline=Deadline.after(10)
            )
            return [WebhookSample(headers=headers, body=body, description="a signed event from the service")]

        async def forged(provider: VerificationProvider) -> list[WebhookSample]:
            [sample] = await genuine(provider)
            return [WebhookSample(sample.headers, sample.body.replace(b"00000001", b"00000002"), "a forged payload")]

        async def stale(provider: VerificationProvider) -> list[WebhookSample]:
            [sample] = await genuine(provider)
            event_id = json.loads(sample.body)["event_id"]
            headers = webhooks.sign(
                provider.config.webhook_secret, sample.body, event_id=event_id, timestamp=int(time.time()) - 7200
            )
            return [WebhookSample(headers, sample.body, "a delivery from the service re-signed two hours ago")]

        async def prepare(provider: VerificationProvider, handle: MonitorHandle) -> None:
            assert isinstance(provider, MockHttpProvider)
            for _ in range(3):
                await provider.emit_event(handle.ref, ProviderEventType.OFFICERS_CHANGED, deadline=Deadline.after(10))

        async def inject(
            provider: VerificationProvider, kind: str, capability: Capability | None, delay: float
        ) -> None:
            assert isinstance(provider, MockHttpProvider)
            await provider.inject_fault(
                FaultKind(kind), capability=capability, delay_seconds=delay, deadline=Deadline.after(10)
            )

        base = mock_target()
        return ConformanceTarget(
            factory=lambda: MockHttpProvider(mock_service),
            known_business=base.known_business,
            resolvable_query=base.resolvable_query,
            unknown_business=base.unknown_business,
            person=base.person,
            business=base.business,
            genuine_webhooks=genuine,
            forged_webhooks=forged,
            stale_webhooks=stale,
            fault_injector=inject,
            prepare_monitor_alerts=prepare,
            expects_pending=True,
            strict=True,
        )
