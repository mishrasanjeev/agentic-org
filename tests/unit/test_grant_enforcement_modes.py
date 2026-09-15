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
from types import SimpleNamespace
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
    classify_enforce_result,
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
    """Fake ``load_flag_rows_strict``: tenant rows from a {"warn"|"deny": bool} map."""
    return _rows(tenant=enabled)


def _rows(*, tenant: dict[str, bool] | None = None, global_: dict[str, bool] | None = None):
    """Fake ``load_flag_rows_strict`` with independent tenant and global rows.

    A key missing from a map has no row; ``False`` is a disabled row.
    """
    from core.feature_flags import FlagRows

    names = {ge.FLAG_WARN: "warn", ge.FLAG_DENY: "deny"}

    def _row(values: dict[str, bool] | None, flag_key: str):
        if values is None or names[flag_key] not in values:
            return None
        return {"enabled": values[names[flag_key]], "rollout_percentage": 100}

    async def _load(flag_key: str, **_: Any):
        return FlagRows(global_row=_row(global_, flag_key), tenant_row=_row(tenant, flag_key))

    return _load


def _counter_value(mode: str, reason: str) -> float:
    from observability.metrics import grant_enforcement_denials_total

    return grant_enforcement_denials_total.labels(mode=mode, reason=reason)._value.get()


# ── Mode resolution ──────────────────────────────────────────────────────


async def test_mode_defaults_to_off_without_tenant_flags(monkeypatch):
    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "off")
    with patch("core.feature_flags.load_flag_rows_strict", _flags()):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.OFF


async def test_deployment_default_applies_when_tenant_has_no_flags(monkeypatch):
    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "warn")
    with patch("core.feature_flags.load_flag_rows_strict", _flags()):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.WARN


async def test_tenant_warn_flag_overrides_off_default(monkeypatch):
    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "off")
    with patch("core.feature_flags.load_flag_rows_strict", _flags(warn=True)):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.WARN


async def test_deny_flag_wins_over_warn_flag(monkeypatch):
    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "off")
    with patch("core.feature_flags.load_flag_rows_strict", _flags(warn=True, deny=True)):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.DENY


async def test_requested_deny_is_honoured_without_a_cap(monkeypatch):
    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "off")
    with patch("core.feature_flags.load_flag_rows_strict", _flags(deny=True)), capture_logs() as logs:
        mode = await resolve_enforcement_mode(TENANT)
    assert mode is EnforcementMode.DENY
    assert not any(entry["event"] == "grant_enforcement_deny_unavailable" for entry in logs)


async def test_tenant_flags_cannot_weaken_the_deployment_default(monkeypatch):
    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "deny")
    with patch("core.feature_flags.load_flag_rows_strict", _flags(warn=True)):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.DENY
    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "warn")
    with patch("core.feature_flags.load_flag_rows_strict", _flags()):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.WARN


async def test_unreadable_flag_store_with_no_known_mode_fails_closed_to_deny(monkeypatch):
    from core.feature_flags import FeatureFlagLookupError
    from observability.metrics import grant_enforcement_mode_fallbacks_total

    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "off")
    counter = grant_enforcement_mode_fallbacks_total.labels(outcome="deny")
    before = counter._value.get()
    failing = AsyncMock(side_effect=FeatureFlagLookupError("down"))
    with patch("core.feature_flags.load_flag_rows_strict", failing), capture_logs() as logs:
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.DENY
    events = [entry for entry in logs if entry["event"] == "grant_enforcement_mode_lookup_failed"]
    assert [(e["reason_code"], e["effective_mode"]) for e in events] == [("flag_store_unreadable", "deny")]
    assert counter._value.get() == before + 1


async def test_unreadable_flag_store_keeps_the_tenants_last_known_stricter_mode(monkeypatch):
    from core.feature_flags import FeatureFlagLookupError

    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "off")
    with patch("core.feature_flags.load_flag_rows_strict", _flags(warn=True)):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.WARN
    failing = AsyncMock(side_effect=FeatureFlagLookupError("down"))
    with patch("core.feature_flags.load_flag_rows_strict", failing):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.WARN


async def test_mode_without_a_tenant_is_the_deployment_default(monkeypatch):
    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "warn")
    strict = AsyncMock()
    with patch("core.feature_flags.load_flag_rows_strict", strict):
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


