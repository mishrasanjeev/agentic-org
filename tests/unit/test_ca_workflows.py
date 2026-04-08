# ruff: noqa: N801
"""Tests for CA workflow YAML files.

Validates that the three core CA workflow definitions parse correctly
and have the expected structure: steps, triggers, HITL steps, and
company_scoped flag.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent.parent / "workflows" / "examples"


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file."""
    try:
        import yaml  # type: ignore[import-untyped]

        with open(path) as fh:
            return yaml.safe_load(fh) or {}
    except ImportError:
        pytest.skip("PyYAML not installed")
        return {}  # unreachable but keeps type checker happy


# ============================================================================
# GSTR Filing Monthly
# ============================================================================


class TestGSTRFilingWorkflow:
    """Validate gstr_filing_monthly.yaml structure."""

    @pytest.fixture()
    def workflow(self) -> dict[str, Any]:
        path = WORKFLOWS_DIR / "gstr_filing_monthly.yaml"
        assert path.exists(), f"Workflow file not found: {path}"
        return _load_yaml(path)

    def test_parses_correctly(self, workflow: dict[str, Any]):
        """GSTR workflow YAML must parse without errors."""
        assert "name" in workflow
        assert "steps" in workflow
        assert workflow["name"] == "Monthly GSTR Filing"

    def test_has_9_steps(self, workflow: dict[str, Any]):
        """GSTR workflow must have exactly 9 steps."""
        steps = workflow["steps"]
        assert len(steps) == 9, f"Expected 9 steps, got {len(steps)}: {[s['id'] for s in steps]}"

    def test_has_partner_review_hitl_step(self, workflow: dict[str, Any]):
        """GSTR workflow must have a partner_review step of type human_in_loop."""
        steps = workflow["steps"]
        partner_review = [s for s in steps if s["id"] == "partner_review"]
        assert len(partner_review) == 1, "Missing partner_review step"
        assert partner_review[0]["type"] == "human_in_loop"
        assert partner_review[0]["role_required"] == "partner"

    def test_trigger_is_schedule(self, workflow: dict[str, Any]):
        """GSTR workflow trigger must be a schedule (cron)."""
        trigger = workflow["trigger"]
        assert trigger["type"] == "schedule"
        assert "cron" in trigger

    def test_company_scoped_true(self, workflow: dict[str, Any]):
        """GSTR workflow must be company-scoped."""
        assert workflow.get("company_scoped") is True

    def test_has_fetch_2a_step(self, workflow: dict[str, Any]):
        """Must start with GSTR-2A fetch."""
        steps = workflow["steps"]
        first_step = steps[0]
        assert first_step["id"] == "fetch_2a"
        assert "gstn:fetch_gstr2a" in first_step.get("tools", [])

    def test_has_file_gstr3b_step(self, workflow: dict[str, Any]):
        """Must have a file_gstr3b step near the end."""
        step_ids = [s["id"] for s in workflow["steps"]]
        assert "file_gstr3b" in step_ids

    def test_has_notify_step(self, workflow: dict[str, Any]):
        """Must have a notify_team step at the end."""
        steps = workflow["steps"]
        last_step = steps[-1]
        assert last_step["id"] == "notify_team"
        assert last_step["type"] == "notify"

    def test_has_mismatch_escalation(self, workflow: dict[str, Any]):
        """Must have a mismatch escalation (HITL) step."""
        step_ids = [s["id"] for s in workflow["steps"]]
        assert "escalate_mismatches" in step_ids


# ============================================================================
# TDS Quarterly Filing
# ============================================================================


