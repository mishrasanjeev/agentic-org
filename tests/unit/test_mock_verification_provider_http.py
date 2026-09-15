# SPDX-License-Identifier: Apache-2.0
"""A-3: the mock provider as a separate HTTP service and the client provider that talks to it.

The service runs on a real socket (``AGENTICORG_TEST_MOCK_PROVIDER_PORT``, or a free port), so these
calls go over the network rather than in-process.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest

from connectors.framework.verification_provider import (
    BusinessQuery,
    BusinessRef,
    BusinessVerification,
    Capability,
    CapabilityNotSupported,
    Deadline,
    Identifier,
    InvalidQuery,
    MonitorOptions,
    NotFound,
    Pending,
    PersonSubject,
    ProviderEventType,
    ProviderRateLimited,
    ProviderResponseInvalid,
    ProviderTimeout,
    ProviderUnavailable,
    ScreenOptions,
    VerifyOptions,
)
from connectors.providers.mock import FaultKind, MockConfig, MockHttpProvider, MockProvider
from connectors.providers.mock.service import ServiceRefusedError, create_app, serve_in_thread

FROZEN = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
BRIGHTWATER = BusinessRef(
    provider="mock",
    provider_ref="mock-gb-00000001",
    jurisdiction="GB",
    identifiers=(Identifier(scheme="gb_company_number", value="00000001"),),
)


def _config(**overrides: object) -> MockConfig:
    return MockConfig(clock=lambda: FROZEN, **overrides)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def service() -> Iterator[tuple[str, MockProvider]]:
    backend = MockProvider(_config(polls_until_ready=1))
    port = int(os.getenv("AGENTICORG_TEST_MOCK_PROVIDER_PORT", "0"))
    with serve_in_thread(backend, port=port) as base_url:
        yield base_url, backend


@pytest.fixture
def client(service: tuple[str, MockProvider]) -> MockHttpProvider:
    base_url, backend = service
    backend.reset()
    return MockHttpProvider(base_url, config=_config())


def deadline(seconds: float = 5) -> Deadline:
    return Deadline.after(seconds)


def test_the_service_listens_on_a_real_socket(service: tuple[str, MockProvider]) -> None:
    base_url, _ = service
    host, port = base_url.removeprefix("http://").split(":")
    with socket.create_connection((host, int(port)), timeout=2):
        pass
    assert httpx.get(f"{base_url}/healthz", timeout=5).json() == {"alive": True, "provider": "mock"}


async def test_resolve_verify_and_ownership_over_the_network(client: MockHttpProvider) -> None:
    [candidate] = await client.resolve_business(BusinessQuery(name="Brightwater"), deadline=deadline())
    assert candidate.ref == BRIGHTWATER

    handle = await client.verify_business(
        candidate.ref, VerifyOptions(idempotency_key="http-verify-1"), deadline=deadline()
    )
    assert (
        await client.verify_business(candidate.ref, VerifyOptions(idempotency_key="http-verify-1"), deadline=deadline())
        == handle
    )
    first = await client.verification_result(handle, deadline=deadline())
    second = await client.verification_result(handle, deadline=deadline())
    assert isinstance(first, Pending) and isinstance(second, BusinessVerification)

    graph = await client.ownership(candidate.ref, deadline=deadline())
    assert graph.subject == BRIGHTWATER and len(graph.edges) == 2


async def test_screening_web_presence_and_monitoring_over_the_network(client: MockHttpProvider) -> None:
    result = await client.screen_person(
        PersonSubject(full_name="Radomir Vexley"), ScreenOptions(idempotency_key="http-screen"), deadline=deadline()
    )
    assert [h.matched_name for h in result.hits] == ["Radomir Vexley"]
    glintmoor = BusinessRef(provider="mock", provider_ref="mock-us-az-0000007", jurisdiction="US-AZ")
    presence = await client.web_presence(glintmoor, deadline=deadline())
    assert presence.pages and all("ignore" not in str(page.content) for page in presence.pages)

    monitor = await client.monitor_enroll(BRIGHTWATER, MonitorOptions(idempotency_key="http-mon"), deadline=deadline())
    headers, body = await client.emit_event(BRIGHTWATER, ProviderEventType.BUSINESS_DISSOLVED, deadline=deadline())
    event = client.verify_webhook(headers, body)
    assert event is not None and event.event_type is ProviderEventType.BUSINESS_DISSOLVED
    assert client.verify_webhook(headers, body.replace(b"00000001", b"00000002")) is None
    alerts = await client.monitor_result(monitor, deadline=deadline())
    assert [a.event_type for a in alerts] == [ProviderEventType.BUSINESS_DISSOLVED]


async def test_error_taxonomy_survives_the_network(client: MockHttpProvider) -> None:
    with pytest.raises(InvalidQuery):
        await client.resolve_business(BusinessQuery(), deadline=deadline())
    with pytest.raises(NotFound):
        await client.ownership(BRIGHTWATER.model_copy(update={"provider_ref": "mock-gb-99999999"}), deadline=deadline())

    await client.inject_fault(FaultKind.UNAVAILABLE, capability=Capability.OWNERSHIP, deadline=deadline())
    with pytest.raises(ProviderUnavailable):
        await client.ownership(BRIGHTWATER, deadline=deadline())

    await client.inject_fault(FaultKind.RATE_LIMITED, deadline=deadline())
    with pytest.raises(ProviderRateLimited) as limited:
        await client.ownership(BRIGHTWATER, deadline=deadline())
    assert limited.value.retry_after_seconds is not None

    await client.inject_fault(FaultKind.RESPONSE_INVALID, deadline=deadline())
    with pytest.raises(ProviderResponseInvalid):
        await client.ownership(BRIGHTWATER, deadline=deadline())


async def test_a_capability_the_service_does_not_offer_is_capability_not_supported(
    service: tuple[str, MockProvider],
) -> None:
    base_url, backend = service
    narrowed = MockProvider(_config(capabilities=frozenset({Capability.RESOLVE})))
    with serve_in_thread(narrowed) as narrow_url:
        honest = MockHttpProvider(narrow_url, config=_config(capabilities=frozenset({Capability.RESOLVE})))
        with pytest.raises(CapabilityNotSupported):
            await honest.ownership(BRIGHTWATER, deadline=deadline())
        # A client that believes the service offers ownership still gets the typed error back.
        optimistic = MockHttpProvider(narrow_url, config=_config())
        with pytest.raises(CapabilityNotSupported) as caught:
            await optimistic.ownership(BRIGHTWATER, deadline=deadline())
        assert caught.value.capability is Capability.OWNERSHIP


async def test_a_hung_service_is_a_provider_timeout_within_the_deadline(client: MockHttpProvider) -> None:
    await client.inject_fault(FaultKind.HANG, deadline=deadline())
    started = time.monotonic()
    with pytest.raises(ProviderTimeout):
        await client.ownership(BRIGHTWATER, deadline=Deadline.after(0.3))
    assert time.monotonic() - started < 2


async def test_cancelling_a_call_in_flight_propagates(client: MockHttpProvider) -> None:
    await client.inject_fault(FaultKind.SLOW, delay_seconds=5, deadline=deadline())
    task = asyncio.create_task(client.ownership(BRIGHTWATER, deadline=deadline(30)))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_an_unreachable_service_is_provider_unavailable() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    unreachable = MockHttpProvider(f"http://127.0.0.1:{port}", config=_config())
    with pytest.raises(ProviderUnavailable):
        await unreachable.ownership(BRIGHTWATER, deadline=deadline())


def test_invalid_bodies_and_deadline_headers_are_invalid_queries(service: tuple[str, MockProvider]) -> None:
    base_url, _ = service
    response = httpx.post(f"{base_url}/v1/ownership", json={"ref": {"provider": "mock"}, "extra": 1}, timeout=5)
    assert response.status_code == 400 and response.json()["error"]["reason"] == "invalid_query"
    response = httpx.post(
        f"{base_url}/v1/ownership",
        json={"ref": BRIGHTWATER.model_dump(mode="json")},
        headers={"X-Request-Deadline-Ms": "forever"},
        timeout=5,
    )
    assert response.status_code == 400 and response.json()["error"]["reason"] == "invalid_query"


def test_admin_endpoints_are_off_unless_enabled() -> None:
    from fastapi.testclient import TestClient

    app = create_app(MockProvider(_config()), admin=False)
    with TestClient(app) as http:
        for path, body in [
            ("/v1/admin/faults", {"kind": "unavailable"}),
            ("/v1/admin/events", {"ref": BRIGHTWATER.model_dump(mode="json"), "event_type": "business.dissolved"}),
            ("/v1/admin/reset", {}),
        ]:
            response = http.post(path, json=body)
            assert response.status_code == 404 and response.json()["error"]["reason"] == "not_found"


@pytest.mark.parametrize("environment", ["production", "staging", ""])
def test_the_service_refuses_to_start_outside_local_and_test(monkeypatch: pytest.MonkeyPatch, environment: str) -> None:
    monkeypatch.setenv("AGENTICORG_ENV", environment)
    with pytest.raises(ServiceRefusedError):
        create_app(MockProvider(_config()))


@pytest.mark.parametrize(
    ("status", "content", "error"),
    [
        (200, b"not json", ProviderResponseInvalid),
        (200, json.dumps({"subject": "wrong shape"}).encode(), ProviderResponseInvalid),
        (500, b"", ProviderUnavailable),
        (418, b"{}", ProviderResponseInvalid),
        (503, json.dumps({"error": {"reason": "provider_unavailable"}}).encode(), ProviderUnavailable),
        (400, json.dumps({"error": {"reason": "something_new"}}).encode(), ProviderResponseInvalid),
    ],
)
async def test_the_client_fails_closed_on_unexpected_responses(
    status: int, content: bytes, error: type[Exception]
) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, content=content))
    odd = MockHttpProvider("http://mock-provider.invalid", config=_config(), transport=transport)
    with pytest.raises(error):
        await odd.ownership(BRIGHTWATER, deadline=deadline())


async def test_the_client_sends_the_remaining_deadline() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["X-Request-Deadline-Ms"])
        return httpx.Response(404, json={"error": {"reason": "not_found"}})

    probe = MockHttpProvider("http://mock-provider.invalid", config=_config(), transport=httpx.MockTransport(handler))
    with pytest.raises(NotFound):
        await probe.ownership(BRIGHTWATER, deadline=Deadline.after(2))
    assert 1000 < int(seen[0]) <= 2000
