"""Core audit 2026-09-13 — RLS-blind sessions (findings 2, 3), BYOK KEK
fallback (6) and the Q4 TDS due date (9).

Tenant-scoped tables are FORCE ROW LEVEL SECURITY (v6z16). A raw
``async_session_factory()`` session has no tenant GUC: SELECT returns zero
rows silently and INSERT/UPDATE fail WITH CHECK under a non-BYPASSRLS
role. Every per-tenant path must enter ``get_tenant_session(tenant_id)``;
cross-tenant beat jobs must enumerate tenants (failing loudly when the
role cannot bypass RLS) and then work per tenant.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

TENANT = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER = uuid.UUID("22222222-2222-2222-2222-222222222222")


class _Result:
    def __init__(self, rows=None, scalar=None):
        self.rows = rows or []
        self._scalar = scalar

    def all(self):
        return self.rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self.rows)

    def scalar_one(self):
        return self._scalar if self._scalar is not None else (self.rows[0][0] if self.rows else 0)

    def scalar_one_or_none(self):
        if self._scalar is not None:
            return self._scalar
        return self.rows[0] if self.rows else None

    def first(self):
        return self.rows[0] if self.rows else None


class _Session:
    """Async-context session returning canned results keyed by SQL substring."""

    def __init__(self, responses: dict[str, _Result] | None = None):
        self.responses = responses or {}
        self.executed: list[str] = []
        self.added: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append(sql)
        for key, result in self.responses.items():
            if key in sql:
                return result
        return _Result()

    async def commit(self):
        return None

    def add(self, obj):
        self.added.append(obj)


def _tenant_session_recorder(session: _Session):
    entered: list = []

    def _factory(tid, *args, **kwargs):
        entered.append(tid)
        return session

    return _factory, entered


# ── Finding 2: Gemini daily-cap spend lookup ────────────────────────


@pytest.mark.asyncio
async def test_gemini_tenant_spend_is_read_inside_tenant_session(monkeypatch):
    from core.llm import router

    session = _Session({"SUM(cost_usd)": _Result(scalar=4.5)})
    factory, entered = _tenant_session_recorder(session)
    monkeypatch.setattr("core.database.get_tenant_session", factory)
    monkeypatch.setattr("core.database.async_session_factory", lambda: pytest.fail("raw session used"))

    spent = await router._todays_gemini_spend_usd(str(TENANT))

    assert spent == 4.5
    assert entered == [str(TENANT)]


@pytest.mark.asyncio
async def test_gemini_spend_lookup_fails_closed_on_unbindable_tenant(monkeypatch):
    from core.llm import router

    def _reject(tid, *a, **k):
        raise ValueError("Invalid tenant_id format")

    monkeypatch.setattr("core.database.get_tenant_session", _reject)

    with pytest.raises(router.DailyBudgetExceeded):
        await router._todays_gemini_spend_usd("not-a-tenant")


@pytest.mark.asyncio
async def test_gemini_platform_spend_disables_row_security_loudly(monkeypatch):
    from core.llm import router

    session = _Session({"SUM(cost_usd)": _Result(scalar=1.0)})
    monkeypatch.setattr("core.database.async_session_factory", lambda: session)

    assert await router._todays_gemini_spend_usd() == 1.0
    assert session.executed[0].startswith("SET LOCAL row_security = off")


# ── Finding 3: invoice generator ────────────────────────────────────


@pytest.mark.asyncio
async def test_invoice_idempotency_check_runs_in_tenant_session(monkeypatch):
    from core.billing import invoice_generator as ig

    tenant = SimpleNamespace(id=TENANT, name="Acme", plan="pro", data_region="IN", deleted_at=None)
    enumeration = _Session({"FROM tenants": _Result(rows=[tenant])})
    monkeypatch.setattr(ig, "async_session_factory", lambda: enumeration)

    existing_invoice = SimpleNamespace(id=uuid.uuid4())
    tenant_session = _Session({"FROM invoices": _Result(scalar=existing_invoice)})
    factory, entered = _tenant_session_recorder(tenant_session)
    monkeypatch.setattr(ig, "get_tenant_session", factory)

    result = await ig.generate_invoices_for_period(ref=datetime(2026, 9, 1, 1, 0, tzinfo=UTC))

    assert result["skipped"] == 1 and result["created"] == 0
    assert entered == [TENANT]
    assert enumeration.executed[0].startswith("SET LOCAL row_security = off")


def test_invoice_generator_never_writes_invoices_in_raw_session():
    from core.billing import invoice_generator as ig

    src = inspect.getsource(ig.generate_invoices_for_period)
    assert "async with get_tenant_session(tenant.id) as check_session" in src
    assert "async with get_tenant_session(tenant.id) as write_session" in src
    assert "async_session_factory() as check_session" not in src
    assert "async_session_factory() as write_session" not in src


# ── Finding 3: budget evaluator ─────────────────────────────────────


@pytest.mark.asyncio
async def test_budget_alerts_are_read_and_written_per_tenant(monkeypatch):
    from core.billing import budget_evaluator as be

    enumeration = _Session({"FROM tenants": _Result(rows=[(TENANT,), (OTHER,)])})
    monkeypatch.setattr(be, "async_session_factory", lambda: enumeration)

    now = datetime.now(be.UTC)
    alert = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        company_id=None,
        cost_center_id=None,
        name="ops",
        period="monthly",
        threshold_usd=be.Decimal("100"),
        warn_at_percent=80,
        notify_channels="",
        last_triggered_at=None,
    )
    sessions: dict = {
        TENANT: _Session({"FROM budget_alerts": _Result(rows=[alert], scalar=alert)}),
        OTHER: _Session(),
    }
    entered: list = []

    def _factory(tid, *a, **k):
        entered.append(tid)
        return sessions[tid]

    monkeypatch.setattr(be, "get_tenant_session", _factory)
    monkeypatch.setattr(be, "_spend_since", AsyncMock(return_value=be.Decimal("90")))
    monkeypatch.setattr(be, "_send_notification", AsyncMock())

    summary = await be.evaluate_budget_alerts()

    assert summary == {"checked": 1, "triggered": 1, "ts": summary["ts"]}
    assert enumeration.executed[0].startswith("SET LOCAL row_security = off")
    # Read for both tenants, trigger persisted inside the alert's tenant session.
    assert entered[:2] == [TENANT, OTHER]
    assert entered.count(TENANT) == 2
    assert alert.last_triggered_at is not None and alert.last_triggered_at >= now


# ── Finding 3: compliance cron ──────────────────────────────────────


@pytest.mark.asyncio
async def test_compliance_cron_works_per_tenant_under_advisory_lock(monkeypatch):
    from core.cron import compliance_alerts as ca

    lock_session = _Session(
        {
            "pg_try_advisory_xact_lock": _Result(scalar=True),
            "FROM tenants": _Result(rows=[(TENANT,), (OTHER,)]),
        }
    )
    monkeypatch.setattr(ca, "async_session_factory", lambda: lock_session)

    company = SimpleNamespace(id=uuid.uuid4(), tenant_id=TENANT, is_active=True)
    sessions = {
        TENANT: _Session({"FROM companies": _Result(rows=[company])}),
        OTHER: _Session(),
    }
    entered: list = []

    def _factory(tid, *a, **k):
        entered.append(tid)
        return sessions[tid]

    monkeypatch.setattr(ca, "get_tenant_session", _factory)

    generated: list = []

    async def _generate(session, comp):
        generated.append((session, comp))
        return 3

    alerted: list = []

    async def _alerts(session, today=None):
        alerted.append(session)
        return {"alerts_7d": 1, "alerts_1d": 0, "overdue": 2, "skipped_no_recipient": 0, "failed": 0}

    monkeypatch.setattr(ca, "generate_deadlines_for_company", _generate)
    monkeypatch.setattr(ca, "send_alerts_for_due_deadlines", _alerts)

    result = await ca.run_compliance_alert_cron()

    assert entered == [TENANT, OTHER]
    assert generated == [(sessions[TENANT], company)]
    assert alerted == [sessions[TENANT], sessions[OTHER]]
    assert result["new_deadlines"] == 3
    assert result["alerts_7d"] == 2 and result["overdue"] == 4
    assert any(sql.startswith("SET LOCAL row_security = off") for sql in lock_session.executed)
    assert "tenant_id" in sessions[TENANT].executed[0]


@pytest.mark.asyncio
async def test_compliance_cron_still_skips_when_locked(monkeypatch):
    from core.cron import compliance_alerts as ca

    monkeypatch.setattr(
        ca, "async_session_factory", lambda: _Session({"pg_try_advisory_xact_lock": _Result(scalar=False)})
    )
    monkeypatch.setattr(ca, "get_tenant_session", lambda *a, **k: pytest.fail("must not run"))

    assert (await ca.run_compliance_alert_cron())["skipped"] == "locked"


# ── Finding 3: workflow A/B ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_pick_variant_enters_tenant_session(monkeypatch):
    from core import workflow_ab

    session = _Session()
    factory, entered = _tenant_session_recorder(session)
    monkeypatch.setattr(workflow_ab, "get_tenant_session", factory)
    monkeypatch.setattr(workflow_ab, "async_session_factory", lambda: pytest.fail("raw session used"))

    assert await workflow_ab.pick_variant(uuid.uuid4(), "user-1", tenant_id=str(TENANT)) is None
    assert entered == [TENANT]
    assert "workflow_variants.tenant_id" in session.executed[0]


@pytest.mark.asyncio
async def test_record_outcome_enters_tenant_session(monkeypatch):
    from core import workflow_ab

    session = _Session()
    factory, entered = _tenant_session_recorder(session)
    monkeypatch.setattr(workflow_ab, "get_tenant_session", factory)
    monkeypatch.setattr(workflow_ab, "async_session_factory", lambda: pytest.fail("raw session used"))

    await workflow_ab.record_outcome(uuid.uuid4(), success=True, tenant_id=TENANT)
    assert entered == [TENANT]
    assert session.executed[0].startswith("UPDATE workflow_variants")
    assert "workflow_variants.tenant_id" in session.executed[0]


# ── Finding 3: approval policy engine ───────────────────────────────


@pytest.mark.asyncio
async def test_policy_engine_binds_tenant_context(monkeypatch):
    from core.approvals import policy_engine
    from core.models.approval_policy import ApprovalPolicy

    session = _Session()
    factory, entered = _tenant_session_recorder(session)
    monkeypatch.setattr(policy_engine, "get_tenant_session", factory)

    assert await policy_engine.resolve_policy(tenant_id=TENANT, policy_name="x") is None
    policy = MagicMock(spec=ApprovalPolicy)
    policy.id = uuid.uuid4()
    policy.tenant_id = OTHER
    assert await policy_engine.first_applicable_step(policy, {}) is None
    assert await policy_engine.next_step_after(policy, 1, {}) is None

    assert entered == [TENANT, OTHER, OTHER]
    assert "async_session_factory" not in inspect.getsource(policy_engine)


# ── Finding 3: KPI cache ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_kpi_cache_pg_helpers_bind_tenant_context(monkeypatch):
    from core import kpi_cache as kc

    session = _Session()
    factory, entered = _tenant_session_recorder(session)
    monkeypatch.setattr(kc, "get_tenant_session", factory)

    cache = kc.KPICache()
    tid = str(TENANT)
    assert await cache._pg_get(tid, "cfo", "revenue") is None
    assert await cache._pg_get_all_for_role(tid, "cfo") == {}
    await cache._pg_upsert(tid, "cfo", "revenue", {"v": 1}, 60, "agent")
    await cache._pg_mark_stale(tid, "cfo", None)
    assert await cache._pg_is_stale(tid, "cfo", "revenue") is True

    assert entered == [tid] * 5
    assert "async_session_factory" not in inspect.getsource(kc)


# ── Finding 6: BYOK KEK lookup must not silently fall back ──────────


def _kek_session(scalar):
    session = MagicMock()
    session.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: scalar))
    return session


class _Ctx:
    def __init__(self, session=None, error: Exception | None = None):
        self.session = session
        self.error = error

    async def __aenter__(self):
        if self.error is not None:
            raise self.error
        return self.session

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_kek_lookup_db_error_raises_instead_of_platform_kek(monkeypatch):
    from core.crypto import tenant_secrets

    monkeypatch.setenv("AGENTICORG_PLATFORM_KEK", "projects/platform/kek")
    with patch.object(
        tenant_secrets.database, "async_session_factory", lambda: _Ctx(error=SQLAlchemyError("down"))
    ):
        with pytest.raises(RuntimeError, match="KEK lookup failed"):
            await tenant_secrets._resolve_kek(TENANT)


@pytest.mark.asyncio
async def test_kek_lookup_missing_tenant_raises(monkeypatch):
    from core.crypto import tenant_secrets

    monkeypatch.setenv("AGENTICORG_PLATFORM_KEK", "projects/platform/kek")
    with patch.object(tenant_secrets.database, "async_session_factory", lambda: _Ctx(_kek_session(None))):
        with pytest.raises(LookupError):
            await tenant_secrets._resolve_kek(TENANT)


@pytest.mark.asyncio
async def test_kek_platform_only_when_row_exists_without_byok(monkeypatch):
    from core.crypto import tenant_secrets

    monkeypatch.setenv("AGENTICORG_PLATFORM_KEK", "projects/platform/kek")
    with patch.object(tenant_secrets.database, "async_session_factory", lambda: _Ctx(_kek_session(""))):
        assert await tenant_secrets._resolve_kek(TENANT) == "projects/platform/kek"
    with patch.object(
        tenant_secrets.database, "async_session_factory", lambda: _Ctx(_kek_session("projects/cust/kek"))
    ):
        assert await tenant_secrets._resolve_kek(TENANT) == "projects/cust/kek"


# ── Finding 9: Q4 TDS 24Q/26Q due 31 May ────────────────────────────


@pytest.mark.parametrize("dtype", ["tds_24q", "tds_26q"])
def test_q4_tds_due_31_may(dtype):
    from core.cron.compliance_alerts import _compute_quarterly_deadlines

    deadlines = _compute_quarterly_deadlines("c1", "t1", date(2026, 5, 1))
    by_period = {d["filing_period"]: d["due_date"] for d in deadlines if d["deadline_type"] == dtype}
    assert by_period["2026-Q4"] == date(2027, 5, 31)
    # Q1-Q3 keep the "31st of the month after quarter end" rule.
    assert by_period["2026-Q1"] == date(2026, 7, 31)
    assert by_period["2026-Q2"] == date(2026, 10, 31)
    assert by_period["2026-Q3"] == date(2027, 1, 31)