class TestTDSQuarterlyWorkflow:
    """Validate tds_quarterly_filing.yaml structure."""

    @pytest.fixture()
    def workflow(self) -> dict[str, Any]:
        path = WORKFLOWS_DIR / "tds_quarterly_filing.yaml"
        assert path.exists(), f"Workflow file not found: {path}"
        return _load_yaml(path)

    def test_parses_correctly(self, workflow: dict[str, Any]):
        """TDS workflow YAML must parse without errors."""
        assert "name" in workflow
        assert "steps" in workflow
        assert workflow["name"] == "Quarterly TDS Filing"

    def test_has_partner_review_step(self, workflow: dict[str, Any]):
        """TDS workflow must have a partner_review step of type human_in_loop."""
        steps = workflow["steps"]
        partner_review = [s for s in steps if s["id"] == "partner_review"]
        assert len(partner_review) == 1, "Missing partner_review step"
        assert partner_review[0]["type"] == "human_in_loop"
        assert partner_review[0]["role_required"] == "partner"

    def test_company_scoped_true(self, workflow: dict[str, Any]):
        """TDS workflow must be company-scoped."""
        assert workflow.get("company_scoped") is True

    def test_trigger_is_quarterly_schedule(self, workflow: dict[str, Any]):
        """TDS workflow trigger must be quarterly schedule."""
        trigger = workflow["trigger"]
        assert trigger["type"] == "schedule"
        # Cron should target months 1,4,7,10
        assert "1,4,7,10" in trigger["cron"]

    def test_has_26q_and_24q_steps(self, workflow: dict[str, Any]):
        """TDS workflow must have both Form 26Q and 24Q preparation steps."""
        step_ids = [s["id"] for s in workflow["steps"]]
        assert "prepare_26q" in step_ids, "Missing prepare_26q step"
        assert "prepare_24q" in step_ids, "Missing prepare_24q step"

    def test_has_form16a_generation(self, workflow: dict[str, Any]):
        """TDS workflow must generate Form 16A certificates."""
        step_ids = [s["id"] for s in workflow["steps"]]
        assert "generate_form16a" in step_ids

    def test_has_26as_reconciliation(self, workflow: dict[str, Any]):
        """TDS workflow must reconcile with 26AS."""
        step_ids = [s["id"] for s in workflow["steps"]]
        assert "reconcile_26as" in step_ids

    def test_has_challan_payment(self, workflow: dict[str, Any]):
        """TDS workflow must have challan payment step."""
        step_ids = [s["id"] for s in workflow["steps"]]
        assert "pay_challan" in step_ids


# ============================================================================
# Bank Reconciliation Daily
# ============================================================================


class TestBankReconWorkflow:
    """Validate bank_recon_daily.yaml structure."""

    @pytest.fixture()
    def workflow(self) -> dict[str, Any]:
        path = WORKFLOWS_DIR / "bank_recon_daily.yaml"
        assert path.exists(), f"Workflow file not found: {path}"
        return _load_yaml(path)

    def test_parses_correctly(self, workflow: dict[str, Any]):
        """Bank recon workflow YAML must parse without errors."""
        assert "name" in workflow
        assert "steps" in workflow
        assert workflow["name"] == "Daily Bank Reconciliation"

    def test_has_auto_match_step(self, workflow: dict[str, Any]):
        """Bank recon must have an auto-match step."""
        step_ids = [s["id"] for s in workflow["steps"]]
        assert "auto_match" in step_ids

    def test_auto_match_has_threshold(self, workflow: dict[str, Any]):
        """Auto-match step must specify a match_threshold."""
        auto_match = next(s for s in workflow["steps"] if s["id"] == "auto_match")
        inputs = auto_match.get("inputs", {})
        assert "match_threshold" in inputs

    def test_company_scoped_true(self, workflow: dict[str, Any]):
        """Bank recon workflow must be company-scoped."""
        assert workflow.get("company_scoped") is True

    def test_trigger_is_weekday_schedule(self, workflow: dict[str, Any]):
        """Bank recon trigger must run weekdays only."""
        trigger = workflow["trigger"]
        assert trigger["type"] == "schedule"
        # Cron "* * 1-5" for weekdays
        assert "1-5" in trigger["cron"]

    def test_has_escalation_step(self, workflow: dict[str, Any]):
        """Bank recon must have an escalation step for breaks."""
        step_ids = [s["id"] for s in workflow["steps"]]
        assert "escalate_breaks" in step_ids

    def test_has_report_generation(self, workflow: dict[str, Any]):
        """Bank recon must generate a reconciliation report."""
        step_ids = [s["id"] for s in workflow["steps"]]
        assert "generate_report" in step_ids

    def test_flags_old_outstanding_items(self, workflow: dict[str, Any]):
        """Bank recon must flag items outstanding > 30 days."""
        step_ids = [s["id"] for s in workflow["steps"]]
        assert "flag_old_outstanding" in step_ids


# ============================================================================
# Cross-workflow: all must be company_scoped
# ============================================================================


class TestAllWorkflowsCompanyScoped:
    """Every CA workflow file must have company_scoped: true."""

    @pytest.mark.parametrize(
        "filename",
        [
            "gstr_filing_monthly.yaml",
            "tds_quarterly_filing.yaml",
            "bank_recon_daily.yaml",
        ],
    )
    def test_company_scoped_flag(self, filename: str):
        path = WORKFLOWS_DIR / filename
        if not path.exists():
            pytest.skip(f"{filename} not found")
        wf = _load_yaml(path)
        assert wf.get("company_scoped") is True, (
            f"{filename} must have company_scoped: true"
        )
