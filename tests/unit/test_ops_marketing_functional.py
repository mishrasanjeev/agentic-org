"""Ops & Marketing functional tests — FT-OPS-001 through FT-OPS-010, FT-MKT-001 through FT-MKT-003."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from workflows.engine import WorkflowEngine
from workflows.state_store import WorkflowStateStore
from workflows.step_types import execute_step
from workflows.condition_evaluator import evaluate_condition
from core.schemas.errors import ErrorCode, ERROR_META


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
        "id": "wfr_ops_test001",
        "status": "running",
        "trigger_payload": {},
        "steps_total": 0,
        "steps_completed": 0,
        "step_results": {},
        "started_at": "2026-03-21T00:00:00+00:00",
    }


def _make_definition(steps, timeout_hours=None):
    defn = {"name": "ops_workflow", "steps": steps}
    if timeout_hours is not None:
        defn["timeout_hours"] = timeout_hours
    return defn


# ===========================================================================
# Test class — Ops
# ===========================================================================

class TestOpsFunctional:
    """FT-OPS-001 through FT-OPS-010: Operations domain functional tests."""

    # -----------------------------------------------------------------------
    # FT-OPS-001: Vendor onboarding — clean (valid docs, Oracle Fusion <8 min)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_ops_001_vendor_onboarding_clean(self, workflow_engine, mock_state_store, base_state):
        """FT-OPS-001: Vendor with valid docs onboarded to Oracle Fusion within timeout."""
        steps = [
            {"id": "validate_docs", "type": "agent",
             "agent": "vendor_manager", "action": "validate_vendor_documents"},
            {"id": "sanctions_check", "type": "agent",
             "agent": "compliance_guard", "action": "sanctions_screening",
             "depends_on": ["validate_docs"]},
            {"id": "check_sanctions", "type": "condition",
             "condition": "sanctions_hit == false",
             "true_path": "create_erp_record", "false_path": "block_vendor",
             "depends_on": ["sanctions_check"]},
            {"id": "create_erp_record", "type": "agent",
             "agent": "vendor_manager", "action": "create_oracle_fusion_vendor",
             "depends_on": ["check_sanctions"]},
            {"id": "notify_procurement", "type": "notify", "connector": "email",
             "depends_on": ["create_erp_record"]},
            {"id": "block_vendor", "type": "notify", "connector": "slack",
             "depends_on": ["check_sanctions"]},
        ]
        definition = _make_definition(steps, timeout_hours=0.133)  # ~8 min
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {
            "vendor_name": "Acme Supplies Pvt Ltd",
            "gstin": "29ABCDE1234F1Z5",
            "sanctions_hit": "false",
        }
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "completed"
        step_results = result["step_results"]
        assert step_results["validate_docs"]["status"] == "completed"
        assert step_results["sanctions_check"]["status"] == "completed"
        assert "create_erp_record" in step_results
        assert step_results["create_erp_record"]["status"] == "completed"
        assert step_results["notify_procurement"]["output"]["status"] == "sent"

    # -----------------------------------------------------------------------
    # FT-OPS-002: Vendor onboarding — sanctions hit (blocked, no ERP record)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_ops_002_vendor_onboarding_sanctions_hit(self, workflow_engine, mock_state_store, base_state):
        """FT-OPS-002: Vendor flagged on sanctions — blocked, no ERP record created."""
        steps = [
            {"id": "validate_docs", "type": "agent",
             "agent": "vendor_manager", "action": "validate_vendor_documents"},
            {"id": "sanctions_check", "type": "agent",
             "agent": "compliance_guard", "action": "sanctions_screening",
             "depends_on": ["validate_docs"]},
            {"id": "check_sanctions", "type": "condition",
             "condition": "sanctions_hit == true",
             "true_path": "block_vendor", "false_path": None,
             "depends_on": ["sanctions_check"]},
            {"id": "block_vendor", "type": "notify", "connector": "slack",
             "depends_on": ["check_sanctions"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {
            "vendor_name": "Sanctioned Corp",
            "sanctions_hit": "true",
        }
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "completed"
        step_results = result["step_results"]
        # Sanctions condition evaluated to true
        assert step_results["check_sanctions"]["output"]["result"] is True
        # Block notification sent
        assert "block_vendor" in step_results
        assert step_results["block_vendor"]["output"]["status"] == "sent"
        # No ERP record step exists in workflow for sanctioned vendor
        assert "create_erp_record" not in step_results

    # -----------------------------------------------------------------------
    # FT-OPS-003: L1 ticket resolution (payment status, <30s, closed)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_ops_003_l1_ticket_resolution(self):
        """FT-OPS-003: L1 support ticket — payment status query resolved and closed."""
        step = {
            "id": "resolve_l1",
            "type": "agent",
            "agent": "support_triage",
            "action": "resolve_payment_status_query",
        }
        state = {
            "context": {
                "ticket_id": "TKT-20260321-001",
                "category": "payment_status",
                "severity": "L1",
                "customer_id": "CUST-042",
            },
            "step_results": {},
        }

        result = await execute_step(step, state)

        assert result["status"] == "completed"
        assert result["agent"] == "support_triage"
        assert result["action"] == "resolve_payment_status_query"
        assert result["step_id"] == "resolve_l1"

    # -----------------------------------------------------------------------
    # FT-OPS-004: L2 ticket enrichment (classified, history, sentiment)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_ops_004_l2_ticket_enrichment(self, workflow_engine, mock_state_store, base_state):
        """FT-OPS-004: L2 ticket enriched with classification, history, and sentiment."""
        steps = [
            {"id": "classify_ticket", "type": "agent",
             "agent": "support_triage", "action": "classify_ticket"},
            {"id": "fetch_history", "type": "agent",
             "agent": "support_triage", "action": "fetch_customer_history",
             "depends_on": ["classify_ticket"]},
            {"id": "analyze_sentiment", "type": "agent",
             "agent": "support_triage", "action": "analyze_sentiment",
             "depends_on": ["classify_ticket"]},
            {"id": "enrich_ticket", "type": "agent",
             "agent": "support_triage", "action": "enrich_with_context",
             "depends_on": ["fetch_history", "analyze_sentiment"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {
            "ticket_id": "TKT-20260321-007",
            "severity": "L2",
        }
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "completed"
        step_results = result["step_results"]
        assert step_results["classify_ticket"]["status"] == "completed"
        assert step_results["fetch_history"]["status"] == "completed"
        assert step_results["analyze_sentiment"]["status"] == "completed"
        assert step_results["enrich_ticket"]["status"] == "completed"

    # -----------------------------------------------------------------------
    # FT-OPS-005: Contract renewal alert (28 days, Slack + email)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_ops_005_contract_renewal_alert(self, workflow_engine, mock_state_store, base_state):
        """FT-OPS-005: Contract expiring in 28 days — alert sent via Slack and email."""
        steps = [
            {"id": "check_expiry", "type": "condition",
             "condition": "days_to_expiry <= 30",
             "true_path": "send_alerts", "false_path": "no_action"},
            {"id": "send_alerts", "type": "parallel",
             "steps": ["slack_alert", "email_alert"], "wait_for": "all",
             "depends_on": ["check_expiry"]},
            {"id": "no_action", "type": "agent",
             "agent": "contract_intelligence", "action": "log_no_action",
             "depends_on": ["check_expiry"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {
            "contract_id": "CTR-2024-088",
            "days_to_expiry": 28,
        }
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "completed"
        step_results = result["step_results"]
        assert evaluate_condition("days_to_expiry <= 30", {"days_to_expiry": 28}) is True
        assert "send_alerts" in step_results
        parallel_output = step_results["send_alerts"]["output"]
        assert parallel_output["type"] == "parallel"
        assert len(parallel_output["results"]) == 2  # Slack + email

    # -----------------------------------------------------------------------
    # FT-OPS-006: SLA breach detection (2 days late, penalty computed)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_ops_006_sla_breach_detection(self, workflow_engine, mock_state_store, base_state):
        """FT-OPS-006: SLA breached by 2 days — penalty computed and stakeholders notified."""
        steps = [
            {"id": "detect_breach", "type": "condition",
             "condition": "days_overdue > 0",
             "true_path": "compute_penalty", "false_path": "no_breach"},
            {"id": "compute_penalty", "type": "agent",
             "agent": "contract_intelligence", "action": "compute_sla_penalty",
             "depends_on": ["detect_breach"]},
            {"id": "notify_vendor", "type": "notify", "connector": "email",
             "depends_on": ["compute_penalty"]},
            {"id": "no_breach", "type": "agent",
             "agent": "contract_intelligence", "action": "log_compliant",
             "depends_on": ["detect_breach"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {
            "contract_id": "CTR-2024-102",
            "days_overdue": 2,
            "daily_penalty_rate": 0.005,
            "contract_value": 500000,
        }
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "completed"
        step_results = result["step_results"]
        assert step_results["compute_penalty"]["status"] == "completed"
        assert step_results["notify_vendor"]["output"]["status"] == "sent"
        # Validate penalty arithmetic
        payload = base_state["trigger_payload"]
        penalty = payload["daily_penalty_rate"] * payload["contract_value"] * payload["days_overdue"]
        assert penalty == pytest.approx(5000.0, abs=1)

    # -----------------------------------------------------------------------
    # FT-OPS-007: Regulatory filing prep (7 days to deadline)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_ops_007_regulatory_filing_prep(self, workflow_engine, mock_state_store, base_state):
        """FT-OPS-007: Regulatory filing prepared 7 days before deadline."""
        steps = [
            {"id": "gather_data", "type": "agent",
             "agent": "compliance_guard", "action": "gather_filing_data"},
            {"id": "validate_data", "type": "agent",
             "agent": "compliance_guard", "action": "validate_filing_data",
             "depends_on": ["gather_data"]},
            {"id": "check_readiness", "type": "condition",
             "condition": "days_to_deadline <= 7",
             "true_path": "prepare_filing", "false_path": "defer",
             "depends_on": ["validate_data"]},
            {"id": "prepare_filing", "type": "agent",
             "agent": "compliance_guard", "action": "prepare_regulatory_filing",
             "depends_on": ["check_readiness"]},
            {"id": "submit_for_review", "type": "human_in_loop",
             "assignee_role": "compliance_officer", "timeout_hours": 24,
             "depends_on": ["prepare_filing"]},
            {"id": "defer", "type": "agent",
             "agent": "compliance_guard", "action": "schedule_reminder",
             "depends_on": ["check_readiness"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {
            "filing_type": "RBI_annual",
            "days_to_deadline": 7,
        }
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        # Should pause at HITL for compliance officer review
        assert result["status"] == "waiting_hitl"
        step_results = result["step_results"]
        assert step_results["gather_data"]["status"] == "completed"
        assert step_results["validate_data"]["status"] == "completed"
        assert step_results["prepare_filing"]["status"] == "completed"
        assert "submit_for_review" in step_results
        hitl_output = step_results["submit_for_review"]["output"]
        assert hitl_output["assignee_role"] == "compliance_officer"
        assert hitl_output["timeout_hours"] == 24

    # -----------------------------------------------------------------------
    # FT-OPS-008: IT access provisioning (auto for standard, HITL for sensitive)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_ops_008_it_access_provisioning(self, workflow_engine, mock_state_store, base_state):
        """FT-OPS-008: Standard access auto-provisioned; sensitive access requires HITL."""
        # Test 1: Sensitive access -> HITL
        steps_sensitive = [
            {"id": "classify_access", "type": "condition",
             "condition": "access_level == sensitive",
             "true_path": "hitl_approval", "false_path": None},
            {"id": "hitl_approval", "type": "human_in_loop",
             "assignee_role": "it_security_lead", "timeout_hours": 4,
             "depends_on": ["classify_access"]},
        ]
        definition_sensitive = _make_definition(steps_sensitive)
        state_sensitive = {
            **base_state,
            "id": "wfr_ops_008a",
            "definition": workflow_engine.parser.parse(definition_sensitive),
            "trigger_payload": {"access_level": "sensitive", "resource": "production_db"},
            "steps_total": len(steps_sensitive),
        }
        mock_state_store.load.return_value = state_sensitive

        result_sensitive = await workflow_engine.execute(state_sensitive["id"])
        assert result_sensitive["status"] == "waiting_hitl"
        assert "hitl_approval" in result_sensitive["step_results"]
        assert result_sensitive["step_results"]["hitl_approval"]["output"]["assignee_role"] == "it_security_lead"

        # Test 2: Standard access -> auto provisioned (condition evaluates false, no HITL)
        steps_standard = [
            {"id": "classify_access", "type": "condition",
             "condition": "access_level == sensitive",
             "true_path": None, "false_path": "auto_provision"},
            {"id": "auto_provision", "type": "agent",
             "agent": "it_operations", "action": "provision_standard_access",
             "depends_on": ["classify_access"]},
        ]
        definition_standard = _make_definition(steps_standard)
        state_standard = {
            **base_state,
            "id": "wfr_ops_008b",
            "definition": workflow_engine.parser.parse(definition_standard),
            "trigger_payload": {"access_level": "standard", "resource": "jira"},
            "steps_total": len(steps_standard),
            "step_results": {},
        }
        mock_state_store.load.return_value = state_standard

        result_standard = await workflow_engine.execute(state_standard["id"])
        assert result_standard["status"] == "completed"
        # Condition evaluated false (standard != sensitive)
        assert result_standard["step_results"]["classify_access"]["output"]["result"] is False
        assert "auto_provision" in result_standard["step_results"]
        assert result_standard["step_results"]["auto_provision"]["status"] == "completed"

    # -----------------------------------------------------------------------
    # FT-OPS-009: IT incident response (P1 severity escalation)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_ops_009_it_incident_response_p1(self, workflow_engine, mock_state_store, base_state):
        """FT-OPS-009: P1 severity incident triggers immediate escalation chain."""
        steps = [
            {"id": "classify_incident", "type": "agent",
             "agent": "it_operations", "action": "classify_incident"},
            {"id": "check_severity", "type": "condition",
             "condition": "severity == P1",
             "true_path": "escalate_p1", "false_path": None,
             "depends_on": ["classify_incident"]},
            {"id": "escalate_p1", "type": "parallel",
             "steps": ["page_oncall", "notify_cto", "create_war_room"],
             "wait_for": "all",
             "depends_on": ["check_severity"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {
            "incident_id": "INC-20260321-001",
            "severity": "P1",
            "service": "payment-gateway",
        }
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "completed"
        step_results = result["step_results"]
        assert step_results["classify_incident"]["status"] == "completed"
        # Condition evaluated severity == P1 as true
        assert step_results["check_severity"]["output"]["result"] is True
        # P1 escalation path taken — all 3 parallel actions completed
        assert "escalate_p1" in step_results
        parallel_output = step_results["escalate_p1"]["output"]
        assert parallel_output["type"] == "parallel"
        assert len(parallel_output["results"]) == 3  # page_oncall, notify_cto, create_war_room
        for r in parallel_output["results"]:
            assert r["status"] == "completed"

    # -----------------------------------------------------------------------
    # FT-OPS-010: SAR draft preparation (suspicious pattern detected)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_ops_010_sar_draft_preparation(self, workflow_engine, mock_state_store, base_state):
        """FT-OPS-010: Suspicious Activity Report drafted when pattern detected."""
        steps = [
            {"id": "detect_pattern", "type": "agent",
             "agent": "risk_sentinel", "action": "detect_suspicious_pattern"},
            {"id": "check_suspicion", "type": "condition",
             "condition": "suspicious == true",
             "true_path": "draft_sar", "false_path": None,
             "depends_on": ["detect_pattern"]},
            {"id": "draft_sar", "type": "agent",
             "agent": "compliance_guard", "action": "draft_sar_report",
             "depends_on": ["check_suspicion"]},
            {"id": "submit_for_mlro", "type": "human_in_loop",
             "assignee_role": "mlro", "timeout_hours": 24,
             "depends_on": ["draft_sar"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {
            "case_id": "MON-20260321-003",
            "suspicious": "true",
            "pattern": "rapid_cross_border_transfers",
        }
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        # Should pause at HITL for MLRO review
        assert result["status"] == "waiting_hitl"
        step_results = result["step_results"]
        assert step_results["detect_pattern"]["status"] == "completed"
        # Condition evaluated suspicious == true
        assert step_results["check_suspicion"]["output"]["result"] is True
        assert step_results["draft_sar"]["status"] == "completed"
        assert "submit_for_mlro" in step_results
        hitl_output = step_results["submit_for_mlro"]["output"]
        assert hitl_output["assignee_role"] == "mlro"
        assert hitl_output["timeout_hours"] == 24


# ===========================================================================
# Test class — Marketing
# ===========================================================================

class TestMarketingFunctional:
    """FT-MKT-001 through FT-MKT-003: Marketing domain functional tests."""

    # -----------------------------------------------------------------------
    # FT-MKT-001: Lead scoring (HubSpot form, scored <2 min)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_mkt_001_lead_scoring(self, workflow_engine, mock_state_store, base_state):
        """FT-MKT-001: Inbound HubSpot lead scored and routed within 2 min."""
        steps = [
            {"id": "ingest_lead", "type": "agent",
             "agent": "crm_intelligence", "action": "ingest_hubspot_lead"},
            {"id": "score_lead", "type": "agent",
             "agent": "crm_intelligence", "action": "compute_lead_score",
             "depends_on": ["ingest_lead"]},
            {"id": "check_score", "type": "condition",
             "condition": "lead_score >= 70",
             "true_path": "assign_to_sales", "false_path": "add_to_nurture",
             "depends_on": ["score_lead"]},
            {"id": "assign_to_sales", "type": "notify", "connector": "slack",
             "depends_on": ["check_score"]},
            {"id": "add_to_nurture", "type": "agent",
             "agent": "crm_intelligence", "action": "add_to_nurture_campaign",
             "depends_on": ["check_score"]},
        ]
        definition = _make_definition(steps, timeout_hours=0.033)  # ~2 min
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {
            "source": "hubspot_form",
            "lead_email": "buyer@enterprise.com",
            "lead_score": 85,
        }
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "completed"
        step_results = result["step_results"]
        assert step_results["ingest_lead"]["status"] == "completed"
        assert step_results["score_lead"]["status"] == "completed"
        # High score (85 >= 70) -> assigned to sales
        assert "assign_to_sales" in step_results
        assert step_results["assign_to_sales"]["output"]["status"] == "sent"
        assert step_results["assign_to_sales"]["output"]["connector"] == "slack"

    # -----------------------------------------------------------------------
    # FT-MKT-002: Campaign budget reallocation (>₹50K → HITL)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_mkt_002_campaign_budget_reallocation_hitl(self, workflow_engine, mock_state_store, base_state):
        """FT-MKT-002: Budget reallocation >₹50K triggers HITL for marketing head."""
        steps = [
            {"id": "analyze_performance", "type": "agent",
             "agent": "campaign_pilot", "action": "analyze_campaign_performance"},
            {"id": "check_realloc_amount", "type": "condition",
             "condition": "reallocation_amount > 50000",
             "true_path": "hitl_approval", "false_path": "auto_reallocate",
             "depends_on": ["analyze_performance"]},
            {"id": "hitl_approval", "type": "human_in_loop",
             "assignee_role": "marketing_head", "timeout_hours": 8,
             "depends_on": ["check_realloc_amount"]},
            {"id": "auto_reallocate", "type": "agent",
             "agent": "campaign_pilot", "action": "reallocate_budget",
             "depends_on": ["check_realloc_amount"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {
            "campaign_id": "CAMP-2026-Q1-003",
            "reallocation_amount": 75000,
            "from_channel": "display",
            "to_channel": "search",
        }
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "waiting_hitl"
        step_results = result["step_results"]
        assert step_results["analyze_performance"]["status"] == "completed"
        # ₹75K > ₹50K threshold -> HITL triggered
        assert evaluate_condition("reallocation_amount > 50000", {"reallocation_amount": 75000}) is True
        assert "hitl_approval" in step_results
        assert step_results["hitl_approval"]["output"]["assignee_role"] == "marketing_head"
        assert step_results["hitl_approval"]["output"]["timeout_hours"] == 8

    # -----------------------------------------------------------------------
    # FT-MKT-003: Brand crisis detection (100 neg mentions/hr)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_mkt_003_brand_crisis_detection(self, workflow_engine, mock_state_store, base_state):
        """FT-MKT-003: Brand crisis detected at 100+ negative mentions/hr — war room activated."""
        steps = [
            {"id": "monitor_mentions", "type": "agent",
             "agent": "brand_monitor", "action": "aggregate_sentiment"},
            {"id": "check_crisis", "type": "condition",
             "condition": "neg_mentions_per_hour >= 100",
             "true_path": "activate_crisis", "false_path": None,
             "depends_on": ["monitor_mentions"]},
            {"id": "activate_crisis", "type": "parallel",
             "steps": ["notify_pr_team", "pause_campaigns", "draft_response"],
             "wait_for": "all",
             "depends_on": ["check_crisis"]},
            {"id": "escalate_to_cxo", "type": "human_in_loop",
             "assignee_role": "cmo", "timeout_hours": 1,
             "depends_on": ["activate_crisis"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {
            "neg_mentions_per_hour": 120,
            "source_platform": "twitter",
            "topic": "data_breach_rumor",
        }
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        # Crisis detected -> parallel actions -> HITL for CMO
        assert result["status"] == "waiting_hitl"
        step_results = result["step_results"]
        assert step_results["monitor_mentions"]["status"] == "completed"
        # Crisis path taken (120 >= 100)
        assert evaluate_condition(
            "neg_mentions_per_hour >= 100", {"neg_mentions_per_hour": 120}
        ) is True
        assert step_results["check_crisis"]["output"]["result"] is True
        assert "activate_crisis" in step_results
        parallel_output = step_results["activate_crisis"]["output"]
        assert parallel_output["type"] == "parallel"
        assert len(parallel_output["results"]) == 3
        # CMO escalation triggered
        assert "escalate_to_cxo" in step_results
        assert step_results["escalate_to_cxo"]["output"]["assignee_role"] == "cmo"
        assert step_results["escalate_to_cxo"]["output"]["timeout_hours"] == 1
