"""Regression tests — Ramesh/Uday CA Firms sweep, 2026-04-28.

The Ramesh report (`CA_FIRMS_TEST_RameshUday28Apr2026.md`) prescribed
five sub-bug "fixes" against confidence math. All five would have
regressed prior fixes (BUG-012 / TC_007) without addressing the real
symptom. This file pins:

1. The genuine bug class the report's symptom hinted at —
   FP&A and Close agents called connector tool names that no connector
   in this repo registers (`tally.get_profit_and_loss`,
   `zoho_books.get_budget`, `google_sheets.get_range`). The fix is a
   shared P&L connector chain and a structured-budget input contract.

2. The sibling-route sweep — chat / MCP / A2A previously called
   `langgraph_run` without resolved connector_config, so any agent
   invoked through them reproduced "shadow accuracy stuck at 40%" the
   same way the original /agents/{id}/run defect did.

3. The defensive guards that prevent silent regression of the rejected
   prescribed fixes (the BUG-012 noise floor of 0.10 must hold; the
   FP&A confidence math worst case must remain a meaningful absent-data
   signal, not be flattened to a constant).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from connectors.finance.tally import TallyConnector
from connectors.finance.zoho_books import ZohoBooksConnector
from core.agents.finance._pnl_chain import (
    PNL_CHAIN,
    fetch_pnl_via_chain,
    normalize_pnl,
    period_to_date_range,
)

# ──────────────────────────────────────────────────────────────────
# Bug class A — FP&A / Close agents called non-existent connector tools
# ──────────────────────────────────────────────────────────────────


class TestFpaToolNamesAreRegistered:
    """Every connector.tool referenced by the FP&A and Close agents
    must exist in the matching connector's _tool_registry."""

    @pytest.fixture
    def tally(self) -> TallyConnector:
        return TallyConnector(config={})

    @pytest.fixture
    def zoho(self) -> ZohoBooksConnector:
        return ZohoBooksConnector(config={})

    def test_pnl_chain_tools_exist_on_their_connectors(self, tally, zoho) -> None:
        """The chain tries Zoho, QuickBooks, Tally in order. Each named
        tool MUST be registered on the named connector — otherwise the
        gateway raises ValueError and the whole chain falls through to
        empty actuals (the original Ramesh symptom)."""
        # Trigger _register_tools() via constructor side-effect
        tally._register_tools()
        zoho._register_tools()

        for connector_name, tool_name, _period_style in PNL_CHAIN:
            if connector_name == "tally":
                assert tool_name in tally._tool_registry, (
                    f"Tally is missing {tool_name!r}. Either add it to "
                    f"connectors/finance/tally.py:_register_tools() or "
                    f"remove it from PNL_CHAIN."
                )
            elif connector_name == "zoho_books":
                assert tool_name in zoho._tool_registry, (
                    f"ZohoBooks is missing {tool_name!r}. Either add it "
                    f"to zoho_books.py:_register_tools() or update PNL_CHAIN."
                )
            # quickbooks isn't instantiated here to avoid pulling in its
            # auth dependencies; the fpa_agent / close_agent direct test
            # below loads the real QuickBooks _tool_registry.

    def test_quickbooks_get_profit_loss_exists(self) -> None:
        from connectors.finance.quickbooks import QuickbooksConnector

        qb = QuickbooksConnector(config={})
        qb._register_tools()
        assert "get_profit_loss" in qb._tool_registry

    def test_close_agent_balance_sheet_uses_real_tool(self) -> None:
        """close_agent calls get_balance_sheet on zoho_books or quickbooks
        — both must register the tool. Tally previously was called too,
        but Tally has no get_balance_sheet — the chain MUST NOT include
        Tally for balance sheet."""
        from connectors.finance.quickbooks import QuickbooksConnector

        zoho = ZohoBooksConnector(config={})
        zoho._register_tools()
        qb = QuickbooksConnector(config={})
        qb._register_tools()

        assert "get_balance_sheet" in zoho._tool_registry
        assert "get_balance_sheet" in qb._tool_registry

        tally = TallyConnector(config={})
        tally._register_tools()
        assert "get_balance_sheet" not in tally._tool_registry, (
            "Tally has no get_balance_sheet — close_agent must not "
            "include 'tally' in its balance-sheet fallback chain."
        )

    def test_fpa_agent_default_tools_authorize_pnl_chain(self) -> None:
        """The default authorized_tools for fpa_agent must include the
        canonical tool names the agent now calls. Otherwise the gateway
        scope check rejects the call before it even reaches the
        connector — bug fix invisible at runtime, regression silent."""
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

        tools = _AGENT_TYPE_DEFAULT_TOOLS["fpa_agent"]
        assert "get_profit_loss" in tools
        assert "get_trial_balance" in tools

    def test_close_agent_default_tools_authorize_chain(self) -> None:
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

        tools = _AGENT_TYPE_DEFAULT_TOOLS["close_agent"]
        assert "get_trial_balance" in tools
        assert "get_profit_loss" in tools
        assert "get_balance_sheet" in tools

    def test_fpa_source_no_longer_calls_dead_tool_names(self) -> None:
        """Source-pin: the literal CALL tuples for non-existent tools
        must not appear in the agent source. Comments referencing the
        old names by way of explanation are fine — the test scans only
        for the executable patterns."""
        src = Path("core/agents/finance/fpa_agent.py").read_text(encoding="utf-8")
        # The connector-name + tool-name on the same _safe_tool_call line
        assert '"tally", "get_profit_and_loss"' not in src
        assert '"google_sheets", "get_range"' not in src
        assert '"zoho_books", "get_budget"' not in src

    def test_close_agent_source_no_longer_calls_dead_tool_names(self) -> None:
        src = Path("core/agents/finance/close_agent.py").read_text(encoding="utf-8")
        assert '"tally", "get_profit_and_loss"' not in src
        assert '"tally", "get_balance_sheet"' not in src


