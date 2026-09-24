# SPDX-License-Identifier: Apache-2.0
"""The provider tool gateway: read tools only, grant check fails closed, calls recorded and counted."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from prometheus_client import REGISTRY

from connectors.framework.verification_provider import (
    BusinessQuery,
    Capability,
    CapabilityNotSupported,
    Deadline,
    NotAvailable,
    ProviderUnavailable,
)
from connectors.providers.mock import FaultKind, MockConfig, MockProvider
from core.tool_gateway.provider_gateway import (
    READ_TOOLS,
    ProviderToolGateway,
    ToolDecision,
    ToolRefusedError,
    ToolSetError,
    canonical_sha256,
    cited_record_ids,
)

AT = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


class Allow:
    async def authorize(self, *, connector: str, tool: str) -> ToolDecision:
        return ToolDecision(allowed=True)


def _gateway(provider: MockProvider | None = None, **kwargs) -> ProviderToolGateway:
    return ProviderToolGateway(
        provider=provider or MockProvider(MockConfig(clock=lambda: AT)),
        agent="test_agent",
        tool_set=kwargs.pop("tool_set", frozenset(READ_TOOLS)),
        authorizer=kwargs.pop("authorizer", Allow()),
        clock=lambda: AT,
        **kwargs,
    )


def _count(capability: str, outcome: str) -> float:
    return (
        REGISTRY.get_sample_value("agenticorg_provider_calls_total", {"capability": capability, "outcome": outcome})
        or 0.0
    )


@pytest.mark.parametrize("tool", ["case_decision", "monitor_enroll", "monitor_delete", "close_hit", "file_report"])
def test_a_tool_set_cannot_contain_a_tool_that_decides_writes_or_closes(tool: str) -> None:
    with pytest.raises(ToolSetError, match="not read tools"):
        _gateway(tool_set=frozenset({"resolve_business", tool}))


def test_read_tools_are_exactly_the_non_mutating_capabilities() -> None:
    assert set(READ_TOOLS.values()) == set(Capability) - {Capability.MONITOR}


async def test_a_tool_outside_the_agent_tool_set_is_refused_and_recorded() -> None:
    gateway = _gateway(tool_set=frozenset({"resolve_business"}))
    with pytest.raises(ToolRefusedError) as refused:
        await gateway.ownership(MockProvider().ref_for("gb-clean-brightwater"), deadline=Deadline.after(5))
    assert refused.value.reason == "tool_not_in_agent_tool_set"
    assert [(r.tool, r.outcome) for r in gateway.records] == [("ownership", "denied")]


async def test_authorized_call_is_recorded_with_hashes_and_cited_records() -> None:
    before = _count("resolve", "ok")
    gateway = _gateway()
    query = BusinessQuery(name="Brightwater Lantern Works")
    [candidate] = await gateway.resolve_business(query, deadline=Deadline.after(5))
    [record] = gateway.records
    assert record.outcome == "ok" and record.capability == "resolve" and record.provider == "mock"
    assert record.input_sha256 == canonical_sha256(query)
    assert record.output_sha256 == canonical_sha256([candidate])
    assert record.record_ids == cited_record_ids([candidate]) == ("mock:company:mock-gb-00000001:profile",)
    assert ("mock", "mock:company:mock-gb-00000001:profile", "legal_name") in gateway.retrieved_evidence
    assert record.to_dict()["started_at"] == AT.isoformat()
    assert _count("resolve", "ok") == before + 1


async def test_missing_authorizer_refuses_before_provider_dispatch() -> None:
    provider = MockProvider(MockConfig(clock=lambda: AT))
    gateway = ProviderToolGateway(
        provider=provider,
        agent="test_agent",
        tool_set=frozenset({"resolve_business"}),
    )
    with pytest.raises(ToolRefusedError, match="authorization_unavailable"):
        await gateway.resolve_business(BusinessQuery(name="Brightwater"), deadline=Deadline.after(5))
    assert provider._state.attempts == {}


async def test_authorizer_failure_refuses_before_provider_dispatch() -> None:
    class Broken:
        async def authorize(self, *, connector: str, tool: str) -> ToolDecision:
            raise TimeoutError("unavailable")

    provider = MockProvider(MockConfig(clock=lambda: AT))
    gateway = _gateway(provider, authorizer=Broken())
    with pytest.raises(ToolRefusedError, match="authorization_unavailable"):
        await gateway.resolve_business(BusinessQuery(name="Brightwater"), deadline=Deadline.after(5))
    assert provider._state.attempts == {}


async def test_denied_grant_never_reaches_the_provider() -> None:
    class Deny:
        async def authorize(self, *, connector: str, tool: str) -> ToolDecision:
            return ToolDecision(allowed=False, reason="purpose_not_allowed")

    provider = MockProvider(MockConfig(clock=lambda: AT))
    gateway = _gateway(provider, authorizer=Deny())
    with pytest.raises(ToolRefusedError, match="purpose_not_allowed"):
        await gateway.resolve_business(BusinessQuery(name="Brightwater"), deadline=Deadline.after(5))
    assert provider._state.attempts == {}


async def test_an_authorizer_returning_something_other_than_a_decision_refuses() -> None:
    class Sloppy:
        async def authorize(self, *, connector: str, tool: str):  # type: ignore[no-untyped-def]
            return True

    with pytest.raises(ToolRefusedError, match="grant_denied"):
        await _gateway(authorizer=Sloppy()).resolve_business(
            BusinessQuery(name="Brightwater"), deadline=Deadline.after(5)
        )


async def test_undeclared_capability_is_not_available_and_not_called() -> None:
    provider = MockProvider(MockConfig(clock=lambda: AT, capabilities=frozenset({Capability.RESOLVE})))
    gateway = _gateway(provider)
    result = await gateway.web_presence(provider.ref_for("gb-clean-brightwater"), deadline=Deadline.after(5))
    assert result == NotAvailable(Capability.WEB_PRESENCE)
    assert [(r.outcome, r.reason) for r in gateway.records] == [("not_available", "capability_not_supported")]


async def test_declared_capability_that_raises_unsupported_degrades_too() -> None:
    provider = MockProvider(MockConfig(clock=lambda: AT))

    async def unsupported(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise CapabilityNotSupported("mock", Capability.OWNERSHIP)

    provider.ownership = unsupported  # type: ignore[method-assign]
    result = await _gateway(provider).ownership(provider.ref_for("gb-clean-brightwater"), deadline=Deadline.after(5))
    assert isinstance(result, NotAvailable)


async def test_provider_errors_are_recorded_counted_and_raised() -> None:
    before = _count("ownership", "error")
    provider = MockProvider(MockConfig(clock=lambda: AT))
    provider.inject_fault(FaultKind.UNAVAILABLE, capability=Capability.OWNERSHIP)
    gateway = _gateway(provider)
    with pytest.raises(ProviderUnavailable):
        await gateway.ownership(provider.ref_for("gb-clean-brightwater"), deadline=Deadline.after(5))
    assert [(r.outcome, r.reason, r.output_sha256) for r in gateway.records] == [
        ("error", "provider_unavailable", None)
    ]
    assert _count("ownership", "error") == before + 1
