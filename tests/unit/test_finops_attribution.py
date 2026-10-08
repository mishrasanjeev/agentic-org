# SPDX-License-Identifier: Apache-2.0
"""Use-case attribution: every cost carries a business unit, department, application and use case."""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.v1 import finops as api
from core.config import settings
from core.finops import attribution

ROOT = Path(__file__).resolve().parents[2]
COST_CENTER = uuid.uuid4()
DEPARTMENT = uuid.uuid4()


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _Session:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return _Result(self.answers.pop(0) if self.answers else [])


def _agent(**over):
    base = {
        "id": uuid.uuid4(),
        "agent_type": "Collections Agent",
        "domain": "finance",
        "cost_center_id": COST_CENTER,
        "config": {},
    }
    base.update(over)
    return SimpleNamespace(**base)


class TestLabelsAndContext:
    def test_a_label_is_a_bounded_lower_cased_identifier(self):
        assert attribution.label(" Loan Origination ") == "loan-origination"
        assert attribution.label("KYC/Onboarding v2") == "kyc-onboarding-v2"
        assert attribution.label(None) == "" and attribution.label("x" * 100) == "x" * attribution.MAX_LABEL

    def test_the_attribution_is_bound_for_the_run_and_reset_after(self):
        assert attribution.current() is None
        token = attribution.bind(attribution.Attribution(use_case="collections", application="chat"))
        assert attribution.current().use_case == "collections"
        attribution.reset(token)
        assert attribution.current() is None

    def test_off_by_default(self):
        assert settings.finops_attribution_enabled is False and attribution.enabled() is False


class TestResolve:
    @pytest.mark.asyncio
    async def test_the_callers_labels_come_first_then_the_agents_configuration_then_its_type_and_domain(self):
        session = _Session([DEPARTMENT])
        agent = _agent()
        given = await attribution.resolve_for_agent(
            session, agent, use_case="Loan Origination", application="chat", business_unit="Retail"
        )
        assert given == attribution.Attribution(
            use_case="loan-origination",
            application="chat",
            business_unit="retail",
            department_id=str(DEPARTMENT),
            cost_center_id=str(COST_CENTER),
            agent_id=str(agent.id),
        )
        assert "cost_centers" in session.calls[0][0]
        configured = await attribution.resolve_for_agent(
            _Session([None]), _agent(config={"use_case": "kyc", "business_unit": "ops", "application": "voice"})
        )
        assert (configured.use_case, configured.business_unit, configured.application) == ("kyc", "ops", "voice")
        fallback = await attribution.resolve_for_agent(None, _agent(cost_center_id=None))
        assert fallback.use_case == "collections-agent" and fallback.business_unit == "finance"
        assert fallback.application == "agents" and fallback.department_id is None and fallback.cost_center_id is None
        bare = await attribution.resolve_for_agent(None, _agent(agent_type="", domain="", cost_center_id=None))
        assert bare.use_case == "unattributed" and bare.business_unit == ""


@pytest.mark.asyncio
async def test_ledger_add_upserts_the_days_row_for_the_attribution():
    tid = uuid.uuid4()
    session = _Session()
    given = attribution.Attribution(use_case="kyc", application="chat", business_unit="retail", agent_id="a1")
    await attribution.ledger_add(session, tid, given, tokens=120, cost_usd=0.0042, period_date=date(2026, 10, 7))
    sql, params = session.calls[0]
    assert "INSERT INTO finops_cost_ledger" in sql
    assert (
        "ON CONFLICT (tenant_id, period_date, COALESCE(agent_id::text, ''), use_case, application, business_unit, "
        " COALESCE(department_id::text, ''), COALESCE(cost_center_id::text, ''))" in sql
    )
    assert "tokens = finops_cost_ledger.tokens + EXCLUDED.tokens" in sql
    assert params == {
        "tid": str(tid),
        "day": date(2026, 10, 7),
        "agent_id": "a1",
        "use_case": "kyc",
        "application": "chat",
        "business_unit": "retail",
        "department_id": None,
        "cost_center_id": None,
        "tokens": 120,
        "cost_usd": 0.0042,
        "calls": 1,
    }
    await attribution.ledger_add(_Session(), tid, attribution.Attribution(), tokens=-5, cost_usd=-1)
    assert True


