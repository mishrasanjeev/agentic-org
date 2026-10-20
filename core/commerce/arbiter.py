# SPDX-License-Identifier: Apache-2.0
"""The commerce arbiter: a deterministic policy decision that seals ``allow`` or ``refuse``.

No human legs. ``decide`` is a pure function of its inputs: it reads no clock
(``context.now`` is an input), does no I/O, never waits for a person and never
returns "pending". Every rule below is evaluated in the fixed order of
``RULES_BY_ACTION``; each one's name is listed in ``rules_evaluated``; the first
rule that fails is ``failing_rule`` and makes the outcome ``refuse``. A missing
input refuses with the rule that needed it (fail closed).

Rules:

``mandate_present_unexpired``
    A buyer mandate reference and its hash are present and the mandate expires
    after ``now``. Not evaluated for ``mandate_request`` (the action that asks
    for a mandate).
``consent_active``
    The buyer's consent reference is present and its status is active.
``passport_amount_cap``
    The Commerce Passport reference, amount cap and currency are present, the
    passport is unexpired, its currency is the order currency and its cap is at
    least the order amount.
``oacp_artifacts_valid``
    For every OACP artifact family the action requires there is an artifact in
    the decision's tenant, merchant and seller scope (and the buyer's, when it
    is buyer-scoped) that the ES256 verifier accepted (an
    ``oacp-artifact-jws-v1;`` verifier ref with an ``oacp-`` key), that is fresh
    (freshness ``fresh``, expiry after ``now``) and whose revocation snapshot is
    ``fresh`` (not revoked, not ambiguous).
``buyer_authority_verified``
    The buyer authority (mandate, consent, passport) carries a server-side
    verification ref. No such ref exists until server-verified mandates land
    (Phase 4), so the evidence is caller-asserted: with
    ``require_verified_authority`` (``AGENTICORG_ARBITER_REQUIRE_VERIFIED_AUTHORITY``,
    on in strict runtimes) the rule refuses; off, it passes and the decision
    records ``buyer_authority_source: caller_asserted``.
``merchant_limits``
    The merchant's arbiter limits are configured and the order is within them:
    a per-order amount cap, a currency allowlist and a per-order quantity cap.
``afa_evidence_present`` (``session_complete`` only, evaluated last)
    When a payment instrument is a UPI AutoPay mandate, the RBI additional
    factor of authentication (AFA) applies (``core.commerce.india_rails``:
    registration and the first debit always, a subsequent debit above the
    RBI e-mandate limit) and server-side AFA evidence must be present. AFA is
    the buyer's out-of-band step; this rule never waits for it, it refuses
    until the evidence exists. Without a UPI AutoPay instrument it passes.
``refund_within_captured`` (``refund``, the only rule of that action)
    The refund is positive, in the order currency, and the captured amount
    covers it together with every earlier refund of the order.

The decision binds ``payload_hash`` (the RFC 8785 SHA-256 of the action's
binding payload, ``binding_payload``) so the executor can refuse anything but
the exact payload that was allowed (``core.commerce.arbiter_store``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from core.commerce.ledger import commerce_payload_hash, json_safe

POLICY_VERSION = "commerce-arbiter-v3"
DECISION_TTL = timedelta(minutes=10)

Action = Literal["purchase_handoff", "mandate_request", "pos_handoff", "session_complete", "refund"]
Outcome = Literal["allow", "refuse"]
ACTIONS: tuple[str, ...] = ("purchase_handoff", "mandate_request", "pos_handoff", "session_complete", "refund")

RULE_MANDATE = "mandate_present_unexpired"
RULE_CONSENT = "consent_active"
RULE_PASSPORT = "passport_amount_cap"
RULE_AUTHORITY_VERIFIED = "buyer_authority_verified"
RULE_ARTIFACTS = "oacp_artifacts_valid"
RULE_MERCHANT_LIMITS = "merchant_limits"
RULE_AFA = "afa_evidence_present"
RULE_REFUND = "refund_within_captured"
RULE_ORDER: tuple[str, ...] = (
    RULE_MANDATE,
    RULE_CONSENT,
    RULE_PASSPORT,
    RULE_AUTHORITY_VERIFIED,
    RULE_ARTIFACTS,
    RULE_MERCHANT_LIMITS,
)
AUTHORITY_SERVER_VERIFIED = "server_verified"
AUTHORITY_CALLER_ASSERTED = "caller_asserted"

ACTIVE_CONSENT_STATUSES = frozenset({"active", "granted"})
VERIFIER_REF_PREFIX = "oacp-artifact-jws-v1;"
# The OACP artifact families each action needs (cache ``artifact_type`` names).
_COMMITMENT_FAMILIES: tuple[str, ...] = (
    "catalog_snapshot",
    "price",
    "inventory",
    "policy",
    "mandate_capability",
    "protocol_adapter",
)
REQUIRED_ARTIFACT_FAMILIES: dict[str, tuple[str, ...]] = {
    "purchase_handoff": _COMMITMENT_FAMILIES,
    "pos_handoff": _COMMITMENT_FAMILIES,
    "session_complete": _COMMITMENT_FAMILIES,
    "mandate_request": ("policy", "mandate_capability"),
}
RULES_BY_ACTION: dict[str, tuple[str, ...]] = {
    "purchase_handoff": RULE_ORDER,
    "pos_handoff": RULE_ORDER,
    "session_complete": (*RULE_ORDER, RULE_AFA),
    "mandate_request": tuple(rule for rule in RULE_ORDER if rule != RULE_MANDATE),
    "refund": (RULE_REFUND,),
}
# ISO 4217 minor-unit exponents that are not 2 (amounts are compared in minor units).
_CURRENCY_EXPONENTS: dict[str, int] = {"JPY": 0, "KRW": 0, "VND": 0, "CLP": 0, "ISK": 0, "BHD": 3, "KWD": 3, "OMR": 3}


@dataclass(frozen=True)
class MandateEvidence:
    ref: str | None
    hash: str | None
    expires_at: datetime | None


@dataclass(frozen=True)
class ConsentEvidence:
    ref: str | None
    status: str | None


@dataclass(frozen=True)
class PassportEvidence:
    ref: str | None
    max_amount_minor_units: int | None
    currency: str | None
    expires_at: datetime | None


@dataclass(frozen=True)
class AfaEvidence:
    """What ``afa_evidence_present`` reads: the debit (for the RBI thresholds) and the server-side evidence ref."""

    debit_kind: str
    amount_minor_units: int | None
    currency: str | None
    purpose: str | None
    limit_minor_units: int
    higher_limit_minor_units: int
    # Set only from AFA evidence recorded server-side (a verified provider event), never from a request.
    evidence_ref: str | None = None


@dataclass(frozen=True)
class RefundEvidence:
    """The order's payment state a ``refund`` decision reads (minor units, from the payment operation records)."""

    order_id: str
    currency: str
    captured_minor_units: int
    refunded_minor_units: int


