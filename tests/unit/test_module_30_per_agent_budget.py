"""Foundation #6 — Module 30 Per-Agent Budget Enforcement.

Source-pin tests for TC-BUDGET-001 through TC-BUDGET-006 from
``docs/qa_test_matrix.yml``. UI tests for TC-BUDGET-007 and
TC-BUDGET-008 live in ``ui/e2e/qa-module-30-per-agent-budget.spec.ts``.

These tests pin the production contracts for the budget surface
without spinning up a real DB:

- Agent.cost_controls accepts ``monthly_cost_cap_usd`` (canonical
  field), ``daily_token_budget``, ``on_budget_exceeded``.
- The pre-run gate in ``api/v1/agents.py`` reads the cap, sums
  AgentCostLedger rows for the current month, and returns
  ``status=budget_exceeded`` with the exact format
  ``Monthly budget exceeded: $X.XX / $Y.YY`` when over.
- The agent run hook adds a row to AgentCostLedger keyed on the
  current ``period_date`` (so monthly resets work mechanically).
- ``GET /agents/{id}/budget`` returns
  ``monthly_cap_usd``, ``monthly_spent_usd``,
  ``monthly_pct_used``, ``warnings`` with a ≥80% warning at the
  documented threshold.
- The UI Cost tab reads ``cost_controls.monthly_cap_usd`` and
  ``cost_controls.cost_current_usd`` (legacy field shape kept by
  the read-side API for backward compat).

Why source-pin instead of integration: integration tests for the
budget gate need a real DB + Celery broker. Foundation #7 PR-E
ships those services in CI; once it lands, a follow-up promotes
these to integration tests against live state.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────
# TC-BUDGET-001 — Create agent with monthly budget cap
# ─────────────────────────────────────────────────────────────────


def test_tc_budget_001_cost_controls_schema_accepts_monthly_cap() -> None:
    """The CostControls Pydantic schema must accept a non-zero
    monthly cap and document the on_budget_exceeded action."""
    src = (REPO / "core" / "schemas" / "api.py").read_text(encoding="utf-8")
    assert "daily_token_budget" in src
    assert "on_budget_exceeded" in src
    # Default behavior is to pause + alert, NOT silently truncate.
    assert '"pause_and_alert"' in src or "'pause_and_alert'" in src


def test_tc_budget_001_agent_model_has_cost_controls_jsonb() -> None:
    """Agent.cost_controls JSONB column is the storage for the cap.
    If this disappears the entire budget surface is gone."""
    src = (REPO / "core" / "models" / "agent.py").read_text(encoding="utf-8")
    assert "cost_controls" in src


# ─────────────────────────────────────────────────────────────────
# TC-BUDGET-002 — Cost tracking after execution
# ─────────────────────────────────────────────────────────────────


def test_tc_budget_002_agent_cost_ledger_has_required_columns() -> None:
    """AgentCostLedger must carry tenant + agent + period_date +
    cost_usd + token_count + task_count. Missing any of these
    breaks monthly aggregation."""
    src = (REPO / "core" / "models" / "agent.py").read_text(encoding="utf-8")
    assert "class AgentCostLedger" in src
    for col in ("tenant_id", "agent_id", "period_date", "token_count",
                "cost_usd", "task_count"):
        assert col in src, f"AgentCostLedger column {col} missing"


def test_tc_budget_002_agent_run_writes_to_cost_ledger() -> None:
    """The agent run hook in api/v1/agents.py must update or insert
    an AgentCostLedger row per (agent, period_date)."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert "AgentCostLedger" in src
    # Either upsert OR explicit get-or-create — both legitimate.
    has_write = ("cost_usd = float(ledger.cost_usd or 0)" in src
                 or "AgentCostLedger(" in src)
    assert has_write, "no path that writes a cost ledger row found"


# ─────────────────────────────────────────────────────────────────
# TC-BUDGET-003 — Budget exceeded → execution blocked
# ─────────────────────────────────────────────────────────────────