@pytest.mark.asyncio
async def test_summary_folds_by_a_dimension_with_totals_and_the_unattributed_share():
    tid = uuid.uuid4()
    session = _Session([("kyc", 1000, 0.5, 10, 2), ("unattributed", 200, 0.25, 4, 1)], [(1200, 0.75, 14, 0.25)])
    folded = await attribution.summary(session, tid, days=7, group_by="use_case")
    assert folded["rows"] == [
        {"use_case": "kyc", "tokens": 1000, "cost_usd": 0.5, "calls": 10, "agents": 2},
        {"use_case": "unattributed", "tokens": 200, "cost_usd": 0.25, "calls": 4, "agents": 1},
    ]
    assert folded["totals"] == {"tokens": 1200, "cost_usd": 0.75, "calls": 14, "unattributed_share": 0.3333}
    assert folded["days"] == 7 and folded["group_by"] == "use_case"
    sql, params = session.calls[0]
    assert sql.startswith("SELECT use_case, SUM(tokens)") and "GROUP BY use_case" in sql and params["tid"] == str(tid)
    with pytest.raises(ValueError):
        await attribution.summary(_Session(), tid, group_by="name; DROP TABLE")
    empty = await attribution.summary(
        _Session([], [(None, None, None, None)]), tid, days=5000, group_by="business_unit"
    )
    assert empty["days"] == attribution.MAX_DAYS and empty["totals"]["unattributed_share"] == 0.0


class TestEndpoint:
    @pytest.mark.asyncio
    async def test_off_not_found_and_on_folded(self, monkeypatch):
        tid = str(uuid.uuid4())
        with pytest.raises(HTTPException) as refused:
            await api.cost_attribution(days=30, group_by="use_case", tenant_id=tid)
        assert refused.value.status_code == 404 and refused.value.detail["error"] == "finops_attribution_disabled"
        monkeypatch.setattr(settings, "finops_attribution_enabled", True)
        with pytest.raises(HTTPException) as refused:
            await api.cost_attribution(days=30, group_by="owner", tenant_id=tid)
        assert refused.value.status_code == 422
        session = _Session([("kyc", 10, 0.1, 1, 1)], [(10, 0.1, 1, 0)])
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: session)
        folded = await api.cost_attribution(days=30, group_by="use_case", tenant_id=tid)
        assert folded["rows"][0]["use_case"] == "kyc" and folded["totals"]["cost_usd"] == 0.1


class TestHooks:
    def test_the_run_binds_its_attribution_and_the_cost_write_adds_the_ledger_row(self):
        src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        run = src[src.index('@router.post("/agents/{agent_id}/run")') :]
        run = run[: run.index("_record_cost_ledger(tid, agent_id, perf)")]
        assert "cost_attribution.resolve_for_agent(" in run and "cost_attribution.bind(" in run
        assert "cost_attribution.reset(" in src
        write = src[src.index("async def _record_cost_ledger(") :]
        write = write[: write.index("\nasync def ", 10)]
        assert "cost_attribution.enabled()" in write and "cost_attribution.ledger_add(" in write

    def test_records_and_tool_calls_carry_the_attribution(self):
        records = (ROOT / "core" / "governance" / "model_gateway_records.py").read_text(encoding="utf-8")
        assert "business_unit: str | None = None" in records and "application: str | None = None" in records
        assert "cost_attribution.current()" in records
        model = (ROOT / "core" / "models" / "model_gateway_record.py").read_text(encoding="utf-8")
        assert "business_unit" in model and "application" in model
        tool = (ROOT / "core" / "models" / "tool_call.py").read_text(encoding="utf-8")
        assert "use_case" in tool and "application" in tool

    def test_the_router_and_the_migration_are_shaped(self):
        main = (ROOT / "api" / "main.py").read_text(encoding="utf-8")
        assert "finops," in main and "app.include_router(finops.router" in main
        migration = (ROOT / "migrations" / "versions" / "v6_z55_finops_attribution.py").read_text(encoding="utf-8")
        assert 'down_revision = "v6z54_model_cards"' in migration
        assert "ux_finops_cost_ledger_attribution_key" in migration
        assert "finops_cost_ledger_tenant_isolation" in migration
        from core.models.finops_ledger import FinopsCostLedger

        assert FinopsCostLedger.__tablename__ == "finops_cost_ledger"


