# SPDX-License-Identifier: Apache-2.0
"""UCP tables under the NOBYPASSRLS application role: sessions, carts, orders, webhooks and idempotency stay
tenant-bound, a cross-tenant write fails WITH CHECK, and a full checkout completes under the app role.

Uses the fixtures of ``test_rls_nobypass_role``. Skipped unless ``AGENTICORG_RLS_TEST_DB_URL`` points at a
NOBYPASSRLS role on a database migrated to ``v6z89_commerce_sessions_orders`` or later.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from core.commerce import executors
from core.commerce.ucp import handlers
from tests import ucp_txn_fixtures as uf
from tests.integration.test_rls_nobypass_role import RLS_DB_URL, _app_role_sessions, env  # noqa: F401

pytestmark = pytest.mark.skipif(
    not RLS_DB_URL,
    reason="set AGENTICORG_RLS_TEST_DB_URL to a NOBYPASSRLS application-role DSN",
)

TABLES = (
    "commerce_checkout_sessions",
    "commerce_carts",
    "commerce_orders",
    "commerce_order_webhook_subscriptions",
    "commerce_order_webhook_deliveries",
    "commerce_ucp_idempotency",
)


async def _count(session, table: str) -> int:
    return int((await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())  # noqa: S608


@pytest.mark.asyncio
async def test_checkout_completes_under_the_app_role_and_tenant_b_sees_nothing(env) -> None:  # noqa: F811
    from core.database import get_tenant_session

    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    fake = uf.FakeExecutor()
    executors.register_executor(fake)
    try:
        runtime = uf.runtime(tenant_a)
        created = await handlers.run(
            "create_checkout", runtime, uf.request(tenant_a, uf.create_body(fulfillment=uf.SHIPPING), key="rls-1")
        )
        assert created.status == 201 and created.body["status"] == "ready_for_complete"
        cart = await handlers.run("create_cart", runtime, uf.request(tenant_a, uf.create_body()))
        assert cart.status == 201
        done = await handlers.run(
            "complete_checkout", runtime, uf.request(tenant_a, uf.complete_body()), created.body["id"]
        )
        assert done.body["status"] == "completed", done.body.get("messages")
    finally:
        executors.unregister_executor(fake.name)

    async with get_tenant_session(uuid.UUID(tenant_a)) as session:
        counts = {table: await _count(session, table) for table in TABLES}
    assert counts == {
        "commerce_checkout_sessions": 1,
        "commerce_carts": 1,
        "commerce_orders": 1,
        "commerce_order_webhook_subscriptions": 1,
        "commerce_order_webhook_deliveries": 1,
        "commerce_ucp_idempotency": 1,
    }
    async with get_tenant_session(uuid.UUID(tenant_b)) as session:
        assert {table: await _count(session, table) for table in TABLES} == dict.fromkeys(TABLES, 0)

    # Tenant B cannot read tenant A's checkout through the handlers either.
    other = await handlers.run("get_checkout", uf.runtime(tenant_b), uf.request(tenant_b), created.body["id"])
    assert other.status == 404


@pytest.mark.asyncio
async def test_cross_tenant_insert_fails_with_check(env) -> None:  # noqa: F811
    from core.database import get_tenant_session

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    with pytest.raises(DBAPIError):
        async with get_tenant_session(tenant_a) as session:
            await session.execute(
                text(
                    "INSERT INTO commerce_ucp_idempotency (tenant_id, merchant_id, idempotency_key, operation, "
                    "body_sha256, status, created_at, expires_at) VALUES (:t, 'm', 'k', 'op', :h, 'pending', now(), "
                    "now() + interval '48 hours')"
                ),
                {"t": str(tenant_b), "h": "a" * 64},
            )
    with pytest.raises(DBAPIError):
        async with get_tenant_session(tenant_a) as session:
            await session.execute(
                text(
                    "INSERT INTO commerce_checkout_sessions (id, tenant_id, merchant_id, seller_agent_id, "
                    "serving_host, ucp_version, status, state, expires_at) VALUES ('chk_x', :t, 'm', 's', "
                    "'h.example', '2026-04-08', 'incomplete', '{}'::jsonb, now() + interval '1 hour')"
                ),
                {"t": str(tenant_b)},
            )


@pytest.mark.asyncio
async def test_idempotency_retention_check_is_enforced(env) -> None:  # noqa: F811
    from core.database import get_tenant_session

    tenant = uuid.uuid4()
    with pytest.raises(DBAPIError):
        async with get_tenant_session(tenant) as session:
            await session.execute(
                text(
                    "INSERT INTO commerce_ucp_idempotency (tenant_id, merchant_id, idempotency_key, operation, "
                    "body_sha256, status, created_at, expires_at) VALUES (:t, 'm', 'k', 'op', :h, 'pending', now(), "
                    "now() + interval '1 hour')"
                ),
                {"t": str(tenant), "h": "b" * 64},
            )
