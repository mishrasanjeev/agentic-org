# SPDX-License-Identifier: Apache-2.0
"""Regression: UCP publishing operator flow, merchant keys at onboarding and the rewritten adapter (G18, G19, G20, G26).

Failures replayed:
* G18 - ``ucp_style_capability_profile`` listed free-text capabilities
  ("seller_product_question_answering", ...) that no UCP platform understands.
  The adapter now carries the spec-shaped profile, or nothing.
* G19 - merchants had no signing key, so a profile could publish no ``keys[]``;
  and a merchant-config upsert rewrites ``public_publishing`` wholesale, which
  would have wiped any key stored there.
* G20 - nothing tied a published namespace to the host serving it.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from api.v1 import commerce_runtime
from api.v1 import commerce_ucp_publishing as publishing
from core.commerce.c6z_runtime_vertical import (
    contains_private_or_executable_value,
    generate_protocol_adapter_payloads,
    select_protocol_adapter_payload,
)
from core.commerce.ucp.profile import build_profile
from core.commerce.ucp.state import ucp_state, with_ucp_state
from core.config import settings
from tests.commerce_ledger_fakes import patch_commerce_ledger
from tests.ucp_fixtures import (
    HOST,
    TENANT_ID,
    VERIFIED_REF,
    catalog_records,
    fake_tenant_encryption,
    key_record,
    merchant_row,
    published_state,
    serve_state,
    synced_products,
)
from tests.ucp_schema_validation import PROFILE_SCHEMAS, assert_ucp_valid


class _Session:
    def __init__(self, row: Any = None) -> None:
        self.row = row

    async def get(self, _model: Any, _key: Any) -> Any:
        return self.row


@pytest.fixture()
def operator(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    fake_tenant_encryption(monkeypatch)
    row = merchant_row({})
    holder: dict[str, Any] = {"row": row, "index": [], "dns": False, "claimed": False}
    holder["ledger"] = patch_commerce_ledger(monkeypatch, publishing)

    @asynccontextmanager
    async def session_cm(_tenant: Any):
        yield _Session(row)

    async def locked_row(_session: Any, **_scope: Any) -> Any:
        return holder["row"]

    async def write_index(_session: Any, **kwargs: Any) -> None:
        holder["index"].append(kwargs)

    async def dns(_challenge: Any) -> bool:
        return holder["dns"]

    async def claimed(_host: str, _tenant: str) -> bool:
        return holder["claimed"]

    monkeypatch.setattr(publishing, "get_tenant_session", session_cm)
    monkeypatch.setattr(publishing, "_locked_row", locked_row)
    monkeypatch.setattr(publishing, "write_host_index", write_index)
    monkeypatch.setattr(publishing, "dns_challenge_satisfied", dns)
    monkeypatch.setattr(publishing, "host_claimed_by_other_tenant", claimed)
    monkeypatch.setattr(settings, "ucp_versions", "2026-04-08")
    return holder


def _request() -> Any:
    return SimpleNamespace(state=SimpleNamespace(user_sub="operator@merchant.example", auth_mode="session"))


def test_operator_flow_attests_the_host_provisions_a_key_and_publishes(operator: dict[str, Any]) -> None:
    scope = publishing.UcpScope()
    challenge = asyncio.run(
        publishing.request_serving_host_challenge(
            "merchant_1", publishing.ServingHostRequest(host="Shop.Merchant.Example"), _request(), TENANT_ID
        )
    )
    assert challenge["host"] == HOST
    assert challenge["dns_record"]["name"] == f"_agenticorg-ucp.{HOST}"

    with pytest.raises(HTTPException) as refused:
        asyncio.run(publishing.verify_serving_host("merchant_1", scope, _request(), TENANT_ID))
    assert refused.value.status_code == 409
    assert refused.value.detail["reason"] == "dns_txt_record_not_found"
    assert ucp_state(operator["row"].public_publishing).get("attestation") is None

    operator["dns"] = True
    operator["claimed"] = True
    with pytest.raises(HTTPException) as taken:
        asyncio.run(publishing.verify_serving_host("merchant_1", scope, _request(), TENANT_ID))
    assert taken.value.detail["reason"] == "host_bound_to_another_tenant"

    operator["claimed"] = False
    status = asyncio.run(publishing.verify_serving_host("merchant_1", scope, _request(), TENANT_ID))
    attestation = status["ucp"]["attestation"]
    assert attestation["host"] == HOST and attestation["method"] == "dns_txt"
    assert attestation["verified_by"] == {"actor_type": "user", "actor_id": "operator@merchant.example"}
    assert "record_value" not in attestation and len(attestation["record_value_sha256"]) == 64
    assert attestation["ref"].startswith("agenticorg:ucp-attestation:")
    assert operator["index"][-1]["host"] == HOST and operator["index"][-1]["merchant_id"] == "merchant_1"

    early = asyncio.run(
        publishing.publish_ucp_profile("merchant_1", publishing.PublishRequest(), _request(), TENANT_ID)
    )
    assert early["published"] is False and early["refusal"] == "signing_key_missing"

    provisioned = asyncio.run(publishing.provision_ucp_signing_key("merchant_1", scope, TENANT_ID))
    assert provisioned["changed"] is True
    again = asyncio.run(publishing.provision_ucp_signing_key("merchant_1", scope, TENANT_ID))
    assert again["changed"] is False
    assert all("private_key_ciphertext" not in key for key in again["ucp"]["signing_keys"])

    bad = asyncio.run(
        publishing.publish_ucp_profile(
            "merchant_1", publishing.PublishRequest(namespace="example.merchant"), _request(), TENANT_ID
        )
    )
    assert bad["refusal"] == "namespace_not_authority_bound"
    published = asyncio.run(
        publishing.publish_ucp_profile("merchant_1", publishing.PublishRequest(), _request(), TENANT_ID)
    )
    assert published["published"] is True and published["ready_to_serve"] is True
    # Opted in; served only once Grantex issues an enabled public_discovery_state artifact.
    assert published["public_discovery_opt_in"] is True and published["public_discovery_allowed"] is False
    assert published["served_capabilities"] == ["dev.ucp.shopping.catalog.lookup", "dev.ucp.shopping.catalog.search"]

    rotated = asyncio.run(publishing.rotate_ucp_signing_key("merchant_1", scope, TENANT_ID))
    assert [key["status"] for key in rotated["ucp"]["signing_keys"]] == ["retired", "active"]

    events = [(event["event"], event["outcome"]) for event in operator["ledger"]]
    assert events == [
        ("commerce.ucp.serving_host.challenge", "success"),
        ("commerce.ucp.serving_host.verify", "refused"),
        ("commerce.ucp.serving_host.verify", "refused"),
        ("commerce.ucp.serving_host.verify", "success"),
        ("commerce.ucp.publish", "refused"),
        ("commerce.ucp.keys.provision", "success"),
        ("commerce.ucp.keys.provision", "success"),
        ("commerce.ucp.publish", "refused"),
        ("commerce.ucp.publish", "success"),
        ("commerce.ucp.keys.rotate", "success"),
    ]

    unpublished = asyncio.run(publishing.unpublish_ucp_profile("merchant_1", scope, TENANT_ID))
    assert unpublished["ready_to_serve"] is False and unpublished["public_discovery_opt_in"] is False


def test_moving_to_a_new_host_needs_a_new_publish(operator: dict[str, Any]) -> None:
    operator["row"].public_publishing = with_ucp_state({}, published_state([key_record()]))
    operator["dns"] = True
    asyncio.run(
        publishing.request_serving_host_challenge(
            "merchant_1", publishing.ServingHostRequest(host="new.merchant.example"), _request(), TENANT_ID
        )
    )
    status = asyncio.run(publishing.verify_serving_host("merchant_1", publishing.UcpScope(), _request(), TENANT_ID))
    assert status["ucp"]["serving_host"] == "new.merchant.example"
    assert status["ucp"]["published"] is None and status["ready_to_serve"] is False
    assert operator["index"][-1]["previous_host"] == HOST


def test_ip_or_path_hosts_are_refused(operator: dict[str, Any]) -> None:
    for host in ("192.0.2.10", "shop.merchant.example/path", "localhost"):
        with pytest.raises(HTTPException) as refused:
            asyncio.run(
                publishing.request_serving_host_challenge(
                    "merchant_1", publishing.ServingHostRequest(host=host), _request(), TENANT_ID
                )
            )
        assert refused.value.status_code == 422


def test_config_upsert_keeps_the_ucp_state_and_responses_redact_key_ciphertext(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tenant_encryption(monkeypatch)
    state = published_state([key_record()])
    row = SimpleNamespace(public_publishing=with_ucp_state({"enabled": True}, state))
    config = {
        "tenant_id": TENANT_ID,
        "merchant_id": "merchant_1",
        "seller_agent_id": "seller_agent_1",
        "merchant_display_name": "Merchant Example",
        # A client-supplied ucp member never replaces the server-managed state.
        "public_publishing": {"enabled": False, "ucp": {"serving_host": "attacker.example"}},
    }
    commerce_runtime._apply_merchant_config(row, config, {})
    assert row.public_publishing["enabled"] is False
    assert ucp_state(row.public_publishing) == state
    full = SimpleNamespace(
        config_id="c",
        tenant_id=TENANT_ID,
        merchant_id="merchant_1",
        seller_agent_id="seller_agent_1",
        merchant_display_name="Merchant Example",
        public_brand_profile={},
        commerce_categories=[],
        source_connectors=[],
        buyer_channels={},
        payment_providers=[],
        offline_pos_stores=[],
        public_publishing=row.public_publishing,
        source_freshness_policy={},
        provider_policy={},
        status="configured",
        raw_payload_stored=False,
        allowed_to_execute=False,
        no_payment_execution=True,
        non_authoritative_for_transaction=True,
    )
    view = commerce_runtime._row_to_merchant_config(full)
    assert "private_key_ciphertext" not in str(view)
    assert view["public_publishing"]["ucp"]["signing_keys"][0]["kid"] == state["signing_keys"][0]["kid"]


def test_onboarding_generates_a_key_only_with_the_profile_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tenant_encryption(monkeypatch)
    row = SimpleNamespace(public_publishing={"enabled": False})
    monkeypatch.setattr(settings, "ucp_profile_enabled", False)
    asyncio.run(commerce_runtime._provision_ucp_key_at_onboarding(row, TENANT_ID))
    assert "ucp" not in row.public_publishing
    monkeypatch.setattr(settings, "ucp_profile_enabled", True)
    asyncio.run(commerce_runtime._provision_ucp_key_at_onboarding(row, TENANT_ID))
    keys = ucp_state(row.public_publishing)["signing_keys"]
    assert len(keys) == 1 and keys[0]["status"] == "active"
    asyncio.run(commerce_runtime._provision_ucp_key_at_onboarding(row, TENANT_ID))
    assert len(ucp_state(row.public_publishing)["signing_keys"]) == 1

    async def broken(_pem: str, _tenant: str) -> str:
        raise RuntimeError("KMS unavailable")

    monkeypatch.setattr("core.commerce.merchant_keys._encrypt_private_key", broken)
    fresh = SimpleNamespace(public_publishing={})
    asyncio.run(commerce_runtime._provision_ucp_key_at_onboarding(fresh, TENANT_ID))
    assert "ucp" not in fresh.public_publishing


def test_verified_policy_artifact_is_captured_for_ucp_policies(monkeypatch: pytest.MonkeyPatch) -> None:
    row = merchant_row({})
    record = catalog_records()[0]
    policy_record = type(record)(**{**record.__dict__, "artifact_type": "policy", "artifact_id": "oacp_art_policy"})
    artifact = {
        "envelope": {"artifact_id": "oacp_art_policy"},
        "payload": {
            "return_policy_summary": "Synthetic 7 day return window.",
            "internal_note": "never copied",
        },
    }
    monkeypatch.setattr(commerce_runtime, "split_grantex_artifact", lambda item: (item["envelope"], item["payload"]))
    asyncio.run(
        commerce_runtime._capture_verified_policy_snapshots(
            _Session(row),
            tenant_id=TENANT_ID,
            artifacts=[artifact, artifact],
            verifier_refs=[None, VERIFIED_REF],
            cache_records=[policy_record, policy_record],
            now_iso="2026-10-20T09:00:00Z",
        )
    )
    snapshot = ucp_state(row.public_publishing)["verified_policy"]
    assert snapshot["artifact_id"] == "oacp_art_policy"
    assert snapshot["policy"] == {"return_policy_summary": "Synthetic 7 day return window."}


def test_adapter_carries_the_spec_profile_not_free_text_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tenant_encryption(monkeypatch)
    now = datetime.now(UTC)
    now_iso = now.isoformat().replace("+00:00", "Z")
    records = catalog_records()
    without = generate_protocol_adapter_payloads(
        cache_records=records,
        products=synced_products(),
        merchant_id="merchant_1",
        seller_agent_id="seller_agent_1",
        now_iso=now_iso,
    )
    assert "ucp_style_capability_profile" not in without["surfaces"]
    assert "ucp_business_profile" not in without["surfaces"]
    assert without["unavailable_surfaces"] == {"ucp_business_profile": "ucp_profile_not_published"}
    assert "seller_product_question_answering" not in str(without)

    serve = serve_state(merchant_row(published_state([key_record(now)])), now=now)
    profile = build_profile(serve, version="2026-04-08", versions=("2026-04-08",))
    payloads = generate_protocol_adapter_payloads(
        cache_records=records,
        products=synced_products(),
        merchant_id="merchant_1",
        seller_agent_id="seller_agent_1",
        now_iso=now_iso,
        ucp_profile=profile,
    )
    surface = select_protocol_adapter_payload(payloads, "ucp_business_profile")["payload"]
    assert surface == profile
    assert_ucp_valid(surface, PROFILE_SCHEMAS["2026-04-08"])
    assert "ucp_business_profile" in payloads["surface_names"]
    assert contains_private_or_executable_value(payloads) is False


def test_authority_request_carries_the_opt_in_only_for_a_published_attested_merchant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.commerce.c6z_runtime_vertical as c6z
    from tests.ucp_fixtures import ATTESTATION_REF

    fake_tenant_encryption(monkeypatch)
    packet_row = SimpleNamespace(merchant_id="merchant_1", seller_agent_id="seller_agent_1")
    row = merchant_row(published_state([key_record()]))
    monkeypatch.setattr(settings, "ucp_profile_enabled", True)
    opt_in = asyncio.run(commerce_runtime._ucp_public_discovery_opt_in(_Session(row), TENANT_ID, packet_row))
    assert opt_in == {"serving_host": HOST, "namespace_attestation_ref": ATTESTATION_REF}
    unpublished = merchant_row({**published_state([key_record()]), "published": None})
    assert (
        asyncio.run(commerce_runtime._ucp_public_discovery_opt_in(_Session(unpublished), TENANT_ID, packet_row)) is None
    )
    monkeypatch.setattr(settings, "ucp_profile_enabled", False)
    assert asyncio.run(commerce_runtime._ucp_public_discovery_opt_in(_Session(row), TENANT_ID, packet_row)) is None

    monkeypatch.setattr(c6z, "validate_seller_onboarding_packet", lambda packet: None)
    monkeypatch.setattr(c6z, "validate_connector_evidence", lambda evidence: None)
    packet = {
        "packet_id": "p1",
        "tenant_id": TENANT_ID,
        "merchant_id": "merchant_1",
        "seller_agent_id": "seller_agent_1",
        "merchant_display_name": "Merchant Example",
        "commerce_categories": ["general"],
        "requested_grantex_authority_scope": ["catalog_snapshot"],
        "artifact_cache_scope": "seller_agent",
        "source_freshness_policy": {"max_age_seconds": 900},
    }
    evidence = {
        "evidence_id": "e1",
        "tenant_id": TENANT_ID,
        "merchant_id": "merchant_1",
        "seller_agent_id": "seller_agent_1",
        "source_evidence_ref": "agenticorg:shopify:evidence:abc:redacted",
        "source_observed_at": "2026-10-20T08:00:00Z",
        "product_count": 0,
        "variant_count": 0,
        "products": [],
    }
    plain = c6z.build_grantex_authority_request_payload(onboarding_packet=packet, connector_evidence=evidence)
    assert plain["request"]["no_public_discovery_enablement"] is True
    assert "public_discovery_requested" not in plain["request"]
    opted = c6z.build_grantex_authority_request_payload(
        onboarding_packet=packet, connector_evidence=evidence, public_discovery_opt_in=opt_in
    )
    keys = ("public_discovery_requested", "no_public_discovery_enablement", "serving_host", "namespace_attestation_ref")
    assert {key: opted["request"][key] for key in keys} == {
        "public_discovery_requested": True,
        "no_public_discovery_enablement": False,
        "serving_host": HOST,
        "namespace_attestation_ref": ATTESTATION_REF,
    }
    assert opted["connector_evidence"]["no_public_discovery_enablement"] is True


def test_only_an_enabled_public_discovery_state_artifact_may_unblock_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.commerce.c6z_runtime_vertical import public_discovery_enabling_payload

    enabled = {
        "no_public_discovery_enablement": False,
        "public_discovery_state": "enabled",
        "public_discovery_publication_allowed": True,
        "serving_host": HOST,
        "namespace_attestation_ref": "agenticorg:ucp-attestation:x",
    }
    monkeypatch.setattr(settings, "ucp_profile_enabled", True)
    assert public_discovery_enabling_payload("public_discovery_state", enabled)
    assert not public_discovery_enabling_payload("catalog_snapshot", enabled)
    assert not public_discovery_enabling_payload("public_discovery_state", {**enabled, "serving_host": ""})
    assert not public_discovery_enabling_payload(
        "public_discovery_state", {**enabled, "public_discovery_publication_allowed": False}
    )
    monkeypatch.setattr(settings, "ucp_profile_enabled", False)
    assert not public_discovery_enabling_payload("public_discovery_state", enabled)


def test_verified_public_discovery_artifact_is_captured_and_a_later_disabled_one_withdraws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = merchant_row({})
    record = catalog_records()[0]
    discovery_record = type(record)(
        **{**record.__dict__, "artifact_type": "public_discovery", "artifact_id": "oacp_art_public_discovery"}
    )
    enabled = {
        "envelope": {},
        "payload": {
            "public_discovery_state": "enabled",
            "public_discovery_publication_allowed": True,
            "serving_host": HOST,
            "namespace_attestation_ref": "agenticorg:ucp-attestation:x",
        },
    }
    disabled = {"envelope": {}, "payload": {"public_discovery_state": "disabled"}}
    monkeypatch.setattr(commerce_runtime, "split_grantex_artifact", lambda item: (item["envelope"], item["payload"]))

    def capture(artifact: dict[str, Any]) -> dict[str, Any]:
        asyncio.run(
            commerce_runtime._capture_verified_policy_snapshots(
                _Session(row),
                tenant_id=TENANT_ID,
                artifacts=[artifact],
                verifier_refs=[VERIFIED_REF],
                cache_records=[discovery_record],
                now_iso="2026-10-20T09:00:00Z",
            )
        )
        return ucp_state(row.public_publishing)["verified_public_discovery"]

    snapshot = capture(enabled)
    assert snapshot["publication_allowed"] is True and snapshot["serving_host"] == HOST
    assert capture(disabled)["publication_allowed"] is False


def test_publishing_actor_for_an_api_key_is_its_subject_never_its_name() -> None:
    """The ledger actor of an API-key request is the middleware subject (apikey:<prefix>), not the key's name."""
    from types import SimpleNamespace

    from api.v1.commerce_ucp_publishing import _actor

    request = SimpleNamespace(
        state=SimpleNamespace(agent_id=None, auth_mode="api_key", user_sub="apikey:ak_1234", api_key_name="ops key")
    )
    assert _actor(request) == {"actor_type": "api_key", "actor_id": "apikey:ak_1234"}  # type: ignore[arg-type]
