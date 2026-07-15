"""Fast, service-free API contract tests for the append-only ledger."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request


def test_capability_readiness_router_is_append_only() -> None:
    from api.v1.capability_readiness import router

    actual = {(route.path, method) for route in router.routes for method in (route.methods or set())}
    assert actual == {
        ("/capability-readiness", "GET"),
        ("/capability-readiness", "POST"),
        ("/capability-readiness/{capability_id}", "GET"),
        ("/capability-readiness/{capability_id}/evidence", "POST"),
        ("/capability-readiness/{capability_id}/transitions", "POST"),
        ("/capability-readiness/{capability_id}/review-renewals", "POST"),
        ("/capability-readiness/{capability_id}/owner-rotations", "POST"),
        ("/capability-readiness/{capability_id}/history", "GET"),
    }
    assert not ({"PUT", "PATCH", "DELETE"} & {method for _, method in actual})


def test_mutation_bodies_forbid_client_asserted_audit_identity_and_time() -> None:
    from api.v1.capability_readiness import (
        EvidenceRegistrationBody,
        GateAttestationBody,
        ReadinessTransitionBody,
    )

    now = datetime.now(UTC)
    evidence = {
        "evidence_version": "v1",
        "evidence_type": "test_run",
        "artifact_uri": "evidence://test/run",
        "sha256_checksum": "a" * 64,
        "environment": "test",
        "provider_account_class": "fixture",
        "product_version": "1.0.0",
        "source_commit_sha": "a" * 40,
        "observed_at": now - timedelta(hours=1),
        "expires_at": now + timedelta(days=1),
        "reviewed_by": "attacker@example.com",
    }
    with pytest.raises(ValidationError, match="reviewed_by"):
        EvidenceRegistrationBody.model_validate(evidence)
    with pytest.raises(ValidationError, match="reviewed_at"):
        GateAttestationBody.model_validate({"gate_id": "quality", "status": "Passed", "reviewed_at": now})
    with pytest.raises(ValidationError, match="approved_by"):
        ReadinessTransitionBody.model_validate(
            {
                "target_internal_maturity": "Missing",
                "target_release_gate": "Blocked",
                "target_public_availability": "Unavailable",
                "target_claim_state": "Hidden",
                "gate_attestations": [],
                "limitations": ["not released"],
                "reason": "remain blocked",
                "approved_by": "attacker@example.com",
            }
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auth_mode", "claims"),
    [
        ("api_key", {"sub": "apikey:test"}),
        ("grantex", {"sub": "agent", "grantex:grant_id": "grant-1"}),
    ],
)
async def test_readiness_mutations_reject_machine_and_delegated_principals(
    auth_mode: str,
    claims: dict[str, str],
) -> None:
    from api.deps import get_active_human_admin

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/capability-readiness",
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1234),
            "scheme": "http",
            "root_path": "",
            "http_version": "1.1",
        }
    )
    request.state.auth_mode = auth_mode
    request.state.claims = claims
    request.state.tenant_id = "11111111-1111-4111-8111-111111111111"
    with pytest.raises(HTTPException) as exc_info:
        await get_active_human_admin(request)
    assert exc_info.value.status_code == 403


def test_expired_review_hides_effective_availability_and_claims() -> None:
    from api.v1.capability_readiness import _record_payload

    now = datetime(2026, 7, 15, tzinfo=UTC)
    record = SimpleNamespace(
        id="record-id",
        tenant_id="tenant-id",
        company_id=None,
        capability_id="HR-C01",
        domain="hr",
        title="Screening",
        description="Human reviewed",
        scope_disposition="Mandatory",
        scope_condition=None,
        scope_details={},
        required_gate_ids=["quality"],
        internal_maturity_state="ProductionProven",
        release_gate_state="Passed",
        public_availability_state="LimitedAvailability",
        claim_state="EvidenceBacked",
        gate_results={"quality": {"status": "Passed"}},
        permitted_claim_ids=["claim.hr.screening"],
        owners={},
        approver_ids=[],
        traceability={},
        limitations=["human review"],
        feature_flag=None,
        review_expires_at=now - timedelta(seconds=1),
        current_promotion_sequence=3,
        updated_at=now,
    )
    payload = _record_payload(record, now=now)
    assert payload["release_gate"] == "Expired"
    assert payload["public_availability"] == "Unavailable"
    assert payload["claim_treatment"] == "Hidden"
    assert payload["permitted_claim_ids"] == []
    assert payload["recorded_state"]["public_availability"] == "LimitedAvailability"
