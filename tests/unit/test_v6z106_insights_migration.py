# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""v6z106: run_facts (monthly partitions), insights_daily and insights_rollup_state.

The migration's DDL must match the ORM models (an empty database is built from
the models, an existing one from the migration), every table and partition must
carry a forced tenant policy, and every statement must be safe to run twice.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from core.config import Settings
from core.models import InsightsDaily, InsightsRollupState, RunFact
from core.models import insights as insights_models

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z106_insights_foundation.py"
MODELS = (RunFact, InsightsDaily, InsightsRollupState)


def _migration():
    spec = importlib.util.spec_from_file_location("_v6_z106_insights_foundation", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Op:
    def __init__(self) -> None:
        self.sql: list[str] = []

    def execute(self, sql) -> None:
        self.sql.append(" ".join(str(sql).split()))


def _strip_ws(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _run(fn_name: str, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    migration = _migration()
    op = _Op()
    monkeypatch.setattr(migration, "op", op)
    getattr(migration, fn_name)()
    return op.sql


def test_revision_chain() -> None:
    migration = _migration()
    assert migration.revision == "v6z106_insights_foundation" and len(migration.revision) <= 32
    assert migration.down_revision == "v6z105_llm_daily_spend"
    assert migration.TABLES == tuple(model.__tablename__ for model in MODELS)


def test_only_one_head_follows_v6z105() -> None:
    children = []
    for path in MIGRATION.parent.glob("*.py"):
        if re.search(r'^down_revision = "v6z105_llm_daily_spend"$', path.read_text(encoding="utf-8"), re.M):
            children.append(path.name)
    assert children == [MIGRATION.name]


def test_partitions_cover_october_2026_to_december_2028_plus_default(monkeypatch) -> None:
    migration = _migration()
    assert migration._months() == list(insights_models.RUN_FACTS_STATIC_MONTHS)
    assert len(insights_models.RUN_FACTS_STATIC_MONTHS) == 27
    assert insights_models.RUN_FACTS_STATIC_MONTHS[0] == (2026, 10)
    assert insights_models.RUN_FACTS_STATIC_MONTHS[-1] == (2028, 12)
    assert insights_models.RUN_FACTS_STATIC_PARTITIONS[0] == "run_facts_y2026m10"
    assert insights_models.RUN_FACTS_STATIC_PARTITIONS[-1] == "run_facts_default"
    assert migration.DEFAULT_PARTITION == insights_models.RUN_FACTS_DEFAULT_PARTITION

    sql = _run("upgrade", monkeypatch)
    created = [s for s in sql if "PARTITION OF run_facts" in s]
    assert len(created) == 28
    assert created[0] == (
        "CREATE TABLE IF NOT EXISTS run_facts_y2026m10 PARTITION OF run_facts "
        "FOR VALUES FROM ('2026-10-01 00:00:00+00') TO ('2026-11-01 00:00:00+00');"
    )
    # The December partition ends at the next year's January.
    assert any(
        s.startswith("CREATE TABLE IF NOT EXISTS run_facts_y2027m12 ") and "TO ('2028-01-01 00:00:00+00')" in s
        for s in created
    )
    assert created[-1] == "CREATE TABLE IF NOT EXISTS run_facts_default PARTITION OF run_facts DEFAULT;"
    assert [s.split()[5] for s in created] == list(insights_models.RUN_FACTS_STATIC_PARTITIONS)
    assert "PARTITION BY RANGE (started_at)" in " ".join(sql)


def test_every_table_and_partition_has_a_forced_tenant_policy(monkeypatch) -> None:
    sql = _run("upgrade", monkeypatch)
    for name in (*insights_models.RUN_FACTS_STATIC_PARTITIONS, *_migration().TABLES):
        assert f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY;" in sql, name
        assert f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY;" in sql, name
        assert f"DROP POLICY IF EXISTS {name}_tenant_isolation ON {name};" in sql, name
        policy = next(s for s in sql if s.startswith(f"CREATE POLICY {name}_tenant_isolation "))
        assert "USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))" in policy
        assert "WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true))" in policy
    # No statement alters an existing table other than enabling its policy.
    assert not any(s.startswith("ALTER TABLE") and "ROW LEVEL" not in s for s in sql)


def test_every_statement_is_idempotent(monkeypatch) -> None:
    sql = _run("upgrade", monkeypatch)
    for statement in sql:
        if statement.startswith("CREATE TABLE"):
            assert statement.startswith("CREATE TABLE IF NOT EXISTS "), statement
        elif statement.startswith(("CREATE INDEX", "CREATE UNIQUE INDEX")):
            assert " INDEX IF NOT EXISTS " in statement, statement
        elif statement.startswith("CREATE POLICY"):
            name = statement.split()[2]
            assert sql.index(statement) > 0 and sql[sql.index(statement) - 1].startswith(
                f"DROP POLICY IF EXISTS {name} "
            )
        elif statement.startswith("DROP"):
            assert " IF EXISTS " in statement, statement
        else:
            assert statement.startswith("ALTER TABLE") and "ROW LEVEL SECURITY" in statement, statement


def test_downgrade_drops_the_three_tables_in_reverse(monkeypatch) -> None:
    sql = _run("downgrade", monkeypatch)
    assert sql == [f"DROP TABLE IF EXISTS {t};" for t in reversed(_migration().TABLES)]


def test_tenant_foreign_keys_are_covered_by_a_leading_index() -> None:
    for model in MODELS:
        table = model.__table__
        leading = [tuple(c.name for c in index.columns) for index in table.indexes]
        fks = list(table.foreign_key_constraints)
        assert [tuple(c.name for c in fk.columns) for fk in fks] == [("tenant_id",)], table.name
        assert fks[0].referred_table.name == "tenants"
        assert any(cols[:1] == ("tenant_id",) for cols in leading), table.name


def test_models_compile_to_the_migration_ddl() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    ddl = ""
    for model in MODELS:
        ddl += str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
        for index in model.__table__.indexes:
            ddl += str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    flat_ddl = _strip_ws(ddl)
    names = set(re.findall(r"CONSTRAINT (\w+)", sql)) | set(re.findall(r"INDEX IF NOT EXISTS (\w+)", sql))
    assert names == {
        "pk_run_facts",
        "ck_run_facts_status",
        "ck_run_facts_entrypoint",
        "ck_run_facts_data_quality",
        "ck_run_facts_environment",
        "ux_run_facts_tenant_run_key",
        "ix_run_facts_tenant_started",
        "ix_run_facts_tenant_agent_started",
        "ck_insights_daily_latency_buckets",
        "ux_insights_daily_tenant_day_dims",
        "ix_insights_daily_tenant_day",
        "ck_insights_rollup_state_status",
        "ux_insights_rollup_state_tenant_day",
    }
    for name in names:
        assert name in ddl, name
    for match in re.finditer(r"(?<!WITH )CHECK \(", sql):
        depth, start, index = 1, match.end(), match.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(sql[index], 0)
            index += 1
        body = _strip_ws(sql[start : index - 1])
        assert f"CHECK({body})" in flat_ddl, body
    # Index column lists, uniqueness included, are the same on both sides.
    for model in MODELS:
        for index in model.__table__.indexes:
            columns = ", ".join(c.name for c in index.columns)
            kind = "UNIQUE INDEX" if index.unique else "INDEX"
            assert f"CREATE {kind} IF NOT EXISTS {index.name} ON {model.__tablename__}({columns});" in " ".join(
                sql.split()
            ).replace('" "', ""), index.name
    for model in MODELS:
        table_sql = sql[sql.index(f"CREATE TABLE IF NOT EXISTS {model.__tablename__} (") :]
        table_sql = table_sql[: table_sql.index(");")]
        declared = set(re.findall(r"^\s{12}([a-z_]+) [A-Z]", table_sql, re.M))
        assert declared == {c.name for c in model.__table__.columns}, model.__tablename__
        for column in model.__table__.columns:
            line = re.search(rf"^\s{{12}}{column.name} (.+?),?$", table_sql, re.M)
            assert line is not None, (model.__tablename__, column.name)
            text = line.group(1)
            is_pk_only = column.primary_key and "PRIMARY KEY" not in text
            if "NOT NULL" in text or "PRIMARY KEY" in text or is_pk_only:
                assert not column.nullable, (model.__tablename__, column.name)
            else:
                assert column.nullable, (model.__tablename__, column.name)
            assert ("DEFAULT" in text) == (column.server_default is not None), (model.__tablename__, column.name)
    flat_sql = _strip_ws(sql)
    assert "PARTITIONBYRANGE(started_at)" in flat_sql
    assert RunFact.__table__.dialect_options["postgresql"]["partition_by"] == "RANGE (started_at)"
    assert {c.name for c in RunFact.__table__.primary_key.columns} == {"id", "started_at"}


def test_column_types_and_sizes_match_the_spec() -> None:
    rf = RunFact.__table__.c
    assert rf.run_key.type.length == 160 and rf.entrypoint.type.length == 24
    assert rf.agent_ref.type.length == 128 and rf.correlation_id.type.length == 160
    assert rf.error_class.type.length == 100 and rf.data_quality.type.length == 12
    assert (rf.cost_usd.type.precision, rf.cost_usd.type.scale) == (14, 6)
    daily = InsightsDaily.__table__.c
    assert daily.dims_hash.type.length == 64
    assert (daily.cost_usd.type.precision, daily.cost_usd.type.scale) == (16, 6)
    assert str(daily.latency_buckets.type.compile(dialect=postgresql.dialect())) == "INTEGER[]"
    state = InsightsRollupState.__table__.c
    assert state.detail.type.length == 200 and state.status.type.length == 16


def test_check_values_match_the_model_constants() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    flat = _strip_ws(source.replace("'\n", "'"))
    for values in (
        insights_models.RUN_FACT_STATUSES,
        insights_models.RUN_FACT_ENTRYPOINTS,
        insights_models.RUN_FACT_ENVIRONMENTS,
        insights_models.DATA_QUALITY_VALUES,
        insights_models.ROLLUP_STATE_STATUSES,
    ):
        assert "IN(" + ",".join(f"'{v}'" for v in values) + ")" in flat, values
    assert insights_models.LATENCY_BUCKET_COUNT == 12


def test_every_new_table_is_named_by_an_rls_migration() -> None:
    from tests.unit.test_rls_tenant_coverage import _rls_tables_declared_in_migrations

    assert {model.__tablename__ for model in MODELS} <= _rls_tables_declared_in_migrations()


def test_drift_allowlist_lists_the_partitions() -> None:
    from tests.integration.alembic_schema_drift_allowlist import MIGRATION_OWNED_TABLES

    assert set(insights_models.RUN_FACTS_STATIC_PARTITIONS) <= set(MIGRATION_OWNED_TABLES)


def test_insights_kill_switch_defaults_off(monkeypatch) -> None:
    monkeypatch.delenv("AGENTICORG_INSIGHTS_ENABLED", raising=False)
    assert Settings.model_fields["insights_enabled"].default is False
    monkeypatch.setenv("AGENTICORG_INSIGHTS_ENABLED", "true")
    assert Settings().insights_enabled is True
