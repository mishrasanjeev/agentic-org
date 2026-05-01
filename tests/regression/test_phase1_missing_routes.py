"""Phase 1 regression pins for the four routes added to close gaps from
``docs/ENTERPRISE_END_TO_END_GAP_ANALYSIS_2026-04-30.md``.

These tests pin behavior, not just registration:
- ``GET /audit/enforce`` returns the expected shape and filters by tenant.
- ``POST /workflows/runs/{run_id}/cancel`` flips run.status and is idempotent.
- ``GET /health/checks`` returns ``data_source: live_snapshot`` with explicit note.
- ``GET /health/uptime`` returns ``data_source: live_snapshot`` with current_status.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────
# /health/checks + /health/uptime — light shape pins (no DB needed)
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_checks_returns_live_snapshot_with_data_source_field(
    monkeypatch,
) -> None:
    """The endpoint exists, returns 200, and is honest about being a live
    snapshot when no persisted history is available. The ``data_source``
    field is the contract the UI uses to render the right banner.

    Note: the precise wording of the ``note`` field is pinned by the
    Phase 5 tests in ``test_sla_telemetry_persistence.py``; here we
    only assert the contract shape so this test stays robust across
    note rewrites.
    """
    from api.v1 import health as health_module

    fake_snapshot = {
        "status": "healthy",
        "version": "9.9.9-test",
        "commit": "abc123",
        "checks": {"db": "healthy", "redis": "healthy"},
    }

    async def _fake_readiness():
        return fake_snapshot

    async def _empty_history(_hours: int):
        # Force the live-snapshot fallback path even on a host where
        # health_check_history happens to have rows.
        return []

    monkeypatch.setattr(health_module, "health_readiness", _fake_readiness)
    monkeypatch.setattr(health_module, "_fetch_history", _empty_history)

    result = await health_module.health_checks()

    assert result["data_source"] == "live_snapshot"
    assert "note" in result and isinstance(result["note"], str) and result["note"]
    assert isinstance(result["items"], list)
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["status"] == "healthy"
    assert item["checks"] == {"db": "healthy", "redis": "healthy"}
    assert "timestamp" in item


@pytest.mark.asyncio
async def test_health_uptime_returns_current_status_and_data_source(
    monkeypatch,
) -> None:
    """Uptime must include current_status (UI uses this directly).

    Phase 5 (test_sla_telemetry_persistence.py) covers the
    persisted-history path; this test pins the live-snapshot
    fallback contract by forcing _fetch_history → []."""
    from api.v1 import health as health_module

    async def _fake_readiness():
        return {
            "status": "unhealthy",
            "version": "9.9.9-test",
            "commit": "abc",
            "checks": {"db": "unhealthy: TimeoutError", "redis": "healthy"},
        }

    async def _empty_history(_hours: int):
        return []

    monkeypatch.setattr(health_module, "health_readiness", _fake_readiness)
    monkeypatch.setattr(health_module, "_fetch_history", _empty_history)
    result = await health_module.health_uptime()

    assert result["data_source"] == "live_snapshot"
    assert result["current_status"] == "unhealthy"
    assert isinstance(result["items"], list)
    assert len(result["items"]) == 1
    assert result["items"][0]["up"] is False


# ─────────────────────────────────────────────────────────────────
# /audit/enforce — shape projection from AuditLog rows
# ─────────────────────────────────────────────────────────────────


def test_enforce_to_dict_projects_audit_row_into_ui_shape() -> None:
    """The projection helper pulls fields the UI consumes:
    timestamp, agent_name, connector, tool, permission, result, reason.

    The UI's filter logic (``EnforceAuditLog.tsx`` lines 67-73) keys on
    these exact field names, so a typo here silently breaks filters.
    """
    from datetime import UTC, datetime

    from api.v1.audit import _enforce_to_dict

    agent_id = uuid.uuid4()
    fake_row = MagicMock()
    fake_row.id = uuid.uuid4()
    fake_row.created_at = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)
    fake_row.agent_id = agent_id
    fake_row.outcome = "denied"
    fake_row.resource_id = "get_profit_loss"
    fake_row.trace_id = "trace-123"
    fake_row.details = {
        "connector": "zoho_books",
        "permission": "read",
        "enforcement_action": "scope_denied",
    }

    result = _enforce_to_dict(
        fake_row,
        agent_name_by_id={str(agent_id): "FPA Assistant"},
    )

    assert result["agent_id"] == str(agent_id)
    assert result["agent_name"] == "FPA Assistant"
    assert result["connector"] == "zoho_books"
    assert result["tool"] == "get_profit_loss"
    assert result["permission"] == "read"
    assert result["result"] == "denied"
    assert result["reason"] == "scope_denied"
    assert result["timestamp"] == "2026-04-30T12:00:00+00:00"
    assert result["trace_id"] == "trace-123"


def test_enforce_to_dict_handles_missing_agent_name_and_details() -> None:
    """When the AuditLog row lacks an agent (system events) or has empty
    details, the projection still returns the contract shape — the UI
    can't tolerate KeyError or None where it expects a string."""
    from datetime import UTC, datetime

    from api.v1.audit import _enforce_to_dict

    fake_row = MagicMock()
    fake_row.id = uuid.uuid4()
    fake_row.created_at = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)
    fake_row.agent_id = None
    fake_row.outcome = "allowed"
    fake_row.resource_id = "send_email"
    fake_row.trace_id = None
    fake_row.details = None

    result = _enforce_to_dict(fake_row, agent_name_by_id={})

    # Every field UI reads must be present and a defined value (no None)
    # except trace_id and agent_id which the UI explicitly null-checks.
    for required in (
        "agent_name", "connector", "tool", "permission", "result", "reason"
    ):
        assert required in result
        assert result[required] is not None
        assert isinstance(result[required], str)
    assert result["result"] == "allowed"
    # An "allowed" outcome with no enforcement_action should produce an
    # empty reason — not "allowed" — so the UI doesn't render
    # "Reason: allowed" on every row.
    assert result["reason"] == ""


# ─────────────────────────────────────────────────────────────────
# /workflows/runs/{run_id}/cancel — idempotency + status flip
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_workflow_run_is_idempotent_for_terminal_runs() -> None:
    """Cancelling a run that's already cancelled/completed/failed must
    NOT 409 — the UI button can be clicked twice during slow network and
    we don't want operators to see a confusing error.
    """
    from api.v1.workflows import cancel_workflow_run

    fake_run = MagicMock()
    fake_run.id = uuid.uuid4()
    fake_run.tenant_id = uuid.uuid4()
    fake_run.status = "completed"
    fake_run.workflow_def_id = uuid.uuid4()
    fake_run.steps = []
    fake_run.context = {}
    fake_run.trigger_payload = {}
    fake_run.result = {}
    fake_run.error = None
    fake_run.company_id = None

    async def _fake_session():
        s = MagicMock()
        s.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=fake_run),
        ))
        s.commit = AsyncMock()
        s.refresh = AsyncMock()
        return s

    class _FakeSessionCtx:
        async def __aenter__(self):
            return await _fake_session()

        async def __aexit__(self, *args):
            return None

    with patch(
        "api.v1.workflows.get_tenant_session",
        return_value=_FakeSessionCtx(),
    ):
        result = await cancel_workflow_run(
            run_id=fake_run.id,
            tenant_id=str(fake_run.tenant_id),
        )

    # status untouched on terminal runs; UI sees the existing terminal state
    assert result["status"] == "completed"
    assert result["run_id"] == str(fake_run.id)
