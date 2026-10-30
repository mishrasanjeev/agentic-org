# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Recorded-spend cost controls (``AGENTICORG_RECORDED_SPEND_CONTROLS_ENABLED``, off by default).

On, the LLM router records each priced call in ``llm_daily_spend``, the Gemini
daily caps read it, and budget alerts read ``agent_cost_ledger``, which agent
runs and chat turns write. Off, nothing is recorded and both read
``agent_task_results`` as before. The Postgres proofs are in
tests/integration/test_llm_daily_spend_postgres.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

TENANT = "11111111-1111-1111-1111-111111111111"
MESSAGES = [{"role": "user", "content": "Summarise the synthetic ledger"}]


class _Session:
    def __init__(self, result=None, error: Exception | None = None):
        self.statements: list = []
        self.result = result
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement, params=None):
        self.statements.append(statement)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(scalar_one=lambda: self.result)


def _sql(statement) -> str:
    return " ".join(str(statement.compile(dialect=postgresql.dialect())).split())


def _controls(monkeypatch, enabled: bool) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "recorded_spend_controls_enabled", enabled)


def _pass_gateway(monkeypatch):
    """The router with the model gateway passed through and the signed record skipped."""
    from core.llm import router

    decision = SimpleNamespace(applied=False, gated=False, tenant_id=TENANT, correlation_id="synthetic")
    monkeypatch.setattr(router, "gateway_decide", AsyncMock(return_value=decision))
    monkeypatch.setattr(router, "gateway_admit", AsyncMock(return_value=None))
    monkeypatch.setattr(router, "gateway_release", AsyncMock())
    monkeypatch.setattr(router, "record_model_call", AsyncMock())
    return router


@pytest.fixture
def router_module(monkeypatch):
    """The router with recorded-spend controls on and the ledger write captured."""
    _controls(monkeypatch, True)
    router = _pass_gateway(monkeypatch)
    monkeypatch.setattr(router, "_record_daily_spend", AsyncMock())
    return router


def _response(router, model: str, cost: float):
    return router.LLMResponse(content="Synthetic summary", model=model, tokens_used=10, cost_usd=cost)


async def test_priced_router_call_is_recorded_for_its_tenant(router_module, monkeypatch):
    llm = router_module.LLMRouter()
    monkeypatch.setattr(llm, "_call_model", AsyncMock(return_value=_response(router_module, "gemini-2.5-flash", 0.6)))

    await llm.complete(MESSAGES, model_override="gemini-2.5-flash", tenant_id=TENANT)

    router_module._record_daily_spend.assert_awaited_once_with(TENANT, "gemini", "gemini-2.5-flash", 0.6)


async def test_fallback_call_is_recorded_under_the_model_that_answered(router_module, monkeypatch):
    llm = router_module.LLMRouter()
    llm.primary_model, llm.fallback_model = "gemini-2.5-pro", "gemini-2.5-flash"
    answered = _response(router_module, "gemini-2.5-flash", 0.2)
    call = AsyncMock(side_effect=[ConnectionError("synthetic outage"), answered])
    monkeypatch.setattr(llm, "_call_model", call)

    await llm.complete(MESSAGES, tenant_id=TENANT)

    router_module._record_daily_spend.assert_awaited_once_with(TENANT, "gemini", "gemini-2.5-flash", 0.2)


async def test_failed_or_tenantless_calls_record_nothing(router_module, monkeypatch):
    llm = router_module.LLMRouter()
    monkeypatch.setattr(llm, "_call_model", AsyncMock(side_effect=ValueError("synthetic refusal")))
    with pytest.raises(ValueError):
        await llm.complete(MESSAGES, model_override="gemini-2.5-flash", tenant_id=TENANT)

    monkeypatch.setattr(llm, "_call_model", AsyncMock(return_value=_response(router_module, "gemini-2.5-flash", 0.6)))
    await llm.complete(MESSAGES, model_override="gemini-2.5-flash")

    router_module._record_daily_spend.assert_not_awaited()


async def test_ledger_write_adds_to_todays_row_in_the_tenant_session(monkeypatch):
    from core.llm import router

    session = _Session()
    entered: list = []

    def _tenant_session(tid, *args, **kwargs):
        entered.append(tid)
        return session

    monkeypatch.setattr("core.database.get_tenant_session", _tenant_session)

    await router._record_daily_spend(TENANT, "gemini", "gemini-2.5-flash", 0.6)

    assert entered == [TENANT]
    sql = _sql(session.statements[0])
    assert sql.startswith("INSERT INTO llm_daily_spend")
    assert "ON CONFLICT ON CONSTRAINT uq_llm_daily_spend_key DO UPDATE" in sql
    assert "call_count = (llm_daily_spend.call_count + %(call_count_1)s" in sql
    assert "cost_usd = (llm_daily_spend.cost_usd + excluded.cost_usd)" in sql
    params = session.statements[0].compile(dialect=postgresql.dialect()).params
    assert params["tenant_id"] == uuid.UUID(TENANT)
    assert params["day"] == datetime.now(UTC).date()
    assert (params["provider"], params["model"], params["cost_usd"]) == ("gemini", "gemini-2.5-flash", 0.6)


