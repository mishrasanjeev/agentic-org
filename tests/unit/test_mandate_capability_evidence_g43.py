# SPDX-License-Identifier: Apache-2.0
"""G43 (AgenticOrg side): the Plural capability result becomes Grantex mandate-capability evidence.

The object must pass the Grantex validator (``oacp.mandate_capability_evidence.v1``, closed member
set, rails from plural / upi_autopay / card_network_token, caps per rail, verified_at at most 6 h
old) and is sent on the C6Z authority request only behind its flag.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from core.commerce import mandate_capability_evidence as mce
from core.commerce.c6z_runtime_vertical import (
    C6ZRuntimeValidationError,
    PluralPineCapabilityEvidence,
    build_grantex_authority_request_payload,
)

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
TENANT = "11111111-1111-4111-8111-111111111111"
FIELDS = {
    "schema_version",
    "evidence_id",
    "tenant_id",
    "merchant_id",
    "provider",
    "provider_environment",
    "result_status",
    "external_validation_performed",
    "rails",
    "caps",
    "token_types",
    "verified_at",
    "expires_at",
    "redacted_evidence_ref",
    "evidence_hash",
}
CAPABILITY = {
    "rails": ["card", "upi"],
    "max_amount_minor_units": 50000,
    "currency": "INR",
    "instrument_types": ["card", "upi_autopay"],
    "upi_autopay": {
        "amount_rule": "max",
        "max_amount_minor_units": 500000,
        "frequency": "monthly",
        "validity_days": 365,
        "authorization_url": "https://payments.example/upi/{mandate_reference}",
    },
}


def result(**overrides) -> PluralPineCapabilityEvidence:
    values = {
        "evidence_id": "plural_pine_capability_abc",
        "provider": "plural_pine",
        "capability_type": "mandate_capability",
        "result_status": "available",
        "checked_at": (NOW - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "expires_at": (NOW + timedelta(hours=5)).isoformat().replace("+00:00", "Z"),
        "redacted_evidence_ref": "provider:plural_pine:capability:0123abcd:redacted",
        "provider_environment": "sandbox",
        "external_validation_performed": True,
        "missing_env_vars": (),
    }
    values.update(overrides)
    return PluralPineCapabilityEvidence(**values)


def grantex_accepts(evidence: dict) -> None:
    """The Grantex validator's rules (lib/commerce/mandate-capability-evidence.ts), mirrored."""
    assert set(evidence) == FIELDS
    assert evidence["schema_version"] == "oacp.mandate_capability_evidence.v1"
    assert evidence["provider"] == "plural_pine" and evidence["provider_environment"] in ("sandbox", "production")
    assert set(evidence["rails"]) <= {"plural", "upi_autopay", "card_network_token"}
    assert len(set(evidence["rails"])) == len(evidence["rails"])
    for cap in evidence["caps"]:
        assert set(cap) == {"rail", "max_amount", "currency"} and cap["rail"] in evidence["rails"]
        assert isinstance(cap["max_amount"], int) and re.fullmatch(r"[A-Z]{3}", cap["currency"])
    assert all(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", t) for t in evidence["token_types"])
    assert re.fullmatch(r"[0-9a-f]{64}", evidence["evidence_hash"])
    assert ":pending:" not in evidence["redacted_evidence_ref"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", evidence["verified_at"])
    if evidence["result_status"] == "available":
        assert evidence["external_validation_performed"] is True and evidence["rails"]


def test_available_result_with_card_and_upi_autopay() -> None:
    evidence = mce.build(result(), tenant_id=TENANT, merchant_id="m1", mandate_capability=CAPABILITY, now=NOW)
    grantex_accepts(evidence)
    assert evidence["rails"] == ["plural", "card_network_token", "upi_autopay"]
    assert {c["rail"]: c["max_amount"] for c in evidence["caps"]} == {
        "plural": 50000,
        "card_network_token": 50000,
        "upi_autopay": 500000,
    }
    assert evidence["token_types"] == ["plural_pine_token", "network_token", "upi_mandate_token"]
    assert evidence["verified_at"] == "2026-10-20T08:55:00.000Z"
    again = mce.build(result(), tenant_id=TENANT, merchant_id="m1", mandate_capability=CAPABILITY, now=NOW)
    assert again["evidence_hash"] == evidence["evidence_hash"]
    changed = mce.build(
        result(result_status="unavailable"), tenant_id=TENANT, merchant_id="m1", mandate_capability=CAPABILITY, now=NOW
    )
    assert changed["evidence_hash"] != evidence["evidence_hash"]


def test_from_a_stored_row_with_datetimes() -> None:
    row = SimpleNamespace(
        **{**result().__dict__, "checked_at": NOW - timedelta(hours=1), "expires_at": NOW + timedelta(hours=5)}
    )
    evidence = mce.build(
        row,
        tenant_id=TENANT,
        merchant_id="m1",
        mandate_capability={**CAPABILITY, "instrument_types": ["card"]},
        now=NOW,
    )
    grantex_accepts(evidence)
    assert evidence["rails"] == ["plural", "card_network_token"]


@pytest.mark.parametrize(
    ("overrides", "capability"),
    [
        ({"checked_at": (NOW - timedelta(hours=7)).isoformat()}, CAPABILITY),
        ({"expires_at": (NOW - timedelta(minutes=1)).isoformat()}, CAPABILITY),
        ({"checked_at": (NOW + timedelta(hours=1)).isoformat()}, CAPABILITY),
        ({"provider_environment": "unknown"}, CAPABILITY),
        ({"result_status": "mystery"}, CAPABILITY),
        ({}, None),
        ({}, {**CAPABILITY, "max_amount_minor_units": None}),
        ({}, {**CAPABILITY, "currency": ""}),
    ],
)
def test_unusable_results_send_nothing(overrides, capability) -> None:
    assert (
        mce.build(result(**overrides), tenant_id=TENANT, merchant_id="m1", mandate_capability=capability, now=NOW)
        is None
    )


def test_authority_request_carries_the_evidence_only_for_its_own_scope() -> None:
    from core.commerce.c6z_runtime_vertical import build_shopify_connector_evidence
    from tests.unit.test_oacp_c6z_runtime_vertical import _iso, _now, _packet, _shopify_product

    packet = _packet()
    connector = build_shopify_connector_evidence(
        packet=packet,
        products=[_shopify_product()],
        synced_at=_iso(_now()),
        source_observed_at=_iso(_now()),
        currency="INR",
    )
    plain = build_grantex_authority_request_payload(onboarding_packet=packet, connector_evidence=connector)
    assert "mandate_capability_evidence" not in plain
    evidence = mce.build(
        result(),
        tenant_id=packet["tenant_id"],
        merchant_id=packet["merchant_id"],
        mandate_capability=CAPABILITY,
        now=NOW,
    )
    payload = build_grantex_authority_request_payload(
        onboarding_packet=packet, connector_evidence=connector, mandate_capability_evidence=evidence
    )
    assert payload["mandate_capability_evidence"] == evidence
    with pytest.raises(C6ZRuntimeValidationError):
        build_grantex_authority_request_payload(
            onboarding_packet=packet,
            connector_evidence=connector,
            mandate_capability_evidence={**evidence, "merchant_id": "someone-else"},
        )


class _Session:
    def __init__(self, stored, config) -> None:
        self.stored, self.config, self.queries = stored, config, 0

    async def execute(self, _query):
        self.queries += 1
        return SimpleNamespace(scalar_one_or_none=lambda: self.stored)

    async def get(self, _model, _key):
        return self.config


@pytest.mark.asyncio
async def test_authority_route_helper_is_flagged_and_reads_only_stored_state(monkeypatch) -> None:
    from api.v1 import commerce_runtime
    from core.config import settings

    fresh = datetime.now(UTC)
    stored = SimpleNamespace(
        **{**result().__dict__, "checked_at": fresh - timedelta(minutes=1), "expires_at": fresh + timedelta(hours=5)}
    )
    config = SimpleNamespace(provider_policy={"ucp": {"mandate_capability": CAPABILITY}})
    packet = {"tenant_id": TENANT, "merchant_id": "m1", "seller_agent_id": "s1"}
    session = _Session(stored, config)
    monkeypatch.setattr(settings, "grantex_mandate_capability_evidence_enabled", False)
    assert await commerce_runtime._mandate_capability_evidence(session, TENANT, packet) is None
    assert session.queries == 0
    monkeypatch.setattr(settings, "grantex_mandate_capability_evidence_enabled", True)
    evidence = await commerce_runtime._mandate_capability_evidence(session, TENANT, packet)
    grantex_accepts(evidence)
    assert evidence["tenant_id"] == TENANT and evidence["merchant_id"] == "m1"
    assert await commerce_runtime._mandate_capability_evidence(_Session(None, config), TENANT, packet) is None
