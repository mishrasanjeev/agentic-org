# SPDX-License-Identifier: Apache-2.0
"""Stored case and bound inbox tenants reach scoped plugin constructors on every caller."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from connectors.framework.verification_provider import VerificationProvider
from connectors.providers.registry import ProviderRegistry, ProviderRegistryError
from core.cases import provider_webhooks
from core.cases import runtime as case_runtime
from core.cases.runtime import CaseRuntime
from core.cases.states import CaseError
from core.policy import EXAMPLES_DIR, load_policy
from core.tool_gateway.provider_gateway import ToolDecision


def test_tenant_factory_lookup_errors_are_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenDescriptor:
        def __get__(self, obj: Any, owner: Any) -> Any:
            raise ValueError("sensitive account configuration")

    class ScopedProvider(VerificationProvider):
        name = "acme_kyb"
        capabilities = frozenset()
        for_tenant = BrokenDescriptor()

    monkeypatch.setattr(ProviderRegistry, "_providers", {})
    ProviderRegistry.register_plugin(ScopedProvider)
    with pytest.raises(ProviderRegistryError) as caught:
        ProviderRegistry.create("acme_kyb", tenant_id=uuid4())
    assert caught.value.reason == "construction_failed"
    assert "sensitive account configuration" not in str(caught.value)


@pytest.mark.parametrize("caller", ["investigate", "screening", "webhook"])
async def test_actual_callers_forward_distinct_tenants(caller: str, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[UUID] = []

    class ScopedProvider(VerificationProvider):
        name = "acme_kyb"
        capabilities = frozenset()

        @classmethod
        def for_tenant(cls, tenant_id: UUID) -> VerificationProvider:
            seen.append(tenant_id)
            return cls()

    monkeypatch.setattr(ProviderRegistry, "_providers", {})
    ProviderRegistry.register_plugin(ScopedProvider)
    session = SimpleNamespace(add=lambda _: None)

    @asynccontextmanager
    async def sessions(tenant: UUID) -> Any:
        yield session

    async def enabled(_: UUID) -> bool:
        return True

    class Allow:
        async def authorize(self, **_: Any) -> ToolDecision:
            return ToolDecision(allowed=True)

    policy = load_policy(EXAMPLES_DIR / "business_onboarding_uk.yaml")
    runtime = CaseRuntime(
        session_factory=sessions,
        flag=enabled,
        policies=lambda: {policy.policy_id: policy},
        authorizer_factory=lambda *_: Allow(),
        push_kick=lambda _: None,
        evidence_service=lambda: None,
    )
    monkeypatch.setattr(case_runtime, "transition", AsyncMock())
    monkeypatch.setattr(case_runtime, "_run_underwriter_safely", AsyncMock(return_value=None))
    monkeypatch.setattr(case_runtime, "_store_investigation", AsyncMock(return_value={}))
    monkeypatch.setattr(case_runtime, "record_update", AsyncMock())
    tenants = [uuid4(), uuid4()]
    for tenant in tenants:
        case = SimpleNamespace(
            tenant_id=tenant,
            state="awaiting_decision" if caller == "screening" else "submitted",
            version=1,
            application={},
            provider="acme_kyb",
            policy_id=policy.policy_id,
            purpose="aml.cdd.onboarding",
            agent_records=[],
            screening_results=[],
            parties=[],
            screening_dispositions=[],
            excerpts_encrypted=[],
            decision_requests=[],
        )

        async def get_case(
            session: Any,
            stored_tenant: UUID,
            ref: str,
            expected: UUID = tenant,
            stored_case: Any = case,
            **_: Any,
        ) -> Any:
            assert stored_tenant == expected
            return stored_case

        monkeypatch.setattr(case_runtime, "get_case", get_case)
        if caller == "investigate":
            await case_runtime.investigate_case(tenant, "case_" + "0" * 24, runtime=runtime, actor="user:analyst")
        elif caller == "screening":
            await case_runtime.dispose_screening_hits(tenant, "case_" + "0" * 24, runtime=runtime, actor="user:analyst")
        else:
            token = provider_webhooks.webhook_path_token(tenant, "acme_kyb")
            receipt = await provider_webhooks.receive_provider_webhook(
                tenant_id=tenant,
                provider_name="acme_kyb",
                path_token=token,
                headers={},
                body=b"{}",
                runtime=runtime,
                requery=AsyncMock(),
                now=datetime.now(UTC),
            )
            assert receipt.outcome == "unverified"
    assert seen == tenants
    if caller == "webhook":
        with pytest.raises(CaseError, match="webhook_not_bound"):
            await provider_webhooks.receive_provider_webhook(
                tenant_id=tenants[1],
                provider_name="acme_kyb",
                path_token=provider_webhooks.webhook_path_token(tenants[0], "acme_kyb"),
                headers={},
                body=b"{}",
                runtime=runtime,
                requery=AsyncMock(),
                now=datetime.now(UTC),
            )
        assert seen == tenants
