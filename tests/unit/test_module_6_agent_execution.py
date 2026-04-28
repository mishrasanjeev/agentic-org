"""Foundation #6 — Module 6 Agent Execution (8 TCs).

Source-pin tests for TC-EXEC-001 through TC-EXEC-008. The
agent-run path is the platform's hot loop — every request the
LangGraph runner accepts is a real LLM call (or fake under
Foundation #7), a real cost ledger write, a real HITL queue
mutation. Contracts pinned here protect the budget gate, the
fallback chain, and the audit trail every other module reads.

Pinned contracts:

- POST /agents/{id}/run requires non-empty ``inputs`` (400 on
  missing); 404 on missing agent; 409 ONLY on retired agents
  (paused agents DO run — UI gates that flow, not the API).
- Pre-flight tool filter: unresolvable tools are dropped with
  a warning log, NOT a hard 400 — preserves backward compat
  with auto-defaulted toolsets that pre-date the registry.
- Budget gate: when monthly_cost_cap_usd > 0, sum cost ledger
  for the current month and refuse new runs with status=
  budget_exceeded + E1008 + the documented message format.
  Postgres advisory-lock serializes concurrent budget checks.
- HITL queue entry created when hitl_trigger is non-empty;
  agent_id + tenant_id + title pinned so the audit can find
  the right HITL row.
- Cost ledger upsert keyed on (tenant_id, agent_id,
  period_date); failure flags hitl_trigger=
  budget_tracking_failed (Foundation #8 false-green: a
  failed ledger write must NOT silently ignore — downstream
  consumers need to know budget tracking is unreliable).
- Shadow accuracy update is atomic via SQL UPDATE; only
  measurable runs (completed/hitl_triggered + confidence
  >= 0.10) move the rolling average.
- LLM router fallback: when primary call fails, log
  llm_falling_back and retry on settings.llm_fallback exactly
  once.
- Clone enforces scope-ceiling (clone tools must be subset of
  parent tools) — 403 with the unauthorized list.
- Clone preserves company_id + connector_ids by default
  (Codex 2026-04-22 audit gap #6 — clone used to silently
  drop these).
- /agents/{id}/budget endpoint cross-pinned with Module 30.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────
# TC-EXEC-001 — Run agent happy path
# ─────────────────────────────────────────────────────────────────


def test_tc_exec_001_run_endpoint_pinned() -> None:
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert '@router.post("/agents/{agent_id}/run")' in src


def test_tc_exec_001_run_requires_non_empty_inputs() -> None:
    """Empty inputs → 400. Foundation #8 false-green prevention:
    silently running with empty inputs would let a misconfigured
    UI form pass null to the agent and produce garbage."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    run_block = src.split('@router.post("/agents/{agent_id}/run")', 1)[1].split(
        "@router.", 1
    )[0]
    assert "if not inputs:" in run_block
    assert (
        'HTTPException(400, "inputs field is required and cannot be empty")'
        in run_block
    )


def test_tc_exec_001_run_returns_404_on_missing_agent() -> None:
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    run_block = src.split('@router.post("/agents/{agent_id}/run")', 1)[1].split(
        "@router.", 1
    )[0]
    assert 'HTTPException(404, "Agent not found")' in run_block


