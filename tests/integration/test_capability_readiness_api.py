"""PostgreSQL-backed API persistence and scope tests for readiness history."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.models.capability_readiness import CapabilityPromotionEvent
from core.readiness.contracts import CANONICAL_GATE_IDS, REQUIRED_OWNER_ROLES
from core.readiness.ledger import verify_event_chain

pytestmark = pytest.mark.asyncio


def _registration(
    company_id: str,
    *,
    title: str,
    owner_id: str,
    capability_id: str = "HR-C91",
) -> dict[str, object]:
    return {
        "company_id": company_id,
        "capability_id": capability_id,
        "domain": "hr",
        "title": title,
        "description": "Governed recommendation workflow with recorded human review.",
        "scope_disposition": "Mandatory",
        "scope_details": {"decision_boundary": "recommendation_only"},
        "required_gate_ids": list(CANONICAL_GATE_IDS),
        "owners": dict.fromkeys(REQUIRED_OWNER_ROLES, owner_id),
        "approver_ids": [owner_id],
        "traceability": {
            "gap_ids": ["P0-08"],
            "roadmap_ids": ["HR-02"],
            "implementation_refs": ["core/readiness/ledger.py"],
            "migration_refs": ["migrations/versions/v6_z5_capability_readiness_ledger.py"],
            "test_refs": ["tests/integration/test_capability_readiness_api.py"],
            "threat_privacy_refs": ["docs/readiness/DOMAIN_READINESS_STANDARD.md"],
            "runbook_refs": ["docs/readiness/DOMAIN_READINESS_STANDARD.md"],
            "slo_dashboard_refs": ["docs/readiness/DOMAIN_READINESS_STANDARD.md"],
            "release_manifest_refs": ["docs/readiness/CAPABILITY_READINESS_REGISTER.md"],
        },
        "limitations": ["No automated final adverse employment decision."],
        "feature_flag": "readiness_hr_screening",
        "review_expires_at": (datetime.now(UTC) + timedelta(days=90)).isoformat(),
    }


def _evidence(company_id: str, *, version: str = "test-proof-v1") -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "company_id": company_id,
        "evidence_version": version,
        "evidence_type": "implementation_test",
        "artifact_uri": "evidence://readiness/integration-test",
        "sha256_checksum": "a" * 64,
        "environment": "test",
        "provider_account_class": "integration-fixture",
        "product_version": "test-build",
        "source_commit_sha": "b" * 40,
        "observed_at": (now - timedelta(hours=2)).isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
        "supports_gate_ids": list(CANONICAL_GATE_IDS),
        "supports_claim_ids": [],
        "evidence_metadata": {"fixture": True},
    }


def _transition(company_id: str, evidence_id: str) -> dict[str, object]:
    return {
        "company_id": company_id,
        "target_internal_maturity": "Implemented",
        "target_release_gate": "NotAssessed",
        "target_public_availability": "Unavailable",
        "target_claim_state": "Hidden",
        "gate_attestations": [
            {
                "gate_id": gate_id,
                "status": "NotAssessed",
                "evidence_ids": [],
            }
            for gate_id in CANONICAL_GATE_IDS
        ],
        "evidence_ids": [evidence_id],
        "permitted_claim_ids": [],
        "limitations": ["Human review remains mandatory."],
        "reason": "Current implementation evidence reviewed.",
    }


async def _create_company(client: AsyncClient, headers: dict[str, str], name: str) -> str:
    response = await client.post(
        "/api/v1/companies",
        headers=headers,
        json={"name": name, "pan": "ABCDE1234F"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def test_readiness_lifecycle_persists_and_hash_chain_survives_requests(
    client: AsyncClient,
    auth_headers: dict[str, str],
    tenant_id: str,
    user_id: str,
) -> None:
    company_id = await _create_company(client, auth_headers, f"Readiness Lifecycle {uuid4().hex[:8]}")
    capability_id = "HR-C91"
    registration = _registration(
        company_id,
        title="Lifecycle persistence proof",
        owner_id=user_id,
        capability_id=capability_id,
    )

    created = await client.post("/api/v1/capability-readiness", headers=auth_headers, json=registration)
    assert created.status_code == 201, created.text
    duplicate = await client.post("/api/v1/capability-readiness", headers=auth_headers, json=registration)
    assert duplicate.status_code == 409

    evidence_body = _evidence(company_id)
    evidence = await client.post(
        f"/api/v1/capability-readiness/{capability_id}/evidence",
        headers=auth_headers,
        json=evidence_body,
    )
    assert evidence.status_code == 201, evidence.text
    assert evidence.json()["trust_state"] == "unverified"
    assert evidence.json()["submitted_by"] == user_id
    assert evidence.json()["reviewed_by"] is None
    assert evidence.json()["reviewed_at"] is None
    duplicate_evidence = await client.post(
        f"/api/v1/capability-readiness/{capability_id}/evidence",
        headers=auth_headers,
        json=evidence_body,
    )
    assert duplicate_evidence.status_code == 409

    transitioned = await client.post(
        f"/api/v1/capability-readiness/{capability_id}/transitions",
        headers=auth_headers,
        json=_transition(company_id, evidence.json()["evidence_id"]),
    )
    assert transitioned.status_code == 409, transitioned.text

    renewed = await client.post(
        f"/api/v1/capability-readiness/{capability_id}/review-renewals",
        headers=auth_headers,
        json={
            "company_id": company_id,
            "expected_sequence": 0,
            "valid_for_days": 90,
            "reason": "Scheduled governance review completed.",
        },
    )
    assert renewed.status_code == 201, renewed.text
    assert renewed.json()["sequence"] == 1
    assert renewed.json()["event_type"] == "review_renewed"

    current = await client.get(
        f"/api/v1/capability-readiness/{capability_id}",
        headers=auth_headers,
        params={"company_id": company_id},
    )
    assert current.status_code == 200
    assert current.json()["internal_maturity"] == "Missing"
    assert current.json()["promotion_sequence"] == 1

    history = await client.get(
        f"/api/v1/capability-readiness/{capability_id}/history",
        headers=auth_headers,
        params={"company_id": company_id},
    )
    assert history.status_code == 200, history.text
    assert [item["sequence"] for item in history.json()] == [0, 1]
    assert history.json()[1]["previous_event_hash"] == history.json()[0]["event_hash"]

    verification_engine = create_async_engine(os.environ["AGENTICORG_DB_URL"], poolclass=NullPool)
    verification_factory = async_sessionmaker(
        verification_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with verification_factory() as verification_session:
        rows = await verification_session.execute(
            select(CapabilityPromotionEvent)
            .where(
                CapabilityPromotionEvent.tenant_id == UUID(tenant_id),
                CapabilityPromotionEvent.company_id == UUID(company_id),
                CapabilityPromotionEvent.capability_id == capability_id,
            )
            .order_by(CapabilityPromotionEvent.sequence)
        )
        persisted = list(rows.scalars().all())
    await verification_engine.dispose()
    assert len(persisted) == 2
    assert verify_event_chain(persisted) == persisted[-1].event_hash

    for method in ("PATCH", "DELETE"):
        response = await client.request(
            method,
            f"/api/v1/capability-readiness/{capability_id}",
            headers=auth_headers,
            params={"company_id": company_id},
        )
        assert response.status_code == 405


async def test_readiness_records_are_isolated_by_tenant_and_company(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_auth_headers,
    user_id: str,
) -> None:
    company_a = await _create_company(client, auth_headers, f"Readiness Scope A {uuid4().hex[:8]}")
    company_b = await _create_company(client, auth_headers, f"Readiness Scope B {uuid4().hex[:8]}")
    capability_id = "HR-C92"

    for company_id, title in ((company_a, "Company A proof"), (company_b, "Company B proof")):
        response = await client.post(
            "/api/v1/capability-readiness",
            headers=auth_headers,
            json=_registration(company_id, title=title, owner_id=user_id, capability_id=capability_id),
        )
        assert response.status_code == 201, response.text

    for company_id, expected_title in ((company_a, "Company A proof"), (company_b, "Company B proof")):
        response = await client.get(
            f"/api/v1/capability-readiness/{capability_id}",
            headers=auth_headers,
            params={"company_id": company_id},
        )
        assert response.status_code == 200
        assert response.json()["title"] == expected_title

    assert (await client.get(f"/api/v1/capability-readiness/{capability_id}", headers=auth_headers)).status_code == 404

    other_tenant_headers = make_auth_headers(tenant_id=str(uuid4()))
    cross_tenant = await client.get(
        f"/api/v1/capability-readiness/{capability_id}",
        headers=other_tenant_headers,
        params={"company_id": company_a},
    )
    assert cross_tenant.status_code == 404
    cross_tenant_write = await client.post(
        "/api/v1/capability-readiness",
        headers=other_tenant_headers,
        json=_registration(
            company_a,
            title="Cross-tenant write",
            owner_id=user_id,
            capability_id="HR-C93",
        ),
    )
    assert cross_tenant_write.status_code == 403