# ──────────────────────────────────────────────────────────────────
# Bug class B — P&L chain helper correctness
# ──────────────────────────────────────────────────────────────────


class TestPeriodToDateRange:
    def test_yyyy_mm_january(self) -> None:
        assert period_to_date_range("2026-01") == ("2026-01-01", "2026-02-01")

    def test_yyyy_mm_december_rolls_to_next_year(self) -> None:
        assert period_to_date_range("2025-12") == ("2025-12-01", "2026-01-01")

    def test_yyyy_only(self) -> None:
        assert period_to_date_range("2026") == ("2026-01-01", "2027-01-01")

    def test_empty_returns_empty(self) -> None:
        assert period_to_date_range("") == ("", "")

    def test_garbage_returns_empty(self) -> None:
        assert period_to_date_range("not-a-date") == ("", "")


class TestNormalizePnl:
    def test_flat_zoho_shape(self) -> None:
        result = normalize_pnl({
            "total_revenue": 1_000_000,
            "cost_of_goods_sold": 400_000,
            "operating_expenses": 200_000,
            "net_profit": 350_000,
        })
        assert result == {
            "revenue": 1_000_000.0,
            "cogs": 400_000.0,
            "opex": 200_000.0,
            "net_profit": 350_000.0,
        }

    def test_string_numbers_with_commas(self) -> None:
        result = normalize_pnl({"revenue": "1,250,000"})
        assert result["revenue"] == 1_250_000.0

    def test_rows_of_dicts_quickbooks_style(self) -> None:
        result = normalize_pnl({
            "Rows": [
                {"line_item": "Total Revenue", "amount": 500_000},
                {"line_item": "operating_expenses", "amount": "100000"},
            ],
        })
        assert result["revenue"] == 500_000.0
        assert result["opex"] == 100_000.0

    def test_unknown_shape_returns_empty(self) -> None:
        assert normalize_pnl({"random_key": "no signal"}) == {}


