# SPDX-License-Identifier: Apache-2.0
"""Grantex countersignature of commerce arbiter decisions (cross-repo contract section 7).

AgenticOrg's arbiter is authoritative; Grantex only countersigns. After a
decision is committed (``core.commerce.arbiter_store.persist_decision``), the
route calls ``countersign_decision``, outside any database transaction:

1. The decision is posted to ``POST /v1/commerce/oacp/arbiter-decisions`` with
   the contract body (``arbiter_decision_body``): ``failing_rule`` null for an
   allow and one of ``rules_evaluated`` for a refusal, all six evidence keys
   (null when absent), ``decided_at`` the decision time (Grantex refuses one
   older than 10 minutes).
2. A refusal is recorded by Grantex and nothing comes back but ``recorded``.
3. An allow comes back with a ``decision+jwt`` grant in the machine profile.
   It is verified against the pinned Grantex JWKS: issuer, audience
   ``urn:grantex:decision``, ``approver_auth == policy_arbiter``,
   ``action_hash == payload_hash``, ``decision_request == decision_id``,
   ``sub == buyer_agent_id``, no ``acr``, unexpired and at most 10 minutes
   after the decision. Its ``jti`` is then attached to the decision row in a
   short transaction of its own (the one-time ``grantex_grant_jti``
   transition) and sealed to the ledger (``commerce.arbiter.decision.countersigned``).

With ``AGENTICORG_ARBITER_REQUIRE_GRANTEX_GRANT`` on (unset: on in strict
runtimes), ``require_allow_decision`` consumes only an allow that carries a
verified grant: an allow whose countersignature is missing (Grantex
unreachable, misconfigured, refused or an unverifiable grant) stays
allow-pending and is refused ``grantex_grant_missing``. Nothing waits or
retries: the caller asks for a new decision. Posting follows
``AGENTICORG_ARBITER_GRANTEX_POST_ENABLED`` (unset: the grant flag).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from core.commerce.arbiter import DECISION_TTL, Decision
from core.commerce.grantex_service import (
    GrantexServiceClient,
    GrantexServiceUnavailableError,
    GrantexTokenError,
    grantex_service_client_from_env,
)
from core.commerce.ledger import record_commerce_event

logger = structlog.get_logger()

ARBITER_DECISIONS_PATH = "/v1/commerce/oacp/arbiter-decisions"
DECISION_GRANT_TYP = "decision+jwt"
DECISION_GRANT_AUDIENCE = "urn:grantex:decision"
POLICY_ARBITER = "policy_arbiter"
COUNTERSIGNED_EVENT = "commerce.arbiter.decision.countersigned"
CLOCK_SKEW = timedelta(seconds=60)
EVIDENCE_KEYS: tuple[str, ...] = (
    "mandate_ref",
    "mandate_hash",
    "consent_ref",
    "consent_status",
    "passport_ref",
    "artifact_ids",
)
STATUSES: tuple[str, ...] = ("granted", "recorded", "skipped", "unavailable", "invalid", "conflict")


@dataclass(frozen=True)
class Countersignature:
    """What became of one decision's countersignature; ``status`` is one of ``STATUSES``."""

    status: str
    reason: str
    jti: str | None = None
    exp: int | None = None

    @property
    def granted(self) -> bool:
        return self.status == "granted"

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reason": self.reason, "jti": self.jti, "exp": self.exp}


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def arbiter_decision_body(decision: Decision) -> dict[str, Any]:
    """The contract section 7 body for one decision."""
    evidence = {key: decision.evidence.get(key) for key in EVIDENCE_KEYS}
    evidence["artifact_ids"] = list(evidence.get("artifact_ids") or [])
    return {
        "decision_id": decision.decision_id,
        "tenant_id": str(decision.tenant_id),
        "merchant_id": decision.merchant_id,
        "seller_agent_id": decision.seller_agent_id,
        "buyer_agent_id": decision.buyer_agent_id,
        "action": decision.action,
        "action_hash": decision.payload_hash,
        "outcome": decision.outcome,
        "rules_evaluated": list(decision.rules_evaluated),
        "failing_rule": decision.failing_rule if decision.outcome == "refuse" else None,
        "evidence": evidence,
        "decided_at": _iso(decision.decided_at),
        "policy_version": decision.policy_version,
    }


def check_grant_claims(claims: dict[str, Any], decision: Decision, *, issuer: str, now: datetime) -> str | None:
    """Why the verified grant's claims do not bind this decision, or None when they do."""
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    exp = claims.get("exp")
    decided = decision.decided_at if decision.decided_at.tzinfo else decision.decided_at.replace(tzinfo=UTC)
    checks: tuple[tuple[bool, str], ...] = (
        (bool(issuer) and claims.get("iss") == issuer, "issuer_mismatch"),
        (DECISION_GRANT_AUDIENCE in audiences, "audience_mismatch"),
        (claims.get("approver_auth") == POLICY_ARBITER, "approver_auth_not_policy_arbiter"),
        (claims.get("action_hash") == decision.payload_hash, "action_hash_mismatch"),
        (claims.get("decision_request") == decision.decision_id, "decision_request_mismatch"),
        (claims.get("sub") == decision.buyer_agent_id, "subject_mismatch"),
        ("acr" not in claims, "human_acr_present"),
        (isinstance(claims.get("jti"), str) and bool(claims.get("jti")), "jti_missing"),
        (isinstance(exp, int) and not isinstance(exp, bool), "exp_missing"),
    )
    for passed, reason in checks:
        if not passed:
            return reason
    expires = datetime.fromtimestamp(int(exp), tz=UTC)
    if expires <= now:
        return "grant_expired"
    if expires > decided + DECISION_TTL + CLOCK_SKEW:
        return "grant_lifetime_exceeds_decision"
    return None