def test_tc_budget_003_pre_run_gate_returns_budget_exceeded_status() -> None:
    """When monthly_spent >= monthly_cap, the run handler must
    return status=budget_exceeded with the specific message
    format the UI parses. CHANGING THIS BREAKS THE STOP-ON-OVER
    UX without warning."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert '"status": "budget_exceeded"' in src
    assert "Monthly budget exceeded:" in src


def test_tc_budget_003_pre_run_gate_uses_with_advisory_lock() -> None:
    """The budget check must guard against concurrent runs that
    each see the cap as not-yet-exceeded. The code comment
    explicitly references this race; if the lock disappears,
    two parallel calls can both blow past the cap."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert "concurrent budget checks" in src or "advisory" in src.lower()


# ─────────────────────────────────────────────────────────────────
# TC-BUDGET-004 — Agent without budget → unlimited execution
# ─────────────────────────────────────────────────────────────────


def test_tc_budget_004_no_cap_means_no_block() -> None:
    """When monthly_cost_cap_usd is 0 / unset, the gate must NOT
    block. The /budget endpoint also reports pct_used = 0 (not a
    div-by-zero error)."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    # The pct-used calculation in get_agent_budget short-circuits
    # on monthly_cap = 0.
    assert "if monthly_cap > 0" in src or "monthly_cap > 0" in src


# ─────────────────────────────────────────────────────────────────
# TC-BUDGET-005 — Budget warning at 80% utilization
# ─────────────────────────────────────────────────────────────────


def test_tc_budget_005_eighty_percent_warning_in_budget_endpoint() -> None:
    """GET /agents/{id}/budget must include the 80%+ warning in
    the warnings list. The numeric threshold is part of the
    contract — if it moves, dashboards lose their amber state."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert "pct_used >= 80" in src
    assert "Monthly budget at 80%+ — approaching limit" in src


def test_tc_budget_005_one_hundred_percent_warning_phrasing() -> None:
    """The 100% warning must use the documented "exceeded" phrasing
    so the UI badge flips to red, not amber."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert "Monthly budget exceeded — agent will be paused on next run" in src


# ─────────────────────────────────────────────────────────────────
# TC-BUDGET-006 — Budget resets monthly
# ─────────────────────────────────────────────────────────────────


def test_tc_budget_006_monthly_window_uses_first_of_month() -> None:
    """The monthly aggregation MUST query
    period_date >= month_start (with day=1 hour=0). Anything else
    (e.g. last 30 days) leaks across monthly boundaries and
    silently keeps "exceeded" tenants blocked into the next
    period."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert "day=1, hour=0, minute=0, second=0, microsecond=0" in src
    assert "period_date >= month_start" in src


def test_tc_budget_006_unique_constraint_keeps_one_row_per_period() -> None:
    """AgentCostLedger has a unique constraint on
    (tenant_id, agent_id, period_date). Without it, parallel
    writes create duplicate rows that double-count the spend."""
    src = (REPO / "core" / "models" / "agent.py").read_text(encoding="utf-8")
    assert ('UniqueConstraint("tenant_id", "agent_id", "period_date")' in src
            or 'UniqueConstraint(\'tenant_id\', \'agent_id\', \'period_date\')' in src)


# ─────────────────────────────────────────────────────────────────
# Cross-pin — UI Cost tab reads the legacy fields
# ─────────────────────────────────────────────────────────────────


def test_ui_cost_tab_reads_legacy_cost_controls_shape() -> None:
    """The UI CostTab reads ``cost_controls.monthly_cap_usd`` and
    ``cost_controls.cost_current_usd`` (note: monthly_cap_usd, NOT
    monthly_cost_cap_usd — the API serialiser flattens). If the
    serialiser changes, the UI Cost tab silently shows zeros.
    """
    src = (REPO / "ui" / "src" / "pages" / "AgentDetail.tsx").read_text(
        encoding="utf-8"
    )
    assert "cost_controls?.monthly_cap_usd" in src
    assert "cost_controls?.cost_current_usd" in src
    # Empty-state path must remain (TC-BUDGET-008 in Playwright).
    assert "No monthly cost cap configured" in src
