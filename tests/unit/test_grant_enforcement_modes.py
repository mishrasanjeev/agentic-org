# SPDX-License-Identifier: Apache-2.0
"""PRD F-1 — ``grants.enforce_closed`` mode resolution and per-call grant checks.

Acceptance criteria covered here:

* the mode is the deployment default unless the tenant's ``warn`` / ``deny``
  flag is on, deny wins, an unknown deployment value fails startup;
* a flag store that cannot be read never silently weakens a tenant's mode;
* in ``warn`` every call the grant would deny is allowed, logged as
  ``grant_enforcement_would_deny`` and counted by (mode, reason);
* in ``deny`` the same calls are refused with the reason code;
* every failure to evaluate a grant is a denial reason, never an allow;
* ``off`` keeps the legacy scope-validation behaviour.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage
from structlog.testing import capture_logs

from auth import grant_enforcement as ge
from auth.grant_enforcement import (
    DenialReason,
    EnforcementMode,
    GrantCallContext,
    check_tool_grant,
    classify_enforce_reason,
    resolve_enforcement_mode,
)
from auth.run_grants import RunGrant

TENANT = str(uuid.UUID(int=0x1F1))
PLACEHOLDER_TOKEN = "placeholder-grant-token"  # noqa: S105 - not a credential


@pytest.fixture(autouse=True)
def _reset_mode_cache():
    ge.clear_mode_cache()
    yield
    ge.clear_mode_cache()


def _flags(**enabled: bool):
    """Fake ``is_enabled_strict`` answering from a {flag_key: bool} map."""
    values = {ge.FLAG_WARN: enabled.get("warn", False), ge.FLAG_DENY: enabled.get("deny", False)}

    async def _strict(flag_key: str, **_: Any) -> bool:
        return values[flag_key]

    return _strict


def _counter_value(mode: str, reason: str) -> float:
    from observability.metrics import grant_enforcement_denials_total

    return grant_enforcement_denials_total.labels(mode=mode, reason=reason)._value.get()


# ── Mode resolution ──────────────────────────────────────────────────────


async def test_mode_defaults_to_off_without_tenant_flags(monkeypatch):
    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "off")
    with patch("core.feature_flags.is_enabled_strict", _flags()):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.OFF


async def test_deployment_default_applies_when_tenant_has_no_flags(monkeypatch):
    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "warn")
    with patch("core.feature_flags.is_enabled_strict", _flags()):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.WARN


async def test_tenant_warn_flag_overrides_off_default(monkeypatch):
    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "off")
    with patch("core.feature_flags.is_enabled_strict", _flags(warn=True)):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.WARN


async def test_deny_flag_wins_over_warn_flag(monkeypatch):
    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "off")
    with patch("core.feature_flags.is_enabled_strict", _flags(warn=True, deny=True)):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.DENY


async def test_requested_deny_is_honoured_without_a_cap(monkeypatch):
    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "off")
    with patch("core.feature_flags.is_enabled_strict", _flags(deny=True)), capture_logs() as logs:
        mode = await resolve_enforcement_mode(TENANT)
    assert mode is EnforcementMode.DENY
    assert not any(entry["event"] == "grant_enforcement_deny_unavailable" for entry in logs)


async def test_tenant_flags_cannot_weaken_the_deployment_default(monkeypatch):
    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "deny")
    with patch("core.feature_flags.is_enabled_strict", _flags(warn=True)):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.DENY
    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "warn")
    with patch("core.feature_flags.is_enabled_strict", _flags()):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.WARN


async def test_unreadable_flag_store_falls_back_to_deployment_default(monkeypatch):
    from core.feature_flags import FeatureFlagLookupError

    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "warn")
    failing = AsyncMock(side_effect=FeatureFlagLookupError("down"))
    with patch("core.feature_flags.is_enabled_strict", failing), capture_logs() as logs:
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.WARN
    assert any(entry["event"] == "grant_enforcement_mode_lookup_failed" for entry in logs)


async def test_unreadable_flag_store_keeps_the_tenants_last_known_stricter_mode(monkeypatch):
    from core.feature_flags import FeatureFlagLookupError

    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "off")
    with patch("core.feature_flags.is_enabled_strict", _flags(warn=True)):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.WARN
    failing = AsyncMock(side_effect=FeatureFlagLookupError("down"))
    with patch("core.feature_flags.is_enabled_strict", failing):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.WARN


async def test_mode_without_a_tenant_is_the_deployment_default(monkeypatch):
    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "warn")
    strict = AsyncMock()
    with patch("core.feature_flags.is_enabled_strict", strict):
        assert await resolve_enforcement_mode("") is EnforcementMode.WARN
    strict.assert_not_awaited()


def test_unknown_deployment_mode_fails_settings_validation():
    from pydantic import ValidationError

    from core.config import Settings

    with pytest.raises(ValidationError):
        Settings(grants_enforce_closed="strict")


def test_deployment_mode_defaults_to_off():
    from core.config import Settings

    assert Settings.model_fields["grants_enforce_closed"].default == "off"


async def test_strict_flag_lookup_raises_when_the_flag_store_is_unreadable():
    from core import feature_flags

    feature_flags.clear_cache()

    attempts: list[int] = []

    def _broken_session(*_: Any, **__: Any):
        attempts.append(1)
        raise RuntimeError("database unavailable")

    try:
        with patch("core.feature_flags.get_tenant_session", _broken_session):
            with pytest.raises(feature_flags.FeatureFlagLookupError):
                await feature_flags.is_enabled_strict(ge.FLAG_DENY, tenant_id=uuid.UUID(TENANT))
            # The lenient evaluator keeps its documented default on the same failure,
            # and its cached failure still reads as unknown to a strict lookup.
            assert await feature_flags.is_enabled(ge.FLAG_DENY, tenant_id=uuid.UUID(TENANT)) is False
            with pytest.raises(feature_flags.FeatureFlagLookupError):
                await feature_flags.is_enabled_strict(ge.FLAG_DENY, tenant_id=uuid.UUID(TENANT))
        # The outage is not re-probed on every call while the failure is cached.
        assert len(attempts) == 1
    finally:
        feature_flags.clear_cache()


# ── Reason classification ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("sdk_reason", "reason", "sub_reason"),
    [
        ("Token verification failed: Signature has expired", DenialReason.TOKEN_INVALID, "expired"),
        ("Token verification failed: Not enough segments", DenialReason.TOKEN_INVALID, "verification_failed"),
        ("token_revoked", DenialReason.GRANT_REVOKED, ""),
        (
            "No manifest loaded for connector 'unknown'. Load a manifest first.",
            DenialReason.MANIFEST_UNKNOWN_TOOL,
            "connector_unknown",
        ),
        (
            "Unknown tool 'x' on connector 'hubspot'. Tool not found in manifest.",
            DenialReason.MANIFEST_UNKNOWN_TOOL,
            "tool_unknown",
        ),
        ("No scope grants access to connector 'hubspot'.", DenialReason.TOOL_NOT_GRANTED, ""),
        ("read scope does not permit write operations on hubspot.", DenialReason.PERMISSION_INSUFFICIENT, ""),
        ("Amount 900 exceeds budget cap of 500 on stripe.", DenialReason.CAP_EXCEEDED, ""),
        ("something new", DenialReason.TOOL_NOT_GRANTED, "unclassified"),
        ("", DenialReason.TOOL_NOT_GRANTED, "unclassified"),
    ],
)
def test_enforce_reasons_map_to_the_denial_vocabulary(sdk_reason, reason, sub_reason):
    assert classify_enforce_reason(sdk_reason) == (reason, sub_reason)


# ── Per-call checks ──────────────────────────────────────────────────────


def _client(allowed: bool, reason: str = "", grant_id: str = "grnt_placeholder") -> MagicMock:
    client = MagicMock()
    client.enforce.return_value = MagicMock(allowed=allowed, reason=reason, grant_id=grant_id)
    return client


CTX = GrantCallContext(tenant_id=TENANT, agent_id="agent-f1", agent_type="analyst", runtime="test")


async def test_check_is_not_used_for_off_mode():
    with pytest.raises(ValueError):
        await check_tool_grant(
            mode=EnforcementMode.OFF,
            grant_token=PLACEHOLDER_TOKEN,
            connector="hubspot",
            tool="get_contact",
            context=CTX,
        )


async def test_allowed_call_records_nothing():
    before = _counter_value("warn", "tool_not_granted")
    with capture_logs() as logs:
        check = await check_tool_grant(
            mode=EnforcementMode.WARN,
            grant_token=PLACEHOLDER_TOKEN,
            connector="hubspot",
            tool="get_contact",
            context=CTX,
            client_factory=lambda: _client(True),
        )
    assert check.dispatch_allowed and check.denial is None
    assert not [entry for entry in logs if entry["event"].startswith("grant_enforcement")]
    assert _counter_value("warn", "tool_not_granted") == before


DENIAL_CASES = [
    pytest.param(None, None, DenialReason.GRANT_MISSING, "mint_failed", id="grant_missing"),
    pytest.param(
        _client(False, "No scope grants access to connector 'hubspot'."),
        None,
        DenialReason.TOOL_NOT_GRANTED,
        "",
        id="tool_not_granted",
    ),
    pytest.param(
        _client(False, "read scope does not permit write operations on hubspot."),
        None,
        DenialReason.PERMISSION_INSUFFICIENT,
        "",
        id="permission_insufficient",
    ),
    pytest.param(_client(False, "grant revoked"), None, DenialReason.GRANT_REVOKED, "", id="grant_revoked"),
    pytest.param(
        _client(False, "Token verification failed: bad signature"),
        None,
        DenialReason.TOKEN_INVALID,
        "verification_failed",
        id="token_invalid",
    ),
    pytest.param(
        _client(False, "Amount 9 exceeds budget cap of 1 on hubspot."),
        None,
        DenialReason.CAP_EXCEEDED,
        "",
        id="cap_exceeded",
    ),
    pytest.param(
        _client(False, "Unknown tool 'x' on connector 'hubspot'."),
        None,
        DenialReason.MANIFEST_UNKNOWN_TOOL,
        "tool_unknown",
        id="manifest_unknown_tool",
    ),
    pytest.param(
        None,
        ValueError("GRANTEX_API_KEY is required"),
        DenialReason.ENFORCEMENT_UNAVAILABLE,
        "ValueError",
        id="client_unavailable",
    ),
]


def _factory(client: MagicMock | None, error: Exception | None):
    def _make():
        if error is not None:
            raise error
        return client

    return _make


@pytest.mark.parametrize(("client", "error", "reason", "sub_reason"), DENIAL_CASES)
async def test_warn_allows_and_records_every_would_deny_reason(client, error, reason, sub_reason):
    token = None if client is None and error is None else PLACEHOLDER_TOKEN
    before = _counter_value("warn", reason.value)
    with capture_logs() as logs:
        check = await check_tool_grant(
            mode=EnforcementMode.WARN,
            grant_token=token,
            connector="hubspot",
            tool="create_contact",
            context=CTX,
            missing_sub_reason="mint_failed",
            client_factory=_factory(client, error),
        )
    assert check.dispatch_allowed is True
    assert check.denial is not None and check.denial.reason is reason and check.denial.sub_reason == sub_reason
    events = [entry for entry in logs if entry["event"] == "grant_enforcement_would_deny"]
    assert len(events) == 1
    assert events[0]["reason"] == reason.value
    assert events[0]["mode"] == "warn"
    assert events[0]["tool"] == "create_contact" and events[0]["connector"] == "hubspot"
    assert PLACEHOLDER_TOKEN not in repr(events[0])
    assert _counter_value("warn", reason.value) == before + 1


@pytest.mark.parametrize(("client", "error", "reason", "sub_reason"), DENIAL_CASES)
async def test_deny_refuses_every_denial_reason(client, error, reason, sub_reason):
    token = None if client is None and error is None else PLACEHOLDER_TOKEN
    before = _counter_value("deny", reason.value)
    with capture_logs() as logs:
        check = await check_tool_grant(
            mode=EnforcementMode.DENY,
            grant_token=token,
            connector="hubspot",
            tool="create_contact",
            context=CTX,
            missing_sub_reason="mint_failed",
            client_factory=_factory(client, error),
        )
    assert check.dispatch_allowed is False
    assert check.denial is not None and check.denial.reason is reason
    assert [entry["reason"] for entry in logs if entry["event"] == "grant_enforcement_denied"] == [reason.value]
    assert _counter_value("deny", reason.value) == before + 1


async def test_enforce_raising_is_a_denial_not_an_allow():
    client = MagicMock()
    client.enforce.side_effect = RuntimeError("jwks fetch failed")
    check = await check_tool_grant(
        mode=EnforcementMode.DENY,
        grant_token=PLACEHOLDER_TOKEN,
        connector="hubspot",
        tool="get_contact",
        context=CTX,
        client_factory=lambda: client,
    )
    assert check.dispatch_allowed is False
    assert check.denial is not None
    assert check.denial.reason is DenialReason.ENFORCEMENT_UNAVAILABLE
    assert check.denial.sub_reason == "RuntimeError"


# ── LangGraph scope-validation node ──────────────────────────────────────


def _state(grant_token: str = "", tool: str = "get_contact") -> dict[str, Any]:
    return {
        "messages": [AIMessage(content="", tool_calls=[{"name": tool, "args": {}, "id": "tc1", "type": "tool_call"}])],
        "grant_token": grant_token,
        "agent_id": "agent-f1",
        "agent_type": "analyst",
        "tenant_id": TENANT,
        "domain": "sales",
        "confidence": 0.0,
        "hitl_trigger": "",
        "output": {},
        "status": "running",
        "error": "",
        "reasoning_trace": [],
        "tool_calls_log": [],
    }


async def test_off_mode_run_grant_keeps_legacy_no_op_without_a_token():
    from core.langgraph.agent_graph import validate_tool_scopes

    with capture_logs() as logs:
        result = await validate_tool_scopes(_state(), run_grant=RunGrant(mode=EnforcementMode.OFF))
    assert result == {}
    assert not [entry for entry in logs if entry["event"].startswith("grant_enforcement")]


async def test_warn_mode_node_allows_a_call_with_no_grant_and_records_grant_missing():
    from core.langgraph.agent_graph import validate_tool_scopes

    grant = RunGrant(mode=EnforcementMode.WARN, source="none", missing_sub_reason="minting_unconfigured")
    with capture_logs() as logs:
        result = await validate_tool_scopes(_state(), run_grant=grant)
    assert result == {}
    events = [entry for entry in logs if entry["event"] == "grant_enforcement_would_deny"]
    assert [(e["reason"], e["sub_reason"], e["runtime"]) for e in events] == [
        ("grant_missing", "minting_unconfigured", "langgraph")
    ]


async def test_deny_mode_node_stops_the_call_with_the_reason_code():
    from core.langgraph.agent_graph import validate_tool_scopes

    grant = RunGrant(mode=EnforcementMode.DENY, source="none", missing_sub_reason="agent_not_registered")
    result = await validate_tool_scopes(_state(), run_grant=grant)
    assert result["status"] == "failed"
    assert result["error"] == "grant_denied: grant_missing"
    assert "grant_missing" in result["messages"][0].content


async def test_node_checks_the_connector_the_tool_is_registered_under():
    from core.langgraph.agent_graph import validate_tool_scopes

    client = _client(True)
    grant = RunGrant(mode=EnforcementMode.WARN, token=PLACEHOLDER_TOKEN, source="supplied")
    with patch("core.langgraph.agent_graph.get_grantex_client", return_value=client):
        result = await validate_tool_scopes(
            _state(PLACEHOLDER_TOKEN, tool="gmail__send_email"),
            tool_refs={"gmail__send_email": ("gmail", "send_email")},
            run_grant=grant,
        )
    assert result == {}
    kwargs = client.enforce.call_args.kwargs
    assert (kwargs["connector"], kwargs["tool"]) == ("gmail", "send_email")
    assert kwargs["grant_token"] == PLACEHOLDER_TOKEN


async def test_warn_mode_node_records_scope_denial_but_lets_the_call_run():
    from core.langgraph.agent_graph import validate_tool_scopes

    client = _client(False, "read scope does not permit write operations on salesforce.")
    grant = RunGrant(mode=EnforcementMode.WARN, token=PLACEHOLDER_TOKEN, source="minted")
    with (
        patch("core.langgraph.agent_graph.get_grantex_client", return_value=client),
        patch(
            "core.langgraph.agent_graph._build_tool_index",
            return_value={"create_lead": ("salesforce", "Create a lead")},
        ),
        capture_logs() as logs,
    ):
        result = await validate_tool_scopes(_state(PLACEHOLDER_TOKEN, tool="create_lead"), run_grant=grant)
    assert result == {}
    events = [entry for entry in logs if entry["event"] == "grant_enforcement_would_deny"]
    assert [(e["reason"], e["connector"], e["grant_source"]) for e in events] == [
        ("permission_insufficient", "salesforce", "minted")
    ]


async def test_warn_mode_never_weakens_a_token_the_legacy_path_already_enforced():
    from core.langgraph.agent_graph import validate_tool_scopes

    client = _client(False, "No scope grants access to connector 'salesforce'.")
    index = {"create_lead": ("salesforce", "Create a lead")}
    for source in ("supplied", "agent_config"):
        grant = RunGrant(mode=EnforcementMode.WARN, token=PLACEHOLDER_TOKEN, source=source)
        with (
            patch("core.langgraph.agent_graph.get_grantex_client", return_value=client),
            patch("core.langgraph.agent_graph._build_tool_index", return_value=index),
            capture_logs() as logs,
        ):
            result = await validate_tool_scopes(_state(PLACEHOLDER_TOKEN, tool="create_lead"), run_grant=grant)
        assert result["status"] == "failed", source
        assert result["error"] == "grant_denied: tool_not_granted"
        assert [e["mode"] for e in logs if e["event"] == "grant_enforcement_denied"] == ["deny"]


def test_call_mode_only_escalates_legacy_enforced_tokens_in_warn():
    assert (
        RunGrant(mode=EnforcementMode.WARN, token=PLACEHOLDER_TOKEN, source="supplied").call_mode
        is EnforcementMode.DENY
    )
    assert (
        RunGrant(mode=EnforcementMode.WARN, token=PLACEHOLDER_TOKEN, source="minted").call_mode is EnforcementMode.WARN
    )
    assert RunGrant(mode=EnforcementMode.WARN, source="none").call_mode is EnforcementMode.WARN
    assert (
        RunGrant(mode=EnforcementMode.OFF, token=PLACEHOLDER_TOKEN, source="supplied").call_mode is EnforcementMode.OFF
    )
    assert (
        RunGrant(mode=EnforcementMode.DENY, token=PLACEHOLDER_TOKEN, source="minted").call_mode is EnforcementMode.DENY
    )
