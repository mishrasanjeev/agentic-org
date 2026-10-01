# SPDX-License-Identifier: Apache-2.0
"""A run grant whose scopes name tools covers only those tools (FINDINGS A-109).

The Grantex SDK's ``enforce`` reads ``tool:<connector>:<permission>:<tool>`` as a
connector-level permission and ignores the tool segment, so a grant delegated with
``tool:mock:read:screen_person`` would pass ``ownership`` on ``mock``. ``check_run_grant``
refuses that before the SDK is asked, in deny mode; records it and proceeds in warn mode;
and leaves a connector-level scope to the SDK.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from structlog.testing import capture_logs

from auth.grant_enforcement import DenialReason, EnforcementMode, GrantCallContext
from auth.run_grants import RunGrant, check_run_grant, tool_scope_denial

SCREENING = ("tool:mock:read:screen_business", "tool:mock:read:screen_person")
CONTEXT = GrantCallContext(
    tenant_id="t", agent_id="a", agent_type="screening_disposition", runtime="governed_case", grant_source="minted"
)


def _grant(scopes: tuple[str, ...], mode: EnforcementMode = EnforcementMode.DENY) -> RunGrant:
    return RunGrant(mode=mode, token="placeholder-run-grant", source="minted", grant_id="grnt_x", scopes=scopes)  # noqa: S106


def _allowing_client() -> MagicMock:
    client = MagicMock()
    client.enforce.return_value = SimpleNamespace(allowed=True, grant_id="grnt_x")
    return client


def test_tool_scope_denial_names_the_tool_the_scopes_leave_out() -> None:
    denial = tool_scope_denial(SCREENING, connector="mock", tool="ownership")
    assert denial is not None
    assert denial.reason is DenialReason.TOOL_NOT_GRANTED and denial.sub_reason == "tool_scope_missing"
    assert tool_scope_denial(SCREENING, connector="mock", tool="screen_person") is None
    # Another connector's scopes say nothing about this one; the SDK decides.
    assert tool_scope_denial(SCREENING, connector="hubspot", tool="get_contact") is None
    # A connector-level scope keeps the SDK's permission reading.
    assert tool_scope_denial(("tool:mock:read",), connector="mock", tool="ownership") is None
    assert tool_scope_denial(("tool:mock:read", *SCREENING), connector="mock", tool="ownership") is None
    assert tool_scope_denial(("agenticorg:mock:read:screen_person",), connector="mock", tool="ownership") is not None
    assert tool_scope_denial((), connector="mock", tool="ownership") is None


def test_deny_mode_refuses_a_tool_outside_the_named_scopes_before_the_sdk() -> None:
    client = _allowing_client()
    check = asyncio.run(
        check_run_grant(
            _grant(SCREENING), connector="mock", tool="ownership", context=CONTEXT, client_factory=lambda: client
        )
    )
    assert not check.dispatch_allowed
    assert check.denial is not None and check.denial.reason is DenialReason.TOOL_NOT_GRANTED
    assert check.denial.sub_reason == "tool_scope_missing"
    client.enforce.assert_not_called()


def test_a_named_tool_goes_on_to_the_sdk_check() -> None:
    client = _allowing_client()
    check = asyncio.run(
        check_run_grant(
            _grant(SCREENING), connector="mock", tool="screen_person", context=CONTEXT, client_factory=lambda: client
        )
    )
    assert check.dispatch_allowed and check.denial is None
    client.enforce.assert_called_once()


def test_warn_mode_records_the_denial_and_proceeds() -> None:
    client = _allowing_client()
    with capture_logs() as logs:
        check = asyncio.run(
            check_run_grant(
                _grant(SCREENING, EnforcementMode.WARN),
                connector="mock",
                tool="ownership",
                context=CONTEXT,
                client_factory=lambda: client,
            )
        )
    assert check.dispatch_allowed
    assert any(log.get("sub_reason") == "tool_scope_missing" for log in logs)
    client.enforce.assert_called_once()


def test_off_mode_is_untouched() -> None:
    grant = RunGrant(mode=EnforcementMode.OFF, token="placeholder", source="supplied", scopes=SCREENING)  # noqa: S106
    assert tool_scope_denial(grant.scopes, connector="mock", tool="ownership") is not None
    # check_run_grant is only for warn and deny; off keeps the legacy path, which never reaches it.
    assert grant.call_mode is EnforcementMode.OFF
