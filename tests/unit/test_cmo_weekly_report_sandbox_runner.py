"""CMO-PROD-3 sandbox runner acceptance tests.

These tests prove the runner is fail-closed: with no credentials it
refuses to insert a proof row; with sandbox evidence it persists
sandbox/partial only (never real-vendor passed); secrets are redacted
in the runbook output.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from core.marketing.weekly_report_pilot_proof import (
    REQUIRED_BACKFILL_CATEGORIES,
    REQUIRED_KPI_KEYS,
    REQUIRED_MAPPINGS,
)
from core.marketing.weekly_report_sandbox_pilot import (
    CMO_PROD_3_VERSION,
    SANDBOX_CONNECTOR_OPTIONS,
    SANDBOX_ENV_PREFIX,
    build_blocked_preflight_envelope,
    discover_sandbox_pilot_config,
    run_sandbox_pilot,
)
from core.models.weekly_report_pilot_proof import WeeklyReportPilotProof

_TENANT_UUID = "00000000-0000-0000-0000-000000000099"
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _populated_env() -> dict[str, str]:
    """Return an env-var dict that satisfies preflight with one option per category."""

    env: dict[str, str] = {
        "AGENTICORG_DB_URL": "postgresql+asyncpg://test:test@localhost/test",
        f"{SANDBOX_ENV_PREFIX}TENANT_ID": _TENANT_UUID,
        f"{SANDBOX_ENV_PREFIX}HUBSPOT_ACCESS_TOKEN": "hub-secret-xyz",  # noqa: S105
        f"{SANDBOX_ENV_PREFIX}GA4_PROPERTY_ID": "ga4-prop-123",
        f"{SANDBOX_ENV_PREFIX}GA4_REFRESH_TOKEN": "ga4-refresh-xyz",  # noqa: S105
        f"{SANDBOX_ENV_PREFIX}GA4_CLIENT_ID": "ga4-client",
        f"{SANDBOX_ENV_PREFIX}GA4_CLIENT_SECRET": "ga4-secret",  # noqa: S105
        f"{SANDBOX_ENV_PREFIX}SENDGRID_API_KEY": "sg-key-xyz",  # noqa: S105
        f"{SANDBOX_ENV_PREFIX}SENDGRID_SENDER": "noreply@sandbox.example",
    }
    # Choose Google Ads as the Ads connector.
    env.update(
        {
            f"{SANDBOX_ENV_PREFIX}GOOGLE_ADS_DEVELOPER_TOKEN": "dev-tok-xyz",  # noqa: S105
            f"{SANDBOX_ENV_PREFIX}GOOGLE_ADS_REFRESH_TOKEN": "refresh-xyz",  # noqa: S105
            f"{SANDBOX_ENV_PREFIX}GOOGLE_ADS_CUSTOMER_ID": "123-456-7890",
            f"{SANDBOX_ENV_PREFIX}GOOGLE_ADS_CLIENT_ID": "gads-client",
            f"{SANDBOX_ENV_PREFIX}GOOGLE_ADS_CLIENT_SECRET": "gads-secret",  # noqa: S105
        }
    )
    return env


def _base_db_env() -> dict[str, str]:
    return {
        "AGENTICORG_DB_URL": "postgresql+asyncpg://test:test@localhost/test",
        f"{SANDBOX_ENV_PREFIX}TENANT_ID": _TENANT_UUID,
    }


def _db_row(
    connector_name: str,
    category: str,
    *,
    proof_scope: str = "vendor_sandbox",
    local_test_only: bool = False,
    mock_or_test_double: bool = False,
    status: str = "configured",
    health_status: str = "healthy",
) -> dict[str, Any]:
    return {
        "connector_name": connector_name,
        "display_name": f"{connector_name} display",
        "status": status,
        "health_status": health_status,
        "config": {
            "cmo_category": category,
            "proof_scope": proof_scope,
            "local_test_only": local_test_only,
            "mock_or_test_double": mock_or_test_double,
        },
        "credentials_encrypted": {"ciphertext_ref": f"{connector_name}-credential-ref"},
    }


def _vendor_sandbox_rows() -> list[dict[str, Any]]:
    return [
        _db_row("hubspot_sandbox", "CRM"),
        _db_row("google_ads_sandbox", "Ads"),
        _db_row("ga4_sandbox", "Analytics"),
        _db_row("sendgrid_sandbox", "Email"),
    ]


def _local_preflight_rows() -> list[dict[str, Any]]:
    return [
        _db_row("hubspot_local", "CRM", proof_scope="preflight_only", local_test_only=True),
        _db_row("google_ads_local", "Ads", proof_scope="preflight_only", local_test_only=True),
        _db_row("ga4_local", "Analytics", proof_scope="preflight_only", local_test_only=True),
        _db_row("sendgrid_local", "Email", proof_scope="preflight_only", local_test_only=True),
    ]


# ---------------------------------------------------------------------------
# Preflight: missing creds → blocked
# ---------------------------------------------------------------------------


def test_preflight_with_empty_env_is_blocked() -> None:
    preflight = discover_sandbox_pilot_config({})
    assert preflight.preflight_status == "blocked"
    assert "AGENTICORG_DB_URL" in {b.get("missing_env") for b in preflight.blockers}
    assert preflight.config.missing_categories == ["CRM", "Ads", "Analytics", "Email"]
    envelope = build_blocked_preflight_envelope(preflight)
    assert envelope["environment_type"] == "vendor_sandbox"
    assert envelope["proof_status"] == "unavailable"
    assert envelope["production_claim_allowed"] is False
    assert envelope["real_vendor_claim_allowed"] is False
    assert envelope["schema_version"] == CMO_PROD_3_VERSION


def test_preflight_with_complete_env_is_ready_and_picks_connectors() -> None:
    preflight = discover_sandbox_pilot_config(_populated_env(), connector_rows=[])
    assert preflight.preflight_status == "ready"
    chosen = preflight.config.chosen_connectors
    assert set(chosen) == {"CRM", "Ads", "Analytics", "Email"}
    assert chosen["CRM"]["connector_key"] == "hubspot"
    assert chosen["Ads"]["connector_key"] == "google_ads"
    assert chosen["Analytics"]["connector_key"] == "ga4"
    assert chosen["Email"]["connector_key"] == "sendgrid"


def test_db_connector_config_rows_satisfy_preflight_by_category() -> None:
    preflight = discover_sandbox_pilot_config(
        _base_db_env(),
        connector_rows=_vendor_sandbox_rows(),
    )

    assert preflight.preflight_status == "ready"
    assert preflight.proof_status == "preflight_ready"
    chosen = preflight.config.chosen_connectors
    assert set(chosen) == {"CRM", "Ads", "Analytics", "Email"}
    assert all(row["source"] == "db" for row in chosen.values())
    assert all(row["readiness_state"] == "ready" for row in chosen.values())
    assert all(row["proof_scope"] == "vendor_sandbox" for row in chosen.values())
    assert not any(row["local_test_only"] for row in chosen.values())


def test_local_test_only_db_rows_return_local_preflight_only_without_running_report() -> None:
    fake_task = MagicMock()
    fake_task.run = MagicMock()

    with patch("core.tasks.report_tasks.generate_report", fake_task):
        result = asyncio.run(
            run_sandbox_pilot(env=_base_db_env(), connector_rows=_local_preflight_rows())
        )

    assert result["preflight_status"] == "ready"
    assert result["proof_status"] == "local_preflight_only"
    assert result["production_claim_allowed"] is False
    assert result["real_vendor_claim_allowed"] is False
    assert result["proof_inserted"] is False
    fake_task.run.assert_not_called()


def test_missing_db_connector_categories_stay_blocked_without_env_fallback() -> None:
    preflight = discover_sandbox_pilot_config(
        _base_db_env(),
        connector_rows=[
            _db_row("hubspot_sandbox", "CRM"),
            _db_row("ga4_sandbox", "Analytics"),
        ],
    )

    assert preflight.preflight_status == "blocked"
    assert preflight.config.missing_categories == ["Ads", "Email"]
    assert preflight.config.chosen_connectors["CRM"]["source"] == "db"
    assert preflight.config.chosen_connectors["Analytics"]["source"] == "db"


def test_db_connector_rows_beat_env_vars_when_both_exist() -> None:
    preflight = discover_sandbox_pilot_config(
        _populated_env(),
        connector_rows=[_db_row("salesforce_sandbox", "CRM")],
    )

    assert preflight.preflight_status == "ready"
    assert preflight.config.chosen_connectors["CRM"]["source"] == "db"
    assert preflight.config.chosen_connectors["CRM"]["connector_key"] == "salesforce_sandbox"
    assert preflight.config.chosen_connectors["Ads"]["source"] == "env"


def test_db_connector_with_mock_marker_blocks_even_when_env_vars_exist() -> None:
    preflight = discover_sandbox_pilot_config(
        _populated_env(),
        connector_rows=[_db_row("hubspot_mock", "CRM", mock_or_test_double=True)],
    )

    assert preflight.preflight_status == "blocked"
    assert preflight.config.chosen_connectors["CRM"]["source"] == "db"
    assert "mock_or_test_double" in preflight.config.chosen_connectors["CRM"]["missing_reason"]


def test_preflight_with_missing_single_category_lists_only_that_category() -> None:
    env = _populated_env()
    for name in list(env):
        if name.startswith(f"{SANDBOX_ENV_PREFIX}GA4_"):
            env.pop(name)
    preflight = discover_sandbox_pilot_config(env, connector_rows=[])
    assert preflight.preflight_status == "blocked"
    assert preflight.config.missing_categories == ["Analytics"]
    assert all(
        cat["connector_category"] != "CRM"
        for cat in preflight.blockers
        if cat.get("category") == "connector"
    )


def test_preflight_envelope_redacts_secret_named_keys() -> None:
    preflight = discover_sandbox_pilot_config(_populated_env(), connector_rows=[])
    envelope = build_blocked_preflight_envelope(preflight)
    # Inject a secret-named value into next_actions and re-redact to confirm
    # that anything carrying a known marker key would be replaced.
    envelope["next_actions"].append(
        {"action_key": "test", "label": "noop", "api_key": "sk-DO-NOT-LEAK"}
    )
    redacted = json.dumps(envelope)
    # Build a fresh envelope wrapping that augmented dict to exercise the redactor.
    from core.marketing.weekly_report_sandbox_pilot import _redact

    redacted_envelope = _redact(envelope)
    serialised = json.dumps(redacted_envelope)
    assert "sk-DO-NOT-LEAK" not in serialised
    assert "[REDACTED]" in serialised
    # The original envelope's serialisation may still contain the raw value
    # because we asserted that the *redactor* hides it after explicit pass.
    assert "sk-DO-NOT-LEAK" in redacted  # raw envelope still has it -> redactor is the gate


# ---------------------------------------------------------------------------
# run_sandbox_pilot: missing creds → blocked envelope, no DB write
# ---------------------------------------------------------------------------


def test_run_sandbox_pilot_with_missing_creds_returns_blocked_envelope_only() -> None:
    persist_calls: list[Any] = []

    def _record(**kwargs):
        persist_calls.append(kwargs)
        return None

    with patch(
        "core.marketing.weekly_report_pilot_persistence.persist_weekly_report_pilot_proof_from_report_output_sync",
        side_effect=_record,
    ):
        result = asyncio.run(run_sandbox_pilot(env={}))
    assert result["preflight_status"] == "blocked"
    assert result["production_claim_allowed"] is False
    assert result["real_vendor_claim_allowed"] is False
    assert result["environment_type"] == "vendor_sandbox"
    assert persist_calls == [], "preflight failure must not trigger any persistence"


# ---------------------------------------------------------------------------
# run_sandbox_pilot: ready preflight → vendor_sandbox persists sandbox/partial only
# ---------------------------------------------------------------------------


def test_run_sandbox_pilot_with_ready_env_persists_sandbox_only() -> None:
    """Ready preflight must drive the real report-task path; the verdict it
    persists is never real-vendor passed because the runner forces
    environment_type=vendor_sandbox upstream."""

    fake_task_result = {
        "report_id": "rep-sandbox-001",
        "report_type": "cmo_weekly",
        "paths": ["sandbox.pdf"],
        "elapsed_sec": 1.23,
        "status": "completed",
        "weekly_report_pilot_proof": {
            "proof_id": "wkly_report_proof_sandbox",
            "environment_type": "vendor_sandbox",
            "proof_status": "sandbox_proven",
            "production_claim_allowed": False,
            "real_vendor_claim_allowed": False,
            "readiness_score": 88,
            "next_action_cta": "none",
        },
    }
    persisted_row = WeeklyReportPilotProof(
        tenant_id=__import__("uuid").UUID(_TENANT_UUID),
        company_id=None,
        proof_id="wkly_report_proof_sandbox",
        environment_type="vendor_sandbox",
        proof_status="sandbox_proven",
        production_claim_allowed=False,
        real_vendor_claim_allowed=False,
        readiness_score=88,
        evaluated_at=datetime(2026, 5, 24, tzinfo=UTC),
        evidence_bundle={"summary": {"proof_status": "sandbox_proven"}},
        verdict={"proof_status": "sandbox_proven"},
        blockers=[],
        next_actions=[],
        report_artifact_refs=[{"artifact_id": "rep-sandbox-001", "format": "pdf"}],
        decision_audit_refs=[{"audit_id": "audit-rep-sandbox-001"}],
    )

    class _SessionCtx:
        def __init__(self, _tid):
            pass

        async def __aenter__(self):
            session = MagicMock()
            result = MagicMock()
            result.scalar_one_or_none = MagicMock(return_value=persisted_row)
            session.execute = MagicMock(return_value=_resolved(result))
            return session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def _resolved(value):
        async def _coro():
            return value

        return _coro()

    fake_task = MagicMock()
    fake_task.run = MagicMock(return_value=fake_task_result)

    with (
        patch("core.tasks.report_tasks.generate_report", fake_task),
        patch("core.database.get_tenant_session", _SessionCtx),
    ):
        result = asyncio.run(run_sandbox_pilot(env=_populated_env(), connector_rows=[]))

    assert result["preflight_status"] == "ready"
    assert result["environment_type"] == "vendor_sandbox"
    proof_summary = result["weekly_report_pilot_proof"]
    assert proof_summary["proof_status"] == "sandbox_proven"
    assert proof_summary["production_claim_allowed"] is False
    assert proof_summary["real_vendor_claim_allowed"] is False
    # The runner re-reads from DB to confirm what landed.
    assert result["latest_weekly_report_pilot_proof"]["proof_status"] == "sandbox_proven"
    assert (
        result["latest_weekly_report_pilot_proof"]["production_claim_allowed"] is False
    )
    # The report task was invoked with vendor_sandbox in params.
    fake_task.run.assert_called_once()
    args, _ = fake_task.run.call_args
    assert args[0]["report_type"] == "cmo_weekly"
    assert args[0]["params"]["pilot_environment_type"] == "vendor_sandbox"
    assert args[0]["tenant_id"] == _TENANT_UUID


def test_run_sandbox_pilot_blocked_verdict_keeps_production_claim_false() -> None:
    """A ready preflight whose underlying report task still returns blocked
    (e.g., a category is configured but data backfill is incomplete) must
    keep production_claim_allowed=False."""

    fake_task_result = {
        "report_id": "rep-sandbox-002",
        "report_type": "cmo_weekly",
        "paths": ["sandbox.pdf"],
        "status": "completed",
        "weekly_report_pilot_proof": {
            "proof_id": "wkly_report_proof_sandbox_blk",
            "environment_type": "vendor_sandbox",
            "proof_status": "blocked",
            "production_claim_allowed": False,
            "real_vendor_claim_allowed": False,
            "readiness_score": 35,
        },
    }

    class _SessionCtx:
        def __init__(self, _tid):
            pass

        async def __aenter__(self):
            raise RuntimeError("DB unreachable in this test")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_task = MagicMock()
    fake_task.run = MagicMock(return_value=fake_task_result)

    with (
        patch("core.tasks.report_tasks.generate_report", fake_task),
        patch("core.database.get_tenant_session", _SessionCtx),
    ):
        result = asyncio.run(run_sandbox_pilot(env=_populated_env(), connector_rows=[]))

    assert result["preflight_status"] == "ready"
    assert result["weekly_report_pilot_proof"]["proof_status"] == "blocked"
    assert result["weekly_report_pilot_proof"]["production_claim_allowed"] is False
    # DB readback failed but the runner tolerated it and returned the task result.
    assert result["latest_weekly_report_pilot_proof"] is None


# ---------------------------------------------------------------------------
# Latest persisted proof: runner can fetch it
# ---------------------------------------------------------------------------


def test_runner_uses_latest_persisted_proof_helper_for_readback() -> None:
    """The runner reads back via ``latest_weekly_report_pilot_proof``."""

    persisted_row = WeeklyReportPilotProof(
        tenant_id=__import__("uuid").UUID(_TENANT_UUID),
        company_id=None,
        proof_id="wkly_report_proof_readback",
        environment_type="vendor_sandbox",
        proof_status="partial",
        production_claim_allowed=False,
        real_vendor_claim_allowed=False,
        readiness_score=70,
        evaluated_at=datetime(2026, 5, 24, tzinfo=UTC),
        evidence_bundle={},
        verdict={"proof_status": "partial"},
        blockers=[],
        next_actions=[],
        report_artifact_refs=[],
        decision_audit_refs=[],
    )

    async def _fake_latest(session, **kwargs):
        return persisted_row

    class _SessionCtx:
        def __init__(self, _tid):
            pass

        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_task = MagicMock()
    fake_task.run = MagicMock(return_value={"status": "completed", "weekly_report_pilot_proof": None})

    with (
        patch("core.tasks.report_tasks.generate_report", fake_task),
        patch("core.database.get_tenant_session", _SessionCtx),
        patch(
            "core.marketing.weekly_report_pilot_persistence.latest_weekly_report_pilot_proof",
            side_effect=_fake_latest,
        ),
    ):
        result = asyncio.run(run_sandbox_pilot(env=_populated_env(), connector_rows=[]))

    latest = result["latest_weekly_report_pilot_proof"]
    assert latest is not None
    assert latest["proof_id"] == "wkly_report_proof_readback"
    assert latest["proof_status"] == "partial"
    assert latest["production_claim_allowed"] is False


# ---------------------------------------------------------------------------
# Secret redaction in runner output
# ---------------------------------------------------------------------------


def test_runner_output_redacts_secret_named_fields_in_task_result() -> None:
    """Anything matching the secret-key markers in the task return value is
    redacted before the runner emits its summary."""

    fake_task_result = {
        "status": "completed",
        "report_id": "rep-secret-leak",
        "paths": ["sandbox.pdf"],
        "weekly_report_pilot_proof": {
            "proof_status": "sandbox_proven",
            "production_claim_allowed": False,
            "real_vendor_claim_allowed": False,
        },
        "credentials": {"api_key": "sk-DO-NOT-LEAK"},
        "authorization": "Bearer EXFIL",
    }

    fake_task = MagicMock()
    fake_task.run = MagicMock(return_value=fake_task_result)

    class _SessionCtx:
        def __init__(self, _tid):
            pass

        async def __aenter__(self):
            raise RuntimeError("skip readback")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    with (
        patch("core.tasks.report_tasks.generate_report", fake_task),
        patch("core.database.get_tenant_session", _SessionCtx),
    ):
        result = asyncio.run(run_sandbox_pilot(env=_populated_env(), connector_rows=[]))

    serialised = json.dumps(result, default=str)
    assert "sk-DO-NOT-LEAK" not in serialised
    assert "EXFIL" not in serialised
    assert "[REDACTED]" in serialised


# ---------------------------------------------------------------------------
# CLI smoke test (preflight-only, no DB / creds): exit code 3
# ---------------------------------------------------------------------------


def _run_cli(args: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    import os

    full_env = dict(os.environ)
    # Strip every sandbox env var to guarantee a clean preflight failure.
    for name in list(full_env):
        if name.startswith(SANDBOX_ENV_PREFIX) or name == "AGENTICORG_DB_URL":
            full_env.pop(name)
    if env:
        full_env.update(env)
    return subprocess.run(  # noqa: S603 -- pinned argv built from this test file only
        [sys.executable, "scripts/run_weekly_report_sandbox_pilot.py", *args],
        cwd=_REPO_ROOT,
        env=full_env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_preflight_only_blocked_exits_three() -> None:
    result = _run_cli(["--preflight-only", "--format", "text"])
    assert result.returncode == 3, result.stdout + result.stderr
    assert "Preflight status:         blocked" in result.stdout
    assert "AGENTICORG_DB_URL" in result.stdout


def test_cli_preflight_only_json_output_is_redacted() -> None:
    # Set one sandbox env var with a secret value to confirm the runner
    # never echoes that value back even when other categories are missing.
    fake_env = {
        f"{SANDBOX_ENV_PREFIX}HUBSPOT_ACCESS_TOKEN": "hubspot-pat-SECRET-XYZ",
    }
    result = _run_cli(["--preflight-only", "--format", "json"], env=fake_env)
    assert result.returncode == 3, result.stdout + result.stderr
    # Names appear (they have "_TOKEN" / "_SECRET" in them) but the values do not.
    assert "hubspot-pat-SECRET-XYZ" not in result.stdout


# ---------------------------------------------------------------------------
# Coverage of REQUIRED_* constants from the pilot proof module
# ---------------------------------------------------------------------------


def test_required_proof_constants_are_aligned() -> None:
    """A safety net: the runner's preflight covers the same categories the
    CMO-PROD-1 validator demands."""

    expected_required = {"CRM", "Ads", "Analytics", "Email"}
    assert set(SANDBOX_CONNECTOR_OPTIONS.keys()) == expected_required
    assert set(REQUIRED_BACKFILL_CATEGORIES) == expected_required
    assert REQUIRED_KPI_KEYS, "CMO-PROD-1 KPI key list must not regress to empty"
    assert REQUIRED_MAPPINGS, "CMO-PROD-1 mapping list must not regress to empty"


# ---------------------------------------------------------------------------
# Sanity: deep-copying the populated env doesn't change preflight outcome
# ---------------------------------------------------------------------------


def test_preflight_is_pure_and_does_not_mutate_env() -> None:
    env = _populated_env()
    snapshot = deepcopy(env)
    preflight = discover_sandbox_pilot_config(env, connector_rows=[])
    assert env == snapshot
    assert preflight.preflight_status == "ready"
