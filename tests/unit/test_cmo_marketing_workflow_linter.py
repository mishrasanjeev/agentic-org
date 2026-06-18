from __future__ import annotations

from pathlib import Path

import pytest

from core.marketing.workflow_linter import (
    lint_marketing_workflow,
    lint_marketing_workflow_file,
)

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent.parent / "workflows" / "examples"


def _workflow(
    step: dict,
    *,
    mode: str = "production",
    domain: str = "marketing",
) -> dict:
    return {
        "id": "wf_test",
        "name": "Workflow Test",
        "domain": domain,
        "mode": mode,
        "steps": [step],
    }


def _agent_step(
    agent_type: str,
    action: str,
    **overrides: object,
) -> dict:
    step = {
        "id": "step_1",
        "name": "Step 1",
        "type": "agent",
        "agent_type": agent_type,
        "action": action,
    }
    step.update(overrides)
    return step


def _write_contract(**overrides: object) -> dict:
    contract = {
        "connector_key": "google_ads",
        "category": "Ads",
        "read_ready": True,
        "write_ready": True,
        "idempotency_key_supported": True,
        "retry_budget": {
            "idempotency_key": "mkt-write-001",
            "idempotency_supported": True,
            "remaining_attempts": 2,
        },
    }
    contract.update(overrides)
    return contract


def _codes(result) -> set[str]:
    return {finding.code for finding in result.findings}


def test_unknown_marketing_agent_type_fails_lint() -> None:
    result = lint_marketing_workflow(
        _workflow(_agent_step("growth_hacker", "launch_campaign"))
    )

    assert result.in_scope is True
    assert result.has_errors is True
    assert "marketing_agent_type_unknown" in _codes(result)
    finding = result.errors[0].to_dict()
    assert finding["workflow_file"] is None
    assert finding["step_id"] == "step_1"
    assert finding["severity"] == "error"
    assert finding["suggested_fix"]


def test_unknown_action_fails_when_agent_action_metadata_exists() -> None:
    result = lint_marketing_workflow(
        _workflow(_agent_step("campaign_pilot", "launch_the_moon"))
    )

    assert result.has_errors is True
    assert "marketing_action_unknown" in _codes(result)


def test_valid_implemented_marketing_agent_action_passes_lint() -> None:
    result = lint_marketing_workflow(
        _workflow(_agent_step("campaign_pilot", "create_plan"))
    )

    assert result.in_scope is True
    assert result.findings == ()


def test_no_marketing_agent_is_unknown_after_missing_agent_buildout() -> None:
    result = lint_marketing_workflow(
        _workflow(
            _agent_step("competitive_intel", "weekly_market_snapshot"),
            mode="target",
        )
    )

    assert "marketing_agent_type_unknown" not in _codes(result)
    assert "marketing_agent_unavailable_target_only" not in _codes(result)


def test_social_media_is_beta_not_unavailable_but_production_write_still_requires_gates() -> None:
    result = lint_marketing_workflow(
        _workflow(
            _agent_step(
                "social_media",
                "schedule_post",
                connector_key="buffer",
            )
        ),
        connector_contracts=[_write_contract(connector_key="buffer", category="Social")],
    )

    codes = _codes(result)
    assert "marketing_agent_type_unknown" not in codes
    assert "marketing_agent_unavailable_for_production" not in codes
    assert "marketing_agent_beta_in_production" in codes
    assert "marketing_external_write_confirmation_metadata_missing" in codes
    assert "marketing_decision_audit_evidence_missing" in codes


def test_abm_is_beta_not_unavailable_but_production_write_still_requires_gates() -> None:
    result = lint_marketing_workflow(
        _workflow(
            _agent_step(
                "abm_agent",
                "update_target_accounts",
            )
        ),
        connector_contracts=[
            _write_contract(connector_key="hubspot", category="CRM"),
            _write_contract(connector_key="bombora", category="ABM"),
        ],
    )

    codes = _codes(result)
    assert "marketing_agent_type_unknown" not in codes
    assert "marketing_agent_unavailable_for_production" not in codes
    assert "marketing_agent_beta_in_production" in codes
    assert "marketing_external_write_confirmation_metadata_missing" in codes
    assert "marketing_decision_audit_evidence_missing" in codes


def test_brand_monitor_is_beta_not_stub_but_public_response_still_requires_gates() -> None:
    result = lint_marketing_workflow(
        _workflow(
            _agent_step(
                "brand_monitor",
                "public_response",
                connector_key="buffer",
            )
        ),
        connector_contracts=[_write_contract(connector_key="buffer", category="Social")],
    )

    codes = _codes(result)
    assert "marketing_agent_type_unknown" not in codes
    assert "marketing_agent_stub_for_production" not in codes
    assert "marketing_agent_unavailable_for_production" not in codes
    assert "marketing_agent_beta_in_production" in codes
    assert "marketing_external_write_confirmation_metadata_missing" in codes
    assert "marketing_decision_audit_evidence_missing" in codes


def test_seo_strategist_is_beta_not_stub_but_site_write_still_requires_gates() -> None:
    result = lint_marketing_workflow(
        _workflow(
            _agent_step(
                "seo_strategist",
                "update_page_metadata",
                connector_key="wordpress",
            )
        ),
        connector_contracts=[_write_contract(connector_key="wordpress", category="CMS")],
    )

    codes = _codes(result)
    assert "marketing_agent_type_unknown" not in codes
    assert "marketing_agent_stub_for_production" not in codes
    assert "marketing_agent_unavailable_for_production" not in codes
    assert "marketing_agent_beta_in_production" in codes
    assert "marketing_external_write_confirmation_metadata_missing" in codes
    assert "marketing_decision_audit_evidence_missing" in codes


