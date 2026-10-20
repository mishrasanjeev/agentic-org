# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""v6z87 (re)installs the audit_log append-only trigger with the sealing transition.

The application role holds ``UPDATE`` on the four audit chain columns so the
sealing task can run under it (``scripts/create_app_role.sql``). Only the
``audit_log_immutable`` trigger stops that grant from rewriting a sealed
row. A database bootstrapped from an empty schema is stamped past the
baselines that created the trigger, so the migration must install it, with
exactly the text ``core.database.init_db`` and ``v6z41`` use.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations" / "versions" / "v6_z87_platform_ctx_policies.py"


def _load(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[str]]:
    executed: list[str] = []

    class _Op:
        @staticmethod
        def execute(sql: str) -> None:
            executed.append(str(sql))

    spec = importlib.util.spec_from_file_location("v6z87_under_test", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "op", _Op)
    return module, executed


def test_migration_seal_trigger_text_equals_the_runtime_text(monkeypatch):
    from core.governance.audit_chain import SEAL_TRIGGER_SQL

    module, _ = _load(monkeypatch)
    assert module._SEAL_TRIGGER_SQL == SEAL_TRIGGER_SQL


def test_upgrade_installs_function_then_trigger_idempotently(monkeypatch):
    from core.governance.audit_chain import SEAL_TRIGGER_SQL

    module, executed = _load(monkeypatch)
    module.upgrade()
    assert SEAL_TRIGGER_SQL in executed
    trigger = [sql for sql in executed if "CREATE TRIGGER audit_log_immutable" in sql]
    assert len(trigger) == 1
    body = " ".join(trigger[0].split())
    assert "to_regclass('public.audit_log') IS NOT NULL" in body
    assert "DROP TRIGGER IF EXISTS audit_log_immutable ON audit_log" in body
    assert "BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_log_reject_mutation()" in body
    # The function is replaced before the trigger that calls it is (re)created.
    assert executed.index(SEAL_TRIGGER_SQL) < executed.index(trigger[0])
    assert "CREATE OR REPLACE FUNCTION audit_log_reject_mutation()" in SEAL_TRIGGER_SQL


def test_downgrade_leaves_the_tamper_control_installed(monkeypatch):
    module, executed = _load(monkeypatch)
    module.downgrade()
    joined = "\n".join(executed)
    assert "audit_log_immutable" not in joined
    assert "audit_log_reject_mutation" not in joined
