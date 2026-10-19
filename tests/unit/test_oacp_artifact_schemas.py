from __future__ import annotations

from pathlib import Path

from core.commerce.oacp_artifacts import (
    OACP_ARTIFACT_SCHEMA_DESCRIPTORS,
    OACP_C6W3_VALID_ARTIFACT_FIXTURES,
    OacpArtifactScope,
    OacpArtifactVerificationInput,
    evaluate_agenticorg_artifact_runtime_use,
    evaluate_required_artifacts_for_final_commitment,
    hash_oacp_payload,
    validate_oacp_artifact_family,
    verify_oacp_artifact,
)

C6W3_DOC_PATH = Path("docs/reports/commerce-agent-c6w3-oacp-artifact-consumer-behavior.md")


def _scope() -> OacpArtifactScope:
    return OacpArtifactScope(
        tenant_id="cten_C6W3",
        merchant_id="mch_C6W3",
        seller_agent_id="seller_C6W3",
        buyer_agent_id="buyer_C6W3",
    )


def test_c6w3_accepts_valid_public_safe_artifact_family_fixtures() -> None:
    assert set(OACP_ARTIFACT_SCHEMA_DESCRIPTORS) == set(OACP_C6W3_VALID_ARTIFACT_FIXTURES)

    for artifact_type, fixture in OACP_C6W3_VALID_ARTIFACT_FIXTURES.items():
        result = validate_oacp_artifact_family(
            envelope=fixture["envelope"],
            payload=fixture["payload"],
            now_iso="2026-06-11T00:00:00.000Z",
        )
        assert result["valid"] is True
        assert result["artifact_type"] == artifact_type


def test_c6w3_refuses_invalid_private_missing_scope_and_time_bad_artifacts() -> None:
    price = OACP_C6W3_VALID_ARTIFACT_FIXTURES["price"]

    payload_with_private = {**price["payload"], "rawProviderPayload": {"private": True}}
    result = validate_oacp_artifact_family(
        envelope={**price["envelope"], "payload_hash": hash_oacp_payload(payload_with_private)},
        payload=payload_with_private,
    )
    assert result["refusal_code"] == "private_or_forbidden_payload_field"

    missing_scope = {**price["envelope"]}
    missing_scope.pop("buyer_agent_id")
    assert validate_oacp_artifact_family(
        envelope=missing_scope,
        payload=price["payload"],
    )["refusal_code"] == "scope_field_missing"

    assert validate_oacp_artifact_family(
        envelope=price["envelope"],
        payload=price["payload"],
        now_iso="2026-06-11T00:06:00.000Z",
    )["refusal_code"] == "artifact_expired_or_stale"

    assert validate_oacp_artifact_family(
        envelope={**price["envelope"], "not_before": "2026-06-11T00:02:00.000Z"},
        payload=price["payload"],
        now_iso="2026-06-11T00:01:00.000Z",
    )["refusal_code"] == "artifact_not_yet_valid"


def test_c6w3_stub_signed_fixtures_are_refused_by_the_local_verifier() -> None:
    """The C6W3 fixtures carry a stub detached JWS; the verifier refuses them on the signature.

    Before the ES256 verifier landed, ``verify_oacp_artifact`` delegated step 6 to a
    caller-supplied callback that tests stubbed to ``True``. The fixtures now never
    pass: no published key verifies them.
    """
    for fixture in OACP_C6W3_VALID_ARTIFACT_FIXTURES.values():
        result = verify_oacp_artifact(
            OacpArtifactVerificationInput(
                envelope=fixture["envelope"],
                payload=fixture["payload"],
                jwks={"keys": []},
                expected_issuer=str(fixture["envelope"].get("issuer") or ""),
                now_iso="2026-06-11T00:01:00.000Z",
                expected_scope=_scope(),
            )
        )
        assert result["valid"] is False
        assert result["refusal_code"] in {
            "signature_missing_or_placeholder",
            "detached_jws_verification_failed",
            "issuer_key_untrusted",
        }


