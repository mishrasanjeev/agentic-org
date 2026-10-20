# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Phase 4 exit gate against a live Grantex: mandates requested, verified, sealed and carried to Plural.

Skipped unless all of these are set (a local stack is fine; Plural is the fake transport):

    AGENTICORG_GRANTEX_LIVE_URL=http://localhost:55642           # issuer and API origin
    GRANTEX_COMMERCE_INTERNAL_TOKEN=<the AgenticOrg service token>
    AGENTICORG_GRANTEX_LIVE_TENANT=<an AgenticOrg tenant UUID in COMMERCE_C6Z_AUTHORITY_SERVICE_TENANTS>
    AGENTICORG_GRANTEX_LIVE_MANDATE_TENANT=<the Grantex commerce tenant, also allow-listed>
    AGENTICORG_GRANTEX_LIVE_MANDATE_MERCHANT=<the Grantex merchant the passports name>
    AGENTICORG_GRANTEX_LIVE_PASSPORT_CMD=<a command printing {"passport_jwt": ...} for
        "<presence> <max_amount> [<credential_id> <cose_public_key_b64url>]">
    AGENTICORG_GRANTEX_LIVE_FIDO_RP_ID / AGENTICORG_GRANTEX_LIVE_FIDO_ORIGIN=<Grantex FIDO_RP_ID / FIDO_ORIGIN>
    AGENTICORG_DB_URL=<the disposable Postgres, migrated to head>

The test plays the buyer's platform: it holds the passkey (a P-256 key generated
here and registered for the buyer by the passport command) and signs the WebAuthn
assertion over the ``checkout_hash`` the checkout response names. AgenticOrg only
passes the assertion through. The runtime is strict: verified authority required,
the Grantex grant required, every decision posted.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import hashlib
import json
import os
import shlex
import subprocess
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

LIVE_URL = os.getenv("AGENTICORG_GRANTEX_LIVE_URL", "").rstrip("/")
TOKEN = os.getenv("GRANTEX_COMMERCE_INTERNAL_TOKEN", "")
TENANT = os.getenv("AGENTICORG_GRANTEX_LIVE_TENANT", "")
MANDATE_TENANT = os.getenv("AGENTICORG_GRANTEX_LIVE_MANDATE_TENANT", "")
MANDATE_MERCHANT = os.getenv("AGENTICORG_GRANTEX_LIVE_MANDATE_MERCHANT", "")
PASSPORT_CMD = os.getenv("AGENTICORG_GRANTEX_LIVE_PASSPORT_CMD", "")
RP_ID = os.getenv("AGENTICORG_GRANTEX_LIVE_FIDO_RP_ID", "")
ORIGIN = os.getenv("AGENTICORG_GRANTEX_LIVE_FIDO_ORIGIN", "")
DB_URL = os.getenv("AGENTICORG_DB_URL", "")

