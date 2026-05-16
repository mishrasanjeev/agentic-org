from __future__ import annotations

from scripts import check_migration_required as guard


def test_core_database_verification_changes_do_not_require_migration(monkeypatch) -> None:
    monkeypatch.setattr(
        guard,
        "_diff",
        lambda *_args: "+async def verify_runtime_schema_current(conn):\n+    raise RuntimeSchemaError('stale')",
    )

    assert guard._schema_relevant_changed_files("base", "head", ["core/database.py"]) == []


def test_core_database_added_ddl_requires_migration(monkeypatch) -> None:
    monkeypatch.setattr(
        guard,
        "_diff",
        lambda *_args: '+await conn.execute(text("ALTER TABLE agents ADD COLUMN foo TEXT"))',
    )

    assert guard._schema_relevant_changed_files("base", "head", ["core/database.py"]) == ["core/database.py"]


def test_multiple_alembic_heads_fail_without_merge_plan(monkeypatch, capsys) -> None:
    monkeypatch.setattr(guard, "_alembic_heads", lambda: ["head_a", "head_b"])
    monkeypatch.setattr(guard, "_changed_files", lambda *_args: [])
    monkeypatch.setattr(guard, "_added_files", lambda *_args: [])
    monkeypatch.delenv(guard.ALLOW_MULTIPLE_ALEMBIC_HEADS_ENV, raising=False)

    assert guard.main() == 1
    assert "Multiple Alembic heads detected" in capsys.readouterr().err


def test_multiple_alembic_heads_allowed_for_explicit_merge_plan(monkeypatch) -> None:
    monkeypatch.setattr(guard, "_alembic_heads", lambda: ["head_a", "head_b"])
    monkeypatch.setattr(guard, "_changed_files", lambda *_args: [])
    monkeypatch.setattr(guard, "_added_files", lambda *_args: [])
    monkeypatch.setenv(guard.ALLOW_MULTIPLE_ALEMBIC_HEADS_ENV, "1")

    assert guard.main() == 0
