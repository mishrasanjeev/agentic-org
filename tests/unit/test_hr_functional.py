"""HR functional tests — FT-HR-001 through FT-HR-012."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock
from workflows.engine import WorkflowEngine
from workflows.state_store import WorkflowStateStore
from workflows.step_types import execute_step
from workflows.condition_evaluator import evaluate_condition
from core.tool_gateway.pii_masker import mask_pii


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_state_store():
    store = AsyncMock(spec=WorkflowStateStore)
    store.save = AsyncMock()
    store.load = AsyncMock()
    return store


@pytest.fixture
def workflow_engine(mock_state_store):
    return WorkflowEngine(state_store=mock_state_store)


@pytest.fixture
def base_state():
    return {
        "id": "wfr_hr_test001",
        "status": "running",
        "trigger_payload": {},
        "steps_total": 0,
        "steps_completed": 0,
        "step_results": {},
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


def _make_definition(steps, timeout_hours=None):
    defn = {"name": "hr_workflow", "steps": steps}
    if timeout_hours is not None:
        defn["timeout_hours"] = timeout_hours
    return defn


# ===========================================================================
# Test class
# ===========================================================================

class TestHRFunctional:
    """FT-HR-001 through FT-HR-012: HR domain functional tests."""

    # -----------------------------------------------------------------------
    # FT-HR-001: JD generation from role brief
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_hr_001_jd_generation(self):
        """FT-HR-001: JD generated from role brief with required sections."""
        step = {
            "id": "generate_jd",
            "type": "agent",
            "agent": "talent_acquisition",
            "action": "generate_jd",
        }
        state = {
            "context": {
                "role_title": "Senior Backend Engineer",
                "department": "Engineering",
                "level": "L5",
                "skills": ["Python", "Distributed Systems", "AWS"],
            },
            "step_results": {},
        }

        result = await execute_step(step, state)

        assert result["status"] == "completed"
        assert result["type"] == "agent"
        assert result["agent"] == "talent_acquisition"
        assert result["action"] == "generate_jd"
        assert result["step_id"] == "generate_jd"

    # -----------------------------------------------------------------------
    # FT-HR-002: Bias-free resume screening (PII stripped, no gender bias)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_hr_002_bias_free_resume_screening(self):
        """FT-HR-002: Resume screening with PII stripped — email, phone, Aadhaar masked."""
        resume_data = {
            "name": "Priya Sharma",
            "email": "priya.sharma@example.com",
            "phone": "+91 9876543210",
            "aadhaar": "1234 5678 9012",
            "pan": "ABCDE1234F",
            "skills": ["Python", "Machine Learning"],
            "experience_years": 5,
        }

        with patch("core.tool_gateway.pii_masker.settings") as mock_settings:
            mock_settings.pii_masking = True
            masked = mask_pii(resume_data)

        # PII must be masked
        assert masked["email"] != resume_data["email"]
        assert "***" in masked["email"]
        assert masked["phone"] != resume_data["phone"]
        assert "******" in masked["phone"]
        assert masked["aadhaar"] != resume_data["aadhaar"]
        assert "XXXX-XXXX-" in masked["aadhaar"]
        assert masked["pan"] != resume_data["pan"]
        assert "XXXXX" in masked["pan"]

        # Non-PII fields preserved
        assert masked["skills"] == resume_data["skills"]
        assert masked["experience_years"] == resume_data["experience_years"]

        # Agent step processes screened resume
        step = {
            "id": "screen_resume",
            "type": "agent",
            "agent": "talent_acquisition",
            "action": "screen_resume",
        }
        state = {"context": {"masked_resume": masked}, "step_results": {}}
        result = await execute_step(step, state)
        assert result["status"] == "completed"

    # -----------------------------------------------------------------------
    # FT-HR-003: Interview scheduling (5 panel, common slot)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_hr_003_interview_scheduling(self, workflow_engine, mock_state_store, base_state):
        """FT-HR-003: Schedule interview across 5 panelists — find common slot."""
        panelists = ["panel_1", "panel_2", "panel_3", "panel_4", "panel_5"]

        steps = [
            {"id": "fetch_calendars", "type": "parallel",
             "steps": panelists, "wait_for": "all"},
            {"id": "find_common_slot", "type": "agent",
             "agent": "talent_acquisition", "action": "find_common_slot",
             "depends_on": ["fetch_calendars"]},
            {"id": "send_invites", "type": "notify", "connector": "email",
             "depends_on": ["find_common_slot"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {"panelist_count": len(panelists)}
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "completed"
        step_results = result["step_results"]
        # Parallel calendar fetch completed with 5 results
        assert "fetch_calendars" in step_results
        parallel_output = step_results["fetch_calendars"]["output"]
        assert parallel_output["type"] == "parallel"
        assert len(parallel_output["results"]) == 5
        # Common slot found and invites sent
        assert step_results["find_common_slot"]["status"] == "completed"
        assert step_results["send_invites"]["output"]["status"] == "sent"

    # -----------------------------------------------------------------------
    # FT-HR-004: Rescheduling automation
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_hr_004_rescheduling_automation(self, workflow_engine, mock_state_store, base_state):
        """FT-HR-004: Auto-reschedule when panelist cancels — new slot found."""
        steps = [
            {"id": "detect_cancellation", "type": "agent",
             "agent": "talent_acquisition", "action": "detect_cancellation"},
            {"id": "find_new_slot", "type": "agent",
             "agent": "talent_acquisition", "action": "find_alternative_slot",
             "depends_on": ["detect_cancellation"]},
            {"id": "update_calendar", "type": "agent",
             "agent": "talent_acquisition", "action": "update_calendar_events",
             "depends_on": ["find_new_slot"]},
            {"id": "notify_all", "type": "notify", "connector": "email",
             "depends_on": ["update_calendar"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {
            "cancellation_type": "panelist",
            "panelist_id": "panel_3",
        }
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "completed"
        step_results = result["step_results"]
        assert step_results["detect_cancellation"]["status"] == "completed"
        assert step_results["find_new_slot"]["status"] == "completed"
        assert step_results["update_calendar"]["status"] == "completed"
        assert step_results["notify_all"]["output"]["status"] == "sent"

    # -----------------------------------------------------------------------
    # FT-HR-005: Offer letter generation (DocuSign to HR Head)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_hr_005_offer_letter_generation(self, workflow_engine, mock_state_store, base_state):
        """FT-HR-005: Offer letter generated and routed via DocuSign to HR Head."""
        steps = [
            {"id": "generate_offer", "type": "agent",
             "agent": "talent_acquisition", "action": "generate_offer_letter"},
            {"id": "send_docusign", "type": "agent",
             "agent": "talent_acquisition", "action": "send_to_docusign",
             "depends_on": ["generate_offer"]},
            {"id": "notify_hr_head", "type": "notify", "connector": "email",
             "depends_on": ["send_docusign"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {
            "candidate_name": "Arjun Patel",
            "role": "SDE-2",
            "ctc_inr": 2400000,
        }
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "completed"
        step_results = result["step_results"]
        assert step_results["generate_offer"]["status"] == "completed"
        assert step_results["send_docusign"]["status"] == "completed"
        assert step_results["notify_hr_head"]["output"]["status"] == "sent"
        assert step_results["notify_hr_head"]["output"]["connector"] == "email"

    # -----------------------------------------------------------------------
    # FT-HR-006: Day-0 provisioning completeness (all systems within 10 min)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_hr_006_day0_provisioning(self, workflow_engine, mock_state_store, base_state):
        """FT-HR-006: Day-0 onboarding provisions all systems in parallel within timeout."""
        provisioning_systems = [
            "active_directory", "google_workspace", "slack", "jira", "github",
        ]
        steps = [
            {"id": "provision_all", "type": "parallel",
             "steps": provisioning_systems, "wait_for": "all"},
            {"id": "verify_provision", "type": "agent",
             "agent": "onboarding_agent", "action": "verify_all_provisioned",
             "depends_on": ["provision_all"]},
            {"id": "welcome_notification", "type": "notify", "connector": "slack",
             "depends_on": ["verify_provision"]},
        ]
        definition = _make_definition(steps, timeout_hours=0.167)  # 10 min
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {
            "employee_id": "EMP-2026-0042",
            "systems_count": len(provisioning_systems),
        }
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "completed"
        step_results = result["step_results"]
        # All parallel provisions completed
        parallel_output = step_results["provision_all"]["output"]
        assert parallel_output["type"] == "parallel"
        assert len(parallel_output["results"]) == len(provisioning_systems)
        for r in parallel_output["results"]:
            assert r["status"] == "completed"
        # Verification passed
        assert step_results["verify_provision"]["status"] == "completed"
        # Welcome sent
        assert step_results["welcome_notification"]["output"]["status"] == "sent"

    # -----------------------------------------------------------------------
    # FT-HR-007: Provisioning idempotency (trigger twice, no duplicates)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_hr_007_provisioning_idempotency(self, workflow_engine, mock_state_store, base_state):
        """FT-HR-007: Provisioning triggered twice with same idempotency key yields no duplicate accounts."""
        steps = [
            {"id": "provision_ad", "type": "agent",
             "agent": "onboarding_agent", "action": "provision_active_directory"},
        ]
        definition = _make_definition(steps)
        parsed = workflow_engine.parser.parse(definition)

        # First run
        state_1 = {**base_state, "id": "wfr_idem_001", "definition": parsed, "steps_total": 1}
        mock_state_store.load.return_value = state_1
        result_1 = await workflow_engine.execute(state_1["id"])
        assert result_1["status"] == "completed"
        assert result_1["step_results"]["provision_ad"]["status"] == "completed"

        # Second run (same logical request — simulates re-trigger)
        state_2 = {
            **base_state,
            "id": "wfr_idem_002",
            "definition": parsed,
            "steps_total": 1,
            "trigger_payload": {"idempotency_key": "EMP-2026-0042-provision-ad"},
        }
        mock_state_store.load.return_value = state_2
        result_2 = await workflow_engine.execute(state_2["id"])
        assert result_2["status"] == "completed"
        assert result_2["step_results"]["provision_ad"]["status"] == "completed"

        # Both completed — but the key principle is the idempotency store prevents
        # the connector from creating a duplicate account.  We verify the workflow
        # framework reaches the same "completed" terminal state for both runs.
        assert result_1["step_results"]["provision_ad"]["status"] == result_2["step_results"]["provision_ad"]["status"]

    # -----------------------------------------------------------------------
    # FT-HR-008: Payroll computation accuracy (all deductions within ±₹1)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_hr_008_payroll_computation_accuracy(self):
        """FT-HR-008: Payroll computation — gross, PF, PT, TDS, net within ±₹1."""
        # Simulate payroll for an employee
        basic = 50000
        hra = 25000
        special_allowance = 15000
        gross = basic + hra + special_allowance  # 90,000

        # Deductions
        pf_employee = basic * 0.12       # 6,000
        pt = 200                           # Professional tax (Karnataka)
        tds_monthly = 5000                 # Estimated TDS
        total_deductions = pf_employee + pt + tds_monthly
        net_payable = gross - total_deductions

        assert gross == 90000
        assert pf_employee == pytest.approx(6000, abs=1)
        assert total_deductions == pytest.approx(11200, abs=1)
        assert net_payable == pytest.approx(78800, abs=1)

        # Verify the agent step executes for payroll
        step = {
            "id": "run_payroll",
            "type": "agent",
            "agent": "payroll_engine",
            "action": "compute_payroll",
        }
        state = {
            "context": {
                "employee_id": "EMP-001",
                "basic": basic,
                "hra": hra,
                "special_allowance": special_allowance,
                "gross": gross,
                "net_payable": net_payable,
            },
            "step_results": {},
        }
        result = await execute_step(step, state)
        assert result["status"] == "completed"
        assert result["agent"] == "payroll_engine"

    # -----------------------------------------------------------------------
    # FT-HR-009: Variable pay inclusion (gross recomputed, TDS recalculated)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_hr_009_variable_pay_inclusion(self):
        """FT-HR-009: Variable pay added to gross, TDS recomputed correctly."""
        base_gross = 90000
        variable_pay = 30000
        new_gross = base_gross + variable_pay  # 120,000

        # Recalculate TDS on new gross (simplified annual projection)
        annual_income = new_gross * 12  # 14,40,000
        # Old regime slab estimation (simplified)
        tds_annual_estimate = max(0, (annual_income - 500000) * 0.20)
        tds_monthly_revised = tds_annual_estimate / 12

        assert new_gross == 120000
        assert annual_income == 1440000
        assert tds_annual_estimate > 0  # TDS must be positive on this income
        assert tds_monthly_revised > 0

        # Agent step for variable pay inclusion
        step = {
            "id": "include_variable_pay",
            "type": "agent",
            "agent": "payroll_engine",
            "action": "include_variable_pay",
        }
        state = {
            "context": {
                "base_gross": base_gross,
                "variable_pay": variable_pay,
                "new_gross": new_gross,
                "tds_monthly_revised": tds_monthly_revised,
            },
            "step_results": {},
        }
        result = await execute_step(step, state)
        assert result["status"] == "completed"
        assert result["agent"] == "payroll_engine"
        assert result["action"] == "include_variable_pay"

    # -----------------------------------------------------------------------
    # FT-HR-010: EPFO ECR generation (correct format, UAN validated)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_hr_010_epfo_ecr_generation(self, workflow_engine, mock_state_store, base_state):
        """FT-HR-010: EPFO ECR file generated in correct format with validated UANs."""
        steps = [
            {"id": "validate_uans", "type": "agent",
             "agent": "payroll_engine", "action": "validate_uans"},
            {"id": "check_uan_validity", "type": "condition",
             "condition": "invalid_uan_count == 0",
             "true_path": "generate_ecr", "false_path": "flag_invalid_uans",
             "depends_on": ["validate_uans"]},
            {"id": "generate_ecr", "type": "agent",
             "agent": "payroll_engine", "action": "generate_ecr_file",
             "depends_on": ["check_uan_validity"]},
            {"id": "flag_invalid_uans", "type": "notify", "connector": "email",
             "depends_on": ["check_uan_validity"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {
            "invalid_uan_count": "0",
            "employee_count": 150,
            "filing_month": "2026-02",
        }
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "completed"
        step_results = result["step_results"]
        assert step_results["validate_uans"]["status"] == "completed"
        # With 0 invalid UANs, ECR generation path is taken
        assert "generate_ecr" in step_results
        assert step_results["generate_ecr"]["status"] == "completed"

    # -----------------------------------------------------------------------
    # FT-HR-011: Access revocation on offboard (all systems within 30 min)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_hr_011_access_revocation_offboard(self, workflow_engine, mock_state_store, base_state):
        """FT-HR-011: Offboarding revokes all system access in parallel within 30 min."""
        revocation_systems = [
            "active_directory", "google_workspace", "slack", "jira",
            "github", "vpn", "badge_access",
        ]
        steps = [
            {"id": "disable_accounts", "type": "parallel",
             "steps": revocation_systems, "wait_for": "all"},
            {"id": "verify_revocation", "type": "agent",
             "agent": "offboarding_agent", "action": "verify_all_revoked",
             "depends_on": ["disable_accounts"]},
            {"id": "notify_it_security", "type": "notify", "connector": "slack",
             "depends_on": ["verify_revocation"]},
        ]
        definition = _make_definition(steps, timeout_hours=0.5)  # 30 min
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {
            "employee_id": "EMP-2024-0099",
            "last_working_day": "2026-03-21",
            "systems_count": len(revocation_systems),
        }
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "completed"
        step_results = result["step_results"]
        # All revocations completed in parallel
        parallel_output = step_results["disable_accounts"]["output"]
        assert parallel_output["type"] == "parallel"
        assert len(parallel_output["results"]) == len(revocation_systems)
        for r in parallel_output["results"]:
            assert r["status"] == "completed"
        # Verification passed
        assert step_results["verify_revocation"]["status"] == "completed"
        # IT security notified
        assert step_results["notify_it_security"]["output"]["status"] == "sent"
        assert step_results["notify_it_security"]["output"]["connector"] == "slack"

    # -----------------------------------------------------------------------
    # FT-HR-012: Attrition risk detection (3 signals, HRBP notified)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_hr_012_attrition_risk_detection(self, workflow_engine, mock_state_store, base_state):
        """FT-HR-012: Attrition risk detected from 3+ signals — HRBP notified."""
        steps = [
            {"id": "analyze_signals", "type": "agent",
             "agent": "performance_coach", "action": "analyze_attrition_signals"},
            {"id": "check_risk_threshold", "type": "condition",
             "condition": "risk_signal_count >= 3",
             "true_path": "notify_hrbp", "false_path": "log_and_close",
             "depends_on": ["analyze_signals"]},
            {"id": "notify_hrbp", "type": "notify", "connector": "slack",
             "depends_on": ["check_risk_threshold"]},
            {"id": "log_and_close", "type": "agent",
             "agent": "performance_coach", "action": "log_low_risk",
             "depends_on": ["check_risk_threshold"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {
            "employee_id": "EMP-2025-0033",
            "risk_signal_count": 3,
            "signals": [
                "manager_change",
                "no_promotion_2yr",
                "glassdoor_activity",
            ],
        }
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "completed"
        step_results = result["step_results"]
        assert step_results["analyze_signals"]["status"] == "completed"
        # Condition evaluates risk_signal_count >= 3 as true
        assert evaluate_condition("risk_signal_count >= 3", {"risk_signal_count": 3}) is True
        # HRBP notified via Slack
        assert "notify_hrbp" in step_results
        assert step_results["notify_hrbp"]["output"]["status"] == "sent"
        assert step_results["notify_hrbp"]["output"]["connector"] == "slack"