def _migration_statements():
    import importlib.util
    from unittest.mock import patch

    path = ROOT / "migrations" / "versions" / "v6_z55_finops_attribution.py"
    spec = importlib.util.spec_from_file_location("v6z55_finops_attribution", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    up: list[str] = []
    with patch.object(mig.op, "execute", side_effect=up.append):
        mig.upgrade()
    down: list[str] = []
    with patch.object(mig.op, "execute", side_effect=down.append):
        mig.downgrade()
    return mig, up, down


class TestMigration:
    def test_the_ledger_policy_is_forced_so_the_owning_role_is_bound(self):
        mig, up, _ = _migration_statements()
        assert len(mig.revision) <= 32
        enable = up.index("ALTER TABLE finops_cost_ledger ENABLE ROW LEVEL SECURITY;")
        force = up.index("ALTER TABLE finops_cost_ledger FORCE ROW LEVEL SECURITY;")
        policy = next(i for i, sql in enumerate(up) if "CREATE POLICY finops_cost_ledger_tenant_isolation" in sql)
        assert enable < force < policy

    def test_the_unique_key_covers_department_and_cost_centre_and_matches_the_upsert(self):
        _, up, _ = _migration_statements()
        key = next(sql for sql in up if "ux_finops_cost_ledger_attribution_key" in sql)
        assert key.startswith("CREATE UNIQUE INDEX IF NOT EXISTS")
        expressions = key[key.index("(") + 1 : key.rindex(")")]
        assert "COALESCE(department_id::text, '')" in expressions
        assert "COALESCE(cost_center_id::text, '')" in expressions
        # The upsert's conflict target names the same expressions, in the same order.
        source = (ROOT / "core" / "finops" / "attribution.py").read_text(encoding="utf-8")
        target = "".join(
            line.strip().strip('"') for line in source.splitlines() if "ON CONFLICT" in line or "COALESCE(dep" in line
        )
        assert " ".join(expressions.split()) in " ".join(target.split())
        # A database that ran the narrower draft key loses it, after the new key exists.
        drop = up.index("DROP INDEX IF EXISTS ux_finops_cost_ledger_key;")
        assert up.index(key) < drop

    def test_legacy_table_alters_are_guarded_on_the_table_existing_both_ways(self):
        _, up, down = _migration_statements()
        for statements, verb in ((up, "ADD COLUMN IF NOT EXISTS"), (down, "DROP COLUMN IF EXISTS")):
            legacy = [sql for sql in statements if "model_gateway_records" in sql or "tool_calls" in sql]
            assert len(legacy) == 4
            for sql in legacy:
                table = "model_gateway_records" if "model_gateway_records" in sql else "tool_calls"
                assert sql.startswith(f"DO $$ BEGIN IF to_regclass('public.{table}') IS NOT NULL THEN ALTER TABLE")
                assert verb in sql and sql.endswith("END IF; END $$;")
        joined = " ".join(up)
        assert "model_gateway_records ADD COLUMN IF NOT EXISTS business_unit VARCHAR(64) NULL" in joined
        assert "model_gateway_records ADD COLUMN IF NOT EXISTS application VARCHAR(64) NULL" in joined
        assert "tool_calls ADD COLUMN IF NOT EXISTS use_case VARCHAR(64) NULL" in joined
        assert "tool_calls ADD COLUMN IF NOT EXISTS application VARCHAR(64) NULL" in joined
        # The ledger table is dropped last on the way down; nothing else is unguarded.
        assert down[-1] == "DROP TABLE IF EXISTS finops_cost_ledger;"