@dataclass(frozen=True)
class ArtifactEvidence:
    artifact_id: str
    family: str
    tenant_id: str | None
    merchant_id: str | None
    seller_agent_id: str | None
    buyer_agent_id: str | None
    verifier_result_ref: str | None
    expires_at: datetime | None
    freshness_status: str | None
    revocation_status: str | None

    @property
    def payload_hash(self) -> str | None:
        for part in str(self.verifier_result_ref or "").split(";"):
            if part.startswith("payload_hash="):
                return part.removeprefix("payload_hash=") or None
        return None


@dataclass(frozen=True)
class MerchantLimits:
    max_order_amount_minor_units: int | None
    currency_allowlist: tuple[str, ...]
    max_quantity_per_order: int | None
    # Not rules: a refusal is also queued for review when escalate_to_human is on.
    escalate_to_human: bool = False
    review_agent_id: str | None = None


@dataclass(frozen=True)
class ArbiterContext:
    """Everything a decision reads. ``now`` is the decision time (an input, never read inside)."""

    tenant_id: str
    merchant_id: str
    seller_agent_id: str
    buyer_agent_id: str | None
    now: datetime
    payload: Mapping[str, Any]
    amount_minor_units: int | None
    currency: str | None
    quantity: int | None
    mandate: MandateEvidence | None = None
    consent: ConsentEvidence | None = None
    passport: PassportEvidence | None = None
    artifacts: tuple[ArtifactEvidence, ...] = ()
    merchant_limits: MerchantLimits | None = None
    # A server-side verification ref for the buyer authority (none exists before Phase 4;
    # never taken from the request body). Without one the authority is caller-asserted.
    authority_verification_ref: str | None = None
    # Refuse caller-asserted authority (AGENTICORG_ARBITER_REQUIRE_VERIFIED_AUTHORITY): an input, not read here.
    require_verified_authority: bool = False
    # RBI AFA inputs for a UPI AutoPay payment (None when no instrument is one).
    afa: AfaEvidence | None = None
    # The order a ``refund`` decision is for.
    refund: RefundEvidence | None = None
    # The intent, cart and payment mandate hashes (lowercase hex SHA-256 of each compact JWT) once
    # verified mandates exist; sealed with the decision and carried in the provider order metadata.
    mandate_hashes: Mapping[str, str | None] | None = None

    @property
    def buyer_authority_source(self) -> str:
        return AUTHORITY_SERVER_VERIFIED if self.authority_verification_ref else AUTHORITY_CALLER_ASSERTED


