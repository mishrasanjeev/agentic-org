"""Buyer-safe fail-closed authority summaries for Agentic Commerce sessions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

SESSION_AUTHORITY_CONTRACT = "agenticorg.commerce.session_authority.v1"

VALID_CONSENT_STATUSES = frozenset({"granted", "approved", "active"})
VALID_PASSPORT_STATUSES = frozenset({"valid", "verified", "issued", "active"})
VALID_SESSION_STATUSES = frozenset({"active", "fresh", "verified"})
VALID_MERCHANT_STATUSES = frozenset({"enabled", "active", "sandbox_enabled", "approved_for_sandbox_preview"})
VALID_AGENT_STATUSES = frozenset({"enabled", "active", "trusted", "verified"})
VALID_POLICY_DECISIONS = frozenset({"allow", "allowed", "approved"})

CONSENT_REFUSAL_STATUSES = {
    "denied": "consent_denied",
    "rejected": "consent_denied",
    "revoked": "consent_revoked",
    "withdrawn": "consent_revoked",
    "expired": "consent_expired",
    "failed": "consent_denied",
}
PASSPORT_REFUSAL_STATUSES = {
    "revoked": "passport_revoked",
    "expired": "passport_expired",
    "not_yet_valid": "passport_not_yet_valid",
    "invalid": "passport_invalid",
}
SESSION_REFUSAL_STATUSES = {
    "revoked": "session_revoked",
    "expired": "session_expired",
    "disabled": "session_disabled",
    "invalid": "session_invalid",
}
DISABLED_STATUSES = frozenset({"disabled", "inactive", "blocked", "suspended", "untrusted", "revoked"})
POLICY_DENIALS = frozenset({"deny", "denied", "blocked", "refused", "rejected"})

PRIVATE_AUTHORITY_MARKERS = (
    "private",
    "internal",
    "provider",
    "credential",
    "secret",
    "token",
    "jwt",
    "passport",
    "raw",
    "payload",
    "postgres://",
    "postgresql://",
    "redis://",
    "http://",
    "https://",
)


def session_authority_from_payload(
    payload: Mapping[str, Any] | None,
    *,
    expected_merchant_id: str | None = None,
    expected_agent_id: str | None = None,
    expected_buyer_id: str | None = None,
    expected_session_id: str | None = None,
    now: datetime | str | None = None,
    max_age_seconds: int = 300,
) -> dict[str, Any]:
    """Normalize Grantex authority into a buyer-safe fail-closed summary."""
    data = _data_object(payload)
    authority = _mapping(
        data.get("session_authority")
        or data.get("authority")
        or data.get("commerce_authority")
        or data.get("buyer_session_authority")
    )
    source = authority or data
    current_time = _parse_datetime(now)

    statuses = {
        "consent": _normalize_status(
            _lookup(source, "consent_status", "consent.status", "consent_request.status")
        ),
        "passport": _normalize_status(
            _lookup(source, "passport_status", "passport.status", "commerce_passport.status")
        ),
        "session": _normalize_status(_lookup(source, "session_status", "session.status")),
        "merchant": _normalize_status(
            _lookup(source, "merchant_status", "merchant.status", "merchant.commerce_status")
        ),
        "agent": _normalize_status(
            _lookup(source, "agent_status", "agent.status", "agent_trust_status", "agent.trust_status")
        ),
        "policy": _normalize_status(_lookup(source, "policy_decision", "policy.decision", "decision")),
    }

    blockers = _authority_blockers(
        source,
        statuses=statuses,
        expected_merchant_id=expected_merchant_id,
        expected_agent_id=expected_agent_id,
        expected_buyer_id=expected_buyer_id,
        expected_session_id=expected_session_id,
        now=current_time,
        max_age_seconds=max_age_seconds,
    )
    authority_valid = not blockers
    refusal_code = blockers[0] if blockers else "checkout_payment_not_enabled_by_c6u6"

    return {
        "contract": SESSION_AUTHORITY_CONTRACT,
        "authority_valid": authority_valid,
        "protected_action_allowed": False,
        "checkout_payment_enabled": False,
        "live_provider_enabled": False,
        "public_discovery_enabled": False,
        "refresh_required": not authority_valid,
        "refusal": True,
        "refusal_code": refusal_code,
        "reason": _buyer_safe_reason(refusal_code),
        "blockers": blockers,
        "statuses": statuses,
        "evidence_keys": _safe_evidence_keys(
            source.get("evidence") or source.get("audit_evidence") or source.get("evidence_checklist")
        ),
    }


def _authority_blockers(
    source: Mapping[str, Any],
    *,
    statuses: Mapping[str, str],
    expected_merchant_id: str | None,
    expected_agent_id: str | None,
    expected_buyer_id: str | None,
    expected_session_id: str | None,
    now: datetime,
    max_age_seconds: int,
) -> list[str]:
    blockers: list[str] = []

    _append_status_blocker(blockers, "consent", statuses["consent"], VALID_CONSENT_STATUSES, CONSENT_REFUSAL_STATUSES)
    _append_status_blocker(
        blockers,
        "passport",
        statuses["passport"],
        VALID_PASSPORT_STATUSES,
        PASSPORT_REFUSAL_STATUSES,
    )
    _append_status_blocker(blockers, "session", statuses["session"], VALID_SESSION_STATUSES, SESSION_REFUSAL_STATUSES)

    merchant_status = statuses["merchant"]
    if merchant_status == "missing":
        blockers.append("merchant_status_missing")
    elif merchant_status in DISABLED_STATUSES:
        blockers.append("merchant_disabled")
    elif merchant_status not in VALID_MERCHANT_STATUSES:
        blockers.append("merchant_status_ambiguous")

    agent_status = statuses["agent"]
    if agent_status == "missing":
        blockers.append("agent_status_missing")
    elif agent_status in DISABLED_STATUSES:
        blockers.append("agent_disabled")
    elif agent_status not in VALID_AGENT_STATUSES:
        blockers.append("agent_status_ambiguous")

    policy_decision = statuses["policy"]
    if policy_decision == "missing":
        blockers.append("policy_decision_missing")
    elif policy_decision in POLICY_DENIALS:
        blockers.append("policy_denied")
    elif policy_decision not in VALID_POLICY_DECISIONS:
        blockers.append("policy_decision_ambiguous")

    checked_at_value = _lookup(
        source,
        "authority_checked_at",
        "verified_at",
        "checked_at",
        "session.authority_checked_at",
    )
    checked_at = _parse_datetime(checked_at_value)
    if checked_at_value in (None, ""):
        blockers.append("authority_freshness_missing")
    elif now - checked_at > timedelta(seconds=max_age_seconds):
        blockers.append("authority_stale")

    for code, value in (
        ("consent_expired", _lookup(source, "consent_expires_at", "consent.expires_at", "consent_request.expires_at")),
        (
            "passport_expired",
            _lookup(source, "passport_expires_at", "passport.expires_at", "commerce_passport.expires_at"),
        ),
        ("session_expired", _lookup(source, "session_expires_at", "session.expires_at")),
    ):
        expires_at = _parse_datetime(value)
        if value not in (None, "") and expires_at <= now:
            blockers.append(code)

    if _truthy(_lookup(source, "revoked", "passport.revoked", "commerce_passport.revoked")):
        blockers.append("passport_revoked")
    if _lookup(source, "revoked_at", "passport.revoked_at", "commerce_passport.revoked_at") not in (None, ""):
        blockers.append("passport_revoked")
    if _truthy(_lookup(source, "consent_revoked", "consent.revoked")):
        blockers.append("consent_revoked")

    blockers.extend(
        _mismatch_blockers(
            source,
            expected_merchant_id=expected_merchant_id,
            expected_agent_id=expected_agent_id,
            expected_buyer_id=expected_buyer_id,
            expected_session_id=expected_session_id,
        )
    )
    return _unique(blockers)


def _append_status_blocker(
    blockers: list[str],
    label: str,
    status: str,
    valid_statuses: frozenset[str],
    refusal_statuses: Mapping[str, str],
) -> None:
    if status == "missing":
        blockers.append(f"{label}_missing")
    elif status in refusal_statuses:
        blockers.append(refusal_statuses[status])
    elif status not in valid_statuses:
        blockers.append(f"{label}_ambiguous")


def _mismatch_blockers(
    source: Mapping[str, Any],
    *,
    expected_merchant_id: str | None,
    expected_agent_id: str | None,
    expected_buyer_id: str | None,
    expected_session_id: str | None,
) -> list[str]:
    pairs = (
        (
            "merchant",
            expected_merchant_id or _text(_lookup(source, "expected_merchant_id", "expected.merchant_id")),
            _text(
                _lookup(source, "merchant_id", "merchant.id", "passport.merchant_id", "commerce_passport.merchant_id")
            ),
        ),
        (
            "agent",
            expected_agent_id or _text(_lookup(source, "expected_agent_id", "expected.agent_id")),
            _text(_lookup(source, "agent_id", "agent.id", "passport.agent_id", "commerce_passport.agent_id")),
        ),
        (
            "buyer",
            expected_buyer_id or _text(_lookup(source, "expected_buyer_id", "expected.buyer_id", "expected.subject")),
            _text(_lookup(source, "buyer_id", "subject", "passport.subject", "commerce_passport.subject")),
        ),
        (
            "session",
            expected_session_id or _text(_lookup(source, "expected_session_id", "expected.session_id")),
            _text(_lookup(source, "session_id", "session.id", "buyer_session_id")),
        ),
    )
    blockers: list[str] = []
    for label, expected, actual in pairs:
        if expected and not actual:
            blockers.append(f"{label}_missing")
        elif expected and actual and expected != actual:
            blockers.append(f"{label}_mismatch")
    return blockers


def _buyer_safe_reason(code: str) -> str:
    messages = {
        "consent_missing": "Fresh Grantex consent is required before continuing.",
        "consent_denied": "Grantex consent is not granted for this action.",
        "consent_revoked": "Grantex consent was revoked and must be refreshed.",
        "consent_expired": "Grantex consent expired and must be refreshed.",
        "passport_missing": "A valid Grantex Commerce Passport is required before continuing.",
        "passport_revoked": "The Commerce Passport is revoked and cannot be used.",
        "passport_expired": "The Commerce Passport expired and must be refreshed.",
        "passport_not_yet_valid": "The Commerce Passport is not valid yet.",
        "merchant_disabled": "The merchant is not enabled for this commerce action.",
        "agent_disabled": "The commerce agent is not enabled for this action.",
        "policy_denied": "Grantex policy did not allow this commerce action.",
        "authority_freshness_missing": "Authority freshness is missing, so the session must be refreshed.",
        "authority_stale": "Authority is stale, so the session must be refreshed.",
        "merchant_mismatch": "Merchant authority does not match the buyer session.",
        "agent_mismatch": "Agent authority does not match the buyer session.",
        "buyer_mismatch": "Buyer authority does not match the buyer session.",
        "session_mismatch": "Session authority does not match the buyer session.",
        "checkout_payment_not_enabled_by_c6u6": "Authority is fresh, but checkout/payment remains disabled in C6U6.",
    }
    return messages.get(code, "Grantex authority is incomplete or ambiguous, so this action is refused.")


def _data_object(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    data = payload.get("data")
    return data if isinstance(data, Mapping) else payload


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _lookup(mapping: Mapping[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = mapping
        for part in path.split("."):
            if not isinstance(current, Mapping) or part not in current:
                current = None
                break
            current = current[part]
        if current not in (None, ""):
            return current
    return None


def _normalize_status(value: Any) -> str:
    token = _safe_token(value)
    return token or "missing"


def _safe_evidence_keys(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        iterable: Sequence[Any] = list(value.values())
    elif isinstance(value, Sequence) and not isinstance(value, str):
        iterable = value
    else:
        return []

    result: list[str] = []
    for item in iterable[:16]:
        token = ""
        if isinstance(item, str):
            token = _safe_token(item)
        elif isinstance(item, Mapping):
            token = _safe_token(item.get("key") or item.get("evidence_key") or item.get("type"))
        if token and token not in result:
            result.append(token)
    return result


def _safe_token(value: Any) -> str:
    text = _text(value).lower().replace("-", "_").replace(" ", "_")
    if not text or len(text) > 80:
        return ""
    if any(marker in text for marker in PRIVATE_AUTHORITY_MARKERS):
        return ""
    return "".join(ch for ch in text if ch.isalnum() or ch == "_")


def _text(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).replace("\x00", "").strip()


def _parse_datetime(value: datetime | str | Any | None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return datetime.max.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "revoked", "disabled"}


def _unique(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