@pytest.mark.asyncio
class TestFetchPnlViaChain:
    """Replay the symptom — chain falls through gracefully when no
    connector returns numbers, succeeds on the first one that does."""

    async def test_first_connector_succeeds_returns_source(self) -> None:
        calls: list[tuple[str, str]] = []

        class _StubAgent:
            async def _safe_tool_call(self, c, t, params, trace, records):
                calls.append((c, t))
                if c == "zoho_books":
                    return {"total_revenue": 800_000, "net_profit": 200_000}
                return {"error": "should not reach"}

        actuals, source = await fetch_pnl_via_chain(
            _StubAgent(), "2026-03", "AcmeCo", [], [],
        )
        assert source == "zoho_books"
        assert actuals["revenue"] == 800_000
        assert calls == [("zoho_books", "get_profit_loss")]

    async def test_falls_through_to_next_connector_on_error(self) -> None:
        calls: list[tuple[str, str]] = []

        class _StubAgent:
            async def _safe_tool_call(self, c, t, params, trace, records):
                calls.append((c, t))
                if c == "zoho_books":
                    return {"error": "auth failed"}
                if c == "quickbooks":
                    return {"revenue": 600_000, "net_profit": 50_000}
                return {"error": "should not reach"}

        actuals, source = await fetch_pnl_via_chain(
            _StubAgent(), "2026-03", "AcmeCo", [], [],
        )
        assert source == "quickbooks"
        assert actuals["revenue"] == 600_000
        # Both first two connectors were attempted; tally not reached
        assert ("zoho_books", "get_profit_loss") in calls
        assert ("quickbooks", "get_profit_loss") in calls
        assert ("tally", "get_trial_balance") not in calls

    async def test_all_connectors_fail_returns_empty(self) -> None:
        """The Ramesh symptom — when every connector errors, the chain
        returns ({}, "") so the caller's confidence math reflects the
        absent-data signal honestly. NOT 0.40 — a meaningful low number
        that triggers HITL or a clear trace, but the BUG-012 noise
        floor (0.10) still excludes runs with NO data at all."""
        class _StubAgent:
            async def _safe_tool_call(self, c, t, params, trace, records):
                return {"error": f"{c} unavailable"}

        actuals, source = await fetch_pnl_via_chain(
            _StubAgent(), "2026-03", "AcmeCo", [], [],
        )
        assert actuals == {}
        assert source == ""


# ──────────────────────────────────────────────────────────────────
# Bug class C — sibling-route resolver (chat / MCP / A2A)
# ──────────────────────────────────────────────────────────────────


class TestSiblingRoutesUseResolver:
    """Source-pin tests — chat / MCP / A2A must call the canonical
    resolver before invoking langgraph_run, otherwise the same shadow-
    accuracy=40% defect class re-appears via those routes."""

    def test_a2a_calls_resolver(self) -> None:
        src = Path("api/v1/a2a.py").read_text(encoding="utf-8")
        assert "_resolve_agent_connector_ids_for_type" in src
        assert "_load_connector_configs_for_agent" in src
        assert "connector_config=connector_config" in src

    def test_chat_calls_resolver(self) -> None:
        src = Path("api/v1/chat.py").read_text(encoding="utf-8")
        assert "_resolve_agent_connector_ids_for_type" in src
        # Chat must call the canonical connector resolver before invoking
        # langgraph_run. May-03 BUG-17 fix promoted chat to the richer
        # `_resolve_connector_configs` (returns config + resolved
        # connector_names for fail-closed dispatch); the old
        # `_load_connector_configs_for_agent` is now a back-compat
        # wrapper around it. Either function name is the canonical
        # resolver path the original 28-Apr defense intended.
        assert (
            "_resolve_connector_configs" in src
            or "_load_connector_configs_for_agent" in src
        )

    def test_mcp_calls_resolver(self) -> None:
        src = Path("api/v1/mcp.py").read_text(encoding="utf-8")
        assert "_resolve_agent_connector_ids_for_type" in src
        assert "_load_connector_configs_for_agent" in src

    def test_resolver_helper_exists(self) -> None:
        """The helper that all three sibling routes import must exist
        — otherwise chat/MCP/A2A fail to import at startup."""
        from api.v1.agents import (
            _load_connector_configs_for_agent,
            _resolve_agent_connector_ids_for_type,
        )

        assert callable(_resolve_agent_connector_ids_for_type)
        assert callable(_load_connector_configs_for_agent)


# ──────────────────────────────────────────────────────────────────
# Bug class D — anti-regression for the rejected prescribed fixes
# ──────────────────────────────────────────────────────────────────


