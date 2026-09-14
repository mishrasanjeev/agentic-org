"""Migration v6z21 (resource ownership) — Rule 8 guards: idempotent, guarded,
reversible, single head, revision id within VARCHAR(32)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
MIG_PATH = REPO_ROOT / "migrations" / "versions" / "v6_z21_resource_ownership.py"


def _load():
    spec = importlib.util.spec_from_file_location("v6z21", MIG_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_v6z21_is_guarded_idempotent_reversible_and_single_head() -> None:
    mig = _load()
    assert mig.revision == "v6z21_resource_ownership"
    assert len(mig.revision) <= 32
    assert mig.down_revision == "v6z20_agent_llm_provider"

    executed: list[str] = []
    with patch.object(mig.op, "execute", side_effect=executed.append):
        mig.upgrade()
    up = " ".join(executed)
    executed.clear()
    with patch.object(mig.op, "execute", side_effect=executed.append):
        mig.downgrade()
    down = " ".join(executed)

    for table in ("agents", "connectors", "hitl_queue"):
        assert f"to_regclass('public.{table}')" in up
    assert "ADD COLUMN IF NOT EXISTS owner_user_id UUID" in up
    assert "ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) NOT NULL DEFAULT 'tenant'" in up
    assert "ADD COLUMN IF NOT EXISTS requested_by_user_id UUID" in up
    assert "CHECK (visibility IN ('tenant', 'personal'))" in up
    assert up.count("ON DELETE SET NULL") == 3
    # Constraint creation is name-guarded so a re-run does not error.
    assert up.count("SELECT 1 FROM pg_constraint WHERE conname") == 4
    # FK columns must lead an index (scripts/check_database_indexes.py gate).
    assert "ON agents (owner_user_id, tenant_id)" in up
    assert "ON connectors (owner_user_id, tenant_id)" in up
    assert "ON hitl_queue (requested_by_user_id)" in up
    for col in ("requested_by_user_id", "owner_user_id", "visibility"):
        assert f"DROP COLUMN IF EXISTS {col}" in down

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini"))).get_heads()
    assert len(heads) == 1, heads


def test_orm_matches_migration_columns() -> None:
    from core.models.agent import Agent
    from core.models.connector import Connector
    from core.models.hitl import HITLQueue

    assert Agent.__table__.c.owner_user_id.nullable is True
    assert Agent.__table__.c.visibility.nullable is False
    assert Connector.__table__.c.owner_user_id.nullable is True
    assert HITLQueue.__table__.c.requested_by_user_id.nullable is True
