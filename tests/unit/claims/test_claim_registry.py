from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from core.claims.registry import ClaimRegistryService
from core.claims.schema import ClaimRegistryDocument

NOW = datetime(2026, 7, 14, 12, tzinfo=UTC)


def _payload(*, evidence: bool = True, expired: bool = False) -> dict:
    expiry = "2026-07-13T00:00:00Z" if expired else "2026-08-01T00:00:00Z"
    rows = []
    if evidence:
        rows.append(
            {
                "evidence_id": "EVID-MKT-C01",
                "capability_ids": ["MKT-C01"],
                "uri": "artifact://pilot/mkt-c01.json",
                "checksum": "sha256:" + "a" * 64,
                "environment": "vendor_sandbox",
                "provider_account_class": "controlled-test-account",
                "product_version": "4.8.0",
                "commit_sha": "b" * 40,
                "executed_at": "2026-07-01T00:00:00Z",
                "state": "Integrated",
                "result": "Passed",
                "reviewer": "release-reviewer",
                "expires_at": expiry,
            }
        )
    return {
        "schema_version": "agenticorg.claim-registry.v1",
        "registry_version": "test.1",
        "product_version": "4.8.0",
        "generated_at": "2026-07-13T00:00:00Z",
        "capabilities": [
            {
                "capability_id": "MKT-C01",
                "domain": "Marketing",
                "title": "Campaign operations",
                "maturity": "Integrated",
                "gate_result": "Passed",
                "public_availability": "Beta",
                "claim_treatment": "EvidenceBacked",
                "owner": "marketing-owner",
                "expires_at": "2026-08-01T00:00:00Z",
                "evidence_ids": ["EVID-MKT-C01"],
                "permitted_claim_ids": ["MKT-CLAIM-PIPELINE"],
                "limitations": [],
            }
        ],
        "evidence": rows,
        "claims": [
            {
                "claim_id": "MKT-CLAIM-PIPELINE",
                "kind": "outcome",
                "treatment": "EvidenceBacked",
                "approved_text": ["Campaign pilot increased qualified pipeline by 12%."],
                "capability_ids": ["MKT-C01"],
                "evidence_ids": ["EVID-MKT-C01"],
                "required_evidence_state": "Integrated",
                "asserted_availability": None,
                "owner": "marketing-owner",
                "approver": "marketing-approver",
                "product_version": "4.8.0",
                "expires_at": expiry,
                "surfaces": ["README.md", "ui/src/pages/Landing.*"],
                "inventory_source": None,
                "limitations": ["Controlled pilot cohort only."],
            }
        ],
    }


def test_valid_evidence_backed_claim_is_authorized() -> None:
    service = ClaimRegistryService(ClaimRegistryDocument.model_validate(_payload()))
    assert service.validate(now=NOW).valid
    assert service.authorize_claim("MKT-CLAIM-PIPELINE", surface="ui/src/pages/Landing.tsx", now=NOW).valid


def test_missing_evidence_fails_closed() -> None:
    service = ClaimRegistryService(ClaimRegistryDocument.model_validate(_payload(evidence=False)))
    report = service.validate(now=NOW)
    codes = {issue.code for issue in report.issues}
    assert not report.valid
    assert "claim_evidence_missing" in codes
    assert "claim_capability_evidence_missing" in codes


def test_expired_evidence_fails_closed() -> None:
    service = ClaimRegistryService(ClaimRegistryDocument.model_validate(_payload(expired=True)))
    codes = {issue.code for issue in service.validate(now=NOW).issues}
    assert "claim_evidence_expired" in codes
    assert "claim_expired" in codes


def test_unapproved_surface_fails_closed() -> None:
    service = ClaimRegistryService(ClaimRegistryDocument.model_validate(_payload()))
    report = service.authorize_claim("MKT-CLAIM-PIPELINE", surface="ui/src/pages/Pricing.tsx", now=NOW)
    assert {issue.code for issue in report.issues} == {"claim_surface_not_permitted"}


def test_illustrative_claim_cannot_carry_evidence() -> None:
    payload = _payload()
    payload["claims"][0]["treatment"] = "Illustrative"
    with pytest.raises(ValidationError, match="illustrative claims must remain separate"):
        ClaimRegistryDocument.model_validate(payload)


def test_inventory_claim_requires_canonical_source() -> None:
    payload = _payload()
    claim = payload["claims"][0]
    claim.update(
        {
            "kind": "inventory",
            "capability_ids": [],
            "evidence_ids": [],
            "required_evidence_state": None,
            "inventory_source": None,
        }
    )
    with pytest.raises(ValidationError, match="inventory claims require inventory_source"):
        ClaimRegistryDocument.model_validate(payload)
