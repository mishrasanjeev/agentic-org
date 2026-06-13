from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from core.commerce.oacp_artifacts import (
    InMemoryOacpArtifactCacheRepository,
    OacpArtifactCacheRepositoryPort,
    OacpArtifactCacheRepositoryQuery,
    OacpPersistentArtifactCacheRecord,
)

C6X3_DOC_PATH = Path("docs/reports/commerce-agent-c6x3-oacp-cache-repository.md")


def _doc() -> str:
    return C6X3_DOC_PATH.read_text(encoding="utf-8")


def _record(**overrides: object) -> OacpPersistentArtifactCacheRecord:
    base = OacpPersistentArtifactCacheRecord(
        cache_record_id="cache_price_C6X3",
        artifact_id="price_C6W3",
        artifact_type="price",
        authority="grantex_canonical_oacp_artifact_authority",
        issuer="grantex",
        scope_kind="buyer_agent",
        tenant_id="cten_C6W3",
        merchant_id="mch_C6W3",
        seller_agent_id="seller_C6W3",
        buyer_agent_id="buyer_C6W3",
        source_refs=("source_ref_price_C6X3",),
        evidence_refs=("evidence_price_C6X3_redacted",),
        generated_at="2026-06-11T00:00:00.000Z",
        cached_at="2026-06-11T00:00:10.000Z",
        expires_at="2026-06-11T00:05:00.000Z",
        freshness_status="fresh",
        revocation_snapshot_status="fresh",
        revocation_snapshot_observed_at="2026-06-11T00:00:30.000Z",
        revocation_snapshot_age_seconds=30,
        ttl_policy_seconds=300,
        risk_tier="low",
        blocked_capabilities=("checkout_payment_creation", "live_provider_call"),
        unsupported_capabilities=("transaction_authority_from_adapter_preview",),
        verifier_result_ref="verifier_result_price_C6X3",
    )
    return replace(base, **overrides)


def test_c6x3_repository_port_stores_and_lists_all_cache_scopes() -> None:
    repository: OacpArtifactCacheRepositoryPort = InMemoryOacpArtifactCacheRepository()
    records = (
        _record(scope_kind="buyer_agent"),
        _record(
            cache_record_id="cache_seller_C6X3",
            artifact_id="seller_agent_capability_C6X3",
            artifact_type="seller_agent_capability",
            scope_kind="seller_agent",
            buyer_agent_id=None,
            ttl_policy_seconds=6 * 60 * 60,
        ),
        _record(
            cache_record_id="cache_tenant_C6X3",
            artifact_id="policy_C6X3",
            artifact_type="policy",
            scope_kind="tenant",
            merchant_id=None,
            seller_agent_id=None,
            buyer_agent_id=None,
            ttl_policy_seconds=6 * 60 * 60,
        ),
        _record(
            cache_record_id="cache_merchant_C6X3",
            artifact_id="merchant_capability_C6X3",
            artifact_type="merchant_capability",
            scope_kind="merchant",
            seller_agent_id=None,
            buyer_agent_id=None,
            ttl_policy_seconds=24 * 60 * 60,
        ),
    )

    for record in records:
        assert repository.upsert(record) == {
            "stored": True,
            "status": "stored",
            "cache_record_id": record.cache_record_id,
            "artifact_id": record.artifact_id,
            "artifact_type": record.artifact_type,
            "scope_kind": record.scope_kind,
            "allowed_to_execute": False,
            "repository_only": True,
            "non_authoritative_for_transaction": True,
            "no_checkout_payment_enablement": True,
            "no_live_provider_enablement": True,
            "no_public_discovery_enablement": True,
            "grantex_runtime_required": False,
        }

    tenant_records = repository.list_for_scope(OacpArtifactCacheRepositoryQuery(tenant_id="cten_C6W3"))
    assert {record.scope_kind for record in tenant_records} == {"buyer_agent", "seller_agent", "tenant", "merchant"}

    buyer_records = repository.list_for_scope(
        OacpArtifactCacheRepositoryQuery(
            scope_kind="buyer_agent",
            tenant_id="cten_C6W3",
            merchant_id="mch_C6W3",
            seller_agent_id="seller_C6W3",
            buyer_agent_id="buyer_C6W3",
        )
    )
    assert [record.cache_record_id for record in buyer_records] == ["cache_price_C6X3"]


def test_c6x3_repository_evaluates_cached_records_without_grantex_toll_booth() -> None:
    repository = InMemoryOacpArtifactCacheRepository()
    repository.upsert(_record())

    preview = repository.evaluate(
        cache_record_id="cache_price_C6X3",
        action_intent="non_binding_preview",
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=False,
        expected_scope={"tenant_id": "cten_C6W3", "merchant_id": "mch_C6W3"},
    )
    assert preview["status"] == "usable_for_non_binding_cache"
    assert preview["allowed_to_preview"] is True
    assert preview["allowed_to_prepare"] is False
    assert preview["allowed_to_execute"] is False
    assert preview["grantex_runtime_required"] is False

    commitment = repository.evaluate(
        cache_record_id="cache_price_C6X3",
        action_intent="final_commitment",
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=False,
        expected_scope={
            "tenant_id": "cten_C6W3",
            "merchant_id": "mch_C6W3",
            "seller_agent_id": "seller_C6W3",
            "buyer_agent_id": "buyer_C6W3",
        },
    )
    assert commitment["status"] == "prepared_only_for_commitment_boundary"
    assert commitment["allowed_to_prepare"] is True
    assert commitment["allowed_to_execute"] is False
    assert commitment["non_authoritative_for_transaction"] is True


def test_c6x3_repository_fails_closed_for_unsafe_store_and_missing_records() -> None:
    repository = InMemoryOacpArtifactCacheRepository()

    unsafe = repository.upsert(_record(evidence_refs=("raw_jwt_ref",)))
    assert unsafe["stored"] is False
    assert unsafe["status"] == "unsafe"
    assert unsafe["refusal_code"] == "cache_refs_missing_or_private"

    executable = repository.upsert(_record(cache_record_id="cache_exec_C6X3", allowed_to_execute=True))
    assert executable["stored"] is False
    assert executable["status"] == "unsafe"
    assert executable["refusal_code"] == "non_enablement_flags_missing"

    missing = repository.evaluate(
        cache_record_id="missing_C6X3",
        action_intent="non_binding_preview",
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=True,
    )
    assert missing["evaluated"] is False
    assert missing["status"] == "blocked"
    assert missing["allowed_to_execute"] is False


def test_c6x3_repository_docs_capture_no_migration_and_guardrails() -> None:
    doc = _doc()
    for heading in (
        "Scope",
        "Correct Ownership Model",
        "Repository Port",
        "Repository Query",
        "Evaluation Behavior",
        "Persistence And Migration Decision",
        "Fail-Closed Rules",
        "Guardrails",
        "What C6X3 Does Not Enable",
        "Future Work",
    ):
        assert f"## {heading}" in doc

    assert "AgenticOrg remains the buyer and seller AI-agent runtime" in doc
    assert "without routing every non-binding turn through Grantex" in doc
    assert "no DB migration" in doc
    assert "allowed_to_execute = false" in doc
    assert "no checkout or payment enablement" in doc
    assert "no live provider rail enablement" in doc
    assert "no merchant private API execution" in doc
