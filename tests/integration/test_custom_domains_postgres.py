# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
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
    state = {
        "txt": False,
        "cname": None,
        "probe": "certificate_pending",
        "emails": [],
        "routing_targets": [],
        "operator": str(uuid.uuid4()),
    }
    monkeypatch.setattr(settings, "custom_domains_enabled", True)
    monkeypatch.setattr(settings, "custom_domain_operator_tenant_id", state["operator"])
    monkeypatch.setattr(settings, "custom_domain_mapping_account", "mapper@platform.example")
    monkeypatch.setattr(settings, "custom_domain_cloud_run_project", "example-project")
    monkeypatch.setattr(settings, "custom_domain_cloud_run_automap_enabled", False)

    async def txt(_challenge):
        return state["txt"]

    async def routing(host, *, target=None):
        state["routing_targets"].append(target)
        observed = state["cname"]
        if observed is None:
            return False, None, "cname_missing"
        if observed != (target or cd.cname_target()):
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


async def _merchant(tenant: str, merchant_id: str = MERCHANT, seller: str = SELLER) -> None:
    from core.database import get_tenant_session
    from core.models.commerce_c6z_runtime import C6ZMerchantCommerceConfigRow

    async with get_tenant_session(uuid.UUID(tenant)) as session:
        session.add(
            C6ZMerchantCommerceConfigRow(
                config_id=merchant_config_id(tenant, merchant_id, seller),
                tenant_id=tenant,
                merchant_id=merchant_id,
                seller_agent_id=seller,
                merchant_display_name="Example Store",
                provider_policy={},
            )
        )


def _request(user: str = "owner@merchant.example") -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(user_sub=user, auth_mode="session"))


def _host() -> str:
    return f"agent-{uuid.uuid4().hex[:10]}.merchant.example"


async def _add(tenant: str, host: str, merchant_id: str = MERCHANT, seller: str | None = SELLER) -> dict:
    return await mcd.add_custom_domain(
        merchant_id, mcd.AddDomainRequest(host=host, seller_agent_id=seller), _request(), tenant
    )


