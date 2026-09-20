# SPDX-License-Identifier: Apache-2.0
"""PRD F-1 — reasons for denials from Grantex SDKs without reason codes (0.5.x).

The pinned SDK (``grantex==0.5.1``) returns only a message. Each test makes the
installed SDK's real ``Grantex.enforce`` produce one of its denial messages and
checks it maps to the Appendix B reason (and the sub-reason the 0.6 SDK uses).
Messages that match none of the known ones stay ``unclassified`` and still
deny. Reason codes, when an SDK returns them, win over the message.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import jwt
import pytest
from grantex import Grantex
from grantex.manifest import ToolManifest

from auth.grant_enforcement import DenialReason, classify_enforce_result

API_KEY = "placeholder-api-key"  # noqa: S105 - not a credential


def _client() -> Grantex:
    client = Grantex(api_key=API_KEY, base_url="https://grantex.invalid")
    client.load_manifest(ToolManifest("hubspot", {"list_contacts": "read", "create_contact": "write"}))
    return client


def _enforce(scopes: list[str], *, connector: str = "hubspot", tool: str = "list_contacts", amount=None, error=None):
    grant = SimpleNamespace(grant_id="grnt_placeholder", agent_did="did:placeholder", scopes=scopes)
    verify = (
        patch("grantex._client.verify_grant_token", side_effect=error)
        if error
        else patch("grantex._client.verify_grant_token", return_value=grant)
    )
    with verify:
        result = _client().enforce(grant_token="placeholder", connector=connector, tool=tool, amount=amount)
    assert result.allowed is False
    return result


def _without_codes(result) -> SimpleNamespace:
    """The 0.5.x shape: no reason_code attribute at all."""
    return SimpleNamespace(allowed=False, reason=result.reason, grant_id=result.grant_id)


def _classify(result, connector: str = "hubspot", tool: str = "list_contacts"):
    return classify_enforce_result(_without_codes(result), connector=connector, tool=tool)


def test_expired_token_message_is_token_invalid_expired():
    result = _enforce([], error=jwt.ExpiredSignatureError("Signature has expired"))
    assert _classify(result) == (DenialReason.TOKEN_INVALID, "expired")


def test_other_verification_failure_message_is_token_invalid():
    result = _enforce([], error=jwt.InvalidSignatureError("Signature verification failed"))
    assert _classify(result) == (DenialReason.TOKEN_INVALID, "")


def test_no_manifest_message_is_manifest_unknown_tool_unknown_connector():
    result = _enforce(["tool:salesforce:read:query"], connector="salesforce", tool="query")
    assert _classify(result, "salesforce", "query") == (DenialReason.MANIFEST_UNKNOWN_TOOL, "unknown_connector")


def test_unknown_tool_message_is_manifest_unknown_tool_unknown_tool():
    result = _enforce(["tool:hubspot:admin:all"], tool="delete_everything")
    assert _classify(result, tool="delete_everything") == (DenialReason.MANIFEST_UNKNOWN_TOOL, "unknown_tool")


def test_no_scope_message_is_tool_not_granted():
    result = _enforce(["tool:salesforce:read:query"])
    assert _classify(result) == (DenialReason.TOOL_NOT_GRANTED, "")


def test_permission_message_is_permission_insufficient():
    result = _enforce(["tool:hubspot:read:list_contacts"], tool="create_contact")
    assert _classify(result, tool="create_contact") == (DenialReason.PERMISSION_INSUFFICIENT, "")


def test_amount_over_cap_message_is_cap_exceeded():
    result = _enforce(["tool:hubspot:write:create_contact:capped:100"], tool="create_contact", amount=500)
    assert _classify(result, tool="create_contact") == (DenialReason.CAP_EXCEEDED, "amount_cap")


def _sdk_has_amount_validation() -> bool:
    import inspect

    return "Amount must be a finite number" in inspect.getsource(Grantex.enforce)


@pytest.mark.skipif(not _sdk_has_amount_validation(), reason="message added in grantex 0.5.1")
def test_non_finite_amount_message_is_cap_exceeded_invalid_amount():
    result = _enforce(["tool:hubspot:write:create_contact:capped:100"], tool="create_contact", amount=float("nan"))
    assert _classify(result, tool="create_contact") == (DenialReason.CAP_EXCEEDED, "invalid_amount")


@pytest.mark.skipif(not _sdk_has_amount_validation(), reason="message added in grantex 0.5.1")
def test_malformed_cap_message_is_cap_exceeded_malformed_cap():
    result = _enforce(["tool:hubspot:write:create_contact:capped:lots"], tool="create_contact", amount=5)
    assert _classify(result, tool="create_contact") == (DenialReason.CAP_EXCEEDED, "malformed_cap")


def test_an_unknown_message_is_unclassified_and_still_a_denial():
    result = SimpleNamespace(allowed=False, reason="Something new went wrong")
    assert classify_enforce_result(result, connector="hubspot", tool="list_contacts") == (
        DenialReason.UNCLASSIFIED,
        "unknown_message",
    )


def test_a_message_for_a_different_connector_does_not_match():
    result = SimpleNamespace(allowed=False, reason="No scope grants access to connector 'salesforce'.")
    assert classify_enforce_result(result, connector="hubspot", tool="list_contacts")[0] is DenialReason.UNCLASSIFIED


def test_reason_codes_win_over_the_message():
    result = SimpleNamespace(
        allowed=False,
        reason="No scope grants access to connector 'hubspot'.",
        reason_code="purpose_not_allowed",
        sub_reason="not_matched",
    )
    assert classify_enforce_result(result, connector="hubspot", tool="list_contacts") == (
        DenialReason.PURPOSE_NOT_ALLOWED,
        "not_matched",
    )


def test_a_reason_code_sdk_that_gives_no_code_is_not_guessed_from_text():
    result = SimpleNamespace(allowed=False, reason="No scope grants access to connector 'hubspot'.", reason_code="")
    assert classify_enforce_result(result, connector="hubspot", tool="list_contacts") == (
        DenialReason.UNCLASSIFIED,
        "no_reason_code",
    )


def test_every_denial_message_in_the_installed_sdk_is_covered():
    """Guard: every message ``Grantex.enforce`` can build maps to a reason, not ``unclassified``."""
    import inspect
    import re

    from auth.grant_enforcement import _classify_sdk_05_message

    source = inspect.getsource(Grantex.enforce)
    if "reason_code" in source:
        pytest.skip("SDK returns reason codes; the message table is not used")
    values = {
        "e": "Signature verification failed",
        "connector": "hubspot",
        "tool": "list_contacts",
        "granted_permission": "read",
        "required_permission": "write",
        "amount": "5",
        "cap": "1.0",
    }
    messages = [m for m in re.findall(r'f"((?:[^"\\]|\\.)*)"', source) if not m.startswith("[grantex]")]
    assert len(messages) >= 5, messages
    for template in messages:
        message = re.sub(r"\{(\w+)\}", lambda match: values[match.group(1)], template)
        reason, _ = _classify_sdk_05_message(message, connector="hubspot", tool="list_contacts")
        assert reason is not DenialReason.UNCLASSIFIED, template