@dataclass(frozen=True)
class RuleResult:
    rule: str
    passed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "passed": self.passed, "reason": self.reason}


@dataclass(frozen=True)
class Decision:
    decision_id: str
    tenant_id: str
    merchant_id: str
    seller_agent_id: str
    buyer_agent_id: str | None
    action: str
    outcome: Outcome
    rules_evaluated: tuple[str, ...]
    failing_rule: str | None
    rule_results: tuple[RuleResult, ...]
    evidence: dict[str, Any]
    evidence_hashes: tuple[str, ...]
    payload_hash: str
    policy_version: str
    decided_at: datetime
    expires_at: datetime
    reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.outcome == "allow"

    @property
    def buyer_authority_source(self) -> str:
        return str(self.extra.get("buyer_authority_source") or AUTHORITY_CALLER_ASSERTED)

    def to_public(self) -> dict[str, Any]:
        """The decision as returned to the caller and sealed to the ledger (no raw evidence values)."""
        return {
            "decision_id": self.decision_id,
            "action": self.action,
            "outcome": self.outcome,
            "rules_evaluated": list(self.rules_evaluated),
            "failing_rule": self.failing_rule,
            "reason": self.reason,
            "evidence_hashes": list(self.evidence_hashes),
            "payload_hash": self.payload_hash,
            "buyer_authority_source": self.buyer_authority_source,
            "policy_version": self.policy_version,
            "decided_at": _iso(self.decided_at),
            "expires_at": _iso(self.expires_at),
        }


MANDATE_KINDS: tuple[str, ...] = ("intent", "cart", "payment")


def mandate_hashes(context: ArbiterContext) -> dict[str, str | None]:
    """The three mandate hashes (null until verified mandates populate them; never from a request body)."""
    source = context.mandate_hashes or {}
    out: dict[str, str | None] = {}
    for kind in MANDATE_KINDS:
        value = source.get(kind)
        valid = isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
        out[kind] = value if valid else None
    return out


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Rules: pure functions of the context. Each returns (passed, reason).
# ---------------------------------------------------------------------------


def rule_mandate_present_unexpired(context: ArbiterContext) -> tuple[bool, str]:
    mandate = context.mandate
    if mandate is None or not mandate.ref or not mandate.hash:
        return False, "mandate_missing"
    if mandate.expires_at is None:
        return False, "mandate_expiry_missing"
    if mandate.expires_at <= context.now:
        return False, "mandate_expired"
    return True, "mandate_present_unexpired"


def rule_consent_active(context: ArbiterContext) -> tuple[bool, str]:
    consent = context.consent
    if consent is None or not consent.ref or not consent.status:
        return False, "consent_missing"
    if consent.status.strip().lower() not in ACTIVE_CONSENT_STATUSES:
        return False, f"consent_{consent.status.strip().lower()[:32] or 'unknown'}"
    return True, "consent_active"


def rule_passport_amount_cap(context: ArbiterContext) -> tuple[bool, str]:
    passport = context.passport
    if passport is None or not passport.ref:
        return False, "passport_missing"
    if passport.max_amount_minor_units is None or not passport.currency:
        return False, "passport_cap_missing"
    if passport.expires_at is None:
        return False, "passport_expiry_missing"
    if passport.expires_at <= context.now:
        return False, "passport_expired"
    if context.amount_minor_units is None or not context.currency:
        return False, "order_amount_missing"
    if passport.currency.upper() != context.currency.upper():
        return False, "passport_currency_mismatch"
    if context.amount_minor_units > passport.max_amount_minor_units:
        return False, "passport_amount_cap_exceeded"
    return True, "within_passport_amount_cap"