async def _check(tenant: str, *, owner: bool = False, merchant_id: str = MERCHANT, seller: str | None = SELLER) -> dict:
    return await mcd.check_custom_domain(
        merchant_id,
        mcd.CheckDomainRequest(seller_agent_id=seller, search_console_owner_added=owner),
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


async def test_remove_withdraws_the_host_and_queues_the_mapping_delete(db, world, monkeypatch):
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

    world["emails"].clear()
    monkeypatch.setattr(settings, "custom_domain_operator_email", "")
    removed = await mcd.remove_custom_domain(MERCHANT, _request(), SELLER, tenant)
    assert removed == {"removed": True, "host": host, "mapping_delete_needed": True}
    store.reset_ucp_host_cache()
    assert await store.find_serving_binding(host) is None
    assert (await mcd.get_custom_domain(MERCHANT, SELLER, tenant))["domain"] is None

    # The mapping deletion is a durable queue item, whatever happens to the email.
    queue = await mcd.list_operator_queue(world["operator"])
    unmap = next(i for i in queue["items"] if i["host"] == host)
    assert unmap["status"] == "pending_unmap" and unmap["action"] == "delete_mapping"
    assert unmap["mapping_in_use"] is False
    assert unmap["commands"]["delete"].startswith("gcloud beta run domain-mappings delete")
    assert any("domain-mappings delete" in line for _subject, lines in world["emails"] for line in lines)
    with pytest.raises(HTTPException):  # an unmap item is not a mapping item
        await mcd.mark_mapped(unmap["id"], _request(), world["operator"])
    done = await mcd.mark_unmapped(unmap["id"], _request("operator@platform.example"), world["operator"])
    assert done["status"] == "removed"
    assert all(i["host"] != host for i in (await mcd.list_operator_queue(world["operator"]))["items"])
    assert "commerce.custom_domain.operator.unmapped" in await _ledger_events(db, tenant)
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


async def test_unmap_items_are_deleted_by_automap_or_closed_when_the_host_is_reused(db, world, monkeypatch):
    deleted: list[str] = []

    async def delete(host):
        deleted.append(host)
        return True

    monkeypatch.setattr(cd, "delete_domain_mapping", delete)
    tenant = await _tenant(db)
    await _merchant(tenant, "merchant-a")
    await _merchant(tenant, "merchant-b")
    world.update(txt=True, cname="ghs.googlehosted.com")
    reused, gone = _host(), _host()
    for host, merchant in ((reused, "merchant-a"), (gone, "merchant-b")):
        await _add(tenant, host, merchant)
        assert (await _check(tenant, owner=True, merchant_id=merchant))["status"] == "pending_mapping"
        await mcd.remove_custom_domain(merchant, _request(), SELLER, tenant)
    # merchant-a adds its host back before the operator deleted the mapping: it must be kept.
    await _add(tenant, reused, "merchant-a")
    queue = {i["host"]: i for i in (await mcd.list_operator_queue(world["operator"]))["items"]}
    assert queue[reused]["mapping_in_use"] is True and queue[gone]["mapping_in_use"] is False

    monkeypatch.setattr(settings, "custom_domain_cloud_run_automap_enabled", True)
    from core.tasks.custom_domain_tasks import _advance_custom_domains_async

    await _advance_custom_domains_async()
    remaining = {i["host"] for i in (await mcd.list_operator_queue(world["operator"]))["items"]}
    assert reused not in remaining and gone not in remaining
    assert gone in deleted and reused not in deleted


async def test_without_a_seller_the_scope_is_never_guessed(db, world):
    tenant = await _tenant(db)
    await _merchant(tenant, MERCHANT, "seller-a")
    await _merchant(tenant, MERCHANT, "seller-b")
    world.update(txt=True, cname="ghs.googlehosted.com")
    host_a, host_b = _host(), _host()
    await _add(tenant, host_a, seller="seller-a")
    await _add(tenant, host_b, seller="seller-b")
    for call in (
        mcd.get_custom_domain(MERCHANT, None, tenant),
        _check(tenant, seller=None),
        mcd.remove_custom_domain(MERCHANT, _request(), None, tenant),
    ):
        with pytest.raises(HTTPException) as ambiguous:
            await call
        assert ambiguous.value.status_code == 409
        assert ambiguous.value.detail["code"] == "seller_agent_id_required"
    assert (await mcd.get_custom_domain(MERCHANT, "seller-b", tenant))["domain"]["host"] == host_b
    # Both domains are untouched.
    assert (await mcd.get_custom_domain(MERCHANT, "seller-a", tenant))["domain"]["host"] == host_a


async def test_a_failed_domain_keeps_its_host_until_removed(db, world):
    tenant = await _tenant(db)
    await _merchant(tenant, "merchant-a")
    await _merchant(tenant, "merchant-b")
    host = _host()
    world.update(txt=True, cname="ghs.googlehosted.com")
    await _add(tenant, host, "merchant-a")
    queued = await _check(tenant, owner=True, merchant_id="merchant-a")
    await mcd.mark_failed(
        queued["id"], mcd.OperatorFailRequest(reason="Mapping refused"), _request(), world["operator"]
    )

    await _add(tenant, host, "merchant-b")
    taken = await _check(tenant, owner=True, merchant_id="merchant-b")
    assert taken["status"] == "pending_dns" and taken["error"]["code"] == "host_in_use"
    binding = await store.find_serving_binding(host)
    assert binding is not None and binding.merchant_id == "merchant-a"
    retried = await _check(tenant, owner=True, merchant_id="merchant-a")
    assert retried["status"] == "pending_mapping"


async def test_checks_use_the_target_the_domain_was_given(db, world, monkeypatch):
    tenant = await _tenant(db)
    await _merchant(tenant)
    world.update(txt=True, cname="ghs.googlehosted.com")
    added = await _add(tenant, _host())
    assert next(r for r in added["records"] if r["type"] == "CNAME")["value"] == "ghs.googlehosted.com."
    monkeypatch.setattr(settings, "custom_domain_cname_target", "new-target.platform.example")
    queued = await _check(tenant, owner=True)
    assert queued["status"] == "pending_mapping"
    assert world["routing_targets"][-1] == "ghs.googlehosted.com"


# --- Firebase Hosting (v6z93) ---------------------------------------------------------------------

FIREBASE_SITE = "merchant-domains-example"
FIREBASE_TARGET = f"{FIREBASE_SITE}.web.app"


@pytest.fixture()
def firebase(world, monkeypatch):
    """Firebase mode on, with a fake Hosting API that records what AgenticOrg asked of it."""
    hosting = {"domains": set(), "created": [], "deleted": [], "serving": False, "refuse": None}
    monkeypatch.setattr(settings, "custom_domain_firebase_enabled", True)
    monkeypatch.setattr(settings, "custom_domain_firebase_site", FIREBASE_SITE)

    async def create(host):
        hosting["created"].append(host)
        if hosting["refuse"]:
            return cd.FirebaseDomainState(exists=False, failed=True, message=hosting["refuse"])
        hosting["domains"].add(host)
        return cd.FirebaseDomainState(exists=True, ownership="OWNERSHIP_MISSING", cert_state="CERT_PREPARING")

    async def get(host):
        if host not in hosting["domains"]:
            return cd.FirebaseDomainState(exists=False)
        if hosting["serving"]:
            return cd.FirebaseDomainState(
                exists=True, host_active=True, ownership="OWNERSHIP_ACTIVE", cert_state="CERT_ACTIVE"
            )
        return cd.FirebaseDomainState(exists=True, ownership="OWNERSHIP_MISSING", cert_state="CERT_VALIDATING")

    async def delete(host):
        hosting["deleted"].append(host)
        hosting["domains"].discard(host)
        return True

    monkeypatch.setattr(cd, "create_firebase_domain", create)
    monkeypatch.setattr(cd, "get_firebase_domain", get)
    monkeypatch.setattr(cd, "delete_firebase_domain", delete)
    store.reset_firebase_host_cache()
    world.update(txt=True, cname=FIREBASE_TARGET, hosting=hosting)
    yield world
    store.reset_firebase_host_cache()


async def test_firebase_flow_needs_two_dns_records_and_no_operator(db, firebase):
    tenant = await _tenant(db)
    await _merchant(tenant)
    host = _host()
    hosting = firebase["hosting"]

    added = await _add(tenant, host)
    assert added["provider"] == "firebase_hosting"
    cname = [r["value"] for r in added["records"] if r["type"] == "CNAME"]
    assert cname == [f"{FIREBASE_TARGET}."]
    assert {r["type"] for r in added["records"]} == {"CNAME", "TXT"}
    assert added["search_console"]["required"] is False

    queued = await _check(tenant)  # no Search Console confirmation needed
    assert queued["status"] == "pending_mapping" and queued["error"] is None
    assert firebase["emails"] == []  # nothing for an operator to do
    queue = await mcd.list_operator_queue(firebase["operator"])
    assert all(item["host"] != host for item in queue["items"])

    connecting = await _check(tenant)
    assert hosting["created"] == [host]
    assert connecting["status"] == "pending_certificate"
    assert connecting["error"]["code"] == "certificate_pending"
    assert "OWNERSHIP_MISSING" in connecting["error"]["detail"]
    store.reset_firebase_host_cache()
    assert await store.firebase_domain_active(host) is True

    hosting["serving"] = True
    firebase["probe"] = "live"
    live = await _check(tenant)
    assert live["status"] == "live" and live["error"] is None
    assert firebase["routing_targets"][-1] == FIREBASE_TARGET
    events = await _ledger_events(db, tenant)
    assert events.count("commerce.custom_domain.status") >= 2


async def test_only_firebase_domains_are_trusted_as_forwarded_hosts(db, firebase, monkeypatch):
    import httpx

    from api.main import app

    tenant = await _tenant(db)
    await _merchant(tenant, "merchant-firebase")
    await _merchant(tenant, "merchant-mapping")
    firebase["hosting"]["serving"] = True
    firebase["probe"] = "live"
    via_firebase = _host()
    await _add(tenant, via_firebase, "merchant-firebase")
    await _check(tenant, merchant_id="merchant-firebase")
    await _check(tenant, merchant_id="merchant-firebase")
    assert (await _check(tenant, merchant_id="merchant-firebase"))["status"] == "live"

    # A domain on a Cloud Run mapping is a real Host, never a forwarded one.
    monkeypatch.setattr(settings, "custom_domain_firebase_enabled", False)
    via_mapping = _host()
    firebase["cname"] = "ghs.googlehosted.com"
    await _add(tenant, via_mapping, "merchant-mapping")
    assert (await _check(tenant, owner=True, merchant_id="merchant-mapping"))["status"] == "pending_mapping"
    monkeypatch.setattr(settings, "custom_domain_firebase_enabled", True)

    store.reset_firebase_host_cache()
    assert await store.firebase_domain_active(via_firebase) is True
    assert await store.firebase_domain_active(via_mapping) is False
    assert await store.firebase_domain_active("unknown.merchant.example") is False

    service_host = "agenticorg-api-example.a.run.app"

    async def ask(forwarded: str) -> dict:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=f"https://{service_host}") as client:
            response = await client.get("/.well-known/agenticorg-host", headers={"x-forwarded-host": forwarded})
        assert response.headers["cache-control"] == "no-store"
        return response.json()

    assert (await ask(via_firebase))["host"] == via_firebase
    assert (await ask(via_mapping))["host"] == service_host
    assert (await ask("unknown.merchant.example"))["host"] == service_host


