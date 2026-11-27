# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The tool gateway's ``execute`` audit row names its grant and carries matchable digests.

A dispatched call's row says what authorised it and which grant was verified, and its
``input_hash``/``output_hash`` are the full canonical ``sha256:`` digests the governed-case
evidence ``tool_call`` records use, taken over the values the connector actually received
and returned. The audit logger's PII masking must not rewrite them.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auth.grant_enforcement import EnforcementMode, GrantCheck
from auth.run_grants import NO_RUN_GRANT_FOR_TESTS, RunGrant
from core.cases.evidence import digest_of
from core.tool_gateway.audit_logger import AuditLogger
from core.tool_gateway.gateway import ToolGateway
from core.tool_gateway.pii_masker import mask_pii
from core.tool_gateway.provider_gateway import canonical_sha256

PARAMS = {"customer_phone": "9876543210", "amount": 420, "note": "refund"}
RESULT = {"status": "ok", "reference": "rf_1234567890"}


def _enforce(allowed: bool = True, grant_id: str = "grnt_verified") -> MagicMock:
    result = MagicMock()
    result.allowed = allowed
    result.reason = "" if allowed else "denied"
    result.grant_id = grant_id
    return result


def _gateway(connector: Any) -> tuple[ToolGateway, MagicMock]:
    audit = AuditLogger(db_session_factory=None)
    gateway = ToolGateway(audit_logger=audit)
    gateway.register_connector("stripe", connector)
    return gateway, audit


def _execute_rows(log: MagicMock) -> list[dict[str, Any]]:
    return [c.kwargs for c in log.info.call_args_list if c.args == ("audit_log",) and c.kwargs["action"] == "execute"]


def _connector(result: Any = None, error: Exception | None = None) -> MagicMock:
    connector = MagicMock()
    connector.execute_tool = AsyncMock(return_value=result, side_effect=error)
    return connector


async def _run(gateway: ToolGateway, **kwargs: Any) -> dict[str, Any]:
    call: dict[str, Any] = {
        "tenant_id": "t1",
        "agent_id": "a1",
        "agent_scopes": ["tool:stripe:admin"],
        "connector_name": "stripe",
        "tool_name": "process_refund",
        "params": dict(PARAMS),
        "run_grant": NO_RUN_GRANT_FOR_TESTS,
    }
    call.update(kwargs)
    return await gateway.execute(**call)


def _altered_digest() -> str:
    """A real digest the PII masking patterns rewrite (they match digit runs inside it)."""
    for i in range(10_000):
        digest = canonical_sha256({"i": i})
        if mask_pii({"h": digest})["h"] != digest:
            return digest
    raise AssertionError("no digest found that masking alters")


@pytest.mark.asyncio
async def test_granted_call_row_names_the_verified_grant_and_canonical_digests() -> None:
    gateway, _ = _gateway(_connector(RESULT))
    client = MagicMock()
    client.enforce.return_value = _enforce(grant_id="grnt_verified")
    with (
        patch("core.langgraph.grantex_auth.get_grantex_client", return_value=client),
        patch("core.tool_gateway.audit_logger.logger") as log,
    ):
        result = await _run(gateway, grant_token="grantex-test-token")  # noqa: S106

    assert result == RESULT
    [row] = _execute_rows(log)
    details = row["details"]
    assert row["outcome"] == "success"
    assert details["authorized_by"] == "grant"
    assert details["grant_id"] == "grnt_verified"
    assert "run_grant_id" not in details
    # Over the raw values the connector received, not the masked ones, so the row can be
    # matched to the evidence tool_call record for the same call.
    assert details["input_hash"] == canonical_sha256(PARAMS) == digest_of(PARAMS)
    assert details["output_hash"] == canonical_sha256(RESULT)
    assert details["input_hash"] != canonical_sha256(mask_pii(PARAMS))
    assert "grantex-test-token" not in repr(row)