def rule_buyer_authority_verified(context: ArbiterContext) -> tuple[bool, str]:
    if context.authority_verification_ref:
        return True, "buyer_authority_server_verified"
    if context.require_verified_authority:
        return False, "buyer_authority_caller_asserted"
    return True, "buyer_authority_caller_asserted_accepted"


def _artifact_in_scope(artifact: ArtifactEvidence, context: ArbiterContext) -> bool:
    if artifact.tenant_id != context.tenant_id or artifact.merchant_id != context.merchant_id:
        return False
    if artifact.seller_agent_id != context.seller_agent_id:
        return False
    return artifact.buyer_agent_id in (None, context.buyer_agent_id)


def _artifact_problem(artifact: ArtifactEvidence, context: ArbiterContext) -> str | None:
    ref = str(artifact.verifier_result_ref or "")
    if not ref.startswith(VERIFIER_REF_PREFIX) or ";kid=oacp-" not in ref or ";status=valid" not in ref:
        return "unverified"
    if artifact.revocation_status == "revoked":
        return "revoked"
    if artifact.revocation_status != "fresh":
        return "revocation_unknown"
    if artifact.expires_at is None or artifact.expires_at <= context.now:
        return "expired"
    if artifact.freshness_status != "fresh":
        return "stale"
    return None


def rule_oacp_artifacts_valid(context: ArbiterContext, action: str) -> tuple[bool, str]:
    required = REQUIRED_ARTIFACT_FAMILIES.get(action)
    if not required:
        return False, "action_unknown"
    for family in required:
        candidates = [a for a in context.artifacts if a.family == family and _artifact_in_scope(a, context)]
        if not candidates:
            return False, f"artifact_missing:{family}"
        problems = [_artifact_problem(a, context) for a in candidates]
        if None not in problems:
            # The most decisive problem is reported: revocation before expiry before staleness.
            for problem in ("revoked", "unverified", "revocation_unknown", "expired", "stale"):
                if problem in problems:
                    return False, f"artifact_{problem}:{family}"
    return True, "oacp_artifacts_verified_fresh_unrevoked"


def rule_merchant_limits(context: ArbiterContext) -> tuple[bool, str]:
    limits = context.merchant_limits
    if limits is None:
        return False, "merchant_limits_missing"
    if limits.max_order_amount_minor_units is None or not limits.currency_allowlist:
        return False, "merchant_limits_missing"
    if limits.max_quantity_per_order is None:
        return False, "merchant_limits_missing"
    if context.amount_minor_units is None or not context.currency or context.quantity is None:
        return False, "order_amount_missing"
    if context.currency.upper() not in {c.upper() for c in limits.currency_allowlist}:
        return False, "currency_not_allowed"
    if context.quantity > limits.max_quantity_per_order:
        return False, "quantity_cap_exceeded"
    if context.amount_minor_units > limits.max_order_amount_minor_units:
        return False, "order_amount_cap_exceeded"
    return True, "within_merchant_limits"


def rule_afa_evidence_present(context: ArbiterContext) -> tuple[bool, str]:
    from core.commerce.india_rails import INSTRUMENT_UPI_AUTOPAY, AfaThresholds, afa_requirement

    payment = context.payload.get("payment") if isinstance(context.payload, Mapping) else None
    upi = any(
        isinstance(item, Mapping) and item.get("type") == INSTRUMENT_UPI_AUTOPAY
        for item in (payment if isinstance(payment, list | tuple) else ())
    )
    afa = context.afa
    if afa is None:
        return (False, "afa_input_missing") if upi else (True, "afa_not_applicable")
    requirement = afa_requirement(
        debit_kind=afa.debit_kind,  # type: ignore[arg-type]
        amount_minor_units=afa.amount_minor_units,
        currency=afa.currency,
        purpose=afa.purpose,
        thresholds=AfaThresholds(afa.limit_minor_units, afa.higher_limit_minor_units),
    )
    if not requirement.required:
        return True, f"afa_not_required:{requirement.basis}"
    if afa.evidence_ref:
        return True, "afa_evidence_present"
    return False, "afa_evidence_missing"


