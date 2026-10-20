# SPDX-License-Identifier: Apache-2.0
"""The commerce arbiter (G11): every rule allows on good input and refuses on each bad or missing input."""

from __future__ import annotations

import ast
import dataclasses
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.commerce import arbiter
from core.commerce.arbiter import (
    ArbiterContext,
    ArtifactEvidence,
    ConsentEvidence,
    MandateEvidence,
    MerchantLimits,
    PassportEvidence,
    amount_minor_units,
    binding_payload,
    decide,
    merchant_limits_from_config,
)

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
TENANT = "11111111-1111-4111-8111-111111111111"
VERIFIED = (
    "oacp-artifact-jws-v1;kid=oacp-2026-10;payload_hash=" + "a" * 64 + ";status=valid;verified_at=2026-10-20T08:59:00Z"
)
FAMILIES = ("catalog_snapshot", "price", "inventory", "policy", "mandate_capability", "protocol_adapter")


def _artifact(family: str, **overrides: object) -> ArtifactEvidence:
    base = ArtifactEvidence(
        artifact_id=f"oacp:{family}:merchant.example",
        family=family,
        tenant_id=TENANT,
        merchant_id="merchant.example",
        seller_agent_id="seller-01",
        buyer_agent_id=None,
        verifier_result_ref=VERIFIED,
        expires_at=NOW + timedelta(minutes=5),
        freshness_status="fresh",
        revocation_status="fresh",
    )
    return dataclasses.replace(base, **overrides)


def _context(**overrides: object) -> ArbiterContext:
    base = ArbiterContext(
        tenant_id=TENANT,
        merchant_id="merchant.example",
        seller_agent_id="seller-01",
        buyer_agent_id="shopper-01",
        now=NOW,
        payload={"handoff_id": "purchase_handoff_1", "quantity": 2},
        amount_minor_units=5000,
        currency="INR",
        quantity=2,
        mandate=MandateEvidence(ref="mandate-1", hash="b" * 64, expires_at=NOW + timedelta(hours=1)),
        consent=ConsentEvidence(ref="consent-1", status="active"),
        passport=PassportEvidence(
            ref="passport-1", max_amount_minor_units=10000, currency="INR", expires_at=NOW + timedelta(hours=1)
        ),
        artifacts=tuple(_artifact(family) for family in FAMILIES),
        merchant_limits=MerchantLimits(
            max_order_amount_minor_units=20000, currency_allowlist=("INR",), max_quantity_per_order=5
        ),
    )
    return dataclasses.replace(base, **overrides)


def test_allow_when_every_rule_passes() -> None:
    decision = decide("purchase_handoff", _context())
    assert decision.outcome == "allow"
    assert decision.failing_rule is None
    assert decision.rules_evaluated == arbiter.RULE_ORDER
    assert decision.policy_version == arbiter.POLICY_VERSION
    assert decision.expires_at - decision.decided_at == arbiter.DECISION_TTL
    assert len(decision.payload_hash) == 64
    assert decision.decision_id.startswith("arbdec_")
    assert set(decision.evidence) == {
        "mandate_ref", "mandate_hash", "consent_ref", "consent_status", "passport_ref", "artifact_ids"
    }
    assert "mandate:" + "b" * 64 in decision.evidence_hashes


def test_decision_is_deterministic_and_bound_to_the_payload() -> None:
    first = decide("purchase_handoff", _context())
    again = decide("purchase_handoff", _context())
    other = decide("purchase_handoff", _context(payload={"handoff_id": "purchase_handoff_1", "quantity": 3}))
    assert first == again
    assert other.payload_hash != first.payload_hash
    assert other.decision_id != first.decision_id