def test_tc_exec_001_run_response_carries_canonical_run_result_keys() -> None:
    """The AgentRunResult contract — pinned by docs/api/agent-
    run-contract.md. ``task_id`` is a deprecated alias for
    ``run_id`` during the v4.8→v5.0 transition."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert '"run_id": msg_id' in src
    assert '"task_id": msg_id' in src
    assert "deprecated alias, removed in v5.0" in src
    assert '"correlation_id": correlation_id' in src
    assert '"hitl_trigger": hitl_trigger or None' in src


# ─────────────────────────────────────────────────────────────────
# TC-EXEC-002 — HITL triggered (low confidence)
# ─────────────────────────────────────────────────────────────────


def test_tc_exec_002_hitl_queue_entry_created_on_trigger() -> None:
    """When hitl_trigger is non-empty, an HITLQueue row is
    inserted with the agent's tenant + agent IDs + a title that
    surfaces the trigger reason. Pin the title format —
    dashboards parse it for "what triggered HITL"."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert "hitl_entry = HITLQueue(" in src
    assert "title=f\"HITL: {agent_config['agent_type']} — {hitl_trigger}\"" in src


def test_tc_exec_002_hitl_trigger_surfaced_in_run_response() -> None:
    """The run response includes ``hitl_trigger`` so the UI can
    render the "needs human review" badge. Without this the
    tab silently doesn't update."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert '"has_hitl": bool(hitl_trigger)' in src
    assert '"hitl_trigger": hitl_trigger or None' in src


def test_tc_exec_002_shadow_accuracy_updates_on_hitl_runs_too() -> None:
    """Both ``completed`` AND ``hitl_triggered`` runs count
    toward shadow accuracy — a HITL trigger is a measurable
    outcome (the agent produced a confidence value, the human
    will judge correctness later). Excluding HITL runs would
    silently shrink the shadow sample pool."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert 'task_status in ("completed", "hitl_triggered")' in src


# ─────────────────────────────────────────────────────────────────
# TC-EXEC-003 — LLM model fallback
# ─────────────────────────────────────────────────────────────────


def test_tc_exec_003_router_falls_back_on_primary_failure() -> None:
    """LLMRouter.complete catches the primary-model exception,
    logs llm_falling_back, and re-tries on the fallback model
    exactly once. Without this, transient Gemini outages turn
    every agent run into a 5xx."""
    src = (REPO / "core" / "llm" / "router.py").read_text(encoding="utf-8")
    complete_block = src.split("async def complete(", 1)[1].split(
        "\n    async def ", 1
    )[0]
    assert "llm_primary_failed" in complete_block
    assert "llm_falling_back" in complete_block
    assert "if model != self.fallback_model:" in complete_block


def test_tc_exec_003_fallback_model_loaded_from_settings() -> None:
    src = (REPO / "core" / "llm" / "router.py").read_text(encoding="utf-8")
    assert "self.fallback_model = settings.llm_fallback" in src


# ─────────────────────────────────────────────────────────────────
# TC-EXEC-004 — Budget exceeded
# ─────────────────────────────────────────────────────────────────


def test_tc_exec_004_budget_check_uses_advisory_lock() -> None:
    """P3.1 — concurrent budget checks must serialize via
    pg_advisory_xact_lock. Without it, two parallel runs both
    see spend < cap and both proceed → overspend.

    Cross-pin with TC-CC-005 (Module 22) and TC-BUDGET-003
    (Module 30)."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    budget_block = src.split("# 5a. Budget check", 1)[1].split(
        "# 5b.", 1
    )[0]
    assert "pg_advisory_xact_lock" in budget_block
    assert "lock_key = abs(hash(str(agent_id))) % (2**31)" in budget_block


def test_tc_exec_004_budget_exceeded_returns_status_e1008() -> None:
    """Documented response shape: status=budget_exceeded +
    error.code=E1008 + the EXACT message format the UI parses
    for the "Monthly budget: X / Y" badge."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    budget_block = src.split("# 5a. Budget check", 1)[1].split(
        "# 5b.", 1
    )[0]
    assert '"status": "budget_exceeded"' in budget_block
    assert '"code": "E1008"' in budget_block
    assert (
        '"message": f"Monthly budget exceeded: ${monthly_spent:.2f} / '
        '${monthly_cap:.2f}"' in budget_block
    )


def test_tc_exec_004_cost_ledger_failure_flags_hitl_trigger() -> None:
    """AGENT-BUDGET-014: if the cost ledger write fails, set
    hitl_trigger=budget_tracking_failed so dashboards know
    budget tracking is unreliable for this run. Foundation #8
    false-green prevention: silently swallowing the failure
    would let agents run blind to the cap."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert "AGENT-BUDGET-014" in src
    assert 'hitl_trigger = hitl_trigger or "budget_tracking_failed"' in src


# ─────────────────────────────────────────────────────────────────
# TC-EXEC-005 — Confidence string handling
# ─────────────────────────────────────────────────────────────────


def test_tc_exec_005_confidence_floor_decimal_conversion_via_str() -> None:
    """confidence_floor stored as Decimal(4, 3); construction
    goes through Decimal(str(value)) so a float like 0.88
    doesn't stringify to 0.8800000000000001 and silently
    introduce rounding drift across writes."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert "Decimal(str(body.confidence_floor))" in src
    # And the str-coercion pattern is repeated wherever a float
    # crosses into the Decimal column.
    assert src.count("Decimal(str(") >= 3