def rule_refund_within_captured(context: ArbiterContext) -> tuple[bool, str]:
    refund = context.refund
    if refund is None:
        return False, "refund_input_missing"
    amount = context.amount_minor_units
    if amount is None or amount < 1:
        return False, "refund_amount_invalid"
    if not context.currency or context.currency.upper() != refund.currency.upper():
        return False, "refund_currency_mismatch"
    if refund.captured_minor_units < 1:
        return False, "order_not_captured"
    if refund.refunded_minor_units + amount > refund.captured_minor_units:
        return False, "refund_exceeds_captured"
    return True, "refund_within_captured"


_RULES: dict[str, Callable[[ArbiterContext, str], tuple[bool, str]]] = {
    RULE_MANDATE: lambda context, _action: rule_mandate_present_unexpired(context),
    RULE_CONSENT: lambda context, _action: rule_consent_active(context),
    RULE_PASSPORT: lambda context, _action: rule_passport_amount_cap(context),
    RULE_AUTHORITY_VERIFIED: lambda context, _action: rule_buyer_authority_verified(context),
    RULE_ARTIFACTS: rule_oacp_artifacts_valid,
    RULE_MERCHANT_LIMITS: lambda context, _action: rule_merchant_limits(context),
    RULE_AFA: lambda context, _action: rule_afa_evidence_present(context),
    RULE_REFUND: lambda context, _action: rule_refund_within_captured(context),
}


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


def evidence_summary(context: ArbiterContext) -> dict[str, Any]:
    """The six evidence keys the Grantex countersignature call carries (null when absent)."""
    return {
        "mandate_ref": context.mandate.ref if context.mandate else None,
        "mandate_hash": context.mandate.hash if context.mandate else None,
        "consent_ref": context.consent.ref if context.consent else None,
        "consent_status": context.consent.status if context.consent else None,
        "passport_ref": context.passport.ref if context.passport else None,
        "artifact_ids": sorted({a.artifact_id for a in context.artifacts}),
    }


def evidence_hashes(context: ArbiterContext) -> tuple[str, ...]:
    hashes: set[str] = set()
    if context.mandate and context.mandate.hash:
        hashes.add(f"mandate:{context.mandate.hash}")
    for artifact in context.artifacts:
        if artifact.payload_hash:
            hashes.add(f"artifact:{artifact.artifact_id}:{artifact.payload_hash}")
    if context.consent and context.consent.ref:
        hashes.add("consent:" + _sha256(f"{context.consent.ref}|{context.consent.status or ''}"))
    if context.passport and context.passport.ref:
        hashes.add("passport:" + _sha256(context.passport.ref))
    if context.afa and context.afa.evidence_ref:
        hashes.add("afa:" + _sha256(context.afa.evidence_ref))
    return tuple(sorted(hashes))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def decision_id_for(context: ArbiterContext, action: str, payload_hash: str) -> str:
    """Deterministic: the same inputs at the same instant give the same id."""
    material = commerce_payload_hash(
        {
            "policy_version": POLICY_VERSION,
            "tenant_id": context.tenant_id,
            "merchant_id": context.merchant_id,
            "seller_agent_id": context.seller_agent_id,
            "buyer_agent_id": context.buyer_agent_id,
            "action": action,
            "payload_hash": payload_hash,
            "evidence_hashes": list(evidence_hashes(context)),
            "buyer_authority_source": context.buyer_authority_source,
            "require_verified_authority": context.require_verified_authority,
            "now": _iso(context.now),
        }
    )
    return f"arbdec_{material[:40]}"


def decide(action: str, context: ArbiterContext) -> Decision:
    """Evaluate every rule for ``action`` in order; the first failure refuses. Pure: no clock, no I/O."""
    payload_hash = commerce_payload_hash(context.payload)
    rules = RULES_BY_ACTION.get(action)
    results: list[RuleResult] = []
    if rules is None:
        results.append(RuleResult(rule="action_supported", passed=False, reason="action_unknown"))
        rules_evaluated: tuple[str, ...] = ("action_supported",)
    else:
        for rule in rules:
            passed, reason = _RULES[rule](context, action)
            results.append(RuleResult(rule=rule, passed=passed, reason=reason))
        rules_evaluated = tuple(rules)
    failing = next((result for result in results if not result.passed), None)
    return Decision(
        decision_id=decision_id_for(context, action, payload_hash),
        tenant_id=context.tenant_id,
        merchant_id=context.merchant_id,
        seller_agent_id=context.seller_agent_id,
        buyer_agent_id=context.buyer_agent_id,
        action=action,
        outcome="refuse" if failing else "allow",
        rules_evaluated=rules_evaluated,
        failing_rule=failing.rule if failing else None,
        rule_results=tuple(results),
        evidence=evidence_summary(context),
        evidence_hashes=evidence_hashes(context),
        payload_hash=payload_hash,
        policy_version=POLICY_VERSION,
        decided_at=context.now,
        expires_at=context.now + DECISION_TTL,
        reason=failing.reason if failing else "all_rules_passed",
        extra={
            "buyer_authority_source": context.buyer_authority_source,
            "mandate_hashes": mandate_hashes(context),
        },
    )


