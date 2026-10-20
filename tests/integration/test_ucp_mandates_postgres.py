# SPDX-License-Identifier: Apache-2.0
"""Phase 4 on a real Postgres: server-requested mandates (D1), the not-present rule set and the buyer agent.

Grantex is the fake in ``tests/grantex_service_fakes.py`` (mandates signed with
an ``oacp-`` key generated here, a Token Status List, decision grants) and
Plural is the MockTransport fake; both count every request. The runtime is
strict: caller-asserted authority is refused, an allow is consumable only with
a verified Grantex grant, and every decision is posted. Requires the disposable
database in ``AGENTICORG_DB_URL`` (migrated to head by the module fixture).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.commerce import executors, oacp_mandates
from core.commerce.executors.plural import PluralExecutor
from core.commerce.oacp_grantex_trust import reset_oacp_trust_caches
from core.commerce.order_refunds import refund_order
from core.commerce.ucp import handlers
from core.commerce.ucp.completion import platform_buyer_agent_id
from tests import ucp_txn_fixtures as uf
from tests.grantex_service_fakes import (
    MANDATE_MERCHANT,
    MANDATE_TENANT,
    PASSPORT_JWT,
    FakeGrantexService,
    webauthn_assertion,
)
from tests.plural_fakes import FakePlural, credentials

GRANTEX_MANDATES = {"tenant_id": MANDATE_TENANT, "merchant_id": MANDATE_MERCHANT}


@pytest.fixture(scope="module", autouse=True)
async def migrated_schema(tmp_path_factory):
    if not os.getenv("AGENTICORG_DB_URL"):
        pytest.skip("Requires the disposable Docker Postgres database")
    env = {**os.environ, "AGENTICORG_MIGRATION_AUDIT_DIR": str(tmp_path_factory.mktemp("migration-audit"))}
    result = await asyncio.to_thread(
        subprocess.run, [sys.executable, "scripts/alembic_migrate.py"], capture_output=True, text=True, env=env
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
def strict(monkeypatch):
    """A strict runtime: verified authority required, the Grantex grant required, every decision posted."""
    from core import config

    monkeypatch.setattr(config, "arbiter_require_verified_authority_enabled", lambda env=None: True)
    monkeypatch.setattr(config, "arbiter_require_grantex_grant_enabled", lambda env=None: True)
    monkeypatch.setattr(config, "arbiter_grantex_post_enabled", lambda env=None: True)
    reset_oacp_trust_caches()
    yield
    reset_oacp_trust_caches()


@pytest.fixture()
def plural():
    fake = FakePlural()
    executor = PluralExecutor(credentials=credentials, transport_factory=fake.factory())
    executors.register_executor(executor)
    try:
        yield fake
    finally:
        executors.unregister_executor(executor.name)


async def _rows(engine, sql: str, *, tenant: str | None = None, **params) -> list[dict]:
    async with engine.connect() as conn:
        if tenant is not None:
            await conn.execute(text("SELECT set_config('agenticorg.tenant_id', :t, false)"), {"t": tenant})
        return [dict(r) for r in (await conn.execute(text(sql), params)).mappings().all()]


async def _ledger(engine, tenant: str) -> list[dict]:
    return await _rows(
        engine,
        "SELECT event_type, details FROM audit_log WHERE tenant_id = :t AND resource_type = 'commerce_ledger' "
        "ORDER BY created_at, id",
        tenant=tenant,
        t=tenant,
    )


def _runtime(tenant: str, grantex: FakeGrantexService, *, config=None, profile=None) -> handlers.UcpRuntime:
    resolved = uf.merchant(tenant, executor="plural_pine", grantex_mandates=GRANTEX_MANDATES)
    runtime = uf.runtime(tenant, resolved=resolved, config=config, profile=profile)
    return dataclasses.replace(runtime, grantex_client=grantex.client, mandates_enabled=lambda: True)


async def _session(runtime, tenant: str) -> dict:
    created = await handlers.run(
        "create_checkout", runtime, uf.request(tenant, uf.create_body(fulfillment=uf.SHIPPING))
    )
    assert created.status == 201 and created.body["status"] == "ready_for_complete", created.body
    return created.body


def _mandate_body(presence: str, **extra) -> dict:
    request = {"passport_jwt": PASSPORT_JWT, "presence": presence, "description": "A tote bag for the office"}
    if presence == "human_present":
        request["webauthn_assertion"] = webauthn_assertion()
    request.update(extra)
    return {"payment": uf.token_payment(), "ai.agenticorg.commerce": {"mandate_request": request}}


async def _complete(runtime, tenant: str, session_id: str, key: str, body: dict):
    return await handlers.run("complete_checkout", runtime, uf.request(tenant, body, key=key), session_id)


async def _decisions(engine, tenant: str) -> list[dict]:
    return await _rows(
        engine,
        "SELECT * FROM commerce_arbiter_decisions WHERE tenant_id = :t ORDER BY decided_at",
        tenant=tenant,
        t=tenant,
    )


@pytest.mark.asyncio
async def test_human_present_purchase_runs_on_verified_mandates_and_carries_their_hashes(db, strict, plural):
    grantex = FakeGrantexService()
    tenant = str(uuid.uuid4())
    runtime = _runtime(tenant, grantex)
    session = await _session(runtime, tenant)
    body = _mandate_body("human_present")
    done = await _complete(runtime, tenant, session["id"], "mandate-hp", body)
    assert done.body["status"] == "completed", done.body

    issued = dict(grantex.mandates)
    hashes = {kind: oacp_mandates.mandate_hash(token) for kind, token in issued.items()}
    assert set(hashes) == {"intent", "cart", "payment"}
    # The buyer's assertion went to Grantex exactly as the platform sent it.
    assert grantex.assertions == [body["ai.agenticorg.commerce"]["mandate_request"]["webauthn_assertion"]]

    [decision] = await _decisions(db, tenant)
    assert decision["outcome"] == "allow" and decision["consumed_at"] is not None
    assert decision["grantex_grant_jti"], "strict runtime consumed only after the verified countersignature"
    evidence = decision["evidence"]
    assert evidence["buyer_authority_source"] == "server_verified"
    assert evidence["mandate_hashes"] == hashes
    assert evidence["mandate_ref"] == evidence["mandate_jtis"]["payment"]
    assert evidence["mandate_hash"] == hashes["payment"]
    assert evidence["presence"] == "human_present"
    ref = oacp_mandates.parse_verification_ref(evidence["authority_verification_ref"])
    assert ref is not None and ref["mandate_hash"] == hashes["payment"]
    buyer = platform_buyer_agent_id(uf.PLATFORM_PROFILE)
    assert decision["buyer_agent_id"] == buyer

    posted = [b for path, b in grantex.posted if path == "/v1/commerce/oacp/arbiter-decisions"]
    assert posted[0]["buyer_agent_id"] == buyer
    assert posted[0]["evidence"]["mandate_hash"] == hashes["payment"]

    metadata = plural.bodies("/pay/v1/orders")[0]["purchase_details"]["merchant_metadata"]
    assert {k: metadata[f"oacp_{k}_mandate_hash"] for k in hashes} == hashes
    ledger = await _ledger(db, tenant)
    events = [row["event_type"] for row in ledger]
    assert "commerce.ucp.checkout.mandates" in events
    sealed = json.dumps([row["details"] for row in ledger])
    for value in hashes.values():
        assert value in sealed, (events, [r["details"] for r in ledger if r["event_type"].endswith("mandates")])
    operations = await _rows(
        db, "SELECT mandate_hashes FROM commerce_payment_operations WHERE tenant_id = :t", tenant=tenant, t=tenant
    )
    assert operations and all(o["mandate_hashes"] == hashes for o in operations)


@pytest.mark.asyncio
async def test_not_present_purchase_above_the_lower_cap_is_refused_with_the_rule_sealed(db, strict, plural):
    grantex = FakeGrantexService()
    tenant = str(uuid.uuid4())
    config = uf.merchant_config(tenant)
    config["provider_policy"]["arbiter"]["human_not_present"] = {"max_order_amount_minor_units": 1000}
    runtime = _runtime(tenant, grantex, config=config)
    session = await _session(runtime, tenant)
    refused = await _complete(runtime, tenant, session["id"], "mandate-np", _mandate_body("human_not_present"))
    assert refused.body["status"] == "ready_for_complete"
    vendor = refused.body["ai.agenticorg.commerce"]
    assert vendor["decision"]["failing_rule"] == "human_not_present_limits"
    assert vendor["mandates"]["status"] == "verified" and vendor["mandates"]["presence"] == "human_not_present"
    assert vendor["grantex_countersignature"]["status"] == "recorded"
    assert plural.requests == []
    [decision] = await _decisions(db, tenant)
    assert (decision["outcome"], decision["failing_rule"]) == ("refuse", "human_not_present_limits")
    [result] = [r for r in decision["rule_results"] if r["rule"] == "human_not_present_limits"]
    assert result["reason"] == "not_present_amount_cap_exceeded"
    sealed = [r for r in await _ledger(db, tenant) if r["event_type"] == "commerce.arbiter.decision"]
    assert sealed[-1]["details"]["decision"]["failing_rule"] == "human_not_present_limits"


@pytest.mark.asyncio
async def test_not_present_within_the_lower_cap_completes(db, strict, plural):
    grantex = FakeGrantexService()
    tenant = str(uuid.uuid4())
    config = uf.merchant_config(tenant)
    config["provider_policy"]["arbiter"]["human_not_present"] = {"max_order_amount_minor_units": 50000}
    runtime = _runtime(tenant, grantex, config=config)
    session = await _session(runtime, tenant)
    done = await _complete(runtime, tenant, session["id"], "mandate-np-ok", _mandate_body("human_not_present"))
    assert done.body["status"] == "completed", done.body


@pytest.mark.asyncio
async def test_caller_asserted_authority_is_refused_on_the_ucp_path_in_strict_runtimes(db, strict, plural):
    grantex = FakeGrantexService()
    tenant = str(uuid.uuid4())
    runtime = _runtime(tenant, grantex)
    session = await _session(runtime, tenant)
    refused = await _complete(runtime, tenant, session["id"], "asserted", uf.complete_body())
    assert refused.body["ai.agenticorg.commerce"]["decision"]["failing_rule"] == "buyer_authority_verified"
    assert plural.requests == []


@pytest.mark.parametrize(
    ("grantex_kwargs", "code"),
    [
        ({"mandate_refusals": {"intent": "passport_cap_exceeded"}}, "passport_cap_exceeded"),
        ({"payment_amount_skew": 1}, "mandate_chain_mismatch"),
        ({"mandate_statuses": {2: 1}}, "mandate_revoked"),
    ],
)
@pytest.mark.asyncio
async def test_unverified_mandates_never_authorise_a_purchase(db, strict, plural, grantex_kwargs, code):
    grantex = FakeGrantexService(**grantex_kwargs)
    tenant = str(uuid.uuid4())
    runtime = _runtime(tenant, grantex)
    session = await _session(runtime, tenant)
    refused = await _complete(runtime, tenant, session["id"], "unverified", _mandate_body("human_not_present"))
    vendor = refused.body["ai.agenticorg.commerce"]
    assert vendor["mandates"]["status"] == "refused" and vendor["mandates"]["code"] == code
    # No verified chain: no mandate evidence reaches the arbiter, so it refuses at the first rule.
    assert vendor["decision"]["failing_rule"] == "mandate_present_unexpired"
    assert vendor["decision"]["buyer_authority_source"] == "caller_asserted"
    assert plural.requests == []


@pytest.mark.asyncio
async def test_refund_decision_names_the_session_buyer_and_is_countersigned(db, strict, plural):
    grantex = FakeGrantexService()
    tenant = str(uuid.uuid4())
    config = uf.merchant_config(tenant)
    config["provider_policy"]["arbiter"]["human_not_present"] = {"max_order_amount_minor_units": 50000}
    runtime = _runtime(tenant, grantex, config=config)
    session = await _session(runtime, tenant)
    done = await _complete(runtime, tenant, session["id"], "mandate-rf", _mandate_body("human_not_present"))
    assert done.body["status"] == "completed", done.body
    refunded = await refund_order(
        tenant_id=tenant,
        order_id=done.body["order"]["id"],
        amount_minor_units=1000,
        reason="Damaged",
        now=datetime.now(UTC),
        grantex_client=grantex.client(),
    )
    assert refunded.status == "succeeded", refunded
    refund = [d for d in await _decisions(db, tenant) if d["action"] == "refund"][0]
    assert refund["buyer_agent_id"] == platform_buyer_agent_id(uf.PLATFORM_PROFILE)
    assert refund["grantex_grant_jti"] and refund["consumed_at"] is not None
    posted = [b for p, b in grantex.posted if p == "/v1/commerce/oacp/arbiter-decisions" and b["action"] == "refund"]
    assert posted and posted[0]["buyer_agent_id"] == refund["buyer_agent_id"]