def test_tc_exec_005_confidence_string_input_parsed_into_decimal() -> None:
    """The CSV-import path (line ~1017) accepts confidence_floor
    as a string and parses to Decimal. Without try/except, a
    bad value would 5xx the import. Pin the parse pattern."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert 'confidence_str = (row.get("confidence_floor") or "").strip()' in src
    assert "Decimal(confidence_str)" in src


def test_tc_exec_005_only_measurable_runs_count_toward_accuracy() -> None:
    """Confidence >= 0.10 is the floor for "measurable" — runs
    that came back with confidence < 0.1 (or None) are too
    noisy to move the rolling average. Pin the threshold so a
    refactor can't silently lower it."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert "float(task_confidence) >= 0.10" in src


# ─────────────────────────────────────────────────────────────────
# TC-EXEC-006 — Run paused agent (only retired blocks at API)
# ─────────────────────────────────────────────────────────────────


def test_tc_exec_006_only_retired_status_blocks_run_at_api_layer() -> None:
    """Honest pin of actual behavior: the API only refuses
    retired agents. Paused agents WILL run via the API; the UI
    gates the pause-state button. Pin the EXACT status check
    so a refactor that adds 'paused' here without updating the
    UI flow doesn't silently break the kill-switch UX."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    run_block = src.split('@router.post("/agents/{agent_id}/run")', 1)[1].split(
        "@router.", 1
    )[0]
    assert 'if agent_row.status == "retired":' in run_block
    assert 'HTTPException(409, "Cannot run a retired agent")' in run_block
    # Paused is NOT explicitly blocked here. If a future change
    # adds it, this test should be updated AND the kill-switch
    # UI flow re-evaluated.
    assert 'if agent_row.status == "paused"' not in run_block


# ─────────────────────────────────────────────────────────────────
# TC-EXEC-007 — Clone agent
# ─────────────────────────────────────────────────────────────────


def test_tc_exec_007_clone_endpoint_admin_gated() -> None:
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    clone_block = src.split('"/agents/{agent_id}/clone"', 1)[1][:300]
    assert "require_tenant_admin" in clone_block


def test_tc_exec_007_clone_enforces_scope_ceiling() -> None:
    """Clone authorized_tools MUST be a subset of the parent's
    authorized_tools. Otherwise a clone could escalate
    privileges by adding tools the parent never had."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    clone_block = src.split("async def clone_agent(", 1)[1].split(
        "\n    return ", 1
    )[0]
    assert "if not clone_tools.issubset(parent_tools):" in clone_block
    assert "Clone cannot exceed parent scope ceiling" in clone_block
    # 403 (forbidden) is the right status — caller has perm to
    # clone, just not to escalate scope.
    assert "HTTPException(\n                    403," in clone_block or 'HTTPException(403' in clone_block


def test_tc_exec_007_clone_preserves_company_id_and_connector_ids() -> None:
    """Codex 2026-04-22 audit gap #6: clone used to silently
    drop company_id and connector_ids. The clone now carries
    parent's values by default; overrides can move the clone
    to a different company OR clear connectors explicitly."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert (
        "clone dropped company_id and connector_ids" in src
        or "Codex 2026-04-22 audit gap #6" in src
    )
    clone_block = src.split("async def clone_agent(", 1)[1].split(
        "\n    return ", 1
    )[0]
    assert 'getattr(parent, "company_id", None)' in clone_block
    assert "parent.connector_ids or []" in clone_block


def test_tc_exec_007_clone_starts_in_shadow_status_by_default() -> None:
    """Clones start in shadow — never go straight to active.
    Promotion goes through the same gate as a fresh agent
    (TC-AGT-009)."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert 'status=body.initial_status or "shadow"' in src


# ─────────────────────────────────────────────────────────────────
# TC-EXEC-008 — Agent budget endpoint (cross-pin Module 30)
# ─────────────────────────────────────────────────────────────────


def test_tc_exec_008_budget_endpoint_pinned() -> None:
    """The /agents/{id}/budget endpoint reports monthly cap +
    spent + pct + warnings. Module 30 (TC-BUDGET-005) pins the
    inner contract; this is the cross-pin from the execution
    angle."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert '@router.get("/agents/{agent_id}/budget")' in src
    budget_block = src.split('@router.get("/agents/{agent_id}/budget")', 1)[1].split(
        "@router.", 1
    )[0]
    for key in ('"monthly_cap_usd"', '"monthly_spent_usd"',
                '"monthly_pct_used"', '"warnings"'):
        assert key in budget_block, f"budget endpoint missing key {key}"