# ---------------------------------------------------------------------------
# Inputs: binding payloads, amounts and evidence from runtime records
# ---------------------------------------------------------------------------

# The handoff fields a decision binds. Everything else in a handoff (execution
# flags, timestamps, labels) may change between preparation and execution
# without changing what was decided.
PURCHASE_BINDING_FIELDS: tuple[str, ...] = (
    "handoff_id",
    "merchant_id",
    "seller_agent_id",
    "buyer_agent_id",
    "product_ref",
    "variant_ref",
    "sku",
    "quantity",
    "price_snapshot",
    "currency",
    "provider",
    "idempotency_key",
)
POS_BINDING_FIELDS: tuple[str, ...] = (
    "packet_id",
    "tenant_id",
    "merchant_id",
    "seller_agent_id",
    "store_id",
    "buyer_session_ref",
    "product_id",
    "variant_id",
    "sku",
    "quantity",
    "displayed_price",
    "currency",
    "artifact_refs",
    "idempotency_key",
)


def binding_payload(action: str, source: Mapping[str, Any]) -> dict[str, Any]:
    """The part of a handoff or packet a decision for ``action`` binds (JSON-safe)."""
    fields = POS_BINDING_FIELDS if action == "pos_handoff" else PURCHASE_BINDING_FIELDS
    return json_safe({"action": action, **{name: source.get(name) for name in fields}})


def amount_minor_units(price: Any, currency: Any, quantity: int | None) -> int | None:
    """``price`` x ``quantity`` in the currency's minor units, or None when either is unusable."""
    if price in (None, "") or not currency or quantity is None or quantity < 1:
        return None
    try:
        unit = Decimal(str(price))
    except (InvalidOperation, ValueError):
        return None
    if not unit.is_finite() or unit < 0:
        return None
    exponent = _CURRENCY_EXPONENTS.get(str(currency).upper(), 2)
    minor = unit * (Decimal(10) ** exponent) * quantity
    if minor != minor.to_integral_value():
        return None
    return int(minor)


def artifact_evidence_from_records(records: Sequence[Any]) -> tuple[ArtifactEvidence, ...]:
    """Arbiter evidence from OACP cache records (``OacpPersistentArtifactCacheRecord``)."""
    out: list[ArtifactEvidence] = []
    for record in records:
        out.append(
            ArtifactEvidence(
                artifact_id=str(record.artifact_id),
                family=str(record.artifact_type),
                tenant_id=record.tenant_id,
                merchant_id=record.merchant_id,
                seller_agent_id=record.seller_agent_id,
                buyer_agent_id=record.buyer_agent_id,
                verifier_result_ref=record.verifier_result_ref,
                expires_at=parse_time(record.expires_at),
                freshness_status=record.freshness_status,
                revocation_status=record.revocation_snapshot_status,
            )
        )
    return tuple(out)


def merchant_limits_from_config(config: Mapping[str, Any] | None) -> MerchantLimits | None:
    """The arbiter limits a merchant config sets under ``provider_policy.arbiter`` (None when absent)."""
    if not isinstance(config, Mapping):
        return None
    policy = config.get("provider_policy")
    arbiter = policy.get("arbiter") if isinstance(policy, Mapping) else None
    if not isinstance(arbiter, Mapping):
        return None
    allowlist = arbiter.get("currency_allowlist")
    return MerchantLimits(
        max_order_amount_minor_units=_int_or_none(arbiter.get("max_order_amount_minor_units")),
        currency_allowlist=tuple(str(c).upper() for c in allowlist) if isinstance(allowlist, list | tuple) else (),
        max_quantity_per_order=_int_or_none(arbiter.get("max_quantity_per_order")),
        escalate_to_human=arbiter.get("escalate_to_human") is True,
        review_agent_id=str(arbiter.get("review_agent_id") or "").strip() or None,
    )


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | str):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None
