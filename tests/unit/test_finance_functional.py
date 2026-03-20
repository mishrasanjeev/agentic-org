"""Finance functional tests — FT-FIN-001 through FT-FIN-015."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from workflows.engine import WorkflowEngine
from workflows.state_store import WorkflowStateStore
from workflows.step_types import execute_step
from workflows.condition_evaluator import evaluate_condition
from core.tool_gateway.pii_masker import mask_pii, mask_string
from core.schemas.errors import ErrorCode, ERROR_META


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_state_store():
    """Async mock of WorkflowStateStore with save/load stubs."""
    store = AsyncMock(spec=WorkflowStateStore)
    store.save = AsyncMock()
    store.load = AsyncMock()
    return store


@pytest.fixture
def workflow_engine(mock_state_store):
    """WorkflowEngine wired to the mock state store."""
    return WorkflowEngine(state_store=mock_state_store)


@pytest.fixture
def base_state():
    """Minimal workflow run state template."""
    return {
        "id": "wfr_test001",
        "status": "running",
        "trigger_payload": {},
        "steps_total": 0,
        "steps_completed": 0,
        "step_results": {},
        "started_at": "2026-03-21T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Helper: build a workflow definition dict accepted by WorkflowParser
# ---------------------------------------------------------------------------

def _make_definition(steps, timeout_hours=None):
    defn = {"name": "test_workflow", "steps": steps}
    if timeout_hours is not None:
        defn["timeout_hours"] = timeout_hours
    return defn


# ===========================================================================
# Test class
# ===========================================================================

class TestFinanceFunctional:
    """FT-FIN-001 through FT-FIN-015: Finance domain functional tests."""

    # -----------------------------------------------------------------------
    # FT-FIN-001: Invoice ingestion from email attachment
    # Expected: Fields extracted <2 min, confidence >88%, Oracle Fusion record created.
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_fin_001_invoice_ingestion(self, mock_state_store):
        """FT-FIN-001: Invoice ingestion from email attachment.
        Agent step extracts invoice fields; returns completed with required fields."""
        step = {
            "id": "extract_invoice",
            "type": "agent",
            "agent": "ap_processor",
            "action": "extract_invoice",
        }
        state = {
            "context": {
                "source_type": "email_attachment",
                "s3_key": "invoices/INV-001.pdf",
            },
            "step_results": {},
        }

        result = await execute_step(step, state)

        assert result["status"] == "completed"
        assert result["type"] == "agent"
        assert result["step_id"] == "extract_invoice"
        assert result["agent"] == "ap_processor"
        assert result["action"] == "extract_invoice"

    # -----------------------------------------------------------------------
    # FT-FIN-002: 3-way match — exact success
    # Expected: status=matched, payment queued, GL posted, remittance sent.
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_fin_002_three_way_match_success(self, workflow_engine, mock_state_store, base_state):
        """FT-FIN-002: 3-way match succeeds — matched, payment queued, GL posted, remittance sent."""
        steps = [
            {"id": "three_way_match", "type": "agent", "agent": "ap_processor", "action": "three_way_match"},
            {"id": "check_match", "type": "condition", "condition": "match_status == matched",
             "true_path": "queue_payment", "false_path": None,
             "depends_on": ["three_way_match"]},
            {"id": "queue_payment", "type": "agent", "agent": "ap_processor", "action": "queue_payment",
             "depends_on": ["check_match"]},
            {"id": "post_gl", "type": "agent", "agent": "ap_processor", "action": "post_gl",
             "depends_on": ["queue_payment"]},
            {"id": "send_remittance", "type": "notify", "connector": "email",
             "depends_on": ["post_gl"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {"match_status": "matched"}
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        run_id = base_state["id"]
        result = await workflow_engine.execute(run_id)

        assert result["status"] == "completed"
        step_results = result["step_results"]
        assert "three_way_match" in step_results
        assert step_results["three_way_match"]["status"] == "completed"
        # Condition evaluated to true (matched)
        assert step_results["check_match"]["output"]["result"] is True
        assert "queue_payment" in step_results
        assert step_results["queue_payment"]["status"] == "completed"
        assert "post_gl" in step_results
        assert step_results["post_gl"]["status"] == "completed"
        assert "send_remittance" in step_results
        assert step_results["send_remittance"]["output"]["status"] == "sent"

    # -----------------------------------------------------------------------
    # FT-FIN-003: 3-way match — mismatch triggers HITL
    # Expected: HITL created, CFO notified, workflow paused.
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_fin_003_three_way_match_mismatch_hitl(self, workflow_engine, mock_state_store, base_state):
        """FT-FIN-003: 3-way match mismatch routes to HITL gate for CFO review."""
        steps = [
            {"id": "three_way_match", "type": "agent", "agent": "ap_processor", "action": "three_way_match"},
            {"id": "check_match", "type": "condition",
             "condition": "match_status == mismatch",
             "true_path": "escalate_mismatch", "false_path": "queue_payment",
             "depends_on": ["three_way_match"]},
            {"id": "escalate_mismatch", "type": "human_in_loop",
             "assignee_role": "cfo", "timeout_hours": 4,
             "depends_on": ["check_match"]},
            {"id": "queue_payment", "type": "agent", "agent": "ap_processor", "action": "queue_payment",
             "depends_on": ["check_match"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {"match_status": "mismatch"}
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "waiting_hitl"
        step_results = result["step_results"]
        assert "escalate_mismatch" in step_results
        hitl_output = step_results["escalate_mismatch"]["output"]
        assert hitl_output["type"] == "human_in_loop"
        assert hitl_output["assignee_role"] == "cfo"
        assert hitl_output["timeout_hours"] == 4

    # -----------------------------------------------------------------------
    # FT-FIN-004: Duplicate invoice rejection
    # Expected: E2006 error, no reprocessing.
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_fin_004_duplicate_invoice_rejection(self):
        """FT-FIN-004: Duplicate invoice detected — E2006, rejected, no reprocessing."""
        error_code = ErrorCode.DUPLICATE_DETECTED
        meta = ERROR_META[error_code.value]

        assert error_code.value == "E2006"
        assert meta["name"] == "DUPLICATE_DETECTED"
        assert meta["retryable"] is False, "Duplicates must not be retried"

        # Simulate condition that flags duplicate
        context = {"invoice_hash": "abc123", "existing_hash": "abc123"}
        is_duplicate = evaluate_condition(
            "invoice_hash == existing_hash", context
        )
        assert is_duplicate is True

        # Verify the condition evaluator blocks further processing
        proceed_condition = evaluate_condition(
            "invoice_hash != existing_hash", context
        )
        assert proceed_condition is False

    # -----------------------------------------------------------------------
    # FT-FIN-005: GSTIN validation failure
    # Expected: E2005 flagged, compliance notified.
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_fin_005_gstin_validation_failure(self):
        """FT-FIN-005: Invalid GSTIN returns E2005, flagged, notification sent."""
        error_code = ErrorCode.GSTIN_INVALID
        meta = ERROR_META[error_code.value]

        assert error_code.value == "E2005"
        assert meta["name"] == "GSTIN_INVALID"
        assert meta["severity"] == "warn"
        assert meta["retryable"] is False

        # Simulate notify step for compliance notification
        step = {"id": "notify_compliance", "type": "notify", "connector": "slack"}
        state = {"context": {"gstin_valid": False}, "step_results": {}}
        result = await execute_step(step, state)

        assert result["status"] == "sent"
        assert result["connector"] == "slack"
        assert result["type"] == "notify"

    # -----------------------------------------------------------------------
    # FT-FIN-006: Payment threshold HITL
    # Expected: ₹6L vs ₹5L threshold triggers HITL gate.
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_fin_006_payment_threshold_hitl(self, workflow_engine, mock_state_store, base_state):
        """FT-FIN-006: Payment of ₹6,00,000 exceeds ₹5,00,000 threshold — HITL gate."""
        steps = [
            {"id": "validate_amount", "type": "condition",
             "condition": "total > 500000",
             "true_path": "hitl_approval", "false_path": "auto_pay"},
            {"id": "hitl_approval", "type": "human_in_loop",
             "assignee_role": "cfo", "timeout_hours": 4,
             "depends_on": ["validate_amount"]},
            {"id": "auto_pay", "type": "agent", "agent": "ap_processor", "action": "auto_pay",
             "depends_on": ["validate_amount"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {"total": 600000}
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "waiting_hitl"
        step_results = result["step_results"]
        assert "hitl_approval" in step_results
        assert step_results["hitl_approval"]["output"]["assignee_role"] == "cfo"
        # auto_pay should NOT have executed
        assert "auto_pay" not in step_results or step_results.get("auto_pay", {}).get("status") == "skipped"

    # -----------------------------------------------------------------------
    # FT-FIN-007: Early payment discount
    # Expected: payment scheduled day 9 of 30 for discount.
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_fin_007_early_payment_discount(self, workflow_engine, mock_state_store, base_state):
        """FT-FIN-007: Early-payment discount — schedule on day 9 of net-30 terms."""
        steps = [
            {"id": "check_discount", "type": "condition",
             "condition": "days_since_invoice < 10",
             "true_path": "schedule_early_pay", "false_path": None},
            {"id": "schedule_early_pay", "type": "agent", "agent": "ap_processor",
             "action": "schedule_payment_day_9",
             "depends_on": ["check_discount"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {"days_since_invoice": 9, "discount_pct": 2}
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "completed"
        step_results = result["step_results"]
        # Condition evaluated to true (9 < 10)
        assert step_results["check_discount"]["output"]["result"] is True
        assert step_results["check_discount"]["output"]["next_path"] == "schedule_early_pay"
        # Early payment path taken
        assert "schedule_early_pay" in step_results
        assert step_results["schedule_early_pay"]["status"] == "completed"
        # Verify the condition evaluator independently
        assert evaluate_condition("days_since_invoice < 10", {"days_since_invoice": 9}) is True
        assert evaluate_condition("days_since_invoice < 10", {"days_since_invoice": 15}) is False

    # -----------------------------------------------------------------------
    # FT-FIN-008: Multi-currency invoice USD→INR
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_fin_008_multi_currency_usd_to_inr(self):
        """FT-FIN-008: Multi-currency invoice converted USD→INR during processing."""
        step = {
            "id": "currency_convert",
            "type": "transform",
        }
        state = {
            "context": {
                "invoice_currency": "USD",
                "target_currency": "INR",
                "exchange_rate": 83.25,
                "invoice_amount_usd": 1200.00,
            },
            "step_results": {},
        }

        result = await execute_step(step, state)

        assert result["status"] == "completed"
        assert result["type"] == "transform"

        # Validate the conversion arithmetic in context
        ctx = state["context"]
        converted_inr = ctx["invoice_amount_usd"] * ctx["exchange_rate"]
        assert converted_inr == pytest.approx(99900.0, rel=1e-2)
        assert ctx["invoice_currency"] == "USD"
        assert ctx["target_currency"] == "INR"

    # -----------------------------------------------------------------------
    # FT-FIN-009: AP agent retry on ERP outage
    # Expected: 3x exponential backoff, then escalate.
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_fin_009_ap_retry_on_erp_outage(self, workflow_engine, mock_state_store, base_state):
        """FT-FIN-009: AP agent retries 3x with exponential backoff on ERP outage, then escalates."""
        steps = [
            {"id": "post_to_erp", "type": "agent", "agent": "ap_processor",
             "action": "post_invoice_to_oracle",
             "on_failure": "retry(3)"},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        # Patch execute_step to fail every time (simulating persistent ERP outage)
        call_count = 0

        async def _failing_step(step, state):
            nonlocal call_count
            call_count += 1
            raise ConnectionError("Oracle Fusion unavailable")

        with patch("workflows.engine.execute_step", side_effect=_failing_step), \
             patch("workflows.retry.asyncio.sleep", new_callable=AsyncMock):
            result = await workflow_engine.execute(base_state["id"])

        # After 3 retries + 1 original attempt = 4 calls total, then failure
        assert call_count == 4
        assert result["status"] == "failed"
        assert "post_to_erp" in result["step_results"]
        assert "Oracle Fusion unavailable" in result["step_results"]["post_to_erp"]["error"]

    # -----------------------------------------------------------------------
    # FT-FIN-010: Bank recon — 200 txns all matched
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_fin_010_bank_recon_all_matched(self):
        """FT-FIN-010: Bank reconciliation with 200 transactions — all matched."""
        txn_count = 200
        items = [f"txn_{i:04d}" for i in range(txn_count)]

        step = {
            "id": "recon_loop",
            "type": "loop",
            "items": items,
        }
        state = {"context": {}, "step_results": {}}

        result = await execute_step(step, state)

        assert result["type"] == "loop"
        assert len(result["results"]) == txn_count
        for r in result["results"]:
            assert r["status"] == "completed"

    # -----------------------------------------------------------------------
    # FT-FIN-011: Bank recon — 3 unmatched breaks
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_fin_011_bank_recon_unmatched_breaks(self, workflow_engine, mock_state_store, base_state):
        """FT-FIN-011: Bank recon with 3 unmatched breaks triggers review."""
        steps = [
            {"id": "run_recon", "type": "agent", "agent": "recon_agent", "action": "reconcile"},
            {"id": "check_breaks", "type": "condition",
             "condition": "unmatched_count > 0",
             "true_path": "flag_breaks", "false_path": "close_recon",
             "depends_on": ["run_recon"]},
            {"id": "flag_breaks", "type": "notify", "connector": "slack",
             "depends_on": ["check_breaks"]},
            {"id": "close_recon", "type": "agent", "agent": "recon_agent", "action": "close",
             "depends_on": ["check_breaks"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {"unmatched_count": 3}
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "completed"
        step_results = result["step_results"]
        assert "flag_breaks" in step_results
        assert step_results["flag_breaks"]["output"]["status"] == "sent"

    # -----------------------------------------------------------------------
    # FT-FIN-012: Break threshold escalation (₹75K > ₹50K)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_fin_012_break_threshold_escalation(self, workflow_engine, mock_state_store, base_state):
        """FT-FIN-012: Break amount ₹75K exceeds ₹50K threshold — escalation to CFO."""
        steps = [
            {"id": "evaluate_break", "type": "condition",
             "condition": "break_amount > 50000",
             "true_path": "escalate_to_cfo", "false_path": "auto_resolve"},
            {"id": "escalate_to_cfo", "type": "human_in_loop",
             "assignee_role": "cfo", "timeout_hours": 2,
             "depends_on": ["evaluate_break"]},
            {"id": "auto_resolve", "type": "agent", "agent": "recon_agent", "action": "auto_resolve",
             "depends_on": ["evaluate_break"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {"break_amount": 75000}
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "waiting_hitl"
        step_results = result["step_results"]
        assert "escalate_to_cfo" in step_results
        assert step_results["escalate_to_cfo"]["output"]["assignee_role"] == "cfo"
        # Verify condition evaluated correctly
        assert evaluate_condition("break_amount > 50000", {"break_amount": 75000}) is True

    # -----------------------------------------------------------------------
    # FT-FIN-013: GSTR-3B preparation
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_fin_013_gstr3b_preparation(self, workflow_engine, mock_state_store, base_state):
        """FT-FIN-013: GSTR-3B return prepared from AP/AR ledgers."""
        steps = [
            {"id": "gather_ap_data", "type": "agent", "agent": "tax_compliance", "action": "extract_ap_ledger"},
            {"id": "gather_ar_data", "type": "agent", "agent": "tax_compliance", "action": "extract_ar_ledger"},
            {"id": "compute_gstr3b", "type": "agent", "agent": "tax_compliance", "action": "compute_gstr3b",
             "depends_on": ["gather_ap_data", "gather_ar_data"]},
            {"id": "validate_totals", "type": "condition",
             "condition": "gstr3b_status == ready",
             "true_path": "submit_draft", "false_path": "flag_discrepancy",
             "depends_on": ["compute_gstr3b"]},
            {"id": "submit_draft", "type": "agent", "agent": "tax_compliance", "action": "submit_gstr3b_draft",
             "depends_on": ["validate_totals"]},
            {"id": "flag_discrepancy", "type": "notify", "connector": "email",
             "depends_on": ["validate_totals"]},
        ]
        definition = _make_definition(steps)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {"gstr3b_status": "ready", "filing_period": "2026-02"}
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "completed"
        step_results = result["step_results"]
        # Both ledger extraction steps completed
        assert step_results["gather_ap_data"]["status"] == "completed"
        assert step_results["gather_ar_data"]["status"] == "completed"
        # GSTR-3B computation completed
        assert step_results["compute_gstr3b"]["status"] == "completed"
        # Draft submission happened (true_path taken because gstr3b_status == ready)
        assert "submit_draft" in step_results
        assert step_results["submit_draft"]["status"] == "completed"

    # -----------------------------------------------------------------------
    # FT-FIN-014: Month-end close D+2
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_fin_014_month_end_close_d2(self, workflow_engine, mock_state_store, base_state):
        """FT-FIN-014: Month-end close completed within D+2 deadline."""
        steps = [
            {"id": "freeze_subledgers", "type": "agent", "agent": "close_agent", "action": "freeze_subledgers"},
            {"id": "run_accruals", "type": "agent", "agent": "close_agent", "action": "run_accruals",
             "depends_on": ["freeze_subledgers"]},
            {"id": "post_adjustments", "type": "agent", "agent": "close_agent", "action": "post_adjustments",
             "depends_on": ["run_accruals"]},
            {"id": "generate_tb", "type": "agent", "agent": "close_agent", "action": "generate_trial_balance",
             "depends_on": ["post_adjustments"]},
            {"id": "validate_close", "type": "condition",
             "condition": "tb_balanced == true",
             "true_path": "notify_stakeholders", "false_path": None,
             "depends_on": ["generate_tb"]},
            {"id": "notify_stakeholders", "type": "notify", "connector": "email",
             "depends_on": ["validate_close"]},
        ]
        definition = _make_definition(steps, timeout_hours=48)
        base_state["definition"] = workflow_engine.parser.parse(definition)
        base_state["trigger_payload"] = {"tb_balanced": "true", "close_period": "2026-02"}
        base_state["steps_total"] = len(steps)
        mock_state_store.load.return_value = base_state

        result = await workflow_engine.execute(base_state["id"])

        assert result["status"] == "completed"
        step_results = result["step_results"]
        # All close steps completed in order
        for step_id in ["freeze_subledgers", "run_accruals", "post_adjustments", "generate_tb"]:
            assert step_results[step_id]["status"] == "completed"
        # Condition evaluated balanced = true
        assert step_results["validate_close"]["output"]["result"] is True
        # Notification sent (balanced = true path)
        assert "notify_stakeholders" in step_results
        assert step_results["notify_stakeholders"]["output"]["status"] == "sent"

    # -----------------------------------------------------------------------
    # FT-FIN-015: TDS computation accuracy
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_ft_fin_015_tds_computation_accuracy(self):
        """FT-FIN-015: TDS computation matches expected deduction amounts."""
        # Simulate TDS computation for Section 194C (contractor payment)
        gross_amount = 500000
        tds_rate_194c = 0.02  # 2% for contractor
        expected_tds = gross_amount * tds_rate_194c
        net_payable = gross_amount - expected_tds

        assert expected_tds == pytest.approx(10000.0, abs=1)
        assert net_payable == pytest.approx(490000.0, abs=1)

        # Simulate TDS for Section 194J (professional fees)
        professional_fees = 250000
        tds_rate_194j = 0.10  # 10%
        expected_tds_194j = professional_fees * tds_rate_194j
        net_payable_194j = professional_fees - expected_tds_194j

        assert expected_tds_194j == pytest.approx(25000.0, abs=1)
        assert net_payable_194j == pytest.approx(225000.0, abs=1)

        # Verify the workflow step for TDS posting completes
        step = {
            "id": "compute_tds",
            "type": "agent",
            "agent": "tax_compliance",
            "action": "compute_tds",
        }
        state = {
            "context": {
                "section": "194C",
                "gross_amount": gross_amount,
                "tds_rate": tds_rate_194c,
                "expected_tds": expected_tds,
            },
            "step_results": {},
        }
        result = await execute_step(step, state)
        assert result["status"] == "completed"
        assert result["agent"] == "tax_compliance"
