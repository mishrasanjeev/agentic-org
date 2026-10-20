# SPDX-License-Identifier: Apache-2.0
"""scripts/seed_oacp_demo_merchant.py on a real Postgres: seed, converge, serve, tear down.

Requires the disposable database in ``AGENTICORG_DB_URL`` (migrated to head by
the module fixture). ``AGENTICORG_RLS_TEST_DB_URL`` (the NOBYPASSRLS
``agenticorg_app`` role on the same database), when set, is used for every
session the seed opens, proving it works under row-level security.

Grantex is an in-process issuer by default: it signs one ES256 artifact per C6Z
family with a key generated at test time and serves the JWKS and a Token
Status List, so the cache intake runs the real verifier in strict mode. With
``AGENTICORG_GRANTEX_DEMO_LIVE_URL`` and ``GRANTEX_COMMERCE_INTERNAL_TOKEN``
set, the authority request goes to that running Grantex instead (it must list
the demo tenant in ``COMMERCE_C6Z_AUTHORITY_SERVICE_TENANTS`` and accept the
synthetic demo source).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.commerce.oacp_demo_merchant import (
    DEMO_ADMIN_USER_ID,
    DEMO_MERCHANT_ID,
    DEMO_SELLER_AGENT_ID,
    DEMO_TENANT_ID,
    TEARDOWN_TABLES,
)
from tests.oacp_signing import (
    ISSUER,
    JWKS_URI,
    STATUS_LIST_URI,
    EcSigningKey,
    FakeGrantex,
    RsaSigningKey,
    build_envelope,
    status_list_token,
)

LIVE_URL = os.getenv("AGENTICORG_GRANTEX_DEMO_LIVE_URL", "").strip().rstrip("/")
LIVE = bool(LIVE_URL and os.getenv("GRANTEX_COMMERCE_INTERNAL_TOKEN"))
HOST = "nilgiritea.agenticorg.ai"
FAMILY_TYPES: tuple[tuple[str, str, int], ...] = (
    ("merchant_profile", "merchant_capability", 24 * 3600),
    ("seller_agent_card", "seller_agent_capability", 6 * 3600),
    ("connector_evidence", "protocol_adapter", 15 * 60),
    ("catalog_snapshot", "catalog_snapshot", 6 * 3600),
    ("offer_price_snapshot", "price", 5 * 60),
    ("inventory_snapshot", "inventory", 60),
    ("policy_scope", "policy", 6 * 3600),
    ("public_discovery_state", "public_discovery", 15 * 60),
    ("mandate_capability", "mandate_capability", 2 * 60),
    ("protocol_adapter", "protocol_adapter", 24 * 3600),
    ("authority_request_status", "protocol_adapter", 15 * 60),
)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


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


@dataclass
class FakeDemoGrantex:
    """Signs the C6Z artifact set the way Grantex does, for the synthetic source."""

    mode: str = "issue"
    key: EcSigningKey = field(default_factory=lambda: EcSigningKey(kid="oacp-20261021-demo-test"))
    status_key: RsaSigningKey = field(default_factory=lambda: RsaSigningKey(kid="platform-test-1"))
    requests: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.grantex = FakeGrantex(jwks={"keys": [self.key.public_jwk(), self.status_key.public_jwk()]})
        epoch = int(datetime.now(UTC).timestamp())
        self.grantex.status_lists[STATUS_LIST_URI] = status_list_token(
            self.status_key, uri=STATUS_LIST_URI, statuses={}, iat=epoch - 60, exp=epoch + 3600
        )

    async def send(self, *, payload: Mapping[str, Any], env: Any = None, transport: Any = None) -> dict[str, Any]:
        self.requests.append(json.loads(json.dumps(payload, default=list)))
        url = "https://grantex.example/v1/commerce/oacp/c6z/authority-requests"
        if self.mode == "forbidden":
            response = httpx.Response(
                403,
                json={"error": {"code": "service_tenant_not_allowed", "message": "not allowed"}},
                request=httpx.Request("POST", url),
            )
            raise httpx.HTTPStatusError("403", request=response.request, response=response)
        if self.mode == "unsupported":
            response = httpx.Response(
                422,
                json={"status": "rejected", "refusal_code": "unsupported_or_enabling_authority_request"},
                request=httpx.Request("POST", url),
            )
            raise httpx.HTTPStatusError("422", request=response.request, response=response)
        request, evidence = payload["request"], payload["connector_evidence"]
        assert request["connector_choice"] == evidence["source_system"] == "synthetic_demo"
        assert request["synthetic_data"] is True and evidence["synthetic_data"] is True
        opted_in = request.get("public_discovery_requested") is True
        now = datetime.now(UTC).replace(microsecond=0)
        artifacts = []
        for index, (family, artifact_type, ttl) in enumerate(FAMILY_TYPES):
            enabled = family == "public_discovery_state" and opted_in
            artifact_payload: dict[str, Any] = {
                "artifact_family": family,
                "tenant_id": request["tenant_id"],
                "merchant_id": request["merchant_id"],
                "seller_agent_id": request["seller_agent_id"],
                "source_system": evidence["source_system"],
                "source_evidence_ref": evidence["source_evidence_ref"],
                "source_observed_at": evidence["source_observed_at"],
                "source_lineage": {
                    "source_system": evidence["source_system"],
                    "synthetic_data": True,
                    "merchant_system_source_of_record": False,
                },
                "allowed_to_execute": False,
                "no_payment_execution": True,
                "no_public_discovery_enablement": not enabled,
                "non_authoritative_for_transaction": True,
            }
            if family == "public_discovery_state":
                artifact_payload["public_discovery_state"] = "enabled" if enabled else "disabled"
                artifact_payload["public_discovery_publication_allowed"] = enabled
                if enabled:
                    artifact_payload["serving_host"] = request["serving_host"]
                    artifact_payload["namespace_attestation_ref"] = request["namespace_attestation_ref"]
            envelope = build_envelope(
                artifact_payload,
                key=self.key,
                issued_at=_iso(now),
                expires_at=_iso(now + timedelta(seconds=ttl)),
                artifact_id=f"oacp_art_{uuid.uuid4().hex[:26].upper()}",
                artifact_type=artifact_type,
                status_idx=index,
            )
            artifacts.append(
                {
                    "artifact_family": family,
                    "artifact_type": artifact_type,
                    "envelope": envelope,
                    "payload": artifact_payload,
                }
            )
        return {
            "status": "artifact_issuance_ready",
            "artifacts": artifacts,
            "no_public_discovery_enablement": not opted_in,
        }


async def _live_send(*, payload: Mapping[str, Any], env: Any = None, transport: Any = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as client:  # loopback: the public egress transport refuses it
        response = await client.post(
            f"{LIVE_URL}/v1/commerce/oacp/c6z/authority-requests",
            json=payload,
            headers={"Authorization": f"Bearer {os.environ['GRANTEX_COMMERCE_INTERNAL_TOKEN']}"},
        )
        response.raise_for_status()
        return response.json()


@pytest.fixture()
def seed(monkeypatch, request):
    """The seed script wired to the test database, the flags and a Grantex (fake or live)."""
    from api.v1 import commerce_runtime
    from core import database
    from core.commerce.oacp_grantex_trust import (
        OacpGrantexArtifactVerifier,
        OacpGrantexTrustConfig,
        reset_oacp_trust_caches,
    )
    from core.commerce.ucp.store import reset_ucp_host_cache
    from core.config import settings
    from scripts import seed_oacp_demo_merchant as script

    reset_oacp_trust_caches()
    reset_ucp_host_cache()
    url = os.environ.get("AGENTICORG_RLS_TEST_DB_URL") or os.environ["AGENTICORG_DB_URL"]
    engine = create_async_engine(url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", factory)
    monkeypatch.setattr(database, "current_session_factory", lambda: factory)
    monkeypatch.setattr(settings, "oacp_demo_merchant_enabled", True)
    monkeypatch.setattr(settings, "ucp_profile_enabled", True)
    monkeypatch.setattr(commerce_runtime, "oacp_verify_strict_enabled", lambda: True)
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_CONNECTORS", "0")
    fake = FakeDemoGrantex()
    if LIVE:
        monkeypatch.setattr(commerce_runtime, "send_grantex_authority_request", _live_send)
        config = OacpGrantexTrustConfig(
            issuer=LIVE_URL,
            jwks_uri=f"{LIVE_URL}/.well-known/jwks.json",
            status_list_origins=frozenset({LIVE_URL}),
            allow_local_endpoints=True,
        )
        transport_factory: Any = httpx.AsyncHTTPTransport
        monkeypatch.setenv("GRANTEX_COMMERCE_BASE_URL", LIVE_URL)
    else:
        monkeypatch.setattr(commerce_runtime, "send_grantex_authority_request", fake.send)
        config = OacpGrantexTrustConfig(issuer=ISSUER, jwks_uri=JWKS_URI, status_list_origins=frozenset({ISSUER}))
        transport_factory = fake.grantex.transport_factory()
        monkeypatch.setenv("GRANTEX_COMMERCE_BASE_URL", "https://grantex.example")
        monkeypatch.setenv("GRANTEX_COMMERCE_INTERNAL_TOKEN", "placeholder-service-token")
    monkeypatch.setattr(
        commerce_runtime,
        "_oacp_artifact_verifier",
        lambda: OacpGrantexArtifactVerifier(config, transport_factory=transport_factory),
    )
    environ = {
        "AGENTICORG_DB_URL": url,
        "GRANTEX_COMMERCE_BASE_URL": os.environ["GRANTEX_COMMERCE_BASE_URL"],
        "GRANTEX_COMMERCE_INTERNAL_TOKEN": os.environ["GRANTEX_COMMERCE_INTERNAL_TOKEN"],
    }

    class Runner:
        grantex = fake
        module = script

        def __call__(self, *argv: str, **extra_env: str) -> int:
            reset_ucp_host_cache()
            return script.main(list(argv), environ={**environ, **extra_env})

    runner = Runner()
    runner("--teardown", "--apply")  # start from no demo commerce data (a no-op on a fresh database)
    yield runner
    asyncio.run(engine.dispose())


def _scalar(sql: str, **params: Any) -> Any:
    async def _run() -> Any:
        engine = create_async_engine(os.environ["AGENTICORG_DB_URL"], poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                return (await conn.execute(text(sql), params)).scalar()
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _count(table: str) -> int:
    return int(_scalar(f"SELECT count(*) FROM {table} WHERE tenant_id::text = :t", t=DEMO_TENANT_ID))  # noqa: S608


def _events(event: str) -> int:
    return int(
        _scalar(
            "SELECT count(*) FROM audit_log WHERE tenant_id::text = :t AND event_type = :e", t=DEMO_TENANT_ID, e=event
        )
    )


def _snapshot() -> dict[str, int]:
    counts = {table: _count(table) for table in TEARDOWN_TABLES}
    counts["tenants"] = int(_scalar("SELECT count(*) FROM tenants WHERE id::text = :t", t=DEMO_TENANT_ID))
    counts["users"] = int(_scalar("SELECT count(*) FROM users WHERE tenant_id::text = :t", t=DEMO_TENANT_ID))
    counts["audit_log"] = int(_scalar("SELECT count(*) FROM audit_log WHERE tenant_id::text = :t", t=DEMO_TENANT_ID))
    return counts


def _summary(out: str) -> dict[str, Any]:
    """The script's one-line JSON summary (other stdout lines are structured logs)."""
    lines = [line for line in out.splitlines() if line.startswith('{"summary"')]
    assert lines, out[-2000:]
    return json.loads(lines[-1])["summary"]