def test_stub_agent_in_active_production_workflow_fails_lint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CRM Intelligence is now beta (CMO-4.3). Inject a synthetic stub agent
    to assert the linter still fails production workflows that depend on a
    stub-only marketing agent."""

    from core.marketing import workflow_linter as wf_linter

    monkeypatch.setitem(wf_linter.MARKETING_AGENT_STATES, "_synthetic_stub", "stub")
    monkeypatch.setitem(wf_linter.MARKETING_AGENT_ACTIONS, "_synthetic_stub", {"score_lead"})

    result = lint_marketing_workflow(
        _workflow(_agent_step("_synthetic_stub", "score_lead"))
    )

    assert result.has_errors is True
    assert "marketing_agent_stub_for_production" in _codes(result)


@pytest.mark.parametrize("mode", ["target", "demo", "shadow"])
def test_stub_agent_in_non_production_workflow_warns_only(
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CRM Intelligence is now beta (CMO-4.3). Inject a synthetic stub agent
    to assert the linter still flags stub agents in non-production workflows
    as warnings only."""

    from core.marketing import workflow_linter as wf_linter

    monkeypatch.setitem(wf_linter.MARKETING_AGENT_STATES, "_synthetic_stub", "stub")
    monkeypatch.setitem(wf_linter.MARKETING_AGENT_ACTIONS, "_synthetic_stub", {"score_lead"})

    result = lint_marketing_workflow(
        _workflow(_agent_step("_synthetic_stub", "score_lead"), mode=mode)
    )

    assert result.has_errors is False
    assert "marketing_agent_stub_target_only" in _codes(result)
    assert result.warnings


def test_external_write_without_write_ready_connector_fails_production_lint() -> None:
    step = _agent_step(
        "campaign_pilot",
        "activate_campaign",
        connector_key="google_ads",
        external_write_confirmation_required=True,
        expected_confirmation_fields=["external_object_id", "confirmed_at"],
        idempotency_key_template="campaign:{campaign_id}:activate",
        decision_audit_required=True,
    )

    result = lint_marketing_workflow(
        _workflow(step),
        connector_contracts=[_write_contract(write_ready=False)],
    )

    assert result.has_errors is True
    assert "marketing_external_write_connector_not_ready" in _codes(result)


def test_external_write_without_idempotency_or_confirmation_metadata_fails_lint() -> None:
    step = _agent_step(
        "campaign_pilot",
        "activate_campaign",
        connector_key="google_ads",
    )

    result = lint_marketing_workflow(
        _workflow(step),
        connector_contracts=[_write_contract()],
    )

    assert result.has_errors is True
    assert "marketing_external_write_confirmation_metadata_missing" in _codes(result)
    assert "marketing_external_write_idempotency_metadata_missing" in _codes(result)


def test_external_write_with_ready_contract_and_metadata_passes_lint() -> None:
    step = _agent_step(
        "campaign_pilot",
        "activate_campaign",
        connector_key="google_ads",
        external_write_confirmation_required=True,
        expected_confirmation_fields=["external_object_id", "confirmed_at"],
        idempotency_key_template="campaign:{campaign_id}:activate",
        decision_audit_required=True,
    )

    result = lint_marketing_workflow(
        _workflow(step),
        connector_contracts=[_write_contract()],
    )

    assert result.findings == ()


def test_shadow_workflow_with_recommendation_only_steps_passes_read_only_lint() -> None:
    result = lint_marketing_workflow(
        _workflow(
            _agent_step("campaign_pilot", "generate_weekly_report"),
            mode="shadow",
        )
    )

    assert result.has_errors is False
    assert result.findings == ()


def test_shadow_workflow_with_external_write_fails_read_only_lint() -> None:
    result = lint_marketing_workflow(
        _workflow(
            _agent_step("campaign_pilot", "activate_campaign", connector_key="google_ads"),
            mode="shadow",
        )
    )

    assert result.has_errors is True
    assert "marketing_shadow_external_write" in _codes(result)


def test_non_marketing_workflow_is_reported_out_of_scope() -> None:
    result = lint_marketing_workflow(
        _workflow(
            _agent_step("ap_processor", "extract_invoice"),
            domain="finance",
        )
    )

    assert result.in_scope is False
    assert result.has_errors is False
    assert _codes(result) == {"marketing_workflow_out_of_scope"}


def test_actual_marketing_example_file_is_lintable_with_structured_findings() -> None:
    result = lint_marketing_workflow_file(
        WORKFLOWS_DIR / "campaign_launch.yaml",
        connector_contracts=[_write_contract()],
    )

    assert result.in_scope is True
    assert result.workflow_file.endswith("campaign_launch.yaml")
    assert result.findings
    assert all(finding.workflow_name == "Campaign Launch" for finding in result.findings)
    codes = _codes(result)
    assert "marketing_agent_unavailable_for_production" not in codes
    assert "marketing_agent_beta_in_production" in codes
    assert "marketing_external_write_confirmation_metadata_missing" in codes
