# SPDX-License-Identifier: Apache-2.0
"""Real migrations and channel RLS, using an isolated database and non-superuser."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
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
    with pytest.raises(DBAPIError):
        async with get_tenant_session(tenant_a) as session:
            session.add(
                ChannelBinding(
                    tenant_id=tenant_b,
                    owner_user_id=user_b,
                    name="Blocked",
                    provider="signed_webhook",
                    target_kind="agent",
                    target_id=uuid.uuid4(),
                    credentials_ciphertext="sealed",
                )
            )
            await session.flush()