def test_dry_run_writes_nothing(seed) -> None:
    before = _snapshot()
    assert seed() == 0
    assert _snapshot() == before


def test_seed_converges_serves_and_tears_down(seed, capsys) -> None:
    from api.v1 import commerce_acp, commerce_runtime
    from core.commerce.ucp.catalog import verified_catalog_source
    from core.commerce.ucp.store import load_catalog_inputs

    assert seed("--apply") == 0
    first = _summary(capsys.readouterr().out)
    assert first["tenant_id"] == DEMO_TENANT_ID and first["merchant_id"] == DEMO_MERCHANT_ID
    assert first["artifacts_this_run"]["issued"] == 11
    assert (
        first["artifacts_this_run"]["verified"] == 11 and first["artifacts_this_run"]["verification_mode"] == "strict"
    )
    assert first["state"]["catalog_available_now"] is True and first["state"]["merchant_signing_key_kid"]
    assert first["enabled_in_this_job"]["payment_executor_for_demo_merchant"] is None
    assert (
        _scalar(
            "SELECT settings->'oacp_demo_merchant'->>'synthetic' FROM tenants WHERE id::text = :t", t=DEMO_TENANT_ID
        )
        == "true"
    )
    assert (
        _scalar("SELECT role || ':' || status FROM users WHERE id::text = :u", u=DEMO_ADMIN_USER_ID) == "admin:active"
    )
    assert (
        _scalar(
            "SELECT source_connectors->0->>'connector_type' FROM commerce_c6z_merchant_configs WHERE tenant_id = :t",
            t=DEMO_TENANT_ID,
        )
        == "synthetic_demo"
    )
    assert (
        _scalar(
            "SELECT jsonb_typeof(provider_policy->'ucp'->'links'->0) "
            "FROM commerce_c6z_merchant_configs WHERE tenant_id = :t",
            t=DEMO_TENANT_ID,
        )
        == "object"
    )
    assert (
        _scalar(
            "SELECT connector_choice FROM commerce_c6z_seller_onboarding_packets WHERE tenant_id = :t", t=DEMO_TENANT_ID
        )
        == "synthetic_demo"
    )
    assert (
        _scalar(
            "SELECT source_system || ':' || product_count "
            "FROM commerce_c6z_connector_evidence_records WHERE tenant_id = :t",
            t=DEMO_TENANT_ID,
        )
        == "synthetic_demo:10"
    )
    assert (
        _scalar(
            "SELECT count(*) FROM oacp_artifact_cache_records "
            "WHERE tenant_id = :t AND verifier_result_ref LIKE 'oacp-artifact-jws-v1;%'",
            t=DEMO_TENANT_ID,
        )
        == 11
    )
    for event in (
        "commerce.runtime.merchant_config.upsert",
        "commerce.runtime.demo_catalog.sync",
        "commerce.runtime.grantex.authority_request",
        "commerce.runtime.artifacts.cache",
    ):
        assert _events(event) >= 1, event

    # The surfaces read what was seeded: ACP feed, buyer Q&A, UCP catalog inputs.
    async def _surfaces() -> tuple[Any, dict[str, Any], Any]:
        feed = await commerce_acp.load_feed(
            tenant_id=DEMO_TENANT_ID,
            merchant_id=DEMO_MERCHANT_ID,
            seller_agent_id=DEMO_SELLER_AGENT_ID,
            now=datetime.now(UTC),
        )
        answer, _a, _r = await commerce_runtime._answer_buyer_question_for_scope(
            tenant_id=DEMO_TENANT_ID,
            merchant_id=DEMO_MERCHANT_ID,
            seller_agent_id=DEMO_SELLER_AGENT_ID,
            buyer_agent_id=None,
            question="Do you have masala chai?",
            action_intent="non_binding_preview",
            grantex_available=True,
            ledger_event="commerce.runtime.buyer_session.ask",
        )
        records, products = await load_catalog_inputs(
            tenant_id=DEMO_TENANT_ID, merchant_id=DEMO_MERCHANT_ID, seller_agent_id=DEMO_SELLER_AGENT_ID
        )
        return (
            feed,
            answer,
            verified_catalog_source(cache_records=records, products=products, now_iso=_iso(datetime.now(UTC))),
        )

    feed, answer, catalog = asyncio.run(_surfaces())
    assert len(feed.products) == 10 and len(feed.csv_rows) == 11 and feed.skipped_rows == 0
    assert answer["status"] == "answered" and "synthetic" in answer["source_label"]
    assert catalog.unavailable_reason is None and len(catalog.products) == 10

    # Converged: a rerun inside the artifacts' lifetime changes nothing and issues nothing.
    before = _snapshot()
    upserts = _events("commerce.runtime.merchant_config.upsert")
    requests = len(seed.grantex.requests)
    frozen = datetime.now(UTC)
    original_now = seed.module._now
    seed.module._now = lambda: frozen  # every artifact still usable at this instant
    try:
        assert seed("--apply") == 0
    finally:
        seed.module._now = original_now
    rerun = _summary(capsys.readouterr().out)
    assert rerun["actions"][:4] == [
        "tenant: unchanged",
        "admin user: present",
        "merchant config: unchanged",
        "ucp signing key: present",
    ]
    assert _snapshot() == before
    assert _events("commerce.runtime.merchant_config.upsert") == upserts
    if not LIVE:
        assert len(seed.grantex.requests) == requests

    # A refresh re-issues against the same evidence row (refreshed in place, never duplicated).
    assert seed("--apply", "--refresh-artifacts") == 0
    capsys.readouterr()
    assert _count("commerce_c6z_connector_evidence_records") == 1
    assert _count("commerce_c6z_merchant_configs") == 1 and _count("commerce_c6z_seller_onboarding_packets") == 1

    # The admin password comes only from the secret, is stored hashed and never printed.
    secret = "placeholder-" + uuid.uuid4().hex
    assert seed("--apply", **{seed.module.ADMIN_LOGIN_ENV: secret}) == 0
    out = capsys.readouterr().out
    assert secret not in out and "set from AGENTICORG_OACP_DEMO_ADMIN_PASSWORD" in out
    stored = _scalar("SELECT password_hash FROM users WHERE id::text = :u", u=DEMO_ADMIN_USER_ID)
    assert bcrypt.checkpw(secret.encode(), stored.encode())

    # Teardown: a dry run lists, --apply deletes the commerce rows and keeps the ledger.
    ledger_rows = _snapshot()["audit_log"]
    assert seed("--teardown") == 0
    listed = _summary(capsys.readouterr().out)["deleted_rows"]
    assert listed["commerce_c6z_merchant_configs"] == 1 and listed["oacp_artifact_cache_records"] >= 11
    assert _count("commerce_c6z_merchant_configs") == 1
    assert seed("--teardown", "--apply") == 0
    capsys.readouterr()
    assert all(_count(table) == 0 for table in TEARDOWN_TABLES)
    assert _snapshot()["audit_log"] == ledger_rows + 1
    assert _events("commerce.runtime.demo_merchant.teardown") >= 1
    assert _scalar("SELECT status FROM users WHERE id::text = :u", u=DEMO_ADMIN_USER_ID) == "inactive"