@pytest.mark.asyncio
async def test_run_grant_id_is_kept_when_the_verified_grant_differs() -> None:
    gateway, _ = _gateway(_connector(RESULT))
    client = MagicMock()
    client.enforce.return_value = _enforce(grant_id="grnt_caller")
    run_grant = RunGrant(mode=EnforcementMode.DENY, token="run-token", source="minted", grant_id="grnt_run")  # noqa: S106
    with (
        patch("core.tool_gateway.gateway.check_run_grant", AsyncMock(return_value=GrantCheck(dispatch_allowed=True))),
        patch("core.langgraph.grantex_auth.get_grantex_client", return_value=client),
        patch("core.tool_gateway.audit_logger.logger") as log,
    ):
        await _run(gateway, grant_token="caller-token", run_grant=run_grant)  # noqa: S106

    [row] = _execute_rows(log)
    assert row["details"]["authorized_by"] == "grant"
    assert row["details"]["grant_id"] == "grnt_caller"
    assert row["details"]["run_grant_id"] == "grnt_run"


@pytest.mark.asyncio
async def test_run_grant_alone_names_the_run_grant() -> None:
    gateway, _ = _gateway(_connector(RESULT))
    run_grant = RunGrant(mode=EnforcementMode.DENY, token="", source="minted", grant_id="grnt_run")
    with (
        patch("core.tool_gateway.gateway.check_run_grant", AsyncMock(return_value=GrantCheck(dispatch_allowed=True))),
        patch("core.tool_gateway.audit_logger.logger") as log,
    ):
        await _run(gateway, run_grant=run_grant)

    [row] = _execute_rows(log)
    assert row["details"]["authorized_by"] == "run_grant"
    assert row["details"]["grant_id"] == "grnt_run"


@pytest.mark.asyncio
async def test_legacy_scope_call_says_so_and_names_no_grant() -> None:
    gateway, _ = _gateway(_connector(RESULT))
    with patch("core.tool_gateway.audit_logger.logger") as log:
        await _run(gateway)

    [row] = _execute_rows(log)
    assert row["details"]["authorized_by"] == "legacy_scopes"
    assert row["details"]["grant_id"] is None


@pytest.mark.asyncio
async def test_connector_error_row_names_the_grant_and_input_digest() -> None:
    gateway, _ = _gateway(_connector(error=RuntimeError("provider down")))
    client = MagicMock()
    client.enforce.return_value = _enforce(grant_id="grnt_verified")
    with (
        patch("core.langgraph.grantex_auth.get_grantex_client", return_value=client),
        patch("core.tool_gateway.audit_logger.logger") as log,
    ):
        result = await _run(gateway, grant_token="grantex-test-token")  # noqa: S106

    assert result["error"]["code"] == "E1001"
    [row] = _execute_rows(log)
    assert row["outcome"] == "error"
    assert row["details"]["grant_id"] == "grnt_verified"
    assert row["details"]["input_hash"] == canonical_sha256(PARAMS)
    assert "output_hash" not in row["details"]


@pytest.mark.asyncio
async def test_audit_digests_survive_pii_masking_and_are_signed() -> None:
    digest = _altered_digest()
    with patch("core.tool_gateway.audit_logger.logger") as log:
        await AuditLogger(db_session_factory=None).log(
            tenant_id="t1",
            action="execute",
            outcome="success",
            details={"phone": "9876543210"},
            digests={"input_hash": digest},
        )
    entry = log.info.call_args.kwargs
    assert entry["details"]["input_hash"] == digest
    assert entry["details"]["phone"] != "9876543210"
    from core.tool_gateway.audit_logger import verify_audit_row

    assert verify_audit_row(entry)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["9876543210", "sha256:" + "A" * 64, "deadbeef", None])
async def test_audit_digests_refuse_anything_but_a_sha256_digest(value: Any) -> None:
    with pytest.raises(ValueError, match="not a sha256 digest"):
        await AuditLogger(db_session_factory=None).log(tenant_id="t1", digests={"input_hash": value})