@pytest.mark.parametrize(
    ("overrides", "rule", "reason"),
    [
        ({"mandate": None}, "mandate_present_unexpired", "mandate_missing"),
        ({"mandate": MandateEvidence(ref="m", hash=None, expires_at=NOW + timedelta(hours=1))},
         "mandate_present_unexpired", "mandate_missing"),
        ({"mandate": MandateEvidence(ref="m", hash="b" * 64, expires_at=None)},
         "mandate_present_unexpired", "mandate_expiry_missing"),
        ({"mandate": MandateEvidence(ref="m", hash="b" * 64, expires_at=NOW)},
         "mandate_present_unexpired", "mandate_expired"),
        ({"consent": None}, "consent_active", "consent_missing"),
        ({"consent": ConsentEvidence(ref="c", status="revoked")}, "consent_active", "consent_revoked"),
        ({"consent": ConsentEvidence(ref="c", status="pending")}, "consent_active", "consent_pending"),
        ({"passport": None}, "passport_amount_cap", "passport_missing"),
        ({"passport": PassportEvidence(ref="p", max_amount_minor_units=None, currency="INR",
                                       expires_at=NOW + timedelta(hours=1))},
         "passport_amount_cap", "passport_cap_missing"),
        ({"passport": PassportEvidence(ref="p", max_amount_minor_units=10000, currency="INR", expires_at=None)},
         "passport_amount_cap", "passport_expiry_missing"),
        ({"passport": PassportEvidence(ref="p", max_amount_minor_units=10000, currency="INR",
                                       expires_at=NOW - timedelta(seconds=1))},
         "passport_amount_cap", "passport_expired"),
        ({"passport": PassportEvidence(ref="p", max_amount_minor_units=10000, currency="USD",
                                       expires_at=NOW + timedelta(hours=1))},
         "passport_amount_cap", "passport_currency_mismatch"),
        ({"passport": PassportEvidence(ref="p", max_amount_minor_units=4999, currency="INR",
                                       expires_at=NOW + timedelta(hours=1))},
         "passport_amount_cap", "passport_amount_cap_exceeded"),
        ({"amount_minor_units": None}, "passport_amount_cap", "order_amount_missing"),
        ({"artifacts": ()}, "oacp_artifacts_valid", "artifact_missing:catalog_snapshot"),
        ({"artifacts": tuple(_artifact(f) for f in FAMILIES if f != "price")},
         "oacp_artifacts_valid", "artifact_missing:price"),
        ({"artifacts": tuple(_artifact(f, verifier_result_ref="oacp:price:x:verified" if f == "price" else VERIFIED)
                             for f in FAMILIES)},
         "oacp_artifacts_valid", "artifact_unverified:price"),
        ({"artifacts": tuple(_artifact(f, revocation_status="revoked" if f == "inventory" else "fresh")
                             for f in FAMILIES)},
         "oacp_artifacts_valid", "artifact_revoked:inventory"),
        ({"artifacts": tuple(_artifact(f, revocation_status="stale" if f == "inventory" else "fresh")
                             for f in FAMILIES)},
         "oacp_artifacts_valid", "artifact_revocation_unknown:inventory"),
        ({"artifacts": tuple(_artifact(f, expires_at=NOW if f == "policy" else NOW + timedelta(minutes=5))
                             for f in FAMILIES)},
         "oacp_artifacts_valid", "artifact_expired:policy"),
        ({"artifacts": tuple(_artifact(f, freshness_status="provisional" if f == "policy" else "fresh")
                             for f in FAMILIES)},
         "oacp_artifacts_valid", "artifact_stale:policy"),
        ({"artifacts": tuple(_artifact(f, merchant_id="other.example") for f in FAMILIES)},
         "oacp_artifacts_valid", "artifact_missing:catalog_snapshot"),
        ({"artifacts": tuple(_artifact(f, buyer_agent_id="other-buyer") for f in FAMILIES)},
         "oacp_artifacts_valid", "artifact_missing:catalog_snapshot"),
        ({"merchant_limits": None}, "merchant_limits", "merchant_limits_missing"),
        ({"merchant_limits": MerchantLimits(max_order_amount_minor_units=None, currency_allowlist=("INR",),
                                            max_quantity_per_order=5)},
         "merchant_limits", "merchant_limits_missing"),
        ({"merchant_limits": MerchantLimits(max_order_amount_minor_units=20000, currency_allowlist=("USD",),
                                            max_quantity_per_order=5)},
         "merchant_limits", "currency_not_allowed"),
        ({"merchant_limits": MerchantLimits(max_order_amount_minor_units=20000, currency_allowlist=("INR",),
                                            max_quantity_per_order=1)},
         "merchant_limits", "quantity_cap_exceeded"),
        ({"merchant_limits": MerchantLimits(max_order_amount_minor_units=4000, currency_allowlist=("INR",),
                                            max_quantity_per_order=5)},
         "merchant_limits", "order_amount_cap_exceeded"),
    ],
)
def test_each_rule_refuses_and_names_itself(overrides: dict, rule: str, reason: str) -> None:
    decision = decide("purchase_handoff", _context(**overrides))
    assert decision.outcome == "refuse"
    assert decision.failing_rule == rule
    assert decision.reason == reason
    # Every rule is evaluated and listed, the failing one among them.
    assert decision.rules_evaluated == arbiter.RULE_ORDER
    assert decision.failing_rule in decision.rules_evaluated
    assert [result.rule for result in decision.rule_results] == list(arbiter.RULE_ORDER)


