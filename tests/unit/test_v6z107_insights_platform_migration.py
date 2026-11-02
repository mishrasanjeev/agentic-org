# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""v6z107: the platform aggregate role, its column grants and policies, and the two SECURITY DEFINER functions.

Static checks of the statements the migration emits; the behaviour is proven
against Postgres in ``tests/integration/test_insights_platform_postgres.py``.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z107_insights_platform.py"


def _migration():
    spec = importlib.util.spec_from_file_location("_v6_z107_insights_platform", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Op:
    def __init__(self) -> None:
        self.sql: list[str] = []

    def execute(self, sql) -> None:
        self.sql.append(" ".join(str(sql).split()))


def _run(fn_name: str, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    migration = _migration()
    op = _Op()
    monkeypatch.setattr(migration, "op", op)
    getattr(migration, fn_name)()
    return op.sql


def test_revision_chain() -> None:
    migration = _migration()
    assert migration.revision == "v6z107_insights_platform" and len(migration.revision) <= 32
    assert migration.down_revision == "v6z106_insights_foundation"


def test_role_is_inert_and_unsafe_preexisting_roles_are_refused(monkeypatch) -> None:
    sql = _run("upgrade", monkeypatch)
    role = sql[0]
    assert "CREATE ROLE agenticorg_insights_platform NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT" in role
    assert "NOREPLICATION NOBYPASSRLS" in role
    assert "rolcanlogin OR rolsuper OR rolbypassrls" in role and "RAISE EXCEPTION" in role


def test_grants_are_column_level_on_exactly_the_read_columns(monkeypatch) -> None:
    sql = _run("upgrade", monkeypatch)
    grants = {
        m.group(2): set(m.group(1).split(","))
        for s in sql
        if (m := re.match(r"GRANT SELECT\(([^)]+)\) ON public\.(\w+) TO agenticorg_insights_platform$", s))
    }
    assert grants == {
        "insights_daily": {"tenant_id", "day", "status", "runs", "tokens_total", "cost_usd", "duration_ms_sum",
                           "duration_n"},
        "insights_rollup_state": {"tenant_id", "day", "status"},
        "tenants": {"id", "name", "plan", "created_at", "deleted_at"},
        "agents": {"tenant_id", "status"},
    }
    assert not [s for s in sql if re.search(r"GRANT (SELECT|INSERT|UPDATE|DELETE|ALL) ON ", s)]
    policies = [s for s in sql if s.startswith("CREATE POLICY")]
    assert policies == [
        f"CREATE POLICY insights_platform_read ON public.{t} FOR SELECT TO agenticorg_insights_platform USING (true)"
        for t in ("insights_daily", "insights_rollup_state", "agents")
    ]


def test_functions_are_definer_pinned_qualified_closed_to_public_and_range_guarded(monkeypatch) -> None:
    migration = _migration()
    for body in (migration.DAILY_FUNCTION_SQL, migration.TENANTS_FUNCTION_SQL):
        flat = " ".join(body.split())
        assert "LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $function$" in flat
        assert "p_to - p_from > 92" in flat and "p_from IS NULL OR p_to IS NULL OR p_to < p_from" in flat
        # Every table reference is schema-qualified.
        for table in re.findall(r"\b(?:FROM|JOIN)\s+([\w.]+)", flat):
            assert table.startswith("public.") or table in {"range_usage", "agent_counts", "rollups", "streaks"}, table
    block = next(s for s in _run("upgrade", monkeypatch) if "EXECUTE $ddl$" in s)
    for signature in migration.FUNCTIONS:
        assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC" in block
        assert f"ALTER FUNCTION {signature} OWNER TO agenticorg_insights_platform" in block
        assert f"GRANT EXECUTE ON FUNCTION {signature} TO agenticorg_app" in block
        assert f"Unsafe preexisting insights platform function {signature}" in block
    # The installer's temporary membership and CREATE are taken back and checked.
    revoke = block.index("REVOKE CREATE ON SCHEMA public FROM agenticorg_insights_platform")
    assert revoke > block.index("EXECUTE $ddl$")
    assert "Installer retained insights platform row access" in block


def test_downgrade_drops_functions_policies_and_grants_but_keeps_the_role(monkeypatch) -> None:
    sql = " ".join(_run("downgrade", monkeypatch))
    for signature in _migration().FUNCTIONS:
        assert f"DROP FUNCTION {signature}" in sql
    for table in ("insights_daily", "insights_rollup_state", "agents"):
        assert f"DROP POLICY IF EXISTS insights_platform_read ON public.{table}" in sql
    for table in ("insights_daily", "insights_rollup_state", "tenants", "agents"):
        assert f"REVOKE ALL ON public.{table} FROM agenticorg_insights_platform" in sql
    assert "DROP ROLE" not in sql
