"""CMO-PROD-2 persistence and report-task wiring tests.

These tests do not require a live Postgres. The persistence helpers are
exercised against a hand-rolled ``AsyncSession`` stub that records the
ORM rows it sees and answers ``select(...)`` calls deterministically.
The Celery report task is exercised via direct function call with the
async DB layer monkeypatched, so a failing DB does not propagate.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from core.marketing.weekly_report_pilot_persistence import (
    build_weekly_report_evidence_from_report_output,
    latest_weekly_report_pilot_proof,
    persist_weekly_report_pilot_proof,
    persist_weekly_report_pilot_proof_from_report_output_sync,
    serialize_persisted_proof,
    summarize_persisted_proof,
)
from core.marketing.weekly_report_pilot_proof import (
    REQUIRED_BACKFILL_CATEGORIES,
    REQUIRED_KPI_KEYS,
    REQUIRED_MAPPINGS,
)
from core.models.weekly_report_pilot_proof import WeeklyReportPilotProof

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


_TENANT_UUID = "00000000-0000-0000-0000-000000000001"
_COMPANY_UUID = "00000000-0000-0000-0000-000000000002"
_TMP_DIR = Path(tempfile.gettempdir())


class FakeSession:
    """Minimal async-session shim that records ORM rows and answers selects."""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flushed = False
        self.preset_rows: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed = True

    async def execute(self, _stmt: Any) -> Any:
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(
            return_value=self.preset_rows[-1] if self.preset_rows else None
        )
        return result


def _real_vendor_evidence() -> dict[str, Any]:
    return {
        "tenant_id": _TENANT_UUID,
        "company_id": _COMPANY_UUID,
        "environment_type": "real_vendor",
        "connector_evidence": [
            {
                "connector_key": "hubspot",
                "category": "CRM",
                "health_status": "healthy",
                "read_ready": True,
                "source_account_id": "hub-9001",
                "last_sync_at": "2026-05-24T11:30:00Z",
            },
            {
                "connector_key": "google_ads",
                "category": "Ads",
                "health_status": "healthy",
                "read_ready": True,
                "source_account_id": "g-ads-9002",
            },
            {
                "connector_key": "ga4",
                "category": "Analytics",
                "health_status": "healthy",
                "read_ready": True,
                "source_account_id": "ga4-9003",
            },
            {
                "connector_key": "sendgrid",
                "category": "Email",
                "health_status": "healthy",
                "read_ready": True,
                "source_account_id": "sg-9004",
            },
        ],
        "mapping_evidence": [
            {"key": key, "status": "valid"} for key in REQUIRED_MAPPINGS
        ],
        "backfill_evidence": [
            {"source_connector_key": "hubspot", "category": cat, "status": "completed"}
            for cat in REQUIRED_BACKFILL_CATEGORIES
        ],
        "kpi_results": [
            {"kpi_key": key, "status": "ready"} for key in REQUIRED_KPI_KEYS
        ],
        "reconciliation_checks": [
            {"check_key": "cac_recon", "status": "pass"},
            {"check_key": "roas_recon", "status": "pass"},
        ],
        "report_quality_gates": [
            {"report_key": "weekly_marketing_report", "status": "pass"}
        ],
        "report_artifact_refs": [
            {"artifact_id": "report-2026W21", "format": "pdf"}
        ],
        "decision_audit_refs": [
            {"audit_id": "audit-weekly-2026W21"}
        ],
        "source_refs": [
            {"connector_key": "hubspot", "ref_id": "portal-9001"},
            {"connector_key": "google_ads", "ref_id": "customer-9002"},
            {"connector_key": "ga4", "ref_id": "property-9003"},
            {"connector_key": "sendgrid", "ref_id": "account-9004"},
        ],
        "source_context": {"source": "real_vendor"},
    }


def _drop_category(evidence: dict[str, Any], key: str, category: str) -> dict[str, Any]:
    cloned = deepcopy(evidence)
    cloned[key] = [row for row in cloned[key] if row.get("category") != category]
    return cloned


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Persistence: environment classification
# ---------------------------------------------------------------------------


def test_persists_demo_evidence_as_demo_only_and_blocks_production_claim() -> None:
    evidence = _real_vendor_evidence()
    evidence["environment_type"] = "demo"
    evidence["source_context"] = {"source": "demo"}
    session = FakeSession()

    row = _run(
        persist_weekly_report_pilot_proof(
            session, tenant_id=_TENANT_UUID, company_id=_COMPANY_UUID, evidence=evidence
        )
    )

    assert session.added == [row]
    assert session.flushed is True
    assert row.environment_type == "demo"
    assert row.proof_status == "demo_only"
    assert row.production_claim_allowed is False
    assert row.real_vendor_claim_allowed is False
    assert row.blockers and row.blockers[0]["category"] == "environment"


def test_persists_test_double_evidence_as_test_only_and_blocks_production_claim() -> None:
    evidence = _real_vendor_evidence()
    evidence["environment_type"] = "test_double"
    session = FakeSession()

    row = _run(
        persist_weekly_report_pilot_proof(
            session, tenant_id=_TENANT_UUID, company_id=_COMPANY_UUID, evidence=evidence
        )
    )

    assert row.proof_status == "test_only"
    assert row.production_claim_allowed is False
    assert row.real_vendor_claim_allowed is False


def test_persists_vendor_sandbox_as_sandbox_proven_not_real_vendor() -> None:
    evidence = _real_vendor_evidence()
    evidence["environment_type"] = "vendor_sandbox"
    session = FakeSession()

    row = _run(
        persist_weekly_report_pilot_proof(
            session, tenant_id=_TENANT_UUID, company_id=_COMPANY_UUID, evidence=evidence
        )
    )

    assert row.environment_type == "vendor_sandbox"
    assert row.proof_status in {"sandbox_proven", "partial"}
    assert row.production_claim_allowed is False
    assert row.real_vendor_claim_allowed is False


def test_persists_real_vendor_passed_only_when_all_criteria_met() -> None:
    session = FakeSession()

    row = _run(
        persist_weekly_report_pilot_proof(
            session,
            tenant_id=_TENANT_UUID,
            company_id=_COMPANY_UUID,
            evidence=_real_vendor_evidence(),
        )
    )

    assert row.environment_type == "real_vendor"
    assert row.proof_status == "passed"
    assert row.production_claim_allowed is True
    assert row.real_vendor_claim_allowed is True
    assert row.readiness_score >= 80


# ---------------------------------------------------------------------------
# Persistence: blocked verdicts for missing evidence
# ---------------------------------------------------------------------------


def test_persists_blocked_when_report_artifact_refs_missing() -> None:
    evidence = _real_vendor_evidence()
    evidence["report_artifact_refs"] = []
    session = FakeSession()

    row = _run(
        persist_weekly_report_pilot_proof(
            session, tenant_id=_TENANT_UUID, company_id=_COMPANY_UUID, evidence=evidence
        )
    )

    assert row.proof_status == "blocked"
    assert row.production_claim_allowed is False
    assert any(item["category"] == "report_artifact" for item in row.blockers)


def test_persists_blocked_when_decision_audit_refs_missing() -> None:
    evidence = _real_vendor_evidence()
    evidence["decision_audit_refs"] = []
    session = FakeSession()

    row = _run(
        persist_weekly_report_pilot_proof(
            session, tenant_id=_TENANT_UUID, company_id=_COMPANY_UUID, evidence=evidence
        )
    )

    assert row.proof_status == "blocked"
    assert any(item["category"] == "decision_audit" for item in row.blockers)


def test_persists_blocked_when_required_connector_missing() -> None:
    evidence = _drop_category(_real_vendor_evidence(), "connector_evidence", "CRM")
    evidence = _drop_category(evidence, "backfill_evidence", "CRM")
    session = FakeSession()

    row = _run(
        persist_weekly_report_pilot_proof(
            session, tenant_id=_TENANT_UUID, company_id=_COMPANY_UUID, evidence=evidence
        )
    )

    assert row.proof_status == "blocked"
    assert any(item["category"] == "connector" for item in row.blockers)


# ---------------------------------------------------------------------------
# Persistence: secret/token redaction
# ---------------------------------------------------------------------------


def test_persisted_evidence_and_verdict_redact_secrets() -> None:
    evidence = _real_vendor_evidence()
    evidence["connector_evidence"][0]["api_key"] = "sk-live-9001"
    evidence["connector_evidence"][1]["authorization"] = "Bearer SECRETXYZ"
    evidence["connector_evidence"][2]["credential"] = {"password": "p@ss"}
    session = FakeSession()

    row = _run(
        persist_weekly_report_pilot_proof(
            session, tenant_id=_TENANT_UUID, company_id=_COMPANY_UUID, evidence=evidence
        )
    )
    serialised = json.dumps(
        {
            "evidence_bundle": row.evidence_bundle,
            "verdict": row.verdict,
            "blockers": row.blockers,
        }
    )

    assert "sk-live-9001" not in serialised
    assert "SECRETXYZ" not in serialised
    assert "p@ss" not in serialised
    assert "[REDACTED]" in serialised


# ---------------------------------------------------------------------------
# Latest-by-tenant retrieval
# ---------------------------------------------------------------------------


def test_latest_weekly_report_pilot_proof_returns_newest_row() -> None:
    session = FakeSession()
    older = WeeklyReportPilotProof(
        tenant_id=__import__("uuid").UUID(_TENANT_UUID),
        company_id=__import__("uuid").UUID(_COMPANY_UUID),
        proof_id="wkly_report_proof_old",
        environment_type="vendor_sandbox",
        proof_status="sandbox_proven",
        production_claim_allowed=False,
        real_vendor_claim_allowed=False,
        readiness_score=80,
        evaluated_at=datetime(2026, 5, 23, tzinfo=UTC),
        evidence_bundle={},
        verdict={},
        blockers=[],
        next_actions=[],
        report_artifact_refs=[],
        decision_audit_refs=[],
    )
    newer = WeeklyReportPilotProof(
        tenant_id=__import__("uuid").UUID(_TENANT_UUID),
        company_id=__import__("uuid").UUID(_COMPANY_UUID),
        proof_id="wkly_report_proof_new",
        environment_type="real_vendor",
        proof_status="passed",
        production_claim_allowed=True,
        real_vendor_claim_allowed=True,
        readiness_score=95,
        evaluated_at=datetime(2026, 5, 24, tzinfo=UTC),
        evidence_bundle={},
        verdict={},
        blockers=[],
        next_actions=[],
        report_artifact_refs=[],
        decision_audit_refs=[],
    )
    session.preset_rows = [older, newer]

    latest = _run(
        latest_weekly_report_pilot_proof(
            session, tenant_id=_TENANT_UUID, company_id=_COMPANY_UUID
        )
    )

    assert latest is newer


# ---------------------------------------------------------------------------
# Evidence builder from ReportOutput
# ---------------------------------------------------------------------------


def test_evidence_builder_attaches_report_artifact_and_audit_refs() -> None:
    evidence = build_weekly_report_evidence_from_report_output(
        tenant_id=_TENANT_UUID,
        company_id=_COMPANY_UUID,
        report_id="report-2026W21",
        rendered_paths=[str(_TMP_DIR / "report.pdf")],
        report_data={
            "report_quality_gate": {
                "report_key": "weekly_marketing_report",
                "status": "pass",
                "required_approval_audit_refs": ["audit-report-1"],
            },
        },
    )

    assert evidence.report_artifact_refs[0]["artifact_id"] == "report-2026W21"
    assert evidence.report_artifact_refs[0]["format"] == "pdf"
    assert any(
        ref.get("audit_id") == "audit-report-1" for ref in evidence.decision_audit_refs
    )
    assert any(
        ref.get("event_type") == "weekly_report_delivered"
        for ref in evidence.decision_audit_refs
    )


def test_evidence_builder_marks_demo_when_report_data_demo_flag_set() -> None:
    evidence = build_weekly_report_evidence_from_report_output(
        tenant_id=_TENANT_UUID,
        company_id=_COMPANY_UUID,
        report_id="report-demo",
        rendered_paths=[],
        report_data={"demo": True, "source": "report_generator_fallback"},
    )
    assert evidence.environment_type == "demo"


# ---------------------------------------------------------------------------
# Celery task wiring: persist_weekly_report_pilot_proof_from_report_output_sync
# ---------------------------------------------------------------------------


def test_sync_wrapper_skips_when_tenant_id_is_not_uuid() -> None:
    result = persist_weekly_report_pilot_proof_from_report_output_sync(
        tenant_id="default",
        company_id="default",
        report_id="rep1",
        report_data=_real_vendor_evidence(),
        rendered_paths=[str(_TMP_DIR / "r.pdf")],
    )
    assert result is None


def test_sync_wrapper_persists_and_returns_summary_when_db_available() -> None:
    """Async DB layer monkeypatched: persistence runs and returns summary."""

    session = FakeSession()

    class _TenantSessionContext:
        def __init__(self, _tenant_id):
            pass

        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    with patch("core.database.get_tenant_session", _TenantSessionContext):
        result = persist_weekly_report_pilot_proof_from_report_output_sync(
            tenant_id=_TENANT_UUID,
            company_id=_COMPANY_UUID,
            report_id="report-W21",
            report_data={
                "report_quality_gate": {
                    "report_key": "weekly_marketing_report",
                    "status": "pass",
                    "required_approval_audit_refs": ["audit-1"],
                },
            },
            rendered_paths=[str(_TMP_DIR / "cmo_weekly_report-W21.pdf")],
            environment_type="vendor_sandbox",
        )

    assert result is not None
    assert result["proof_status"] in {"sandbox_proven", "partial", "blocked"}
    assert result["production_claim_allowed"] is False
    # The fake session received the new row.
    assert session.added and isinstance(session.added[0], WeeklyReportPilotProof)
    row = session.added[0]
    assert row.environment_type == "vendor_sandbox"
    assert any(
        ref.get("event_type") == "weekly_report_delivered"
        for ref in row.decision_audit_refs
    )


def test_sync_wrapper_persists_blocked_when_evidence_incomplete() -> None:
    session = FakeSession()

    class _TenantSessionContext:
        def __init__(self, _tenant_id):
            pass

        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    with patch("core.database.get_tenant_session", _TenantSessionContext):
        result = persist_weekly_report_pilot_proof_from_report_output_sync(
            tenant_id=_TENANT_UUID,
            company_id=_COMPANY_UUID,
            report_id="rep-empty",
            report_data={},  # no quality gate, no connector evidence
            rendered_paths=[],  # no artifacts
            environment_type="real_vendor",
        )

    assert result is not None
    assert result["proof_status"] == "blocked"
    assert result["production_claim_allowed"] is False


def test_sync_wrapper_swallows_db_errors_and_returns_none() -> None:
    class _TenantSessionContext:
        def __init__(self, _tenant_id):
            raise RuntimeError("DB unavailable")

    with patch("core.database.get_tenant_session", _TenantSessionContext):
        result = persist_weekly_report_pilot_proof_from_report_output_sync(
            tenant_id=_TENANT_UUID,
            company_id=_COMPANY_UUID,
            report_id="rep-down",
            report_data=_real_vendor_evidence(),
            rendered_paths=[str(_TMP_DIR / "x.pdf")],
        )
    assert result is None


# ---------------------------------------------------------------------------
# Report task integration
# ---------------------------------------------------------------------------


def test_report_task_calls_persistence_for_weekly_run() -> None:
    """`generate_report` should call the persistence sync wrapper for cmo_weekly."""

    from core.tasks import report_tasks

    fake_summary = {
        "proof_id": "wkly_report_proof_abc",
        "proof_status": "blocked",
        "production_claim_allowed": False,
    }
    persist_calls: list[dict[str, Any]] = []

    def _capture_persist(**kwargs):
        persist_calls.append(kwargs)
        return fake_summary

    fake_output = MagicMock()
    fake_output.content_data = {"report_quality_gate": {"status": "pass"}}
    fake_output.content_html = "<html/>"
    fake_output.report_type = "cmo_weekly"

    fake_generator = MagicMock()
    fake_generator.generate.return_value = fake_output

    with (
        patch.object(
            report_tasks,
            "_REPORTS_DIR",
            _TMP_DIR / "agentic_test_reports",
        ),
        patch("core.reports.generator.ReportGenerator", return_value=fake_generator),
        patch("core.reports.renderer.render_pdf", return_value=None),
        patch(
            "core.marketing.weekly_report_pilot_persistence.persist_weekly_report_pilot_proof_from_report_output_sync",
            side_effect=_capture_persist,
        ),
    ):
        result = report_tasks.generate_report.run(
            {
                "report_type": "cmo_weekly",
                "params": {},
                "company_id": _COMPANY_UUID,
                "tenant_id": _TENANT_UUID,
                "format": "pdf",
                "delivery_channels": [],
            },
        )

    assert result["status"] == "completed"
    assert result["weekly_report_pilot_proof"] == fake_summary
    assert persist_calls and persist_calls[0]["tenant_id"] == _TENANT_UUID
    assert persist_calls[0]["report_data"] is fake_output.content_data


def test_report_task_does_not_call_persistence_for_non_weekly_runs() -> None:
    from core.tasks import report_tasks

    fake_output = MagicMock()
    fake_output.content_data = {}
    fake_output.report_type = "cfo_daily"

    fake_generator = MagicMock()
    fake_generator.generate.return_value = fake_output

    persist_calls: list[dict[str, Any]] = []

    def _capture_persist(**kwargs):
        persist_calls.append(kwargs)
        return None

    with (
        patch.object(
            report_tasks,
            "_REPORTS_DIR",
            _TMP_DIR / "agentic_test_reports",
        ),
        patch("core.reports.generator.ReportGenerator", return_value=fake_generator),
        patch("core.reports.renderer.render_pdf", return_value=None),
        patch(
            "core.marketing.weekly_report_pilot_persistence.persist_weekly_report_pilot_proof_from_report_output_sync",
            side_effect=_capture_persist,
        ),
    ):
        result = report_tasks.generate_report.run(
            {
                "report_type": "cfo_daily",
                "params": {},
                "company_id": _COMPANY_UUID,
                "tenant_id": _TENANT_UUID,
                "format": "pdf",
                "delivery_channels": [],
            },
        )

    assert result["status"] == "completed"
    assert "weekly_report_pilot_proof" not in result
    assert persist_calls == []


# ---------------------------------------------------------------------------
# /kpis/cmo projection
# ---------------------------------------------------------------------------


def test_kpis_cmo_helper_returns_none_when_no_persisted_row() -> None:
    from api.v1 import kpis as kpis_api

    @AsyncMock
    async def _fake_session_ctx(_tenant_id):
        return None  # not used because tenant session never entered

    class _Ctx:
        def __init__(self, _tid):
            pass

        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    with patch("api.v1.kpis.get_tenant_session", _Ctx):
        result = _run(
            kpis_api._load_latest_weekly_report_pilot_proof(_TENANT_UUID, _COMPANY_UUID)
        )
    assert result is None


def test_kpis_cmo_helper_serialises_persisted_row() -> None:
    from api.v1 import kpis as kpis_api

    row = WeeklyReportPilotProof(
        tenant_id=__import__("uuid").UUID(_TENANT_UUID),
        company_id=__import__("uuid").UUID(_COMPANY_UUID),
        proof_id="wkly_report_proof_x",
        environment_type="vendor_sandbox",
        proof_status="sandbox_proven",
        production_claim_allowed=False,
        real_vendor_claim_allowed=False,
        readiness_score=82,
        evaluated_at=datetime(2026, 5, 24, tzinfo=UTC),
        evidence_bundle={},
        verdict={},
        blockers=[],
        next_actions=[],
        report_artifact_refs=[],
        decision_audit_refs=[],
    )

    class _Ctx:
        def __init__(self, _tid):
            pass

        async def __aenter__(self):
            session = FakeSession()
            session.preset_rows = [row]
            return session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    with patch("api.v1.kpis.get_tenant_session", _Ctx):
        result = _run(
            kpis_api._load_latest_weekly_report_pilot_proof(_TENANT_UUID, _COMPANY_UUID)
        )

    assert result is not None
    assert result["latest_weekly_report_pilot_proof"]["proof_id"] == "wkly_report_proof_x"
    assert result["latest_weekly_report_pilot_proof_summary"]["proof_status"] == "sandbox_proven"


def test_persisted_row_summariser_handles_missing_fields() -> None:
    assert summarize_persisted_proof(None) is None
    assert serialize_persisted_proof(None) is None