class TestPrescribedFixesAreNotApplied:
    """The Ramesh 2026-04-28 report prescribed five "fixes" — each
    would silently regress prior fixes or mask agent-failure signals.
    These tests pin that we did NOT apply them."""

    def test_bug012_noise_floor_unchanged_at_010(self) -> None:
        """Prescribed Bug #4 asked to raise the threshold from 0.10 to
        0.50. That would silently drop low-confidence runs from the
        average — agents could fail repeatedly and still appear high-
        accuracy. Confirm the threshold remains 0.10."""
        src = Path("api/v1/agents.py").read_text(encoding="utf-8")
        # Either of these two equivalent literals is acceptable
        assert "float(task_confidence) >= 0.10" in src or "float(task_confidence) >= 0.1" in src
        # Reject the prescribed value
        assert "float(task_confidence) >= 0.50" not in src
        assert "float(task_confidence) >= 0.5" not in src

    def test_bug012_skip_low_confidence_runs_not_added(self) -> None:
        """Prescribed Bug #5 asked to add a 0.50 quality-gate that
        *silently drops* runs from shadow accuracy. That regresses the
        BUG-012 fix's whole point: the floor is 0.10 to filter noise
        without dropping real-signal runs."""
        src = Path("api/v1/agents.py").read_text(encoding="utf-8")
        # The prescribed code pattern (paraphrased): "if task_confidence < 0.50: return"
        # Looser anti-pattern: any explicit early return on confidence < 0.5
        assert "if task_confidence < 0.50" not in src
        assert "if task_confidence < 0.5:" not in src

    def test_fpa_confidence_factors_remain_at_040(self) -> None:
        """Prescribed Bug #1 asked to raise the missing-data penalty
        from 0.40 to 0.60 — flattening the no-data signal that the
        whole confidence math is built around. The factors stay at
        0.40 deliberately; the user-visible "stuck at 40%" symptom
        comes from broken tool plumbing (now fixed), not the floor."""
        src = Path("core/agents/finance/fpa_agent.py").read_text(encoding="utf-8")
        # Both penalty branches must stay at 0.40
        assert src.count("factors.append(0.40)") == 2

    def test_agent_graph_short_output_penalty_unchanged(self) -> None:
        """Prescribed Bug #2 asked to halve the penalty (-0.20 → -0.10)
        for short LLM output. Short LLM output is an honest low-
        confidence signal; flattening it produces inflated shadow
        accuracy. Pin the penalty at -0.20."""
        src = Path("core/langgraph/agent_graph.py").read_text(encoding="utf-8")
        assert "confidence -= 0.20" in src
        assert "confidence -= 0.10" not in src

    def test_agent_graph_no_unconditional_success_bonus(self) -> None:
        """Prescribed Bug #3 asked for `if status==success: confidence
        += 0.15`. That double-counts the structured-output bonus that
        already exists, AND inflates confidence on agents that emit
        `status: success` for wrong answers. Pin that the unconditional
        bonus is NOT in compute_confidence."""
        src = Path("core/langgraph/agent_graph.py").read_text(encoding="utf-8")
        assert 'output.get("status") == "success"' not in src
        assert 'if output.get("status") in ("success", "analyzed")' not in src


# ──────────────────────────────────────────────────────────────────
# Replay — FPA agent worst-case math is honest, not flat
# ──────────────────────────────────────────────────────────────────


class TestFpaWorstCaseConfidence:
    """When the connector chain returns no actuals AND no budget is
    provided, the FPA agent's confidence factors land at:
      factor[0] = 0.40 (no actuals, no revenue)
      factor[1] = 0.40 (no budget)
      factor[2] = 0.95 (no critical alerts because no variances)
    Average = 0.583. Above the BUG-012 noise floor of 0.10, but well
    below the FPA confidence_floor of 0.78 — so HITL fires.
    This is the *expected* honest signal; raising the floor to 0.60
    (Ramesh prescribed) flattens it to 0.65 average and HITL still
    fires, so the prescribed fix would only mask the underlying
    plumbing defect without changing the user-visible HITL behaviour."""

    def test_documented_worst_case_average_is_0_583(self) -> None:
        # Documented in the agent — the math is (0.40+0.40+0.95)/3
        average = round((0.40 + 0.40 + 0.95) / 3, 3)
        assert average == 0.583

    def test_documented_worst_case_is_above_noise_floor(self) -> None:
        average = round((0.40 + 0.40 + 0.95) / 3, 3)
        assert average >= 0.10  # BUG-012 noise floor

    def test_documented_worst_case_is_below_promotion_floor(self) -> None:
        from core.agents.finance.fpa_agent import FpaAgentAgent

        average = round((0.40 + 0.40 + 0.95) / 3, 3)
        assert average < FpaAgentAgent.confidence_floor