def test_c6w3_refuses_final_commitments_when_required_artifacts_are_absent() -> None:
    result = evaluate_required_artifacts_for_final_commitment(
        "payment_intent",
        {"merchant_capability", "seller_agent_capability", "price", "policy"},
    )
    assert result == {
        "allowed": False,
        "status": "refused",
        "refusal_code": "required_artifact_missing",
        "missing_artifact_types": ["mandate_capability"],
    }


def test_c6w3_pins_public_discovery_mandate_and_authority_limits() -> None:
    assert evaluate_agenticorg_artifact_runtime_use(
        artifact_type="public_discovery",
        action="public_discovery_publish",
    )["refusal_code"] == "public_discovery_offline_change_forbidden"

    assert evaluate_agenticorg_artifact_runtime_use(
        artifact_type="mandate_capability",
        action="payment_intent",
        provider_verification=False,
    )["refusal_code"] == "provider_verification_required"

    assert evaluate_agenticorg_artifact_runtime_use(
        artifact_type="mandate_capability",
        action="payment_intent",
        provider_verification=True,
    )["allowed"] is True

    assert evaluate_agenticorg_artifact_runtime_use(
        artifact_type="seller_agent_capability",
        action="payment_intent",
    )["refusal_code"] == "artifact_not_commerce_authority"

    assert evaluate_agenticorg_artifact_runtime_use(
        artifact_type="protocol_adapter",
        action="payment_intent",
    )["refusal_code"] == "artifact_not_commerce_authority"


def test_c6w3_family_specific_schema_rules_fail_closed() -> None:
    discovery = OACP_C6W3_VALID_ARTIFACT_FIXTURES["public_discovery"]
    discovery_payload = {**discovery["payload"], "publish_offline_allowed": True}
    assert validate_oacp_artifact_family(
        envelope={**discovery["envelope"], "payload_hash": hash_oacp_payload(discovery_payload)},
        payload=discovery_payload,
    )["refusal_code"] == "public_discovery_offline_change_forbidden"

    mandate = OACP_C6W3_VALID_ARTIFACT_FIXTURES["mandate_capability"]
    mandate_payload = {**mandate["payload"], "provider_direct_verification_required": False}
    assert validate_oacp_artifact_family(
        envelope={**mandate["envelope"], "payload_hash": hash_oacp_payload(mandate_payload)},
        payload=mandate_payload,
    )["refusal_code"] == "mandate_provider_verification_required"

    adapter = OACP_C6W3_VALID_ARTIFACT_FIXTURES["protocol_adapter"]
    adapter_payload = {**adapter["payload"], "referenced_artifact_expires_at": ["2026-06-11T00:04:00.000Z"]}
    assert validate_oacp_artifact_family(
        envelope={**adapter["envelope"], "payload_hash": hash_oacp_payload(adapter_payload)},
        payload=adapter_payload,
    )["refusal_code"] == "protocol_adapter_outlives_references"

    evidence = OACP_C6W3_VALID_ARTIFACT_FIXTURES["commitment_evidence"]
    evidence_payload = {**evidence["payload"], "commitment_type": "payment_capture"}
    assert validate_oacp_artifact_family(
        envelope={**evidence["envelope"], "payload_hash": hash_oacp_payload(evidence_payload)},
        payload=evidence_payload,
    )["refusal_code"] == "commitment_evidence_forbidden_implication"


def test_c6w3_consumer_doc_captures_runtime_handling_and_non_enablement() -> None:
    doc = C6W3_DOC_PATH.read_text(encoding="utf-8")

    for heading in (
        "Scope",
        "Buyer-Agent Handling",
        "Seller-Agent Handling",
        "Channel-Safe Refusals",
        "Cache And Freshness",
        "What This Does Not Enable",
    ):
        assert f"## {heading}" in doc

    assert "AgenticOrg does not invent commerce facts" in doc
    assert "No endpoint, migration, workflow, provider adapter, public discovery, checkout/payment" in doc