@pytest.mark.real_flag_store
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
                await feature_flags.load_flag_rows_strict(ge.FLAG_DENY, tenant_id=uuid.UUID(TENANT))
            # The lenient evaluator keeps its documented default on the same failure.
            assert await feature_flags.is_enabled(ge.FLAG_DENY, tenant_id=uuid.UUID(TENANT)) is False
            # A failed strict read is never cached: the next read probes the store again.
            with pytest.raises(feature_flags.FeatureFlagLookupError):
                await feature_flags.load_flag_rows_strict(ge.FLAG_DENY, tenant_id=uuid.UUID(TENANT))
        assert len(attempts) == 3
    finally:
        feature_flags.clear_cache()


# ── Reason classification ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("reason_code", "sdk_sub_reason", "reason", "sub_reason"),
    [
        ("token_invalid", "", DenialReason.TOKEN_INVALID, ""),
        (
            "token_invalid",
            "malformed_authorization_details",
            DenialReason.TOKEN_INVALID,
            "malformed_authorization_details",
        ),
        ("grant_revoked", "", DenialReason.GRANT_REVOKED, ""),
        ("manifest_unknown_tool", "unknown_connector", DenialReason.MANIFEST_UNKNOWN_TOOL, "unknown_connector"),
        ("manifest_unknown_tool", "unknown_tool", DenialReason.MANIFEST_UNKNOWN_TOOL, "unknown_tool"),
        ("tool_not_granted", "", DenialReason.TOOL_NOT_GRANTED, ""),
        ("permission_insufficient", "", DenialReason.PERMISSION_INSUFFICIENT, ""),
        ("cap_exceeded", "amount", DenialReason.CAP_EXCEEDED, "amount"),
        ("purpose_not_allowed", "", DenialReason.PURPOSE_NOT_ALLOWED, ""),
        ("decision_required", "", DenialReason.DECISION_REQUIRED, ""),
        ("decision_invalid", "expired", DenialReason.DECISION_INVALID, "expired"),
        ("region_mismatch", "", DenialReason.REGION_MISMATCH, ""),
        ("something_new", "", DenialReason.UNCLASSIFIED, "unknown_reason_code"),
        ("", "", DenialReason.UNCLASSIFIED, "no_reason_code"),
    ],
)
def test_enforce_reason_codes_map_exactly_to_the_denial_vocabulary(reason_code, sdk_sub_reason, reason, sub_reason):
    result = SimpleNamespace(
        allowed=False, reason="text is ignored", reason_code=reason_code, sub_reason=sdk_sub_reason
    )
    assert classify_enforce_result(result) == (reason, sub_reason)


def test_result_without_reason_codes_is_unclassified_even_when_its_text_names_a_reason():
    # An SDK older than reason codes: the text is not parsed for a reason.
    result = SimpleNamespace(allowed=False, reason="No scope grants access to connector 'hubspot'.")
    assert classify_enforce_result(result) == (DenialReason.UNCLASSIFIED, "no_reason_code")


# ── Per-call checks ──────────────────────────────────────────────────────


def _client(
    allowed: bool, reason_code: str = "", grant_id: str = "grnt_placeholder", sub_reason: str = ""
) -> MagicMock:
    client = MagicMock()
    client.enforce.return_value = SimpleNamespace(
        allowed=allowed,
        reason=reason_code.replace("_", " "),
        reason_code="" if allowed else reason_code,
        sub_reason=sub_reason,
        grant_id=grant_id,
    )
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
        _client(False, "tool_not_granted"),
        None,
        DenialReason.TOOL_NOT_GRANTED,
        "",
        id="tool_not_granted",
    ),
    pytest.param(
        _client(False, "permission_insufficient"),
        None,
        DenialReason.PERMISSION_INSUFFICIENT,
        "",
        id="permission_insufficient",
    ),
    pytest.param(_client(False, "grant_revoked"), None, DenialReason.GRANT_REVOKED, "", id="grant_revoked"),
    pytest.param(
        _client(False, "token_invalid"),
        None,
        DenialReason.TOKEN_INVALID,
        "",
        id="token_invalid",
    ),
    pytest.param(
        _client(False, "cap_exceeded"),
        None,
        DenialReason.CAP_EXCEEDED,
        "",
        id="cap_exceeded",
    ),
    pytest.param(
        _client(False, "manifest_unknown_tool", sub_reason="unknown_tool"),
        None,
        DenialReason.MANIFEST_UNKNOWN_TOOL,
        "unknown_tool",
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

    client = _client(False, "permission_insufficient")
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

    client = _client(False, "tool_not_granted")
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