def test_first_failure_wins_in_rule_order() -> None:
    decision = decide("purchase_handoff", _context(consent=None, merchant_limits=None))
    assert decision.failing_rule == "consent_active"
    failed = [result.rule for result in decision.rule_results if not result.passed]
    assert failed == ["consent_active", "merchant_limits"]


def test_an_empty_context_refuses_on_the_first_rule() -> None:
    decision = decide(
        "pos_handoff",
        ArbiterContext(
            tenant_id=TENANT, merchant_id="m", seller_agent_id="s", buyer_agent_id=None, now=NOW,
            payload={}, amount_minor_units=None, currency=None, quantity=None,
        ),
    )
    assert decision.outcome == "refuse"
    assert decision.failing_rule == "mandate_present_unexpired"
    # Every evidence rule fails; buyer_authority_verified only refuses when verified authority is required.
    assert all(not result.passed for result in decision.rule_results if result.rule != "buyer_authority_verified")


def test_mandate_request_does_not_require_a_mandate() -> None:
    decision = decide("mandate_request", _context(mandate=None))
    assert decision.outcome == "allow"
    assert "mandate_present_unexpired" not in decision.rules_evaluated


def test_unknown_action_refuses() -> None:
    decision = decide("refund", _context())
    assert decision.outcome == "refuse"
    assert decision.failing_rule == "action_supported"


def test_allowed_decision_expires_after_the_ttl_from_the_input_clock() -> None:
    later = NOW + timedelta(days=3)
    decision = decide("purchase_handoff", _context(now=later, mandate=MandateEvidence(
        ref="m", hash="b" * 64, expires_at=later + timedelta(hours=1)), passport=PassportEvidence(
        ref="p", max_amount_minor_units=10000, currency="INR", expires_at=later + timedelta(hours=1)),
        artifacts=tuple(_artifact(f, expires_at=later + timedelta(minutes=1)) for f in FAMILIES)))
    assert decision.outcome == "allow"
    assert decision.decided_at == later
    assert decision.expires_at == later + timedelta(minutes=10)


def test_binding_payload_ignores_execution_flags_and_timestamps() -> None:
    handoff = {
        "handoff_id": "h1", "merchant_id": "m", "seller_agent_id": "s", "buyer_agent_id": "b",
        "product_ref": "p", "variant_ref": "v", "sku": "SKU", "quantity": 1, "price_snapshot": "25.00",
        "currency": "INR", "provider": "plural_pine", "idempotency_key": "k",
    }
    assert binding_payload("purchase_handoff", {**handoff, "live_execution_enabled": False}) == binding_payload(
        "purchase_handoff", {**handoff, "live_execution_enabled": True}
    )
    assert binding_payload("purchase_handoff", handoff) != binding_payload(
        "purchase_handoff", {**handoff, "price_snapshot": "26.00"}
    )


@pytest.mark.parametrize(
    ("price", "currency", "quantity", "expected"),
    [("25.00", "INR", 2, 5000), ("100", "JPY", 3, 300), ("1.234", "KWD", 1, 1234), ("1.005", "INR", 1, None),
     (None, "INR", 1, None), ("abc", "INR", 1, None), ("-1", "INR", 1, None), ("25.00", None, 1, None)],
)
def test_amount_minor_units(price, currency, quantity, expected) -> None:
    assert amount_minor_units(price, currency, quantity) == expected


