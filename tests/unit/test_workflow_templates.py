"""Test all workflow YAML templates parse and validate.

Covers all 11 workflow template files in workflows/examples/:
  3 existing + 8 new workflows.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Discovery — find all YAML workflow templates
# ---------------------------------------------------------------------------

_WORKFLOWS_DIR = Path(__file__).resolve().parent.parent.parent / "workflows" / "examples"

_ALL_WORKFLOWS = sorted(_WORKFLOWS_DIR.glob("*.yaml"))

# Expected workflow file names (11 total)
_EXPECTED_WORKFLOW_FILES = [
    "campaign_launch.yaml",
    "content_pipeline.yaml",
    "daily_treasury.yaml",
    "employee_onboarding.yaml",
    "invoice_processing_v2.yaml",
    "invoice_to_pay_v3.yaml",
    "lead_nurture.yaml",
    "month_end_close.yaml",
    "support_triage.yaml",
    "tax_calendar.yaml",
    "weekly_marketing_report.yaml",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_workflow(path: Path) -> dict:
    """Load and parse a YAML workflow file."""
    with open(path) as f:
        return yaml.safe_load(f)


def _collect_all_step_ids(steps: list) -> set[str]:
    """Recursively collect all step IDs from a workflow, including sub_steps."""
    ids = set()
    for step in steps:
        if "id" in step:
            ids.add(step["id"])
        for sub in step.get("sub_steps", []):
            if "id" in sub:
                ids.add(sub["id"])
    return ids


# ═══════════════════════════════════════════════════════════════════════════
# 1. Discovery — verify all 11 workflows exist
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkflowDiscovery:
    """Verify the expected workflow files exist."""

    def test_workflows_dir_exists(self):
        assert _WORKFLOWS_DIR.is_dir(), f"Workflows dir not found: {_WORKFLOWS_DIR}"

    def test_expected_workflow_count(self):
        assert len(_ALL_WORKFLOWS) >= 11, (
            f"Expected at least 11 workflow files, found {len(_ALL_WORKFLOWS)}"
        )

    @pytest.mark.parametrize("filename", _EXPECTED_WORKFLOW_FILES)
    def test_expected_workflow_file_exists(self, filename):
        path = _WORKFLOWS_DIR / filename
        assert path.exists(), f"Missing workflow file: {filename}"


# ═══════════════════════════════════════════════════════════════════════════
# 2. YAML parsing — verify each file loads without error
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkflowYAMLParsing:
    """Verify each workflow YAML parses correctly."""

    @pytest.mark.parametrize("path", _ALL_WORKFLOWS, ids=lambda p: p.name)
    def test_yaml_loads_without_error(self, path):
        data = _load_workflow(path)
        assert isinstance(data, dict), f"{path.name} did not parse to a dict"

    @pytest.mark.parametrize("path", _ALL_WORKFLOWS, ids=lambda p: p.name)
    def test_yaml_is_not_empty(self, path):
        data = _load_workflow(path)
        assert len(data) > 0, f"{path.name} is an empty document"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Required top-level fields
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkflowRequiredFields:
    """Verify required top-level fields exist in each workflow."""

    @pytest.mark.parametrize("path", _ALL_WORKFLOWS, ids=lambda p: p.name)
    def test_has_name(self, path):
        data = _load_workflow(path)
        assert "name" in data, f"{path.name} missing 'name'"
        assert isinstance(data["name"], str)
        assert len(data["name"]) > 0

    @pytest.mark.parametrize("path", _ALL_WORKFLOWS, ids=lambda p: p.name)
    def test_has_version(self, path):
        data = _load_workflow(path)
        assert "version" in data, f"{path.name} missing 'version'"

    # Note: 3 legacy workflows (employee_onboarding, support_triage, invoice_processing_v2)
    # lack an explicit domain field. New workflows (v3+) always include it.
    _NEW_WORKFLOWS = [
        p for p in _ALL_WORKFLOWS
        if p.name not in ("employee_onboarding.yaml", "support_triage.yaml", "invoice_processing_v2.yaml")
    ]

    @pytest.mark.parametrize("path", _NEW_WORKFLOWS, ids=lambda p: p.name)
    def test_new_workflows_have_domain(self, path):
        data = _load_workflow(path)
        assert "domain" in data, f"{path.name} missing 'domain'"
        assert data["domain"] in ("finance", "hr", "marketing", "operations", "sales", "comms")

    @pytest.mark.parametrize("path", _ALL_WORKFLOWS, ids=lambda p: p.name)
    def test_has_trigger(self, path):
        data = _load_workflow(path)
        assert "trigger" in data, f"{path.name} missing 'trigger'"
        assert isinstance(data["trigger"], dict)

    @pytest.mark.parametrize("path", _ALL_WORKFLOWS, ids=lambda p: p.name)
    def test_has_steps(self, path):
        data = _load_workflow(path)
        assert "steps" in data, f"{path.name} missing 'steps'"
        assert isinstance(data["steps"], list)
        assert len(data["steps"]) > 0


# ═══════════════════════════════════════════════════════════════════════════
# 4. Trigger validation
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkflowTriggers:
    """Verify trigger has valid type."""

    _VALID_TRIGGER_TYPES = {"schedule", "manual", "api_event", "email_received"}

    @pytest.mark.parametrize("path", _ALL_WORKFLOWS, ids=lambda p: p.name)
    def test_trigger_has_type(self, path):
        data = _load_workflow(path)
        trigger = data["trigger"]
        assert "type" in trigger, f"{path.name} trigger missing 'type'"

    @pytest.mark.parametrize("path", _ALL_WORKFLOWS, ids=lambda p: p.name)
    def test_trigger_type_is_valid(self, path):
        data = _load_workflow(path)
        trigger_type = data["trigger"]["type"]
        assert trigger_type in self._VALID_TRIGGER_TYPES, (
            f"{path.name}: invalid trigger type '{trigger_type}', "
            f"expected one of {self._VALID_TRIGGER_TYPES}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 5. Step structure validation
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkflowStepStructure:
    """Verify each step has required fields: id, name, type."""

    # New (v3+) workflows with full step schema
    _NEW_WORKFLOWS = [
        p for p in _ALL_WORKFLOWS
        if p.name not in ("employee_onboarding.yaml", "support_triage.yaml", "invoice_processing_v2.yaml")
    ]

    @pytest.mark.parametrize("path", _ALL_WORKFLOWS, ids=lambda p: p.name)
    def test_each_step_has_id(self, path):
        data = _load_workflow(path)
        for step in data["steps"]:
            assert "id" in step, f"{path.name}: step missing 'id': {step}"

    @pytest.mark.parametrize("path", _NEW_WORKFLOWS, ids=lambda p: p.name)
    def test_new_workflow_steps_have_name(self, path):
        data = _load_workflow(path)
        for step in data["steps"]:
            assert "name" in step, f"{path.name}: step '{step.get('id', '?')}' missing 'name'"

    @pytest.mark.parametrize("path", _ALL_WORKFLOWS, ids=lambda p: p.name)
    def test_each_step_has_type(self, path):
        data = _load_workflow(path)
        for step in data["steps"]:
            assert "type" in step, f"{path.name}: step '{step.get('id', '?')}' missing 'type'"

    @pytest.mark.parametrize("path", _ALL_WORKFLOWS, ids=lambda p: p.name)
    def test_step_ids_are_unique(self, path):
        data = _load_workflow(path)
        _collect_all_step_ids(data["steps"])
        id_list = []
        for step in data["steps"]:
            id_list.append(step["id"])
            for sub in step.get("sub_steps", []):
                if "id" in sub:
                    id_list.append(sub["id"])
        assert len(id_list) == len(set(id_list)), (
            f"{path.name}: duplicate step IDs found: {[x for x in id_list if id_list.count(x) > 1]}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 6. Dependency validation — no dangling depends_on
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkflowDependencies:
    """Verify step dependencies reference valid step IDs."""

    # New (v3+) workflows with strict dependency graphs
    _NEW_WORKFLOWS = [
        p for p in _ALL_WORKFLOWS
        if p.name not in ("employee_onboarding.yaml", "support_triage.yaml", "invoice_processing_v2.yaml")
    ]

    @pytest.mark.parametrize("path", _NEW_WORKFLOWS, ids=lambda p: p.name)
    def test_no_dangling_depends_on(self, path):
        data = _load_workflow(path)
        all_ids = _collect_all_step_ids(data["steps"])

        for step in data["steps"]:
            deps = step.get("depends_on", [])
            for dep_id in deps:
                assert dep_id in all_ids, (
                    f"{path.name}: step '{step['id']}' depends on '{dep_id}' "
                    f"which does not exist. Available: {sorted(all_ids)}"
                )

    @pytest.mark.parametrize("path", _ALL_WORKFLOWS, ids=lambda p: p.name)
    def test_all_depends_on_are_lists(self, path):
        data = _load_workflow(path)
        for step in data["steps"]:
            deps = step.get("depends_on")
            if deps is not None:
                assert isinstance(deps, list), (
                    f"{path.name}: step '{step['id']}' depends_on should be a list"
                )


# ═══════════════════════════════════════════════════════════════════════════
# 7. HITL step validation
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkflowHITLSteps:
    """Verify HITL steps have assignee and decision_options."""

    # New workflows with full HITL schema (legacy ones use assignee_role, no decision_options)
    _NEW_WORKFLOWS = [
        p for p in _ALL_WORKFLOWS
        if p.name not in ("employee_onboarding.yaml", "support_triage.yaml", "invoice_processing_v2.yaml")
    ]

    @pytest.mark.parametrize("path", _ALL_WORKFLOWS, ids=lambda p: p.name)
    def test_hitl_steps_have_assignee_or_role(self, path):
        data = _load_workflow(path)
        for step in data["steps"]:
            if step["type"] == "human_in_loop":
                has_assignee = "assignee" in step or "assignee_role" in step
                assert has_assignee, (
                    f"{path.name}: HITL step '{step['id']}' missing 'assignee' or 'assignee_role'"
                )

    @pytest.mark.parametrize("path", _NEW_WORKFLOWS, ids=lambda p: p.name)
    def test_new_hitl_steps_have_decision_options(self, path):
        data = _load_workflow(path)
        for step in data["steps"]:
            if step["type"] == "human_in_loop":
                assert "decision_options" in step, (
                    f"{path.name}: HITL step '{step['id']}' missing 'decision_options'"
                )
                opts = step["decision_options"]
                assert isinstance(opts, list)
                assert len(opts) >= 2, (
                    f"{path.name}: HITL step '{step['id']}' needs at least 2 decision_options"
                )


# ═══════════════════════════════════════════════════════════════════════════
# 8. Notification step validation
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkflowNotificationSteps:
    """Verify notification steps have channel and target/to."""

    @pytest.mark.parametrize("path", _ALL_WORKFLOWS, ids=lambda p: p.name)
    def test_notify_substeps_have_channel(self, path):
        data = _load_workflow(path)
        for step in data["steps"]:
            for sub in step.get("sub_steps", []):
                if sub.get("type") == "notify":
                    assert "channel" in sub, (
                        f"{path.name}: notify sub_step '{sub.get('id', '?')}' missing 'channel'"
                    )

    @pytest.mark.parametrize("path", _ALL_WORKFLOWS, ids=lambda p: p.name)
    def test_notify_substeps_have_recipient(self, path):
        data = _load_workflow(path)
        for step in data["steps"]:
            for sub in step.get("sub_steps", []):
                if sub.get("type") == "notify":
                    has_target = "to" in sub or "target" in sub
                    assert has_target, (
                        f"{path.name}: notify sub_step '{sub.get('id', '?')}' "
                        f"missing 'to' or 'target'"
                    )

    @pytest.mark.parametrize("path", _ALL_WORKFLOWS, ids=lambda p: p.name)
    def test_top_level_notifications_have_channel(self, path):
        data = _load_workflow(path)
        notifications = data.get("notifications", {})
        for event_type in ("on_complete", "on_timeout", "on_failure"):
            for notif in notifications.get(event_type, []):
                assert "channel" in notif, (
                    f"{path.name}: {event_type} notification missing 'channel'"
                )
                has_target = "target" in notif or "to" in notif
                assert has_target, (
                    f"{path.name}: {event_type} notification missing 'target' or 'to'"
                )


# ═══════════════════════════════════════════════════════════════════════════
# 9. Domain-specific workflow content
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkflowDomainContent:
    """Verify domain-specific workflow content makes sense."""

    def test_invoice_to_pay_is_finance(self):
        data = _load_workflow(_WORKFLOWS_DIR / "invoice_to_pay_v3.yaml")
        assert data["domain"] == "finance"
        step_types = {s["id"] for s in data["steps"]}
        assert "extract" in step_types
        assert "execute_payment" in step_types

    def test_campaign_launch_is_marketing(self):
        data = _load_workflow(_WORKFLOWS_DIR / "campaign_launch.yaml")
        assert data["domain"] == "marketing"
        step_ids = {s["id"] for s in data["steps"]}
        assert "campaign_plan" in step_ids

    def test_employee_onboarding_is_hr_workflow(self):
        data = _load_workflow(_WORKFLOWS_DIR / "employee_onboarding.yaml")
        # Legacy workflow — domain inferred from agent type, not explicit field
        assert "onboarding" in data["name"]

    def test_daily_treasury_is_finance(self):
        data = _load_workflow(_WORKFLOWS_DIR / "daily_treasury.yaml")
        assert data["domain"] == "finance"

    def test_month_end_close_is_finance(self):
        data = _load_workflow(_WORKFLOWS_DIR / "month_end_close.yaml")
        assert data["domain"] == "finance"

    def test_lead_nurture_is_marketing(self):
        data = _load_workflow(_WORKFLOWS_DIR / "lead_nurture.yaml")
        assert data["domain"] == "marketing"

    def test_support_triage_is_operations_workflow(self):
        data = _load_workflow(_WORKFLOWS_DIR / "support_triage.yaml")
        # Legacy workflow — domain inferred from agent name
        assert "support" in data["name"] or "triage" in data["name"]

    def test_tax_calendar_is_finance(self):
        data = _load_workflow(_WORKFLOWS_DIR / "tax_calendar.yaml")
        assert data["domain"] == "finance"

    def test_weekly_marketing_report_is_marketing(self):
        data = _load_workflow(_WORKFLOWS_DIR / "weekly_marketing_report.yaml")
        assert data["domain"] == "marketing"
