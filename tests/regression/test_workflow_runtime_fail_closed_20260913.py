"""Regression tests for the 2026-09-13 workflow-runtime audit findings.

Each test reproduces a confirmed bug (failing before the fix, passing after):

1. HITL rejection must terminate the run, not execute dependents.
2. Condition steps must skip the branch that was not taken.
3/10. hitl_condition evaluator: BoolOp/strings/missing fields fail closed,
      shared by the LangGraph runtime and BaseAgent.
4. Workflow condition evaluator: missing fields never satisfy a guard.
5. Celery wait/timeout resumption re-drives the engine.
6. A/B outcomes are recorded only on terminal runs; atomic counter update.
7. Content-safety duplicate window is scoped per tenant.
8. Gemini daily cap is tenant-scoped; no fallback on DailyBudgetExceeded.
9. routing_filter match requires at least one overlapping key.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from workflows.engine import WorkflowEngine
from workflows.state_store import InMemoryWorkflowStateRepository, WorkflowStateStore


# ---------------------------------------------------------------------------
# Engine harness
# ---------------------------------------------------------------------------
def _engine() -> tuple[WorkflowEngine, InMemoryWorkflowStateRepository]:
    repo = InMemoryWorkflowStateRepository()
    return WorkflowEngine(WorkflowStateStore(repository=repo, redis=None)), repo


async def _fake_execute_step(step: dict, state: dict) -> dict[str, Any]:
    """Agent steps complete; condition/HITL steps use the real handlers."""
    from workflows import step_types

    if step.get("type") == "condition":
        return await step_types._execute_condition(step, state)
    if step.get("type") == "human_in_loop":
        return await step_types._execute_hitl(step, state)
    return {"step_id": step["id"], "type": "agent", "status": "completed", "output": {"ok": True}}


# ---------------------------------------------------------------------------
# 1. HITL rejection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_hitl_reject_terminates_run_and_never_runs_dependents() -> None:
    engine, repo = _engine()
    definition = {
        "name": "reject",
        "steps": [
            {"id": "approve", "type": "human_in_loop"},
            {"id": "pay", "type": "agent", "agent": "x", "depends_on": ["approve"]},
        ],
    }
    with patch("workflows.engine.execute_step", side_effect=_fake_execute_step):
        run_id = await engine.start_run(definition)
        assert (await engine.execute(run_id))["status"] == "waiting_hitl"

        result = await engine.resume_from_hitl(run_id, {"decision": "reject", "notes": "no"})

    assert result["status"] == "failed"
    state = repo.states[run_id]["state"]
    assert state["status"] == "failed"
    assert state["error"]["code"] == "hitl_rejected"
    assert state["step_results"]["approve"]["status"] == "rejected"
    assert "pay" not in state["step_results"]
    assert repo.transitions[-1]["metadata"]["event"] == "hitl_rejected"

    # A second execute() must not re-drive a rejected run.
    with patch("workflows.engine.execute_step", side_effect=_fake_execute_step):
        again = await engine.execute(run_id)
    assert again["status"] == "failed"
    assert "pay" not in repo.states[run_id]["state"]["step_results"]


@pytest.mark.asyncio
async def test_hitl_approve_still_continues() -> None:
    engine, repo = _engine()
    definition = {
        "name": "approve",
        "steps": [
            {"id": "approve", "type": "human_in_loop"},
            {"id": "pay", "type": "agent", "agent": "x", "depends_on": ["approve"]},
        ],
    }
    with patch("workflows.engine.execute_step", side_effect=_fake_execute_step):
        run_id = await engine.start_run(definition)
        await engine.execute(run_id)
        result = await engine.resume_from_hitl(run_id, {"decision": "approve"})
    assert result["status"] == "completed"
    assert repo.states[run_id]["state"]["step_results"]["pay"]["status"] == "completed"


# ---------------------------------------------------------------------------
# 2. Condition branch skipping
# ---------------------------------------------------------------------------
_BRANCH_DEF = {
    "name": "branch",
    "steps": [
        {
            "id": "gate",
            "type": "condition",
            "condition": "amount > 100",
            "true_path": "big",
            "false_path": "small",
        },
        {"id": "big", "type": "agent", "agent": "x", "depends_on": ["gate"]},
        {"id": "small", "type": "agent", "agent": "x", "depends_on": ["gate"]},
        {"id": "after_small", "type": "agent", "agent": "x", "depends_on": ["small"]},
    ],
}


@pytest.mark.asyncio
async def test_condition_skips_branch_not_taken_in_execute() -> None:
    engine, repo = _engine()
    with patch("workflows.engine.execute_step", side_effect=_fake_execute_step):
        run_id = await engine.start_run(_BRANCH_DEF, {"amount": 500})
        result = await engine.execute(run_id)

    assert result["status"] == "completed"
    results = repo.states[run_id]["state"]["step_results"]
    assert results["gate"]["branch_target"] == "big"
    assert results["big"]["status"] == "completed"
    assert results["small"] == {
        "output": None,
        "status": "skipped",
        "confidence": None,
        "reason": "branch_not_taken",
    }
    assert results["after_small"]["status"] == "skipped"
    assert results["after_small"]["reason"] == "branch_not_taken"


@pytest.mark.asyncio
async def test_condition_skips_branch_not_taken_in_execute_next() -> None:
    engine, repo = _engine()
    with patch("workflows.engine.execute_step", side_effect=_fake_execute_step):
        run_id = await engine.start_run(_BRANCH_DEF, {"amount": 5})
        for _ in range(10):
            out = await engine.execute_next(run_id)
            if out.get("status") == "completed" and "step_results" in out:
                break

    results = repo.states[run_id]["state"]["step_results"]
    assert results["small"]["status"] == "completed"
    assert results["after_small"]["status"] == "completed"
    assert results["big"]["status"] == "skipped"
    assert results["big"]["reason"] == "branch_not_taken"


# ---------------------------------------------------------------------------
# 3 / 10. Shared fail-closed hitl_condition evaluator
# ---------------------------------------------------------------------------
class TestHitlConditionEvaluator:
    def test_boolop_or_triggers(self) -> None:
        from core.langgraph.hitl_condition import evaluate_hitl_condition

        triggered, reason = evaluate_hitl_condition(
            "amount > 500000 OR status == 'flagged'", {"amount": 10, "status": "flagged"}
        )
        assert triggered and "matched" in reason

    def test_boolop_and_not_triggered(self) -> None:
        from core.langgraph.hitl_condition import evaluate_hitl_condition

        triggered, _ = evaluate_hitl_condition(
            "amount > 500000 and status == 'flagged'", {"amount": 10, "status": "flagged"}
        )
        assert triggered is False

    def test_in_and_bool(self) -> None:
        from core.langgraph.hitl_condition import evaluate_hitl_condition

        assert evaluate_hitl_condition("plan in ['enterprise']", {"plan": "enterprise"})[0]
        assert not evaluate_hitl_condition("plan in ['enterprise']", {"plan": "free"})[0]
        assert evaluate_hitl_condition("needs_review == True", {"needs_review": True})[0]

    def test_missing_field_fails_closed(self) -> None:
        from core.langgraph.hitl_condition import evaluate_hitl_condition

        triggered, reason = evaluate_hitl_condition("amount > 500000", {"other": 1})
        assert triggered and "fail closed" in reason

    def test_parse_failure_fails_closed(self) -> None:
        from core.langgraph.hitl_condition import evaluate_hitl_condition

        triggered, _ = evaluate_hitl_condition("amount >> ??", {"amount": 1})
        assert triggered

    def test_confidence_is_available_to_expression(self) -> None:
        from core.langgraph.hitl_condition import evaluate_hitl_condition

        assert evaluate_hitl_condition("confidence < 0.9", {"x": 1}, confidence=0.5)[0]
        assert not evaluate_hitl_condition("confidence < 0.9", {"x": 1}, confidence=0.95)[0]

    def test_agent_graph_uses_boolop(self) -> None:
        from core.langgraph.agent_graph import _check_hitl_trigger

        out = {"amount": 1, "status": "mismatch"}
        assert "matched" in _check_hitl_trigger(0.95, 0.88, "amount > 100 OR status == 'mismatch'", out)
        assert _check_hitl_trigger(0.95, 0.88, "amount > 100 AND status == 'mismatch'", out) == ""
        # Confidence-floor path unchanged.
        assert "confidence" in _check_hitl_trigger(0.5, 0.88, "", out)

    def test_base_agent_evaluates_hitl_condition(self) -> None:
        from core.agents.base import BaseAgent

        agent = BaseAgent("a1", "t1", hitl_condition="amount > 100000")
        hitl = agent._evaluate_hitl({"amount": 250000}, confidence=0.99)
        assert hitl is not None and hitl.trigger_type == "condition_matched"
        assert agent._evaluate_hitl({"amount": 10}, confidence=0.99) is None
        # Missing field fails closed at the agent level too.
        assert agent._evaluate_hitl({"total": 10}, confidence=0.99) is not None
        # Floor still takes precedence.
        assert agent._evaluate_hitl({"amount": 10}, confidence=0.1).trigger_type == "confidence_below_floor"


# ---------------------------------------------------------------------------
# 4. Workflow condition evaluator missing keys
# ---------------------------------------------------------------------------
class TestConditionEvaluatorMissingKeys:
    def test_missing_field_numeric_guard_is_false(self) -> None:
        from workflows.condition_evaluator import evaluate_condition

        assert evaluate_condition("amount > 100000", {}) is False
        assert evaluate_condition("amount > 100000", {"amount": 200000}) is True

    def test_missing_field_in_list_is_false(self) -> None:
        from workflows.condition_evaluator import evaluate_condition

        assert evaluate_condition('plan in ["enterprise"]', {}) is False
        assert evaluate_condition('plan in ["enterprise", "pro"]', {"plan": "pro"}) is True
        assert evaluate_condition('plan not in ["enterprise"]', {"plan": "free"}) is True
        assert evaluate_condition('plan not in ["enterprise"]', {}) is False

    def test_lowercase_and_or_and_quoted_strings(self) -> None:
        from workflows.condition_evaluator import evaluate_condition

        ctx = {"amount": 60000, "domain": "finance"}
        assert evaluate_condition('amount >= 50000 and domain == "finance"', ctx) is True
        assert evaluate_condition('amount >= 500000 or domain == "finance"', ctx) is True
        assert evaluate_condition('amount >= 500000 and domain == "finance"', ctx) is False

    def test_dotted_path_missing_step_is_false(self) -> None:
        from workflows.condition_evaluator import evaluate_condition

        assert evaluate_condition("review.output.score > 0", {}) is False
        assert evaluate_condition("review.output.score > 0", {"review": {"output": {"score": 3}}})

    def test_bare_true_default_and_bare_missing(self) -> None:
        from workflows.condition_evaluator import evaluate_condition

        assert evaluate_condition("true", {}) is True
        assert evaluate_condition("flag", {}) is False
        assert evaluate_condition("flag", {"flag": True}) is True


# ---------------------------------------------------------------------------
# 5. Celery resumption re-drives the engine
# ---------------------------------------------------------------------------
def _import_workflow_tasks():
    """Import core.tasks.workflow_tasks, tolerating the PEP 695 syntax in
    core.tasks.async_runner on interpreters older than 3.12 (CI runs 3.12)."""
    try:
        from core.tasks import workflow_tasks
    except SyntaxError:
        stub = types.ModuleType("core.tasks.async_runner")

        def run_async(awaitable):  # pragma: no cover - fallback shim only
            import asyncio

            return asyncio.run(awaitable)

        stub.run_async = run_async
        sys.modules["core.tasks.async_runner"] = stub
        from core.tasks import workflow_tasks
    return workflow_tasks


def _waiting_state(run_id: str, status: str) -> dict:
    return {
        "id": run_id,
        "status": status,
        "waiting_step_id": "wait-1",
        "definition": {
            "steps": [
                {"id": "wait-1", "type": "wait_for_event"},
                {"id": "after", "type": "agent", "agent": "x", "depends_on": ["wait-1"]},
            ]
        },
        "step_results": {},
        "steps_completed": 0,
    }


@pytest.mark.asyncio
async def test_resume_workflow_wait_redrives_engine() -> None:
    workflow_tasks = _import_workflow_tasks()
    repo = InMemoryWorkflowStateRepository()
    store = WorkflowStateStore(repository=repo, redis=None)
    store.init = AsyncMock()
    store.close = AsyncMock()
    await store.save(_waiting_state("run-redrive", "waiting_event"))

    with patch.object(workflow_tasks, "_state_store", return_value=store), patch(
        "workflows.engine.execute_step", side_effect=_fake_execute_step
    ):
        result = await workflow_tasks._resume_workflow_wait_async("run-redrive", "wait-1")

    assert result["status"] == "resumed"
    assert result["engine_status"] == "completed"
    state = repo.states["run-redrive"]["state"]
    assert state["status"] == "completed"
    assert state["step_results"]["after"]["status"] == "completed"


@pytest.mark.asyncio
async def test_timeout_workflow_event_redrives_engine() -> None:
    workflow_tasks = _import_workflow_tasks()
    from workflows.event_waits import InMemoryWorkflowEventWaitRepository, WorkflowEventWaitStore

    repo = InMemoryWorkflowStateRepository()
    store = WorkflowStateStore(repository=repo, redis=None)
    store.init = AsyncMock()
    store.close = AsyncMock()
    await store.save(_waiting_state("run-timeout", "waiting_event"))
    event_store = WorkflowEventWaitStore(repository=InMemoryWorkflowEventWaitRepository(), redis=None)
    await event_store.register(engine_run_id="run-timeout", step_id="wait-1", event_type="email.opened")

    with patch.object(workflow_tasks, "_state_store", return_value=store), patch.object(
        workflow_tasks, "_event_wait_store", return_value=event_store
    ), patch("workflows.engine.execute_step", side_effect=_fake_execute_step):
        result = await workflow_tasks._timeout_workflow_event_async("run-timeout", "wait-1")

    assert result["status"] == "timed_out"
    state = repo.states["run-timeout"]["state"]
    # The wait step timed out, so its dependent is skipped and the run ends.
    assert state["status"] == "completed"
    assert state["step_results"]["wait-1"]["status"] == "timed_out"
    assert state["step_results"]["after"]["status"] == "skipped"


@pytest.mark.asyncio
async def test_resume_syncs_workflow_run_when_context_present() -> None:
    workflow_tasks = _import_workflow_tasks()
    repo = InMemoryWorkflowStateRepository()
    store = WorkflowStateStore(repository=repo, redis=None)
    store.init = AsyncMock()
    store.close = AsyncMock()
    state = _waiting_state("run-sync", "waiting_delay")
    state["tenant_id"] = "22222222-2222-2222-2222-222222222222"
    state["workflow_run_id"] = "33333333-3333-3333-3333-333333333333"
    await store.save(state)

    sync = AsyncMock()
    with patch.object(workflow_tasks, "_state_store", return_value=store), patch(
        "workflows.engine.execute_step", side_effect=_fake_execute_step
    ), patch("workflows.run_sync.sync_engine_state_to_workflow_run", sync):
        await workflow_tasks._resume_workflow_wait_async("run-sync", "wait-1")

    sync.assert_awaited_once()
    kwargs = sync.await_args.kwargs
    assert str(kwargs["tenant_id"]) == state["tenant_id"]
    assert str(kwargs["workflow_run_id"]) == state["workflow_run_id"]
    assert kwargs["state"]["status"] == "completed"


# ---------------------------------------------------------------------------
# 6. A/B outcome recording
# ---------------------------------------------------------------------------
class _Run:
    def __init__(self, status: str, variant_id: str | None = "44444444-4444-4444-4444-444444444444"):
        self.status = status
        self.context = {"ab": {"variant_id": variant_id}} if variant_id else {}


@pytest.mark.asyncio
async def test_ab_outcome_not_recorded_for_paused_run() -> None:
    from workflows.run_sync import record_ab_outcome_if_terminal

    with patch("core.workflow_ab.record_outcome", new=AsyncMock()) as rec:
        await record_ab_outcome_if_terminal(_Run("waiting_hitl"))
        await record_ab_outcome_if_terminal(_Run("running"))
    rec.assert_not_awaited()


@pytest.mark.asyncio
async def test_ab_outcome_recorded_once_on_terminal() -> None:
    from workflows.run_sync import record_ab_outcome_if_terminal

    run = _Run("completed")
    with patch("core.workflow_ab.record_outcome", new=AsyncMock()) as rec:
        await record_ab_outcome_if_terminal(run)
        await record_ab_outcome_if_terminal(run)  # resume path re-sync
    rec.assert_awaited_once()
    assert rec.await_args.kwargs["success"] is True
    assert run.context["ab"]["outcome_recorded"] is True

    failed = _Run("failed")
    with patch("core.workflow_ab.record_outcome", new=AsyncMock()) as rec:
        await record_ab_outcome_if_terminal(failed)
    assert rec.await_args.kwargs["success"] is False


@pytest.mark.asyncio
async def test_record_outcome_uses_atomic_update() -> None:
    import uuid

    from core import workflow_ab

    session = AsyncMock()

    class _Factory:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return False

    with patch.object(workflow_ab, "async_session_factory", _Factory):
        await workflow_ab.record_outcome(uuid.uuid4(), success=True)

    stmt = session.execute.await_args.args[0]
    sql = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    assert sql.startswith("UPDATE workflow_variants")
    assert "run_count=(workflow_variants.run_count + " in sql
    assert "success_count=(workflow_variants.success_count + " in sql
    assert "failure_count" not in sql


# ---------------------------------------------------------------------------
# 7. Content-safety duplicate window is tenant-scoped
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_duplicate_window_is_scoped_per_tenant() -> None:
    from core.content_safety.checker import _RECENT_HASHES, check_content_safety

    _RECENT_HASHES.clear()
    cfg = {"check_pii": False, "check_toxicity": False}
    text = "Quarterly revenue is up twelve percent year over year."

    first = await check_content_safety(text, {**cfg, "tenant_id": "tenant-a"})
    other_tenant = await check_content_safety(text, {**cfg, "tenant_id": "tenant-b"})
    same_tenant_again = await check_content_safety(text, {**cfg, "tenant_id": "tenant-a"})

    assert first["safe"] is True
    assert other_tenant["safe"] is True, "tenant B must not see tenant A's outputs"
    assert same_tenant_again["safe"] is False
    assert same_tenant_again["scores"]["duplicate"] == 1.0


@pytest.mark.asyncio
async def test_duplicate_check_without_tenant_does_not_dedupe() -> None:
    from core.content_safety.checker import _RECENT_HASHES, check_content_safety

    _RECENT_HASHES.clear()
    cfg = {"check_pii": False, "check_toxicity": False}
    await check_content_safety("same text twice", cfg)
    second = await check_content_safety("same text twice", cfg)
    assert second["safe"] is True
    assert _RECENT_HASHES == {}


# ---------------------------------------------------------------------------
# 8. Gemini cap tenant scoping + no fallback on DailyBudgetExceeded
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_gemini_cap_is_tenant_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.llm import router as r

    monkeypatch.setenv("AGENTICORG_GEMINI_DAILY_USD_CAP", "5.0")
    monkeypatch.setenv("AGENTICORG_GEMINI_PLATFORM_DAILY_USD_CAP", "100.0")
    spend = {"tenant-a": 5.0, "tenant-b": 0.1, None: 5.1}
    calls: list[str | None] = []

    async def _fake_spent(tenant_id=None) -> float:
        calls.append(tenant_id)
        return spend[tenant_id]

    monkeypatch.setattr(r, "_todays_gemini_spend_usd", _fake_spent)
    with pytest.raises(r.DailyBudgetExceeded):
        await r.assert_under_gemini_cap(tenant_id="tenant-a")
    # Tenant B is unaffected by tenant A exhausting its own cap.
    await r.assert_under_gemini_cap(tenant_id="tenant-b")
    assert "tenant-b" in calls and None in calls  # per-tenant + platform-wide checks


@pytest.mark.asyncio
async def test_gemini_platform_cap_still_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.llm import router as r

    monkeypatch.setenv("AGENTICORG_GEMINI_DAILY_USD_CAP", "5.0")
    monkeypatch.setenv("AGENTICORG_GEMINI_PLATFORM_DAILY_USD_CAP", "50.0")

    async def _fake_spent(tenant_id=None) -> float:
        return 0.5 if tenant_id else 50.0

    monkeypatch.setattr(r, "_todays_gemini_spend_usd", _fake_spent)
    with pytest.raises(r.DailyBudgetExceeded, match="platform"):
        await r.assert_under_gemini_cap(tenant_id="tenant-b")


@pytest.mark.asyncio
async def test_spend_query_filters_by_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.database
    from core.llm import router as r

    captured: dict[str, Any] = {}

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, stmt, params):
            captured["sql"] = str(stmt)
            captured["params"] = params
            return type("R", (), {"scalar_one": lambda self: 1.25})()

    monkeypatch.setattr(core.database, "async_session_factory", lambda: _Session())
    tenant = "22222222-2222-2222-2222-222222222222"
    assert await r._todays_gemini_spend_usd(tenant) == 1.25
    assert "tenant_id = :tenant_id" in captured["sql"]
    assert str(captured["params"]["tenant_id"]) == tenant

    await r._todays_gemini_spend_usd()
    assert "tenant_id" not in captured["sql"]


@pytest.mark.asyncio
async def test_no_gemini_fallback_on_daily_budget_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTICORG_TEST_FAKE_LLM", raising=False)
    from core.llm.router import DailyBudgetExceeded, LLMRouter

    router = LLMRouter()
    router.primary_model = "gemini-2.5-flash"
    router.fallback_model = "gemini-2.5-pro"
    called: list[str] = []

    async def _gemini(model, *a, **kw):
        called.append(model)
        raise DailyBudgetExceeded("cap")

    monkeypatch.setattr(router, "_call_gemini", _gemini)
    with pytest.raises(DailyBudgetExceeded):
        await router.complete([{"role": "user", "content": "hi"}], tenant_id="t1")
    assert called == ["gemini-2.5-flash"]


@pytest.mark.asyncio
async def test_router_passes_tenant_id_to_gemini_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTICORG_TEST_FAKE_LLM", raising=False)
    from core.llm import router as r

    seen: dict[str, Any] = {}

    async def _cap(estimated_cost_usd=0.0, tenant_id=None):
        seen["tenant_id"] = tenant_id
        raise RuntimeError("stop before provider call")

    monkeypatch.setattr(r, "assert_under_gemini_cap", _cap)
    monkeypatch.setattr(r.external_keys, "google_gemini_api_key", "k")
    router = r.LLMRouter()
    router.fallback_model = "gemini-2.5-flash"
    with pytest.raises(RuntimeError):
        await router.complete(
            [{"role": "user", "content": "hi"}], model_override="gemini-2.5-flash", tenant_id="t-42"
        )
    assert seen["tenant_id"] == "t-42"


# ---------------------------------------------------------------------------
# 9. routing_filter overlap
# ---------------------------------------------------------------------------
class _Agent:
    def __init__(self, id_: str, routing_filter: dict | None, specialization: str = ""):
        self.id = id_
        self.routing_filter = routing_filter
        self.specialization = specialization


@pytest.mark.asyncio
async def test_routing_filter_requires_overlapping_key() -> None:
    from core.orchestrator.task_router import TaskRouter

    agents = [
        _Agent("generic", None),
        _Agent("apac", {"region": "APAC"}),
        _Agent("west", {"region": "west"}),
    ]

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return agents

    session = AsyncMock()
    session.execute = AsyncMock(return_value=_Result())

    # No overlapping keys: must fall through to the default, not match "apac" vacuously.
    picked = await TaskRouter.resolve_agent_instance(
        "t", "sales", {"vendor_tier": "gold"}, session
    )
    assert picked == "generic"  # first-active fallback, not the vacuous "apac"
    picked = await TaskRouter.resolve_agent_instance("t", "sales", {"region": "west"}, session)
    assert picked == "west"
    picked = await TaskRouter.resolve_agent_instance("t", "sales", {"region": "east"}, session)
    assert picked == "generic"  # fallback, no filter matched