@pytest.mark.skipif(LIVE, reason="the in-process issuer emulates public-discovery issuance")
def test_serving_host_is_attested_published_and_served_through_the_app(seed, capsys, monkeypatch) -> None:
    """``nilgiritea.agenticorg.ai`` end to end through the real app and its middleware (Host preserved)."""
    from api.v1 import commerce_ucp_publishing
    from core.config import settings

    satisfied = {"value": False}

    async def _dns(_challenge: Mapping[str, Any]) -> bool:
        return satisfied["value"]

    monkeypatch.setattr(commerce_ucp_publishing, "dns_challenge_satisfied", _dns)
    assert seed("--apply") == 0
    capsys.readouterr()

    # Step 1: request the challenge and print the exact record; a rerun reprints the same one.
    assert seed("--apply", "--serving-host", HOST) == 0
    first = _summary(capsys.readouterr().out)
    record = first["dns_record_to_create"]
    assert record["name"] == f"_agenticorg-ucp.{HOST}" and record["type"] == "TXT"
    assert record["host_in_zone"] == "_agenticorg-ucp.nilgiritea" and record["zone"] == "agenticorg.ai"
    assert record["value"].startswith("agenticorg-ucp-verify=")
    assert seed("--apply", "--serving-host", HOST) == 0
    assert _summary(capsys.readouterr().out)["dns_record_to_create"] == record

    # Step 2 before propagation: reported, nothing published.
    assert seed("--apply", "--verify-host") == 0
    early = _summary(capsys.readouterr().out)
    assert any("not verified (dns_txt_record_not_found)" in action for action in early["actions"])
    assert early["state"]["ucp"]["published"] is False

    # Step 2 after propagation: attested, published under ai.agenticorg.nilgiritea, artifacts re-issued with the opt-in.
    satisfied["value"] = True
    assert seed("--apply", "--verify-host") == 0
    done = _summary(capsys.readouterr().out)
    assert done["state"]["ucp"]["serving_host"] == HOST and done["state"]["ucp"]["published"] is True
    assert done["state"]["ucp"]["namespace"] == "ai.agenticorg.nilgiritea"
    assert done["state"]["ucp"]["public_discovery_allowed"] is True
    last = seed.grantex.requests[-1]["request"]
    assert last["public_discovery_requested"] is True and last["serving_host"] == HOST

    key = _create_platform_key(["ucp:read", "ucp:write", "acp:read", "acp:write"])
    monkeypatch.setattr(settings, "ucp_checkout_enabled", True)
    monkeypatch.setattr(settings, "acp_checkout_enabled", True)
    from api.main import app
    from core.commerce.ucp import negotiation
    from tests import acp_fixtures, ucp_txn_fixtures

    async def _platform_profile(_url: str) -> dict[str, Any]:
        return ucp_txn_fixtures.platform_profile()  # the demo platform's profile, served in process

    monkeypatch.setattr(negotiation, "default_profile_fetcher", _platform_profile)

    async def _through_the_app() -> dict[str, httpx.Response]:
        transport = httpx.ASGITransport(app=app, client=("192.0.2.10", 443))
        base = {"host": HOST}
        agent = {"UCP-Agent": 'profile="https://platform.example/.well-known/ucp"'}
        async with httpx.AsyncClient(transport=transport, base_url=f"https://{HOST}") as client:
            return {
                "profile": await client.get("/.well-known/ucp", headers=base),
                "search": await client.post(
                    "/ucp/catalog/search",
                    headers=base,
                    json={"query": "masala", "context": {"currency": "INR"}},
                ),
                "locations": await client.post("/ucp/locations/search", headers=base, json={}),
                "ucp_checkout": await client.post(
                    "/ucp/checkout-sessions",
                    headers={**base, **agent, "X-API-Key": key, "Idempotency-Key": uuid.uuid4().hex},
                    json={"line_items": [{"item": {"id": "NTH-MASALA-250"}, "quantity": 1}]},
                ),
                "ucp_checkout_unauthenticated": await client.post(
                    "/ucp/checkout-sessions",
                    headers={**base, **agent, "Idempotency-Key": uuid.uuid4().hex},
                    json={"line_items": [{"item": {"id": "NTH-MASALA-250"}, "quantity": 1}]},
                ),
                "acp_checkout": await client.post(
                    "/acp/checkout_sessions",
                    headers={
                        **base,
                        "Authorization": f"Bearer {key}",
                        "API-Version": "2026-04-17",
                        "Idempotency-Key": uuid.uuid4().hex,
                    },
                    json=acp_fixtures.create_body("NTH-MASALA-250"),
                ),
                "unknown_host": await client.get("/.well-known/ucp", headers={"host": "other.agenticorg.ai"}),
            }

    responses = asyncio.run(_through_the_app())
    assert responses["profile"].status_code == 200, responses["profile"].text
    profile = responses["profile"].json()
    assert profile["ucp"]["version"] and "https://nilgiritea.agenticorg.ai/ucp" in json.dumps(profile)
    assert responses["search"].status_code == 200, responses["search"].text
    assert [p["title"] for p in responses["search"].json().get("products") or []] == ["Masala Chai Blend"]
    assert responses["locations"].status_code == 200, responses["locations"].text
    assert responses["ucp_checkout"].status_code == 201, responses["ucp_checkout"].text
    session = responses["ucp_checkout"].json()
    assert session["currency"] == "INR" and session["line_items"][0]["item"]["id"] == "NTH-MASALA-250"
    assert responses["ucp_checkout_unauthenticated"].status_code == 401
    assert responses["acp_checkout"].status_code == 201, responses["acp_checkout"].text
    assert responses["unknown_host"].status_code == 404

    assert seed("--teardown", "--apply") == 0
    capsys.readouterr()
    assert _scalar("SELECT settings ? 'ucp_serving_hosts' FROM tenants WHERE id::text = :t", t=DEMO_TENANT_ID) is False