pytestmark = pytest.mark.skipif(
    not all((LIVE_URL, TOKEN, TENANT, MANDATE_TENANT, MANDATE_MERCHANT, PASSPORT_CMD, RP_ID, ORIGIN, DB_URL)),
    reason="set the AGENTICORG_GRANTEX_LIVE_* variables, GRANTEX_COMMERCE_INTERNAL_TOKEN and AGENTICORG_DB_URL",
)


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class Passkey:
    """The buyer's platform authenticator (synthetic, test-time key)."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = _b64u(os.urandom(16))
        self.counter = 0

    def cose(self) -> str:
        numbers = self.key.public_key().public_numbers()
        x, y = numbers.x.to_bytes(32, "big"), numbers.y.to_bytes(32, "big")
        # CBOR map {1: 2 (EC2), 3: -7 (ES256), -1: 1 (P-256), -2: x, -3: y} (RFC 9053 section 7.1.1)
        prefix = bytes([0xA5, 0x01, 0x02, 0x03, 0x26, 0x20, 0x01, 0x21, 0x58, 0x20])
        return _b64u(prefix + x + bytes([0x22, 0x58, 0x20]) + y)

    def assertion(self, challenge: str) -> dict:
        self.counter += 1
        client_data = json.dumps(
            {"type": "webauthn.get", "challenge": challenge, "origin": ORIGIN, "crossOrigin": False}
        ).encode()
        authenticator = hashlib.sha256(RP_ID.encode()).digest() + bytes([0x05]) + self.counter.to_bytes(4, "big")
        signature = self.key.sign(authenticator + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": self.credential_id,
            "rawId": self.credential_id,
            "type": "public-key",
            "response": {
                "clientDataJSON": _b64u(client_data),
                "authenticatorData": _b64u(authenticator),
                "signature": _b64u(signature),
            },
            "clientExtensionResults": {},
        }


def _passport(presence: str, max_amount: int, passkey: Passkey | None = None) -> str:
    args = [presence, str(max_amount)]
    if passkey is not None:
        args += [passkey.credential_id, passkey.cose()]
    # The operator-configured live-stack helper (test-only environment variable), never request input.
    result = subprocess.run(  # noqa: S603
        [*shlex.split(PASSPORT_CMD), *args], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout)["passport_jwt"]


def _client():
    from core.commerce.grantex_service import GrantexServiceClient
    from core.commerce.oacp_grantex_trust import OacpGrantexArtifactVerifier, OacpGrantexTrustConfig

    def plain() -> httpx.AsyncBaseTransport:
        # A local stack listens on loopback, which the public egress transport refuses by design.
        return httpx.AsyncHTTPTransport()

    verifier = OacpGrantexArtifactVerifier(
        OacpGrantexTrustConfig(
            issuer=LIVE_URL,
            jwks_uri=f"{LIVE_URL}/.well-known/jwks.json",
            status_list_origins=frozenset({LIVE_URL}),
            allow_local_endpoints=True,
        ),
        transport_factory=plain,
    )
    return GrantexServiceClient(
        base_url=LIVE_URL, token=TOKEN, verifier=verifier, transport_factory=plain, allow_local_endpoints=True
    )


@pytest.fixture(scope="module", autouse=True)
async def migrated_schema(tmp_path_factory):
    import sys

    env = {**os.environ, "AGENTICORG_MIGRATION_AUDIT_DIR": str(tmp_path_factory.mktemp("migration-audit"))}
    result = await asyncio.to_thread(
        subprocess.run, [sys.executable, "scripts/alembic_migrate.py"], capture_output=True, text=True, env=env
    )
    assert result.returncode == 0, result.stderr[-4000:]


@pytest.fixture()
async def db(monkeypatch):
    from core import config, database
    from core.commerce.oacp_grantex_trust import reset_oacp_trust_caches

    engine = create_async_engine(DB_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", factory)
    monkeypatch.setattr(database, "current_session_factory", lambda: factory)
    monkeypatch.setattr(config, "arbiter_require_verified_authority_enabled", lambda env=None: True)
    monkeypatch.setattr(config, "arbiter_require_grantex_grant_enabled", lambda env=None: True)
    monkeypatch.setattr(config, "arbiter_grantex_post_enabled", lambda env=None: True)
    reset_oacp_trust_caches()
    try:
        yield engine
    finally:
        reset_oacp_trust_caches()
        await engine.dispose()


@pytest.fixture()
def plural():
    from core.commerce import executors
    from core.commerce.executors.plural import PluralExecutor
    from tests.plural_fakes import FakePlural, credentials

    fake = FakePlural()
    executor = PluralExecutor(credentials=credentials, transport_factory=fake.factory())
    executors.register_executor(executor)
    try:
        yield fake
    finally:
        executors.unregister_executor(executor.name)


async def _rows(engine, sql: str, **params) -> list[dict]:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT set_config('agenticorg.tenant_id', :t, false)"), {"t": TENANT})
        return [dict(r) for r in (await conn.execute(text(sql), params)).mappings().all()]


def _runtime(config=None):
    from tests import ucp_txn_fixtures as uf

    settings = {"tenant_id": MANDATE_TENANT, "merchant_id": MANDATE_MERCHANT}
    resolved = uf.merchant(TENANT, executor="plural_pine", grantex_mandates=settings)
    runtime = uf.runtime(TENANT, resolved=resolved, config=config)
    return dataclasses.replace(runtime, grantex_client=_client, mandates_enabled=lambda: True)


async def _checkout(runtime) -> dict:
    from core.commerce.ucp import handlers
    from tests import ucp_txn_fixtures as uf

    created = await handlers.run(
        "create_checkout", runtime, uf.request(TENANT, uf.create_body(fulfillment=uf.SHIPPING))
    )
    assert created.status == 201 and created.body["status"] == "ready_for_complete", created.body
    return created.body


async def _complete(runtime, session_id: str, request: dict):
    from core.commerce.ucp import handlers
    from tests import ucp_txn_fixtures as uf

    body = {"payment": uf.token_payment(), "ai.agenticorg.commerce": {"mandate_request": request}}
    return await handlers.run(
        "complete_checkout", runtime, uf.request(TENANT, body, key=f"live-{uuid.uuid4().hex}"), session_id
    )


@pytest.mark.asyncio
async def test_live_human_present_purchase_seals_the_three_mandate_hashes(db, plural):
    from core.commerce import oacp_mandates

    runtime = _runtime()
    session = await _checkout(runtime)
    named = session["ai.agenticorg.commerce"]["mandate_checkout"]
    assert named["checkout_hash"] == oacp_mandates.checkout_hash(named["checkout"])
    passkey = Passkey()
    passport = await asyncio.to_thread(_passport, "human_present", 100000, passkey)
    done = await _complete(
        runtime,
        session["id"],
        {
            "passport_jwt": passport,
            "presence": "human_present",
            "description": "A tote bag for the office, delivered to Pune",
            "webauthn_assertion": passkey.assertion(named["checkout_hash"]),
        },
    )
    assert done.body["status"] == "completed", json.dumps(done.body)[:3000]

    [decision] = await _rows(
        db,
        "SELECT * FROM commerce_arbiter_decisions WHERE tenant_id = :t AND decision_id = "
        "(SELECT decision_id FROM commerce_checkout_sessions WHERE id = :s)",
        t=TENANT,
        s=session["id"],
    )
    evidence = decision["evidence"]
    hashes = evidence["mandate_hashes"]
    assert set(hashes) == {"intent", "cart", "payment"} and all(len(v) == 64 for v in hashes.values())
    assert evidence["buyer_authority_source"] == "server_verified" and evidence["presence"] == "human_present"
    assert decision["grantex_grant_jti"] and decision["consumed_at"] is not None

    assert evidence["mandate_ref"] == evidence["mandate_jtis"]["payment"]
    assert evidence["mandate_hash"] == hashes["payment"]

    ledger = await _rows(
        db,
        "SELECT event_type, details FROM audit_log WHERE tenant_id = :t AND resource_type = 'commerce_ledger' "
        "AND resource_id IN (:s, :d) ORDER BY created_at",
        t=TENANT,
        s=session["id"],
        d=decision["decision_id"],
    )
    events = {row["event_type"] for row in ledger}
    assert {
        "commerce.ucp.checkout.mandates",
        "commerce.arbiter.decision",
        "commerce.arbiter.decision.countersigned",
    } <= events
    sealed = json.dumps([row["details"] for row in ledger])
    assert all(value in sealed for value in hashes.values())
    metadata = plural.bodies("/pay/v1/orders")[0]["purchase_details"]["merchant_metadata"]
    assert {k: metadata[f"oacp_{k}_mandate_hash"] for k in hashes} == hashes


@pytest.mark.asyncio
async def test_live_not_present_purchase_above_caps_is_refused_with_the_rule_sealed(db, plural):
    from tests import ucp_txn_fixtures as uf

    config = uf.merchant_config(TENANT)
    config["provider_policy"]["arbiter"]["human_not_present"] = {"max_order_amount_minor_units": 1000}
    runtime = _runtime(config=config)
    session = await _checkout(runtime)
    passport = await asyncio.to_thread(_passport, "human_not_present", 100000)
    refused = await _complete(
        runtime,
        session["id"],
        {"passport_jwt": passport, "presence": "human_not_present", "description": "Reorder the office tote bag"},
    )
    vendor = refused.body["ai.agenticorg.commerce"]
    assert vendor["mandates"]["status"] == "verified", vendor
    assert vendor["decision"]["failing_rule"] == "human_not_present_limits"
    assert vendor["grantex_countersignature"]["status"] == "recorded"
    assert plural.requests == []
    decisions = await _rows(
        db,
        "SELECT outcome, failing_rule, rule_results FROM commerce_arbiter_decisions WHERE tenant_id = :t "
        "AND decision_id = :d",
        t=TENANT,
        d=vendor["decision"]["decision_id"],
    )
    assert [(d["outcome"], d["failing_rule"]) for d in decisions] == [("refuse", "human_not_present_limits")]


@pytest.mark.asyncio
async def test_live_server_verify_agrees_with_the_local_verifier(db, plural):
    """Tests only: Grantex's /mandates/verify cross-checks a mandate the local verifier accepted."""
    from core.commerce import mandates_client as mc
    from core.commerce import oacp_mandates

    client = _client()
    passport = await asyncio.to_thread(_passport, "human_not_present", 100000)
    checkout = {
        "id": f"chk_live_{uuid.uuid4().hex[:12]}",
        "revision": 1,
        "currency": "INR",
        "merchant": {"id": MANDATE_MERCHANT},
        "line_items": [{"id": "li_1", "item": {"id": "TOTE-1", "price": 2500}, "quantity": 1}],
        "totals": [{"type": "total", "amount": 2500}],
    }
    issued = await mc.request_mandate_chain(
        mc.MandateChainRequest(
            tenant_id=MANDATE_TENANT,
            passport_jwt=passport,
            description="One tote bag",
            max_amount_minor_units=5000,
            currency="INR",
            presence="human_not_present",
            checkout=checkout,
            rail="plural",
            token_binding={"type": "plural_pine_token", "id": "sha256:" + "a" * 64},
        ),
        client=client,
    )
    now = datetime.now(UTC)
    jwks, reader, local = oacp_mandates.trust_sources(client.verifier, now)
    chain = await oacp_mandates.verify_mandate_chain(
        {k: v.mandate for k, v in issued.items()},
        issuer=client.issuer,
        jwks=jwks,
        now=now,
        expected=oacp_mandates.MandateExpectation(
            tenant_id=MANDATE_TENANT,
            merchant_id=MANDATE_MERCHANT,
            checkout_hash=oacp_mandates.checkout_hash(checkout),
            amount_minor_units=2500,
            currency="INR",
        ),
        status_reader=reader,
        allow_local_endpoints=local,
    )
    assert chain.valid, chain
    for kind, item in issued.items():
        response = await client.post_json(
            "/v1/commerce/oacp/mandates/verify",
            {
                "tenant_id": MANDATE_TENANT,
                "mandate": item.mandate,
                "expected_type": kind,
                "expected_merchant_id": MANDATE_MERCHANT,
            },
        )
        assert response.status_code == 200 and response.body["valid"] is True, response.body
        assert response.body["mandate_hash"] == chain.hashes[kind]
    # Revoking the intent cascades; the local verifier then refuses the chain from the live status list.
    revoked = await client.post_json(f"/v1/commerce/oacp/mandates/{issued['intent'].jti}/revoke", {})
    assert revoked.status_code in (200, 201), revoked.body
    from core.commerce.oacp_grantex_trust import reset_oacp_trust_caches

    reset_oacp_trust_caches()
    jwks, reader, local = oacp_mandates.trust_sources(client.verifier, datetime.now(UTC))
    again = await oacp_mandates.verify_mandate_chain(
        {k: v.mandate for k, v in issued.items()},
        issuer=client.issuer,
        jwks=jwks,
        now=datetime.now(UTC),
        expected=oacp_mandates.MandateExpectation(
            tenant_id=MANDATE_TENANT,
            merchant_id=MANDATE_MERCHANT,
            checkout_hash=oacp_mandates.checkout_hash(checkout),
            amount_minor_units=2500,
            currency="INR",
        ),
        status_reader=reader,
        allow_local_endpoints=local,
    )
    assert (again.valid, again.refusal_code) == (False, "mandate_revoked")
