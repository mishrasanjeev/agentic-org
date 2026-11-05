# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""A shop's local delivery area on a real Postgres: outside it the checkout never becomes payable.

Requires the disposable database in ``AGENTICORG_DB_URL`` (migrated to head by the module fixture).
"""

from __future__ import annotations

import asyncio
import copy
import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.commerce import executors
from core.commerce.ucp import handlers
from tests import ucp_txn_fixtures as uf

AREA = {
    "shipping": True,
    "pickup": True,
    "multi_destination": [],
    "method_combinations": [],
    "postal_code_prefixes": ["5600", "5601"],
}


@pytest.fixture(scope="module", autouse=True)
async def migrated_schema(tmp_path_factory):
    if not os.getenv("AGENTICORG_DB_URL"):
        pytest.skip("Requires the disposable Docker Postgres database")
    env = {**os.environ, "AGENTICORG_MIGRATION_AUDIT_DIR": str(tmp_path_factory.mktemp("migration-audit"))}
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "scripts/alembic_migrate.py"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr[-4000:]


@pytest.fixture()
async def db(monkeypatch):
    from core import database

    engine = create_async_engine(os.environ["AGENTICORG_DB_URL"], poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", factory)
    monkeypatch.setattr(database, "current_session_factory", lambda: factory)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture()
def executor():
    fake = uf.FakeExecutor()
    executors.register_executor(fake)
    try:
        yield fake
    finally:
        executors.unregister_executor(fake.name)


def _runtime(tenant: str) -> handlers.UcpRuntime:
    return uf.runtime(
        tenant,
        resolved=uf.merchant(tenant, fulfillment=AREA),
        config=uf.merchant_config(tenant, fulfillment=AREA),
    )


def _shipping_to(postal_code: str | None) -> dict:
    body = copy.deepcopy(uf.SHIPPING)
    destination = body["methods"][0]["destinations"][0]
    if postal_code is None:
        destination.pop("postal_code", None)
    else:
        destination["postal_code"] = postal_code
    return body


@pytest.mark.asyncio
async def test_outside_the_area_the_checkout_names_the_postal_code_and_cannot_complete(db, executor):
    tenant = str(uuid.uuid4())
    runtime = _runtime(tenant)
    created = await handlers.run(
        "create_checkout", runtime, uf.request(tenant, uf.create_body(fulfillment=_shipping_to("411001")))
    )
    assert created.status == 201, created.body
    assert created.body["status"] != "ready_for_complete"
    contents = [m["content"] for m in created.body["messages"] if m.get("code") == "address_undeliverable"]
    assert contents == ["This shop delivers only to postal codes starting with 5600 or 5601; 411001 is outside it."]

    done = await handlers.run("complete_checkout", runtime, uf.request(tenant, uf.complete_body()), created.body["id"])
    assert done.body.get("status") != "completed"
    assert any("not ready to complete" in m["content"] for m in done.body["messages"])
    assert executor.calls == []


@pytest.mark.asyncio
async def test_a_missing_postal_code_is_asked_for_and_inside_the_area_it_completes(db, executor):
    tenant = str(uuid.uuid4())
    runtime = _runtime(tenant)
    missing = await handlers.run(
        "create_checkout", runtime, uf.request(tenant, uf.create_body(fulfillment=_shipping_to(None)))
    )
    assert missing.body["status"] != "ready_for_complete"
    assert any(m.get("code") == "missing" and "Add the postal code" in m["content"] for m in missing.body["messages"])

    inside = await handlers.run(
        "create_checkout", runtime, uf.request(tenant, uf.create_body(fulfillment=_shipping_to("560 034")))
    )
    assert inside.body["status"] == "ready_for_complete", inside.body.get("messages")
    done = await handlers.run("complete_checkout", runtime, uf.request(tenant, uf.complete_body()), inside.body["id"])
    assert done.body["status"] == "completed", done.body.get("messages")


@pytest.mark.asyncio
async def test_pickup_is_not_held_to_the_delivery_area(db, executor):
    tenant = str(uuid.uuid4())
    runtime = _runtime(tenant)
    pickup = {
        "methods": [
            {
                "type": "pickup",
                "selected_destination_id": "store-1",
                "groups": [{"selected_option_id": "pickup-store-1"}],
            }
        ]
    }
    created = await handlers.run("create_checkout", runtime, uf.request(tenant, uf.create_body(fulfillment=pickup)))
    assert created.body["status"] == "ready_for_complete", created.body.get("messages")
