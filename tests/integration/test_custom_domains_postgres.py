# SPDX-License-Identifier: Apache-2.0
"""Merchant custom domains on a real Postgres: the merchant flow, the operator queue, RLS and host claims.

DNS, the HTTPS probe and email are fakes; everything else (merchant config,
serving-host attestation and index, commerce ledger, row-level security) is the
real code against the disposable database in ``AGENTICORG_DB_URL`` (migrated to
head by the module fixture). RLS is checked as a temporary NOBYPASSRLS role so
the assertions hold whether the test connects as the owner or the app role.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from api.v1 import commerce_ucp_publishing as publishing
from api.v1 import merchant_custom_domains as mcd
from core.commerce import custom_domains as cd
from core.commerce.oacp_merchant_config import merchant_config_id
from core.commerce.ucp import store
from core.config import settings

pytestmark = pytest.mark.asyncio

SELLER = "seller-1"
MERCHANT = "merchant-1"


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
    store.reset_ucp_host_cache()
    try:
        yield engine
    finally:
        store.reset_ucp_host_cache()
        await engine.dispose()


@pytest.fixture()
def world(monkeypatch):
    """Feature flags on, an operator tenant, and fakes for DNS, the probe and email."""
    state = {"txt": False, "cname": None, "probe": "certificate_pending", "emails": [], "operator": str(uuid.uuid4())}
    monkeypatch.setattr(settings, "custom_domains_enabled", True)
    monkeypatch.setattr(settings, "custom_domain_operator_tenant_id", state["operator"])
    monkeypatch.setattr(settings, "custom_domain_mapping_account", "mapper@platform.example")
    monkeypatch.setattr(settings, "custom_domain_cloud_run_project", "example-project")
    monkeypatch.setattr(settings, "custom_domain_cloud_run_automap_enabled", False)

    async def txt(_challenge):
        return state["txt"]

    async def routing(_host):
        observed = state["cname"]
        if observed is None:
            return False, None, "cname_missing"
        if observed != cd.cname_target():
            return False, observed, "cname_wrong_target"
        return True, observed, None

    async def apex(host):
        return host.count(".") == 1

    async def probe(_host):
        return cd.ProbeResult(state["probe"], None)

    def email(*, subject, lines):
        state["emails"].append((subject, lines))
        return True

    monkeypatch.setattr(publishing, "dns_challenge_satisfied", txt)
    monkeypatch.setattr(cd, "check_routing", routing)
    monkeypatch.setattr(cd, "is_zone_apex", apex)
    monkeypatch.setattr(cd, "probe_host", probe)
    monkeypatch.setattr(mcd, "_send_operator_email", email)
    return state


async def _exec(engine, sql: str, **params) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(sql), params)


async def _tenant(engine) -> str:
    tenant = uuid.uuid4()
    await _exec(
        engine,
        "INSERT INTO tenants (id, name, slug, plan, data_region, settings) "
        "VALUES (:id, :name, :slug, 'enterprise', 'IN', CAST(:settings AS jsonb))",
        id=tenant,
        name=f"custom-domain-{tenant.hex}",
        slug=f"custom-domain-{tenant.hex}",
        settings=json.dumps({}),
    )
    return str(tenant)


async def _merchant(tenant: str, merchant_id: str = MERCHANT) -> None:
    from core.database import get_tenant_session
    from core.models.commerce_c6z_runtime import C6ZMerchantCommerceConfigRow

    async with get_tenant_session(uuid.UUID(tenant)) as session:
        session.add(
            C6ZMerchantCommerceConfigRow(
                config_id=merchant_config_id(tenant, merchant_id, SELLER),
                tenant_id=tenant,
                merchant_id=merchant_id,
                seller_agent_id=SELLER,
                merchant_display_name="Example Store",
                provider_policy={},
            )
        )


def _request(user: str = "owner@merchant.example") -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(user_sub=user, auth_mode="session"))


def _host() -> str:
    return f"agent-{uuid.uuid4().hex[:10]}.merchant.example"


async def _add(tenant: str, host: str, merchant_id: str = MERCHANT) -> dict:
    return await mcd.add_custom_domain(
        merchant_id, mcd.AddDomainRequest(host=host, seller_agent_id=SELLER), _request(), tenant
    )


async def _check(tenant: str, *, owner: bool = False, merchant_id: str = MERCHANT) -> dict:
    return await mcd.check_custom_domain(
        merchant_id,
        mcd.CheckDomainRequest(seller_agent_id=SELLER, search_console_owner_added=owner),
        _request(),
        tenant,
    )


async def _ledger_events(engine, tenant: str) -> list[str]:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT set_config('agenticorg.tenant_id', :t, false)"), {"t": tenant})
        rows = await conn.execute(
            text("SELECT action FROM audit_log WHERE tenant_id = CAST(:t AS uuid) ORDER BY created_at"), {"t": tenant}
        )
        return [row[0] for row in rows]


async def test_merchant_flow_reaches_live_through_the_operator_queue(db, world):
    tenant = await _tenant(db)
    await _merchant(tenant)
    host = _host()

    added = await _add(tenant, host)
    assert added["status"] == "pending_dns"
    records = {record["type"]: record for record in added["records"]}
    assert records["CNAME"] == {**records["CNAME"], "name": host, "value": "ghs.googlehosted.com."}
    assert records["TXT"]["name"] == f"_agenticorg-ucp.{host}"
    assert records["TXT"]["value"].startswith("agenticorg-ucp-verify=")
    assert added["search_console"]["account"] == "mapper@platform.example"
    assert (await _add(tenant, host))["id"] == added["id"]  # same host: idempotent

    first = await _check(tenant)
    assert first["status"] == "pending_dns"
    assert first["error"]["code"] == "ownership_txt_missing" and "cname_missing" in first["error"]["detail"]
    assert [s["state"] for s in first["steps"]][:2] == ["error", "error"]

    world["txt"] = True
    world["cname"] = "elsewhere.example"
    wrong = await _check(tenant)
    assert wrong["ownership_verified"] is True
    assert wrong["error"]["code"] == "cname_wrong_target" and "elsewhere.example" in wrong["error"]["detail"]

    world["cname"] = "ghs.googlehosted.com"
    unconfirmed = await _check(tenant)
    assert unconfirmed["status"] == "pending_dns"
    assert unconfirmed["error"]["code"] == "search_console_owner_not_confirmed"

    queued = await _check(tenant, owner=True)
    assert queued["status"] == "pending_mapping" and queued["error"] is None
    assert len(world["emails"]) == 1
    subject, lines = world["emails"][0]
    assert host in subject
    assert any(line.startswith("gcloud beta run domain-mappings create") and host in line for line in lines)

    # The public host index points at the merchant once ownership is attested.
    binding = await store.find_serving_binding(host)
    assert binding is not None and binding.tenant_id == tenant and binding.merchant_id == MERCHANT

    other = await _tenant(db)
    with pytest.raises(HTTPException) as hidden:
        await mcd.list_operator_queue(other)
    assert hidden.value.status_code == 404

    queue = await mcd.list_operator_queue(world["operator"])
    item = next(i for i in queue["items"] if i["host"] == host)
    assert item["tenant_id"] == tenant and item["search_console_owner_confirmed"] is True
    assert item["commands"]["create"] == (
        f"gcloud beta run domain-mappings create --service=agenticorg-api --domain={host} "
        "--project=example-project --region=asia-southeast1"
    )

    mapped = await mcd.mark_mapped(item["id"], _request("operator@platform.example"), world["operator"])
    assert mapped["status"] == "pending_certificate" and mapped["mapping_source"] == "operator"

    waiting = await _check(tenant)
    assert waiting["status"] == "pending_certificate"
    assert waiting["error"]["code"] == "certificate_pending"

    world["probe"] = "live"
    live = await _check(tenant)
    assert live["status"] == "live" and live["error"] is None and live["live_at"]
    assert all(step["state"] == "done" for step in live["steps"])

    events = await _ledger_events(db, tenant)
    for event in (
        "commerce.custom_domain.add",
        "commerce.custom_domain.check",
        "commerce.ucp.serving_host.verify",
        "commerce.custom_domain.operator.mapped",
        "commerce.custom_domain.status",
    ):
        assert event in events, event


async def test_operator_failure_is_shown_to_the_merchant_and_retry_requeues(db, world):
    tenant = await _tenant(db)
    await _merchant(tenant)
    host = _host()
    await _add(tenant, host)
    world.update(txt=True, cname="ghs.googlehosted.com")
    queued = await _check(tenant, owner=True)
    assert queued["status"] == "pending_mapping"

    failed = await mcd.mark_failed(
        queued["id"], mcd.OperatorFailRequest(reason="Search Console owner missing"), _request(), world["operator"]
    )
    assert failed["status"] == "failed"
    view = (await mcd.get_custom_domain(MERCHANT, SELLER, tenant))["domain"]
    assert view["error"] == {
        "code": "mapping_failed",
        "message": cd.error_message("mapping_failed"),
        "detail": "Search Console owner missing",
        "codes": ["mapping_failed"],
    }
    assert view["can_retry"] is True

    retried = await _check(tenant, owner=True)
    assert retried["status"] == "pending_mapping" and retried["error"] is None
    assert len(world["emails"]) == 2  # the operator hears about the retry


async def test_rows_are_isolated_by_rls_and_listed_only_in_platform_context(db, world):
    tenant_a, tenant_b = await _tenant(db), await _tenant(db)
    await _merchant(tenant_a)
    await _add(tenant_a, _host())

    role = f"cd_rls_{uuid.uuid4().hex[:8]}"
    async with db.begin() as conn:
        # The table's schema as the test connection resolves it: a CI database can keep the
        # tables in a per-user schema that the probe role's own search_path would not reach.
        schema = str((await conn.execute(text("SELECT current_schema()"))).scalar_one())
        assert re.fullmatch(r"[a-z_][a-z0-9_]*", schema), schema
        await conn.execute(text(f"CREATE ROLE {role} NOLOGIN NOBYPASSRLS"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO {role}"))
        await conn.execute(text(f"GRANT SELECT, INSERT ON {schema}.merchant_custom_domains TO {role}"))

    async def as_probe(conn, **ctx: str) -> None:
        await conn.execute(text(f"SET LOCAL ROLE {role}"))
        await conn.execute(text("SELECT set_config('search_path', :s, true)"), {"s": schema})
        for key, value in ctx.items():
            await conn.execute(text("SELECT set_config(:k, :v, true)"), {"k": f"agenticorg.{key}", "v": value})

    async def count(**ctx: str) -> int:
        async with db.begin() as conn:
            await as_probe(conn, **ctx)
            result = await conn.execute(
                text("SELECT count(*) FROM merchant_custom_domains WHERE tenant_id = CAST(:t AS uuid)"), {"t": tenant_a}
            )
            return int(result.scalar_one())

    async def insert_as_probe(host: str, **ctx: str) -> None:
        async with db.begin() as conn:
            await as_probe(conn, **ctx)
            await conn.execute(
                text(
                    "INSERT INTO merchant_custom_domains "
                    "(id, tenant_id, merchant_id, host, cname_target, created_by) "
                    "VALUES (:id, CAST(:t AS uuid), 'm', :host, 'ghs.googlehosted.com', 'test')"
                ),
                {"id": uuid.uuid4(), "t": tenant_a, "host": host},
            )

    try:
        assert await count(tenant_id=tenant_a) == 1
        assert await count(tenant_id=tenant_b) == 0
        assert await count() == 0
        assert await count(platform_ctx="1") == 1
        with pytest.raises(Exception, match="row-level security"):
            await insert_as_probe("x.merchant.example", tenant_id=tenant_b)
        # The platform context reads but never writes.
        with pytest.raises(Exception, match="row-level security"):
            await insert_as_probe("y.merchant.example", platform_ctx="1")
    finally:
        async with db.begin() as conn:
            await conn.execute(text(f"REVOKE ALL ON {schema}.merchant_custom_domains FROM {role}"))
            await conn.execute(text(f"REVOKE USAGE ON SCHEMA {schema} FROM {role}"))
            await conn.execute(text(f"DROP ROLE {role}"))


async def test_a_host_claimed_by_one_tenant_cannot_be_claimed_by_another(db, world):
    tenant_a, tenant_b = await _tenant(db), await _tenant(db)
    await _merchant(tenant_a)
    await _merchant(tenant_b)
    host = _host()
    world.update(txt=True, cname="ghs.googlehosted.com")
    await _add(tenant_a, host)
    assert (await _check(tenant_a, owner=True))["status"] == "pending_mapping"

    await _add(tenant_b, host)
    contested = await _check(tenant_b, owner=True)
    assert contested["status"] == "pending_dns"
    assert contested["error"]["code"] == "host_bound_to_another_tenant"
    binding = await store.find_serving_binding(host)
    assert binding is not None and binding.tenant_id == tenant_a


async def test_same_tenant_second_merchant_cannot_take_the_host(db, world):
    tenant = await _tenant(db)
    await _merchant(tenant, "merchant-a")
    await _merchant(tenant, "merchant-b")
    host = _host()
    world.update(txt=True, cname="ghs.googlehosted.com")
    await _add(tenant, host, "merchant-a")
    assert (await _check(tenant, owner=True, merchant_id="merchant-a"))["status"] == "pending_mapping"
    await _add(tenant, host, "merchant-b")
    taken = await _check(tenant, owner=True, merchant_id="merchant-b")
    assert taken["error"]["code"] == "host_in_use"
    binding = await store.find_serving_binding(host)
    assert binding is not None and binding.merchant_id == "merchant-a"


async def test_remove_withdraws_the_host_and_asks_for_the_mapping_delete(db, world):
    tenant = await _tenant(db)
    await _merchant(tenant)
    host = _host()
    world.update(txt=True, cname="ghs.googlehosted.com", probe="live")
    await _add(tenant, host)
    await _check(tenant, owner=True)
    queue = await mcd.list_operator_queue(world["operator"])
    item = next(i for i in queue["items"] if i["host"] == host)
    await mcd.mark_mapped(item["id"], _request(), world["operator"])
    assert (await _check(tenant))["status"] == "live"

    removed = await mcd.remove_custom_domain(MERCHANT, _request(), SELLER, tenant)
    assert removed == {"removed": True, "host": host, "mapping_delete_needed": True}
    store.reset_ucp_host_cache()
    assert await store.find_serving_binding(host) is None
    assert any("domain-mappings delete" in line for _subject, lines in world["emails"] for line in lines)
    assert (await mcd.get_custom_domain(MERCHANT, SELLER, tenant))["domain"] is None
    assert (await _add(tenant, host))["status"] == "pending_dns"  # can be added again


async def test_apex_and_bad_hosts_are_refused_and_the_flag_hides_everything(db, world, monkeypatch):
    tenant = await _tenant(db)
    await _merchant(tenant)
    with pytest.raises(HTTPException) as apex:
        await _add(tenant, "merchant.example")
    assert apex.value.status_code == 422 and apex.value.detail["code"] == "apex_domain_needs_subdomain"
    with pytest.raises(HTTPException) as bad:
        await _add(tenant, "https://agent.merchant.example/path")
    assert bad.value.detail["code"] == "invalid_host"
    monkeypatch.setattr(settings, "custom_domains_enabled", False)
    for call in (
        mcd.get_custom_domain(MERCHANT, SELLER, tenant),
        _add(tenant, _host()),
        mcd.list_operator_queue(world["operator"]),
    ):
        with pytest.raises(HTTPException) as off:
            await call
        assert off.value.status_code == 404


async def test_status_task_lists_across_tenants_and_moves_mapped_domains_live(db, world):
    from core.tasks.custom_domain_tasks import _advance_custom_domains_async

    tenant = await _tenant(db)
    await _merchant(tenant)
    host = _host()
    world.update(txt=True, cname="ghs.googlehosted.com")
    await _add(tenant, host)
    await _check(tenant, owner=True)
    world["emails"].clear()

    # The operator created the mapping but never marked it: the task finds the live certificate anyway.
    world["probe"] = "live"
    outcome = await _advance_custom_domains_async()
    assert outcome["enabled"] is True and outcome["errors"] == 0 and outcome["live"] >= 1
    view = (await mcd.get_custom_domain(MERCHANT, SELLER, tenant))["domain"]
    assert view["status"] == "live"
    assert world["emails"] == []
