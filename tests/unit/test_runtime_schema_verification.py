from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import database

DDL_PATTERNS = (
    "ALTER TABLE",
    "CREATE INDEX",
    "CREATE POLICY",
    "CREATE TABLE",
    "CREATE TRIGGER",
    "ENABLE ROW LEVEL SECURITY",
)


class _FakeScalars:
    def __init__(self, values: list[str]) -> None:
        self._values = values

    def all(self) -> list[str]:
        return self._values


class _FakeResult:
    def __init__(self, values: list[str] | None = None) -> None:
        self._values = values or []

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._values)


class _FakeConnection:
    def __init__(self, *, alembic_table_exists: bool = True, versions: list[str] | None = None) -> None:
        self.alembic_table_exists = alembic_table_exists
        self.versions = versions or ["head"]
        self.statements: list[str] = []

    async def execute(self, statement: object) -> _FakeResult:
        sql = str(statement)
        self.statements.append(sql)
        if "SELECT version_num FROM alembic_version" in sql:
            return _FakeResult(self.versions)
        return _FakeResult()

    async def scalar(self, statement: object) -> bool:
        sql = str(statement)
        self.statements.append(sql)
        if "to_regclass" in sql:
            return self.alembic_table_exists
        return True


class _FakeBegin:
    def __init__(self, conn: _FakeConnection) -> None:
        self.conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self.conn

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _FakeEngine:
    def __init__(self, conn: _FakeConnection) -> None:
        self.conn = conn

    def begin(self) -> _FakeBegin:
        return _FakeBegin(self.conn)


def _contains_ddl(statements: list[str]) -> bool:
    joined = "\n".join(statements).upper()
    return any(pattern in joined for pattern in DDL_PATTERNS)


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch,
    conn: _FakeConnection,
    *,
    env: str,
    expected_heads: set[str] | None = None,
) -> None:
    monkeypatch.setattr(database, "engine", _FakeEngine(conn))
    monkeypatch.setattr(database, "settings", SimpleNamespace(env=env))
    monkeypatch.setattr(database, "get_expected_alembic_heads", lambda: frozenset(expected_heads or {"head"}))

    async def _noop_seed() -> None:
        return None

    monkeypatch.setattr(database, "_seed_demo_ca_companies_if_enabled", _noop_seed)


@pytest.mark.asyncio
async def test_strict_env_missing_alembic_version_fails_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConnection(alembic_table_exists=False)
    _patch_runtime(monkeypatch, conn, env="production")

    with pytest.raises(database.RuntimeSchemaError, match="missing `alembic_version`"):
        await database.init_db()

    assert not _contains_ddl(conn.statements)


@pytest.mark.asyncio
async def test_strict_env_stale_revision_fails_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConnection(versions=["old_head"])
    _patch_runtime(monkeypatch, conn, env="staging")

    with pytest.raises(database.RuntimeSchemaError, match="stale or divergent"):
        await database.init_db()

    assert not _contains_ddl(conn.statements)


@pytest.mark.asyncio
async def test_strict_env_current_revision_passes_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConnection(versions=["head"])
    _patch_runtime(monkeypatch, conn, env="preview")

    await database.init_db()

    assert not _contains_ddl(conn.statements)


@pytest.mark.asyncio
async def test_strict_env_ignores_legacy_flags_and_never_runs_ddl(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConnection(versions=["head"])
    _patch_runtime(monkeypatch, conn, env="production")
    monkeypatch.delenv("AGENTICORG_DDL_MANAGED_BY_ALEMBIC", raising=False)
    monkeypatch.setenv(database.ENABLE_LEGACY_STARTUP_DDL_ENV, "1")

    await database.init_db()

    assert not _contains_ddl(conn.statements)


@pytest.mark.asyncio
async def test_old_alembic_flag_no_longer_bypasses_strict_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConnection(versions=["old_head"])
    _patch_runtime(monkeypatch, conn, env="production")
    monkeypatch.setenv("AGENTICORG_DDL_MANAGED_BY_ALEMBIC", "true")

    with pytest.raises(database.RuntimeSchemaError, match="stale or divergent"):
        await database.init_db()


@pytest.mark.asyncio
async def test_relaxed_env_does_not_run_legacy_ddl_without_explicit_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConnection(alembic_table_exists=False)
    _patch_runtime(monkeypatch, conn, env="development")
    monkeypatch.delenv(database.ENABLE_LEGACY_STARTUP_DDL_ENV, raising=False)

    await database.init_db()

    assert not _contains_ddl(conn.statements)


@pytest.mark.asyncio
async def test_legacy_helper_refuses_strict_env_even_with_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(database, "settings", SimpleNamespace(env="production"))
    monkeypatch.setenv(database.ENABLE_LEGACY_STARTUP_DDL_ENV, "1")

    with pytest.raises(database.RuntimeSchemaError, match="ignored in strict runtimes"):
        await database._legacy_startup_schema_repair_for_local_only()


@pytest.mark.asyncio
async def test_multiple_alembic_heads_fail_without_merge_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConnection(versions=["head_a", "head_b"])
    monkeypatch.delenv(database.ALLOW_MULTIPLE_ALEMBIC_HEADS_ENV, raising=False)

    with pytest.raises(database.RuntimeSchemaError, match="multiple Alembic heads"):
        await database.verify_runtime_schema_current(conn, expected_heads={"head_a", "head_b"})