async def test_removing_a_firebase_domain_disconnects_it_automatically(db, firebase):
    from core.tasks.custom_domain_tasks import _advance_custom_domains_async

    tenant = await _tenant(db)
    await _merchant(tenant)
    host = _host()
    firebase["hosting"]["serving"] = True
    firebase["probe"] = "live"
    await _add(tenant, host)
    await _check(tenant)
    await _check(tenant)
    assert (await _check(tenant))["status"] == "live"

    removed = await mcd.remove_custom_domain(MERCHANT, _request(), SELLER, tenant)
    assert removed["mapping_delete_needed"] is True
    queue = await mcd.list_operator_queue(firebase["operator"])
    assert all(item["host"] != host for item in queue["items"])  # nothing for the operator
    assert firebase["emails"] == []

    await _advance_custom_domains_async()
    assert host in firebase["hosting"]["deleted"]
    store.reset_firebase_host_cache()
    assert await store.firebase_domain_active(host) is False
    assert "commerce.custom_domain.status" in await _ledger_events(db, tenant)


async def test_a_refused_firebase_domain_fails_and_reaches_the_operator(db, firebase):
    tenant = await _tenant(db)
    await _merchant(tenant)
    host = _host()
    firebase["hosting"]["refuse"] = "The domain is connected to another Firebase project. Remove it there first."
    await _add(tenant, host)
    await _check(tenant)
    failed = await _check(tenant)
    assert failed["status"] == "failed"
    assert "another Firebase project" in failed["error"]["detail"]

    queue = await mcd.list_operator_queue(firebase["operator"])
    item = next(i for i in queue["items"] if i["host"] == host)
    assert item["provider"] == "firebase_hosting" and item["commands"] is None
    with pytest.raises(HTTPException) as automatic:
        await mcd.mark_mapped(item["id"], _request(), firebase["operator"])
    assert automatic.value.status_code == 409

    firebase["hosting"]["refuse"] = None
    retried = await _check(tenant)  # the retry connects the domain again at once
    assert retried["status"] == "pending_certificate"
    assert firebase["hosting"]["created"] == [host, host]
