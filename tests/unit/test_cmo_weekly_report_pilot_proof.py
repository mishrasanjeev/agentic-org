"""Acceptance tests for CMO-PROD-1 weekly-marketing-report pilot proof."""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from core.marketing.weekly_report_pilot_proof import (
    REQUIRED_BACKFILL_CATEGORIES,
    REQUIRED_KPI_KEYS,
    REQUIRED_MAPPINGS,
    WEEKLY_REPORT_PROOF_VERSION,
    build_weekly_marketing_report_evidence_bundle,
    build_weekly_marketing_report_proof_projection,
    evaluate_weekly_marketing_report_proof,
    serialize_weekly_marketing_report_evidence_bundle,
    summarize_weekly_marketing_report_proof,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _real_vendor_evidence() -> dict[str, Any]:
    """A complete, real-vendor evidence bundle that should pass the proof."""

    return {
        "tenant_id": "tenant-pilot-001",
        "company_id": "company-pilot-001",
        "environment_type": "real_vendor",
        "generated_at": "2026-05-24T12:00:00Z",
        "connector_evidence": [
            {
                "connector_key": "hubspot",
                "category": "CRM",
                "health_status": "healthy",
                "read_ready": True,
                "source_account_id": "hub-portal-9001",
                "last_sync_at": "2026-05-24T11:30:00Z",
            },
            {
                "connector_key": "google_ads",
                "category": "Ads",
                "health_status": "healthy",
                "read_ready": True,
                "source_account_id": "g-ads-customer-9002",
                "last_sync_at": "2026-05-24T11:35:00Z",
            },
            {
                "connector_key": "ga4",
                "category": "Analytics",
                "health_status": "healthy",
                "read_ready": True,
                "source_account_id": "ga4-property-9003",
                "last_sync_at": "2026-05-24T11:40:00Z",
            },
            {
                "connector_key": "sendgrid",
                "category": "Email",
                "health_status": "healthy",
                "read_ready": True,
                "source_account_id": "sg-account-9004",
                "last_sync_at": "2026-05-24T11:45:00Z",
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
            {
                "report_key": "weekly_marketing_report",
                "status": "pass",
                "next_action_cta": "none",
            }
        ],
        "report_artifact_refs": [
            {
                "artifact_id": "report-2026W21",
                "format": "pdf",
                "delivered_at": "2026-05-24T12:00:00Z",
            }
        ],
        "decision_audit_refs": [
            {"audit_id": "audit-weekly-2026W21", "event_type": "weekly_report_delivered"}
        ],
        "source_refs": [
            {"connector_key": "hubspot", "ref_id": "portal-9001"},
            {"connector_key": "google_ads", "ref_id": "customer-9002"},
            {"connector_key": "ga4", "ref_id": "property-9003"},
            {"connector_key": "sendgrid", "ref_id": "account-9004"},
        ],
        "source_context": {"source": "real_vendor"},
    }


def _drop_connector(evidence: dict[str, Any], category: str) -> dict[str, Any]:
    cloned = deepcopy(evidence)
    cloned["connector_evidence"] = [
        row for row in cloned["connector_evidence"] if row.get("category") != category
    ]
    cloned["backfill_evidence"] = [
        row for row in cloned["backfill_evidence"] if row.get("category") != category
    ]
    return cloned


# ---------------------------------------------------------------------------
# Environment classification
# ---------------------------------------------------------------------------


def test_demo_evidence_returns_demo_only_and_blocks_production_claim() -> None:
    evidence = _real_vendor_evidence()
    evidence["environment_type"] = "demo"
    evidence["source_context"] = {"source": "demo"}
    proof = evaluate_weekly_marketing_report_proof(evidence)

    assert proof["environment_type"] == "demo"
    assert proof["proof_status"] == "demo_only"
    assert proof["production_claim_allowed"] is False
    assert proof["real_vendor_claim_allowed"] is False
    assert any(item["category"] == "environment" for item in proof["blockers"])


def test_test_double_evidence_returns_test_only_and_blocks_production_claim() -> None:
    evidence = _real_vendor_evidence()
    evidence["environment_type"] = "test_double"
    proof = evaluate_weekly_marketing_report_proof(evidence)

    assert proof["environment_type"] == "test_double"
    assert proof["proof_status"] == "test_only"
    assert proof["production_claim_allowed"] is False
    assert proof["real_vendor_claim_allowed"] is False


def test_unknown_environment_blocks_real_vendor_production_claim() -> None:
    evidence = _real_vendor_evidence()
    evidence["environment_type"] = "unknown"
    evidence["source_context"] = {}
    proof = evaluate_weekly_marketing_report_proof(evidence)

    assert proof["environment_type"] == "unknown"
    assert proof["production_claim_allowed"] is False
    assert proof["real_vendor_claim_allowed"] is False


def test_mock_or_test_double_marker_on_any_connector_blocks_proof() -> None:
    evidence = _real_vendor_evidence()
    evidence["connector_evidence"][0]["mock_or_test_double"] = True
    proof = evaluate_weekly_marketing_report_proof(evidence)

    assert proof["proof_status"] == "blocked"
    assert any(
        item["category"] == "environment"
        and "mock/test-double" in item["message"]
        for item in proof["blockers"]
    )


# ---------------------------------------------------------------------------
# Vendor sandbox
# ---------------------------------------------------------------------------


def test_vendor_sandbox_evidence_does_not_pass_as_real_vendor() -> None:
    evidence = _real_vendor_evidence()
    evidence["environment_type"] = "vendor_sandbox"
    evidence["source_context"] = {"source": "vendor_sandbox"}
    proof = evaluate_weekly_marketing_report_proof(evidence)

    assert proof["environment_type"] == "vendor_sandbox"
    assert proof["proof_status"] == "sandbox_proven"
    assert proof["production_claim_allowed"] is False
    assert proof["real_vendor_claim_allowed"] is False
    assert proof["proof_scope"] == "sandbox"


def test_vendor_sandbox_missing_optional_evidence_reports_partial() -> None:
    evidence = _real_vendor_evidence()
    evidence["environment_type"] = "vendor_sandbox"
    evidence["report_artifact_refs"] = []
    evidence["decision_audit_refs"] = []
    proof = evaluate_weekly_marketing_report_proof(evidence)

    assert proof["environment_type"] == "vendor_sandbox"
    assert proof["proof_status"] in {"sandbox_proven", "partial"}
    assert proof["production_claim_allowed"] is False


# ---------------------------------------------------------------------------
# Real vendor pass / fail
# ---------------------------------------------------------------------------


def test_real_vendor_with_all_critical_criteria_passes() -> None:
    proof = evaluate_weekly_marketing_report_proof(_real_vendor_evidence())

    assert proof["environment_type"] == "real_vendor"
    assert proof["proof_status"] == "passed"
    assert proof["production_claim_allowed"] is True
    assert proof["real_vendor_claim_allowed"] is True
    assert proof["readiness_score"] >= 80
    capability_keys = {row["capability_key"] for row in proof["proven_capabilities"]}
    assert {"weekly_report_connectors", "weekly_report_mappings", "weekly_report_kpis"}.issubset(
        capability_keys
    )


@pytest.mark.parametrize("missing_category", ["CRM", "Ads", "Analytics", "Email"])
def test_real_vendor_missing_required_connector_blocks_proof(missing_category: str) -> None:
    evidence = _drop_connector(_real_vendor_evidence(), missing_category)
    proof = evaluate_weekly_marketing_report_proof(evidence)

    assert proof["proof_status"] == "blocked"
    assert proof["production_claim_allowed"] is False
    assert any(
        item["category"] == "connector"
        and missing_category in (item.get("affected") or [])
        for item in proof["blockers"]
    )


def test_real_vendor_missing_required_mapping_blocks_proof() -> None:
    evidence = _real_vendor_evidence()
    evidence["mapping_evidence"] = [
        row for row in evidence["mapping_evidence"] if row.get("key") != "lifecycle_stages"
    ]
    proof = evaluate_weekly_marketing_report_proof(evidence)

    assert proof["proof_status"] == "blocked"
    assert any(
        item["category"] == "mapping"
        and "lifecycle_stages" in (item.get("affected") or [])
        for item in proof["blockers"]
    )


def test_real_vendor_missing_required_backfill_blocks_proof() -> None:
    evidence = _real_vendor_evidence()
    evidence["backfill_evidence"] = [
        row for row in evidence["backfill_evidence"] if row.get("category") != "Email"
    ]
    proof = evaluate_weekly_marketing_report_proof(evidence)

    assert proof["proof_status"] == "blocked"
    assert any(item["category"] == "backfill" for item in proof["blockers"])


def test_failed_kpi_reconciliation_blocks_proof() -> None:
    evidence = _real_vendor_evidence()
    evidence["reconciliation_checks"] = [
        {"check_key": "cac_recon", "status": "failed"}
    ]
    proof = evaluate_weekly_marketing_report_proof(evidence)

    assert proof["proof_status"] == "blocked"
    assert any(item["category"] == "reconciliation" for item in proof["blockers"])


def test_blocked_report_quality_gate_blocks_proof() -> None:
    evidence = _real_vendor_evidence()
    evidence["report_quality_gates"] = [
        {"report_key": "weekly_marketing_report", "status": "blocked"}
    ]
    proof = evaluate_weekly_marketing_report_proof(evidence)

    assert proof["proof_status"] == "blocked"
    assert any(item["category"] == "report_quality" for item in proof["blockers"])


def test_missing_audit_or_report_artifact_blocks_real_vendor_proof() -> None:
    evidence = _real_vendor_evidence()
    evidence["report_artifact_refs"] = []
    evidence["decision_audit_refs"] = []
    proof = evaluate_weekly_marketing_report_proof(evidence)

    assert proof["proof_status"] == "blocked"
    categories = {item["category"] for item in proof["blockers"]}
    assert {"report_artifact", "decision_audit"}.issubset(categories)


def test_demo_or_test_source_marker_on_source_refs_blocks_proof() -> None:
    evidence = _real_vendor_evidence()
    evidence["source_refs"] = [{"connector_key": "demo"}]
    proof = evaluate_weekly_marketing_report_proof(evidence)

    assert proof["proof_status"] == "blocked"
    assert any(item["category"] == "source_lineage" for item in proof["blockers"])


# ---------------------------------------------------------------------------
# Serialisation / redaction
# ---------------------------------------------------------------------------


def test_proof_bundle_serialisation_redacts_secrets() -> None:
    evidence = _real_vendor_evidence()
    evidence["connector_evidence"][0]["api_key"] = "sk-live-9001"
    evidence["connector_evidence"][1]["authorization"] = "Bearer SECRETXYZ"
    evidence["connector_evidence"][2]["credential"] = {"password": "p@ss"}
    proof = evaluate_weekly_marketing_report_proof(evidence)
    bundle = build_weekly_marketing_report_evidence_bundle(proof)
    serialised = serialize_weekly_marketing_report_evidence_bundle(bundle)

    assert "sk-live-9001" not in serialised
    assert "SECRETXYZ" not in serialised
    assert "p@ss" not in serialised
    assert "[REDACTED]" in serialised


def test_summary_reports_proof_status_and_next_action() -> None:
    proof = evaluate_weekly_marketing_report_proof({"environment_type": "demo"})
    summary = summarize_weekly_marketing_report_proof(proof)
    assert summary["proof_status"] == "demo_only"
    assert summary["production_claim_allowed"] is False
    assert summary["next_action_cta"] == "connect_real_or_sandbox_sources"
    assert summary["schema_version"] == WEEKLY_REPORT_PROOF_VERSION


# ---------------------------------------------------------------------------
# Projection / KPI API integration
# ---------------------------------------------------------------------------


def test_projection_wraps_proof_summary_and_bundle() -> None:
    projection = build_weekly_marketing_report_proof_projection(_real_vendor_evidence())

    assert projection["weekly_report_pilot_proof_version"] == WEEKLY_REPORT_PROOF_VERSION
    assert projection["weekly_report_pilot_proof"]["proof_status"] == "passed"
    assert projection["weekly_report_pilot_proof_summary"]["production_claim_allowed"] is True
    assert projection["weekly_report_pilot_evidence_bundle"]["bundle_type"] == "weekly_marketing_report_pilot_proof"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def real_vendor_evidence_file(tmp_path: Path) -> Path:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(_real_vendor_evidence()), encoding="utf-8")
    return path


@pytest.fixture
def demo_evidence_file(tmp_path: Path) -> Path:
    path = tmp_path / "evidence_demo.json"
    path.write_text(
        json.dumps({"environment_type": "demo", "source_context": {"source": "demo"}}),
        encoding="utf-8",
    )
    return path


def _run_cli(args: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- pinned argv built from this test file only
        [sys.executable, "scripts/validate_weekly_report_pilot_proof.py", *args],
        cwd=Path(__file__).resolve().parent.parent.parent,
        capture_output=True,
        text=True,
        input=stdin,
        check=False,
    )


def test_cli_returns_zero_for_real_vendor_passed(real_vendor_evidence_file: Path) -> None:
    result = _run_cli(["--evidence", str(real_vendor_evidence_file)])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "passed" in result.stdout
    assert "production claim allowed" in result.stdout.lower()


def test_cli_returns_nonzero_for_demo_evidence(demo_evidence_file: Path) -> None:
    result = _run_cli(["--evidence", str(demo_evidence_file)])
    assert result.returncode == 3
    assert "demo_only" in result.stdout
    assert "Blockers:" in result.stdout


def test_cli_json_output_is_redacted(tmp_path: Path) -> None:
    evidence = _real_vendor_evidence()
    evidence["connector_evidence"][0]["api_key"] = "sk-live-XYZ"
    path = tmp_path / "evidence_secret.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    result = _run_cli(["--evidence", str(path), "--format", "json"])
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    serialised = json.dumps(payload)
    assert "sk-live-XYZ" not in serialised
    assert "[REDACTED]" in serialised


def test_cli_reads_from_stdin_when_no_evidence_path() -> None:
    result = _run_cli(["--evidence", "-"], stdin=json.dumps({"environment_type": "unknown"}))
    assert result.returncode == 3
    assert "unavailable" in result.stdout or "blocked" in result.stdout
