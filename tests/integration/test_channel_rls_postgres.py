# SPDX-License-Identifier: Apache-2.0
"""Real migrations and channel RLS, using an isolated database and non-superuser."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from api.v1.channels import _binding
from core.database import get_tenant_session
from core.models.channel import ChannelBinding, ChannelDelivery, ChannelSenderLink
from core.models.user import User
from tests.integration.conftest import admin_user_row, tenant_row
from tests.integration.test_feature_flags_rls_postgres import (
    _Db,
)
from tests.integration.test_feature_flags_rls_postgres import (
    as_rls_role as as_rls_role,
)
from tests.integration.test_feature_flags_rls_postgres import (
    migrated_db as migrated_db,
)
from tests.integration.test_feature_flags_rls_postgres import (
    pytestmark as pytestmark,
)


@pytest.mark.timeout(300)
async def test_all_channel_tables_enforce_scope_and_owner(as_rls_role: _Db) -> None:
    tenant_a, tenant_b, user_a, user_b = (uuid.uuid4() for _ in range(4))
    binding_a, binding_b = uuid.uuid4(), uuid.uuid4()
    with as_rls_role.owner.begin() as conn:
        for tenant, user, binding in ((tenant_a, user_a, binding_a), (tenant_b, user_b, binding_b)):
            conn.execute(
                text(
                    "INSERT INTO tenants (id,name,slug,plan,data_region,settings) "
                    "VALUES (:id,:name,:slug,:plan,:region,:settings)"
                ),
                tenant_row(str(tenant)),
            )
            conn.execute(
                text(
                    "INSERT INTO users (id,tenant_id,email,name,role,status,mfa_enabled) "
                    "VALUES (:id,:tenant_id,:email,:name,'admin','active',false)"
                ),
                admin_user_row(str(tenant), str(user), f"channel-{user.hex}@example.test"),
            )
            conn.execute(
                text(
                    "INSERT INTO channel_bindings "
                    "(id,tenant_id,owner_user_id,name,provider,target_kind,target_id,credentials_ciphertext) "
                    "VALUES (:id,:tenant,:user,'Test inbox','signed_webhook','agent',:target,'sealed')"
                ),
                {"id": binding, "tenant": tenant, "user": user, "target": uuid.uuid4()},
            )
            conn.execute(
                text(
                    "INSERT INTO channel_sender_links (id,tenant_id,binding_id,user_id,expires_at) "
                    "VALUES (:id,:tenant,:binding,:user,:expiry)"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant,
                    "binding": binding,
                    "user": user,
                    "expiry": datetime.now(UTC) + timedelta(minutes=10),
                },
            )
            conn.execute(
                text(
                    "INSERT INTO channel_deliveries "
                    "(id,tenant_id,binding_id,event_id,content_hash,message_ciphertext) "
                    "VALUES (:id,:tenant,:binding,'event','hash','sealed')"
                ),
                {"id": uuid.uuid4(), "tenant": tenant, "binding": binding},
            )
    tables = ("channel_bindings", "channel_sender_links", "channel_deliveries")
    snapshot_queries = {
        "channel_bindings": text("SELECT row_to_json(r) FROM channel_bindings r ORDER BY id"),
        "channel_sender_links": text("SELECT row_to_json(r) FROM channel_sender_links r ORDER BY id"),
        "channel_deliveries": text("SELECT row_to_json(r) FROM channel_deliveries r ORDER BY id"),
    }

    def retained_rows() -> dict[str, list[object]]:
        with as_rls_role.owner.connect() as conn:
            return {table: list(conn.scalars(query)) for table, query in snapshot_queries.items()}

    before = retained_rows()
    env = {**os.environ, "AGENTICORG_DB_URL": as_rls_role.url}
    repo = Path(__file__).resolve().parents[2]
    config = Config(str(repo / "alembic.ini"))
    config.set_main_option("script_location", str(repo / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head is not None
    for action, revision in (("downgrade", "v6z82_spend_reconciliation"), ("upgrade", "head")):
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "alembic", action, revision],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        assert result.returncode == 0, result.stderr[-4000:]
        assert retained_rows() == before
        with as_rls_role.owner.connect() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == (
                head if action == "upgrade" else revision
            )
            for table in tables:
                assert tuple(
                    conn.execute(
                        text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid=to_regclass(:table)"),
                        {"table": table},
                    ).one()
                ) == (True, True)
                assert (
                    conn.scalar(
                        text("SELECT count(*) FROM pg_policies WHERE tablename=:table AND policyname=:policy"),
                        {"table": table, "policy": f"{table}_tenant_isolation"},
                    )
                    == 1
                )
    for tenant, own_binding in ((tenant_a, binding_a), (tenant_b, binding_b)):
        async with get_tenant_session(tenant) as session:
            role = (
                await session.execute(text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"))
            ).one()
            assert tuple(role) == (False, False)
            for model in (ChannelBinding, ChannelSenderLink, ChannelDelivery):
                rows = (await session.scalars(select(model))).all()
                assert len(rows) == 1 and rows[0].tenant_id == tenant
            assert await session.get(ChannelBinding, own_binding) is not None
    async with get_tenant_session(tenant_a) as session:
        actor = await session.get(User, user_a)
    assert actor is not None
    assert (await _binding(tenant_a, binding_a, actor)).id == binding_a
    with pytest.raises(HTTPException) as cross_tenant:
        await _binding(tenant_a, binding_b, actor)
    assert cross_tenant.value.status_code == 404
    actor.id = uuid.uuid4()
    with pytest.raises(HTTPException) as cross_owner:
        await _binding(tenant_a, binding_a, actor)
    assert cross_owner.value.status_code == 404
    forbidden_rows = (
        ChannelBinding(
            tenant_id=tenant_b,
            owner_user_id=user_b,
            name="Blocked",
            provider="signed_webhook",
            target_kind="agent",
            target_id=uuid.uuid4(),
            credentials_ciphertext="sealed",
        ),
        ChannelSenderLink(
            tenant_id=tenant_b,
            binding_id=binding_b,
            user_id=user_b,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        ),
        ChannelDelivery(
            tenant_id=tenant_b,
            binding_id=binding_b,
            event_id="blocked-write",
            content_hash="hash",
            message_ciphertext="sealed",
        ),
    )
    for forbidden_row in forbidden_rows:
        with pytest.raises(DBAPIError):
            async with get_tenant_session(tenant_a) as session:
                session.add(forbidden_row)
                await session.flush()


async def test_reply_mode_migration_preserves_legacy_defaults_and_rls(migrated_db: _Db, monkeypatch) -> None:
    migration = importlib.import_module("migrations.versions.v6_z86_telegram_answers")
    with migrated_db.owner.connect() as conn:
        transaction = conn.begin()
        try:
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
            # Simulate the populated pre-upgrade shape, not just an already stamped DB.
            conn.execute(text("ALTER TABLE channel_bindings DROP COLUMN reply_mode CASCADE"))
            conn.execute(text("ALTER TABLE channel_deliveries DROP COLUMN reply_mode"))
            tenant, user, binding = (uuid.uuid4() for _ in range(3))
            conn.execute(
                text(
                    "INSERT INTO tenants (id,name,slug,plan,data_region,settings) "
                    "VALUES (:id,:name,:slug,:plan,:region,:settings)"
                ),
                tenant_row(str(tenant)),
            )
            conn.execute(
                text(
                    "INSERT INTO users (id,tenant_id,email,name,role,status,mfa_enabled) "
                    "VALUES (:id,:tenant_id,:email,:name,'admin','active',false)"
                ),
                admin_user_row(str(tenant), str(user), f"reply-{user.hex}@example.test"),
            )
            conn.execute(
                text(
                    "INSERT INTO channel_bindings "
                    "(id,tenant_id,owner_user_id,name,provider,target_kind,target_id,credentials_ciphertext) "
                    "VALUES (:id,:tenant,:user,'Legacy bot','telegram','agent',:target,'sealed')"
                ),
                {"id": binding, "tenant": tenant, "user": user, "target": uuid.uuid4()},
            )
            conn.execute(
                text(
                    "INSERT INTO channel_deliveries "
                    "(id,tenant_id,binding_id,event_id,content_hash,message_ciphertext) "
                    "VALUES (:id,:tenant,:binding,'old-event','hash','sealed')"
                ),
                {"id": uuid.uuid4(), "tenant": tenant, "binding": binding},
            )
            migration.upgrade()
            migration.upgrade()
            migration.downgrade()
            assert (
                conn.scalar(text("SELECT reply_mode FROM channel_bindings WHERE id=:id"), {"id": binding}) == "status"
            )
            assert (
                conn.scalar(text("SELECT reply_mode FROM channel_deliveries WHERE binding_id=:id"), {"id": binding})
                == "status"
            )
            for table in ("channel_bindings", "channel_deliveries"):
                assert tuple(
                    conn.execute(
                        text(
                            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=CAST(:table AS regclass)"
                        ),
                        {"table": table},
                    ).one()
                ) == (True, True)
            with pytest.raises(DBAPIError), conn.begin_nested():
                conn.execute(
                    text("UPDATE channel_bindings SET reply_mode='export_everything' WHERE id=:id"), {"id": binding}
                )
            # Empty installations without channel tables must not fail or create partial tables.
            conn.execute(text("DROP TABLE channel_deliveries,channel_sender_links,channel_bindings CASCADE"))
            migration.upgrade()
            assert conn.scalar(text("SELECT to_regclass('public.channel_bindings')")) is None
        finally:
            transaction.rollback()
