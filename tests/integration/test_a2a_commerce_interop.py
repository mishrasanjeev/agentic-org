# SPDX-License-Identifier: Apache-2.0
"""Seller-approved external buyer A2A requests through real HTTP and PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from core.commerce.oacp_artifacts import DurableOacpArtifactCacheRepository, OacpPersistentArtifactCacheRecord
from core.database import get_tenant_session
from core.models.commerce_a2a_buyer_access import CommerceA2ABuyerAccess
from core.models.commerce_c6z_runtime import C6ZConnectorEvidenceRow, C6ZSellerOnboardingPacketRow
from tests.integration.conftest import TEST_TENANT_ID


def _message(text: str, **metadata: str) -> dict:
    return {"message": {
        "messageId": str(uuid.uuid4()), "role": "ROLE_USER",
        "parts": [{"text": text}], "metadata": metadata,
    }}


@pytest.mark.asyncio
async def test_external_buyer_is_scoped_revocable_and_receives_sourced_answer(client, auth_headers) -> None:
    suffix = uuid.uuid4().hex[:12]
    merchant = f"merchant_a2a_{suffix}"
    seller_agent = f"seller_a2a_{suffix}"
    packet_id = f"packet_a2a_{suffix}"
    card = await client.get("/.well-known/agent-card.json")
    assert card.status_code == 200
    assert card.json()["supportedInterfaces"][0]["protocolBinding"] == "HTTP+JSON"
    assert card.json()["supportedInterfaces"][0]["protocolVersion"] == "1.0"
    assert not any(merchant in str(skill) for skill in card.json()["skills"])
    assert not any(skill["id"] == "seller_commerce_query" for skill in card.json()["skills"])

    now = datetime.now(UTC)
    async with get_tenant_session(uuid.UUID(TEST_TENANT_ID)) as session:
        session.add(C6ZSellerOnboardingPacketRow(
            packet_id=packet_id, tenant_id=TEST_TENANT_ID,
            merchant_id=merchant, seller_agent_id=seller_agent,
            merchant_display_name="A2A Test Store", commerce_categories=["accessories"],
        ))

    issued = await client.post("/api/v1/a2a/commerce/buyer-access", json={
        "merchant_id": merchant, "seller_agent_id": seller_agent,
        "buyer_agent_id": "outside-agent-1", "expires_days": 1,
    }, headers=auth_headers)
    assert issued.status_code == 200, issued.text
    token = issued.json()["token"]
    assert token.startswith("ao_buyer_")
    buyer_headers = {
        "Authorization": f"Bearer {token}", "Content-Type": "application/a2a+json",
        "A2A-Version": "1.0",
    }

    listed = await client.get(f"/api/v1/a2a/commerce/buyer-access?merchant_id={merchant}", headers=auth_headers)
    assert listed.status_code == 200
    assert token not in listed.text

    extended = await client.get("/api/v1/a2a/extendedAgentCard", headers=buyer_headers)
    assert extended.status_code == 200
    assert extended.json()["skills"][0]["id"] == "seller_commerce_query"
    assert "A2A Test Store" in extended.text

    no_cache = await client.post("/api/v1/a2a/message:send", json=_message("Canvas Tote"), headers=buyer_headers)
    assert no_cache.status_code == 200, no_cache.text
    assert no_cache.json()["message"]["metadata"]["status"] == "needs_refresh"
    assert no_cache.json()["message"]["metadata"]["allowedToExecute"] is False
    unsupported = await client.post(
        "/api/v1/a2a/message:send", json=_message("Canvas Tote"),
        headers={**buyer_headers, "A2A-Version": "0.3"},
    )
    assert unsupported.status_code == 400
    assert unsupported.headers["content-type"] == "application/problem+json"
    assert unsupported.json()["supportedVersions"] == ["1.0"]
    unversioned = await client.post(
        "/api/v1/a2a/message:send", json=_message("Canvas Tote"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert unversioned.status_code == 400

    def iso(dt: datetime) -> str:
        return dt.isoformat().replace("+00:00", "Z")
    async with get_tenant_session(uuid.UUID(TEST_TENANT_ID)) as session:
        session.add(C6ZConnectorEvidenceRow(
            evidence_id=f"evidence_a2a_{suffix}", packet_id=packet_id,
            tenant_id=TEST_TENANT_ID, merchant_id=merchant, seller_agent_id=seller_agent,
            source_evidence_ref="agenticorg:shopify:evidence:a2a:redacted",
            source_observed_at=now, synced_at=now,
            products=[{"title": "Canvas Tote", "vendor": "A2A Test Store", "variants": [{
                "sku": "TOTE-1", "price": "1299", "currency": "INR", "inventory_quantity_snapshot": 7,
            }]}], product_count=1, variant_count=1,
        ))
        repo = DurableOacpArtifactCacheRepository(session)
        shared_record = OacpPersistentArtifactCacheRecord(
            cache_record_id=f"cache_a2a_shared_{suffix}", artifact_id=f"artifact_a2a_catalog_{suffix}",
            artifact_type="catalog_snapshot", authority="grantex.internal.oacp.authority",
            issuer="grantex.internal.oacp.authority", scope_kind="seller_agent",
            tenant_id=TEST_TENANT_ID, merchant_id=merchant, seller_agent_id=seller_agent,
            buyer_agent_id=None,
            source_refs=("agenticorg:shopify:evidence:a2a:redacted",),
            evidence_refs=("agenticorg:shopify:evidence:a2a:redacted",),
            generated_at=iso(now - timedelta(seconds=30)), cached_at=iso(now - timedelta(seconds=20)),
            expires_at=iso(now + timedelta(minutes=5)),
            freshness_status="fresh", revocation_snapshot_status="fresh",
            revocation_snapshot_observed_at=iso(now - timedelta(seconds=10)),
            revocation_snapshot_age_seconds=10, ttl_policy_seconds=330,
            risk_tier="low", blocked_capabilities=("checkout", "payment"),
            unsupported_capabilities=("execution", "public_discovery"),
            verifier_result_ref="artifact_a2a_catalog:verified",
        )
        from dataclasses import replace

        other_buyer = replace(
            shared_record, cache_record_id=f"cache_a2a_other_buyer_{suffix}",
            artifact_id=f"artifact_a2a_other_buyer_{suffix}",
            scope_kind="buyer_agent", buyer_agent_id="outside-agent-2",
        )
        assert (await repo.upsert(other_buyer))["stored"] is True
    isolated = await client.post("/api/v1/a2a/message:send", json=_message("Canvas Tote"), headers=buyer_headers)
    assert isolated.json()["message"]["metadata"]["status"] == "needs_refresh"

    async with get_tenant_session(uuid.UUID(TEST_TENANT_ID)) as session:
        stored = await DurableOacpArtifactCacheRepository(session).upsert(shared_record)
        assert stored["stored"] is True, stored

    answered = await client.post("/api/v1/a2a/message:send", json=_message("Canvas Tote"), headers=buyer_headers)
    assert answered.status_code == 200, answered.text
    payload = answered.json()["message"]
    assert payload["role"] == "ROLE_AGENT"
    assert "Canvas Tote" in payload["parts"][0]["text"]
    assert payload["metadata"]["status"] == "answered"
    assert payload["metadata"]["freshnessLabel"].startswith("Freshness:")
    assert payload["metadata"]["nonAuthoritativeForTransaction"] is True

    for wrong in (
        _message("Canvas Tote", merchantId="another-merchant"),
        _message("Canvas Tote", sellerAgentId="another-seller"),
        _message("Canvas Tote", buyerAgentId="another-buyer"),
        _message("Canvas Tote", actionIntent="final_commitment"),
    ):
        refused = await client.post("/api/v1/a2a/message:send", json=wrong, headers=buyer_headers)
        assert refused.status_code == 403
    assert (await client.get("/api/v1/a2a/tasks", headers=buyer_headers)).status_code == 403
    assert (await client.post("/api/v1/a2a/tasks", json={}, headers=buyer_headers)).status_code == 403
    assert (await client.get("/api/v1/commerce/runtime/products", headers=buyer_headers)).status_code == 403
    assert (await client.post("/api/v1/a2a/message:send", json={
        "message": {"messageId": "raw-1", "role": "ROLE_USER", "parts": [{"text": "x", "raw": "data"}]},
    }, headers=buyer_headers)).status_code == 422
    assert (await client.post("/api/v1/a2a/message:send", json={
        "message": {"messageId": "task-1", "role": "ROLE_USER", "parts": [{"text": "x"}], "taskId": "other"},
    }, headers=buyer_headers)).status_code == 422

    bad_token = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert (await client.post(
        "/api/v1/a2a/message:send", json=_message("Canvas Tote"),
        headers={"Authorization": f"Bearer {bad_token}"},
    )).status_code == 401

    async with get_tenant_session(uuid.UUID(TEST_TENANT_ID)) as session:
        access = await session.get(CommerceA2ABuyerAccess, uuid.UUID(issued.json()["id"]))
        assert access is not None
        access.expires_at = now - timedelta(minutes=1)
    assert (await client.post(
        "/api/v1/a2a/message:send", json=_message("Canvas Tote"), headers=buyer_headers,
    )).status_code == 401
    async with get_tenant_session(uuid.UUID(TEST_TENANT_ID)) as session:
        access = await session.get(CommerceA2ABuyerAccess, uuid.UUID(issued.json()["id"]))
        seller = await session.get(C6ZSellerOnboardingPacketRow, packet_id)
        assert access is not None and seller is not None
        access.expires_at = now + timedelta(days=1)
        seller.status = "rejected"
    assert (await client.post(
        "/api/v1/a2a/message:send", json=_message("Canvas Tote"), headers=buyer_headers,
    )).status_code == 401
    async with get_tenant_session(uuid.UUID(TEST_TENANT_ID)) as session:
        seller = await session.get(C6ZSellerOnboardingPacketRow, packet_id)
        assert seller is not None
        seller.status = "future_unknown_state"
    assert (await client.post(
        "/api/v1/a2a/message:send", json=_message("Canvas Tote"), headers=buyer_headers,
    )).status_code == 401
    async with get_tenant_session(uuid.UUID(TEST_TENANT_ID)) as session:
        seller = await session.get(C6ZSellerOnboardingPacketRow, packet_id)
        assert seller is not None
        seller.status = "received"

    revoked = await client.delete(
        f"/api/v1/a2a/commerce/buyer-access/{issued.json()['id']}", headers=auth_headers,
    )
    assert revoked.status_code == 200
    denied = await client.post("/api/v1/a2a/message:send", json=_message("Canvas Tote"), headers=buyer_headers)
    assert denied.status_code == 401


@pytest.mark.asyncio
async def test_generic_agent_message_requires_explicit_run_scope(
    client, auth_headers, make_auth_headers, monkeypatch,
) -> None:
    from api.v1 import a2a as legacy_a2a

    called: list[str] = []

    async def fake_create_task(body, _request, tenant_id):
        called.append(f"{body.agent_type}:{tenant_id}:{body.inputs['message']}")
        return {"status": "completed", "output": {"answer": "ok"}}

    monkeypatch.setattr(legacy_a2a, "create_task", fake_create_task)
    body = _message("Review this", agentType="support_triage", companyId=str(uuid.uuid4()))
    denied = await client.post(
        "/api/v1/a2a/message:send", json=body,
        headers={**make_auth_headers(scopes=["agents:read"]), "A2A-Version": "1.0"},
    )
    assert denied.status_code == 403
    assert called == []
    allowed = await client.post(
        "/api/v1/a2a/message:send", json=body,
        headers={**auth_headers, "A2A-Version": "1.0"},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["message"]["role"] == "ROLE_AGENT"
    assert allowed.json()["message"]["parts"][0]["text"] == '{"answer": "ok"}'
    assert called == [f"support_triage:{TEST_TENANT_ID}:Review this"]
    commerce = await client.post(
        "/api/v1/a2a/message:send",
        json=_message("Buy", agentType="commerce_sales_agent", companyId=str(uuid.uuid4())),
        headers={**auth_headers, "A2A-Version": "1.0"},
    )
    assert commerce.status_code == 403
    assert len(called) == 1
