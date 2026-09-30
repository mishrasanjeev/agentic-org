# SPDX-License-Identifier: Apache-2.0
"""``feature_flags`` global rows for a role bound by row-level security (2026-09-27).

The only policy on ``feature_flags`` (v6z16) compares ``tenant_id`` with the session's
tenant, so a global row (``tenant_id`` NULL) was invisible to every role that does not
bypass row-level security. Revision ``v6z30_flag_global_read`` adds a SELECT-only policy
for those rows. The behaviour is tested against Postgres, as a ``NOSUPERUSER
NOBYPASSRLS`` role, in tests/integration/test_feature_flags_rls_postgres.py; these checks
pin the revision's place in the chain and keep it read-only.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _migration():
    path = _ROOT / "migrations" / "versions" / "v6_z30_feature_flags_global_read.py"
    spec = importlib.util.spec_from_file_location("v6_z30_feature_flags_global_read", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Recorder:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


def test_revision_remains_in_the_single_head_migration_chain() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    module = _migration()
    assert module.revision == "v6z30_flag_global_read"
    assert len(module.revision) <= 32
    assert module.down_revision == "v6z29_admin_scope_compat"
    heads = ScriptDirectory.from_config(Config(str(_ROOT / "alembic.ini"))).get_heads()
    assert heads == ["v6z31_a2a_buyers"]


def test_upgrade_adds_only_a_select_policy_for_global_rows() -> None:
    module = _migration()
    recorder = _Recorder()
    module.op = recorder
    module.upgrade()
    [statement] = recorder.statements
    creates = re.findall(r"CREATE POLICY (\w+) ON (\w+) FOR (\w+) USING \(([^)]*)\)", statement)
    assert creates == [("feature_flags_global_read", "feature_flags", "SELECT", "tenant_id IS NULL")]
    # Writes of global rows stay with privileged roles: no WITH CHECK, no write command.
    assert "WITH CHECK" not in statement
    assert not re.search(r"FOR (ALL|INSERT|UPDATE|DELETE)", statement)
    assert "to_regclass('public.feature_flags')" in statement


def test_downgrade_drops_only_that_policy() -> None:
    module = _migration()
    recorder = _Recorder()
    module.op = recorder
    module.downgrade()
    [statement] = recorder.statements
    assert "DROP POLICY IF EXISTS feature_flags_global_read ON feature_flags;" in statement
    assert "ROW LEVEL SECURITY" not in statement
