# SPDX-License-Identifier: Apache-2.0
"""The governed-case provider boundary must have a positive grant decision."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from auth.grant_enforcement import EnforcementMode
from auth.run_grants import RunGrant
from core.cases import grant_authorizer as case_grants
from core.tool_gateway.provider_gateway import READ_TOOLS

TENANT = str(uuid.uuid4())
CASE = "case_" + "a" * 24


def test_mock_provider_manifest_covers_only_read_tools() -> None:
    from grantex import ToolManifest

    manifest = ToolManifest.from_file(str(Path(__file__).resolve().parents[3] / "manifests" / "mock.json"))
    assert set(manifest.tools) == set(READ_TOOLS)
    assert set(manifest.tools.values()) == {"read"}


@pytest.mark.parametrize(
    "purposes",
    [[], ["aml.cdd.onboarding", "aml.cdd.onboarding"], ["invalid"], ["aml.cdd.onboarding", "Bad.Value"]],
)
def test_invalid_case_purpose_policy_is_refused(purposes: list[str]) -> None:
    with pytest.raises(ValueError):
        case_grants.validate_case_purposes(purposes)


def test_case_purpose_policy_preserves_exact_names() -> None:
    assert case_grants.validate_case_purposes(["aml.cdd.onboarding", "x-example.custom"]) == [
        "aml.cdd.onboarding", "x-example.custom"
    ]


async def test_missing_role_registration_refuses_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    async def absent(tenant_id: str, role: str) -> None:
        assert tenant_id == TENANT and role == "business_underwriter"
        return None

    monkeypatch.setattr(case_grants, "_active_agent", absent)
    result = await case_grants.case_authorizer(TENANT, CASE, "business_underwriter", "aml.cdd.onboarding").authorize(
        connector="mock", tool="resolve_business"
    )
    assert not result.allowed
    assert result.reason == "grant_missing" and result.sub_reason == "case_agent_not_configured"


async def test_failed_role_lookup_refuses_with_its_own_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failed(tenant_id: str, role: str) -> None:
        raise TimeoutError("database unavailable")

    monkeypatch.setattr(case_grants, "_active_agent", failed)
    result = await case_grants.case_authorizer(TENANT, CASE, "business_underwriter", "aml.cdd.onboarding").authorize(
        connector="mock", tool="resolve_business"
    )
    assert not result.allowed
    assert result.reason == "grant_missing" and result.sub_reason == "agent_lookup_failed"


async def test_registered_role_uses_a_strict_run_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    agent_id = str(uuid.uuid4())
    seen: dict[str, Any] = {}

    async def registered(tenant_id: str, role: str) -> tuple[str, dict[str, Any]]:
        assert tenant_id == TENANT and role == "screening_disposition"
        return agent_id, {
            "grantex_agent_id": "ag_registered",
            "grantex_scopes": ["tool:mock:read"],
            "case_purposes": ["aml.cdd.onboarding"],
        }

    async def resolved(**kwargs: Any) -> RunGrant:
        seen.update(kwargs)
        return RunGrant(mode=EnforcementMode.DENY, token="signed-token", source="minted")

    class Client:
        def enforce(self, **kwargs: Any) -> Any:
            seen["enforce"] = kwargs
            return SimpleNamespace(allowed=True)

    monkeypatch.setattr(case_grants, "_active_agent", registered)
    monkeypatch.setattr(case_grants, "resolve_run_grant", resolved)
    monkeypatch.setattr("core.langgraph.grantex_auth.get_grantex_client", lambda: Client())
    result = await case_grants.case_authorizer(TENANT, CASE, "screening_disposition", "aml.cdd.onboarding").authorize(
        connector="mock", tool="screen_person"
    )
    assert result.allowed
    assert seen["mode"] is EnforcementMode.DENY
    assert seen["tenant_id"] == TENANT and seen["agent_id"] == agent_id
    assert "grant_token" not in seen["grantex_config"]
    assert seen["enforce"]["grant_token"] == "signed-token"
    assert seen["enforce"]["connector"] == "mock" and seen["enforce"]["tool"] == "screen_person"


@pytest.mark.parametrize(
    "registered_purposes",
    [None, [], ["payments.payout"], ["aml.cdd.onboarding", 7]],
)
async def test_case_purpose_not_registered_for_role_refuses_before_grant_resolution(
    monkeypatch: pytest.MonkeyPatch,
    registered_purposes: Any,
) -> None:
    async def registered(tenant_id: str, role: str) -> tuple[str, dict[str, Any]]:
        return str(uuid.uuid4()), {"case_purposes": registered_purposes}

    async def unexpected(**kwargs: Any) -> RunGrant:
        raise AssertionError("must not resolve a grant for an unauthorized purpose")

    monkeypatch.setattr(case_grants, "_active_agent", registered)
    monkeypatch.setattr(case_grants, "resolve_run_grant", unexpected)
    result = await case_grants.case_authorizer(TENANT, CASE, "business_underwriter", "aml.cdd.onboarding").authorize(
        connector="mock", tool="resolve_business"
    )
    assert not result.allowed
    assert result.reason == "purpose_not_allowed" and result.sub_reason == "case_purpose_not_registered"


async def test_enforcement_service_failure_refuses_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    async def registered(tenant_id: str, role: str) -> tuple[str, dict[str, Any]]:
        return str(uuid.uuid4()), {"case_purposes": ["aml.cdd.onboarding"]}

    async def resolved(**kwargs: Any) -> RunGrant:
        return RunGrant(mode=EnforcementMode.DENY, token="signed-token", source="minted")

    class BrokenClient:
        def enforce(self, **kwargs: Any) -> Any:
            raise TimeoutError("unavailable")

    monkeypatch.setattr(case_grants, "_active_agent", registered)
    monkeypatch.setattr(case_grants, "resolve_run_grant", resolved)
    monkeypatch.setattr("core.langgraph.grantex_auth.get_grantex_client", lambda: BrokenClient())
    result = await case_grants.case_authorizer(TENANT, CASE, "business_underwriter", "aml.cdd.onboarding").authorize(
        connector="mock", tool="verify_business"
    )
    assert not result.allowed and result.reason == "enforcement_unavailable"