def _create_platform_key(scopes: list[str]) -> str:
    """A tenant API key for the demo tenant, stored the way the API-key route stores one."""
    from api.v1.api_keys import _generate_api_key

    full_key, prefix, key_hash = _generate_api_key()

    async def _insert() -> None:
        engine = create_async_engine(os.environ["AGENTICORG_DB_URL"], poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO api_keys (id, tenant_id, user_id, name, prefix, key_hash, scopes, status) "
                        "VALUES (:id, :t, :u, 'demo platform', :p, :h, :s, 'active')"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "t": uuid.UUID(DEMO_TENANT_ID),
                        "u": uuid.UUID(DEMO_ADMIN_USER_ID),
                        "p": prefix,
                        "h": key_hash,
                        "s": scopes,
                    },
                )
        finally:
            await engine.dispose()

    asyncio.run(_insert())
    return full_key


@pytest.mark.skipif(LIVE, reason="replays Grantex refusals with the in-process issuer")
@pytest.mark.parametrize(
    ("mode", "exit_code", "message"),
    [
        ("forbidden", 3, DEMO_TENANT_ID),
        ("unsupported", 4, "Nothing was presented to Grantex as Shopify"),
    ],
)
def test_grantex_refusals_stop_with_the_operator_step(seed, capsys, mode: str, exit_code: int, message: str) -> None:
    seed.grantex.mode = mode
    assert seed("--apply") == exit_code
    assert message in capsys.readouterr().err
    assert _count("oacp_artifact_cache_records") == 0
    # What was written before Grantex answered is the converged config, not a half-made one.
    assert _count("commerce_c6z_merchant_configs") == 1 and _count("commerce_c6z_connector_evidence_records") == 1


def test_a_tenant_without_the_demo_marker_is_never_touched(seed, capsys) -> None:
    assert seed("--apply") == 0
    capsys.readouterr()

    async def _set_marker(value: str) -> None:
        engine = create_async_engine(os.environ["AGENTICORG_DB_URL"], poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE tenants SET settings = jsonb_set(settings, '{oacp_demo_merchant,synthetic}', "
                        "CAST(:v AS jsonb)) WHERE id::text = :t"
                    ),
                    {"v": value, "t": DEMO_TENANT_ID},
                )
        finally:
            await engine.dispose()

    asyncio.run(_set_marker("false"))
    try:
        before = _snapshot()
        assert seed("--apply") == 2
        assert "without the demo marker" in capsys.readouterr().err
        assert seed("--teardown", "--apply") == 2
        assert _snapshot() == before
    finally:
        asyncio.run(_set_marker("true"))
