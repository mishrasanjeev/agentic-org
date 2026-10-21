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


def test_model_comment_only_change_does_not_require_migration(monkeypatch) -> None:
    # The 2026-10-21 relicensing changed only the SPDX comment line of every model file.
    monkeypatch.setattr(
        guard,
        "_diff",
        lambda *_args: (
            "diff --git a/core/models/agent.py b/core/models/agent.py\n"
            "--- a/core/models/agent.py\n"
            "+++ b/core/models/agent.py\n"
            "@@ -1 +1 @@\n"
            "-# SPDX-License-" "Identifier: Apache-2.0\n"
            "+# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0"
        ),
    )

    assert guard._schema_relevant_changed_files("base", "head", ["core/models/agent.py"]) == []


def test_model_code_change_next_to_a_comment_still_requires_migration(monkeypatch) -> None:
    monkeypatch.setattr(
        guard,
        "_diff",
        lambda *_args: (
            "--- a/core/models/agent.py\n"
            "+++ b/core/models/agent.py\n"
            "@@ -1,0 +1,2 @@\n"
            "+# new column\n"
            "+    nickname = Column(String(64))"
        ),
    )

    assert guard._schema_relevant_changed_files("base", "head", ["core/models/agent.py"]) == ["core/models/agent.py"]


def test_model_change_with_an_empty_diff_still_requires_migration(monkeypatch) -> None:
    monkeypatch.setattr(guard, "_diff", lambda *_args: "")

    assert guard._schema_relevant_changed_files("base", "head", ["core/models/agent.py"]) == ["core/models/agent.py"]


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