def test_merchant_limits_come_from_provider_policy_arbiter() -> None:
    config = {"provider_policy": {"arbiter": {
        "max_order_amount_minor_units": 50000, "currency_allowlist": ["inr"], "max_quantity_per_order": "3",
        "escalate_to_human": True, "review_agent_id": "22222222-2222-4222-8222-222222222222",
    }}}
    limits = merchant_limits_from_config(config)
    assert limits == MerchantLimits(
        max_order_amount_minor_units=50000, currency_allowlist=("INR",), max_quantity_per_order=3,
        escalate_to_human=True, review_agent_id="22222222-2222-4222-8222-222222222222",
    )
    assert merchant_limits_from_config({"provider_policy": {}}) is None
    assert merchant_limits_from_config(None) is None
    # escalate_to_human defaults to false
    assert merchant_limits_from_config({"provider_policy": {"arbiter": {}}}).escalate_to_human is False


def test_arbiter_module_has_no_clock_io_or_human_dependency() -> None:
    """No human legs: the arbiter imports nothing from HITL, reads no clock and awaits nothing (G12 c)."""
    source = Path(inspect.getfile(arbiter)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not any("hitl" in name or "approval" in name for name in imported)
    assert not any(name.startswith(("httpx", "requests", "sqlalchemy", "core.database", "redis")) for name in imported)
    assert not any(isinstance(node, ast.Await | ast.AsyncFunctionDef) for node in ast.walk(tree))
    calls = {ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    assert not any(call.endswith((".now", ".utcnow", ".today")) or call in {"time.time", "time.monotonic"}
                   for call in calls)
    assert "pending" not in {arbiter.Outcome.__args__[0], arbiter.Outcome.__args__[1]}


# ---------------------------------------------------------------------------
# buyer_authority_verified (D1): fail closed on caller-asserted authority
# ---------------------------------------------------------------------------


def test_caller_asserted_authority_is_refused_when_verified_authority_is_required() -> None:
    decision = decide("purchase_handoff", _context(require_verified_authority=True))
    assert decision.outcome == "refuse"
    assert decision.failing_rule == "buyer_authority_verified"
    assert decision.reason == "buyer_authority_caller_asserted"
    assert decision.to_public()["buyer_authority_source"] == "caller_asserted"


def test_caller_asserted_authority_is_recorded_and_allowed_when_not_required() -> None:
    decision = decide("purchase_handoff", _context(require_verified_authority=False))
    assert decision.outcome == "allow"
    assert "buyer_authority_verified" in decision.rules_evaluated
    assert decision.to_public()["buyer_authority_source"] == "caller_asserted"
    [result] = [r for r in decision.rule_results if r.rule == "buyer_authority_verified"]
    assert result.passed and result.reason == "buyer_authority_caller_asserted_accepted"


def test_server_verified_authority_passes_the_rule_when_required() -> None:
    decision = decide(
        "pos_handoff", _context(require_verified_authority=True, authority_verification_ref="mandate-verification-1")
    )
    assert decision.outcome == "allow"
    assert decision.to_public()["buyer_authority_source"] == "server_verified"


def test_the_requirement_changes_the_decision_id() -> None:
    assert decide("pos_handoff", _context()).decision_id != decide(
        "pos_handoff", _context(require_verified_authority=True)
    ).decision_id


def test_mandate_request_also_requires_verified_authority() -> None:
    decision = decide("mandate_request", _context(mandate=None, require_verified_authority=True))
    assert decision.failing_rule == "buyer_authority_verified"


@pytest.mark.parametrize(
    ("env", "explicit", "expected"),
    [
        ("production", None, True),
        ("staging", None, True),
        ("preview-unknown", None, True),
        ("development", None, False),
        ("test", None, False),
        ("production", False, False),
        ("development", True, True),
    ],
)
def test_require_verified_authority_flag_defaults_on_in_strict_runtimes(monkeypatch, env, explicit, expected) -> None:
    from core import config

    monkeypatch.setattr(config.settings, "arbiter_require_verified_authority", explicit)
    assert config.arbiter_require_verified_authority_enabled(env) is expected


def test_buyer_authority_body_cannot_assert_a_verification_ref() -> None:
    from pydantic import ValidationError

    from api.v1.commerce_runtime import BuyerAuthorityEvidence

    with pytest.raises(ValidationError):
        BuyerAuthorityEvidence(authority_verification_ref="forged")  # type: ignore[call-arg]