@pytest.mark.parametrize("cost", [0, 0.0, -1.0, float("nan"), float("inf"), "not-a-number"])
async def test_costless_or_invalid_costs_write_nothing(monkeypatch, cost):
    from core.llm import router

    monkeypatch.setattr("core.database.get_tenant_session", lambda *a, **k: pytest.fail("nothing to record"))

    await router._record_daily_spend(TENANT, "gemini", "gemini-2.5-flash", cost)


async def test_a_failed_ledger_write_keeps_the_answer(monkeypatch):
    from core.llm import router

    session = _Session(error=OperationalError("INSERT", {}, ConnectionError("synthetic")))
    monkeypatch.setattr("core.database.get_tenant_session", lambda *a, **k: session)

    await router._record_daily_spend(TENANT, "gemini", "gemini-2.5-flash", 0.6)

    assert len(session.statements) == 1


async def test_an_unexpected_ledger_error_never_turns_an_answer_into_a_second_billed_call(monkeypatch):
    _controls(monkeypatch, True)
    router = _pass_gateway(monkeypatch)

    def _broken(*args, **kwargs):
        raise RuntimeError("synthetic session failure")

    monkeypatch.setattr("core.database.get_tenant_session", _broken)
    llm = router.LLMRouter()
    llm.primary_model, llm.fallback_model = "gemini-2.5-pro", "gemini-2.5-flash"
    call = AsyncMock(return_value=_response(router, "gemini-2.5-pro", 0.9))
    monkeypatch.setattr(llm, "_call_model", call)

    response = await llm.complete(MESSAGES, tenant_id=TENANT)

    assert response.cost_usd == 0.9
    call.assert_awaited_once()


async def test_cap_reads_todays_gemini_spend_from_the_ledger(monkeypatch):
    from core.llm import router

    _controls(monkeypatch, True)
    session = _Session(result=1.5)
    monkeypatch.setattr("core.database.get_tenant_session", lambda *a, **k: session)
    platform = _Session(result=2.5)
    monkeypatch.setattr("core.database.async_session_factory", lambda: platform)

    assert await router._todays_gemini_spend_usd(TENANT) == 1.5
    assert await router._todays_gemini_spend_usd() == 2.5
    sql = " ".join(str(session.statements[0]).split())
    assert "FROM llm_daily_spend WHERE day = :day AND provider = 'gemini'" in sql
    assert "agent_task_results" not in sql
    assert [str(s) for s in platform.statements] == ["SELECT public.gemini_daily_ledger_spend()"]


async def test_controls_off_record_nothing_and_keep_the_previous_sources(monkeypatch):
    """Off (the default) behaves as before: no ledger write, both caps and budgets on agent_task_results."""
    from core.billing import budget_evaluator

    _controls(monkeypatch, False)
    router = _pass_gateway(monkeypatch)
    monkeypatch.setattr(router, "_record_daily_spend", AsyncMock())
    llm = router.LLMRouter()
    monkeypatch.setattr(llm, "_call_model", AsyncMock(return_value=_response(router, "gemini-2.5-flash", 0.6)))
    await llm.complete(MESSAGES, model_override="gemini-2.5-flash", tenant_id=TENANT)
    router._record_daily_spend.assert_not_awaited()

    tenant_session = _Session(result=0)
    platform = _Session(result=0)
    monkeypatch.setattr("core.database.get_tenant_session", lambda *a, **k: tenant_session)
    monkeypatch.setattr("core.database.async_session_factory", lambda: platform)
    await router._todays_gemini_spend_usd(TENANT)
    await router._todays_gemini_spend_usd()
    assert "FROM agent_task_results" in str(tenant_session.statements[0])
    assert [str(s) for s in platform.statements] == ["SELECT public.gemini_daily_platform_spend()"]

    budget_session = _Session(result=0)
    monkeypatch.setattr(budget_evaluator, "get_tenant_session", lambda *a, **k: budget_session)
    await budget_evaluator._spend_since(uuid.UUID(TENANT), None, None, datetime(2026, 10, 1, tzinfo=UTC))
    assert "FROM agent_task_results" in str(budget_session.statements[0])


@pytest.mark.parametrize(
    ("company_id", "cost_center_id", "joined"),
    [(None, None, False), (uuid.uuid4(), None, True), (None, uuid.uuid4(), True)],
)
async def test_budget_spend_reads_the_agent_cost_ledger(monkeypatch, company_id, cost_center_id, joined):
    from core.billing import budget_evaluator

    _controls(monkeypatch, True)
    session = _Session(result=None)
    entered: list = []

    def _tenant_session(tid, *args, **kwargs):
        entered.append(tid)
        return session

    monkeypatch.setattr(budget_evaluator, "get_tenant_session", _tenant_session)
    tenant = uuid.UUID(TENANT)
    since = datetime(2026, 10, 1, tzinfo=UTC)

    spend = await budget_evaluator._spend_since(tenant, company_id, cost_center_id, since)

    assert spend == 0
    assert entered == [tenant]
    statement = session.statements[0]
    sql = _sql(statement)
    assert "FROM agent_cost_ledger" in sql
    assert "agent_task_results" not in sql
    assert ("JOIN agents ON agents.id = agent_cost_ledger.agent_id" in sql) is joined
    params = statement.compile(dialect=postgresql.dialect()).params
    # The ledger keeps naive UTC midnights.
    assert params["period_date_1"] == since.replace(tzinfo=None)
    assert (params.get("company_id_1"), params.get("cost_center_id_1")) == (company_id, cost_center_id)