async def countersign_decision(
    decision: Decision,
    *,
    client: GrantexServiceClient | None = None,
    post_enabled: bool | None = None,
    now: datetime | None = None,
) -> Countersignature:
    """Post ``decision`` to Grantex and, for an allow, verify and attach the grant. Never raises, never waits.

    Call it after the decision's transaction committed: it opens its own short
    transaction only to attach a verified grant.
    """
    from core.config import arbiter_grantex_post_enabled

    if not (arbiter_grantex_post_enabled() if post_enabled is None else post_enabled):
        return Countersignature("skipped", "post_disabled")
    if not decision.buyer_agent_id:
        # Grantex binds the grant to the buyer agent (sub); a decision without one cannot be countersigned.
        return Countersignature("skipped", "buyer_agent_missing")
    service = client if client is not None else grantex_service_client_from_env()
    if service is None:
        return Countersignature("unavailable", "grantex_not_configured")
    try:
        response = await service.post_json(ARBITER_DECISIONS_PATH, arbiter_decision_body(decision))
    except GrantexServiceUnavailableError as exc:
        logger.warning("commerce_arbiter_countersign_unavailable", decision_id=decision.decision_id, reason=str(exc))
        return Countersignature("unavailable", "grantex_unreachable")
    if response.status_code == 409:
        logger.error("commerce_arbiter_countersign_conflict", decision_id=decision.decision_id)
        return Countersignature("conflict", "decision_id_reused_with_another_body")
    if response.status_code not in (200, 201):
        code = str(response.body.get("code") or "")[:64]
        logger.warning(
            "commerce_arbiter_countersign_refused",
            decision_id=decision.decision_id,
            http_status=response.status_code,
            code=code,
        )
        return Countersignature("unavailable", f"grantex_http_{response.status_code}")
    body = response.body
    if body.get("decision_id") != decision.decision_id or body.get("outcome") != decision.outcome:
        return Countersignature("invalid", "answer_does_not_name_this_decision")
    if decision.outcome == "refuse":
        if body.get("recorded") is not True or "decision_grant" in body:
            return Countersignature("invalid", "refusal_answer_malformed")
        return Countersignature("recorded", "refusal_recorded")
    moment = now or datetime.now(UTC)
    try:
        claims = await service.verify_token(body.get("decision_grant"), typ=DECISION_GRANT_TYP)
    except GrantexServiceUnavailableError:
        return Countersignature("unavailable", "grantex_jwks_unavailable")
    except GrantexTokenError as exc:
        logger.error("commerce_arbiter_grant_irregularity", decision_id=decision.decision_id, reason=str(exc))
        return Countersignature("invalid", "grant_signature_invalid")
    problem = check_grant_claims(claims, decision, issuer=service.issuer, now=moment)
    if problem is None and body.get("jti") != claims.get("jti"):
        problem = "jti_mismatch"
    if problem is not None:
        logger.error("commerce_arbiter_grant_irregularity", decision_id=decision.decision_id, reason=problem)
        return Countersignature("invalid", problem)
    jti = str(claims["jti"])
    await attach_grant(decision, jti=jti, recorded_at=moment)
    return Countersignature("granted", "grant_verified", jti=jti, exp=int(claims["exp"]))


async def attach_grant(decision: Decision, *, jti: str, recorded_at: datetime) -> bool:
    """Attach the verified grant's jti to the decision row (one-time) and seal it; False when already attached."""
    from sqlalchemy import update

    from core.database import get_tenant_session
    from core.models.commerce_arbiter import CommerceArbiterDecisionRow

    table = CommerceArbiterDecisionRow
    tenant = decision.tenant_id if isinstance(decision.tenant_id, uuid.UUID) else uuid.UUID(str(decision.tenant_id))
    async with get_tenant_session(tenant) as session:
        attached = (
            await session.execute(
                update(table)
                .where(
                    table.decision_id == decision.decision_id,
                    table.tenant_id == tenant,
                    table.outcome == "allow",
                    table.grantex_grant_jti.is_(None),
                )
                .values(grantex_grant_jti=jti[:255], grantex_recorded_at=recorded_at)
                .returning(table.decision_id)
            )
        ).first()
        if attached is None:
            return False
        await record_commerce_event(
            session,
            tenant_id=tenant,
            event=COUNTERSIGNED_EVENT,
            outcome="success",
            merchant_id=decision.merchant_id,
            seller_agent_id=decision.seller_agent_id,
            buyer_agent_id=decision.buyer_agent_id,
            payload_hash=decision.payload_hash,
            resource_id=decision.decision_id,
            details={"decision_id": decision.decision_id, "grantex_grant_jti": jti},
            now=recorded_at,
        )
    return True
