"""Fail-closed shared public discovery state decisions for Agentic Commerce."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

PUBLIC_DISCOVERY_STATE_CONTRACT = "agenticorg.commerce.public_discovery_state.v1"

PUBLIC_DISCOVERY_STATES = frozenset(
    {
        "hidden",
        "draft",
        "sandbox_review",
        "approved_for_sandbox_preview",
        "blocked",
        "rejected",
        "expired",
        "production_pending",
        "future_public_enabled",
    }
)

PRIVATE_STATE_MARKERS = (
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

REQUIRED_FUTURE_EVIDENCE = (
    "grantex_review_decision",
    "agenticorg_exposure_decision",
    "source_freshness",
    "rollback_owner",
)


def public_discovery_decision_from_payload(
    grantex_payload: Mapping[str, Any] | None,
    *,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Return a buyer-safe fail-closed decision from Grantex and AgenticOrg state."""
    data = _data_object(grantex_payload)
    state_payload = _mapping(data.get("public_discovery_state"))
    grantex_state = _normalize_state(
        _first_value(
            state_payload,
            data,
            keys=("grantex_state", "grantex_public_discovery_state", "public_discovery_state"),
        )
    )
    agenticorg_state = _normalize_state(
        _first_value(
            state_payload,
            data,
            keys=("agenticorg_state", "agenticorg_public_discovery_state"),
        )
    )

    evidence_keys = _safe_evidence_keys(
        state_payload.get("evidence") or data.get("public_discovery_evidence") or data.get("evidence_checklist")
    )
    blockers = _state_blockers(
        grantex_state=grantex_state,
        agenticorg_state=agenticorg_state,
        evidence_keys=evidence_keys,
        state_payload=state_payload,
        data=data,
        now=_parse_datetime(now),
    )
    internal_preview_allowed = not blockers and grantex_state == agenticorg_state == "approved_for_sandbox_preview"
    refusal_code = (
        "public_discovery_not_enabled"
        if internal_preview_allowed
        else blockers[0]
        if blockers
        else "public_discovery_not_enabled"
    )

    return {
        "contract": PUBLIC_DISCOVERY_STATE_CONTRACT,
        "grantex_state": grantex_state,
        "agenticorg_state": agenticorg_state,
        "buyer_visibility": "internal_preview" if internal_preview_allowed else "hidden",
        "public_discovery_visible": False,
        "public_discovery_refusal": True,
        "internal_preview_allowed": internal_preview_allowed,
        "future_public_enabled_active": False,
        "refusal_code": refusal_code,
        "reason": _buyer_safe_reason(refusal_code),
        "required_evidence_missing": [
            key for key in REQUIRED_FUTURE_EVIDENCE if key not in set(evidence_keys)
        ],
        "evidence_keys": evidence_keys,
    }


def _state_blockers(
    *,
    grantex_state: str,
    agenticorg_state: str,
    evidence_keys: Sequence[str],
    state_payload: Mapping[str, Any],
    data: Mapping[str, Any],
    now: datetime,
) -> list[str]:
    blockers: list[str] = []
    if grantex_state == "missing" or agenticorg_state == "missing":
        blockers.append("public_discovery_state_missing")
    if grantex_state == "unsupported" or agenticorg_state == "unsupported":
        blockers.append("public_discovery_state_unsupported")
    if grantex_state in PUBLIC_DISCOVERY_STATES and agenticorg_state in PUBLIC_DISCOVERY_STATES:
        if grantex_state != agenticorg_state:
            blockers.append("public_discovery_state_mismatch")
    if grantex_state in {"blocked", "rejected", "expired"}:
        blockers.append(f"grantex_state_{grantex_state}")
    if agenticorg_state in {"blocked", "rejected", "expired"}:
        blockers.append(f"agenticorg_state_{agenticorg_state}")
    if grantex_state == "future_public_enabled" or agenticorg_state == "future_public_enabled":
        blockers.append("future_public_enabled_not_enabled_by_c6u5")
    if _is_stale_or_expired(state_payload, data, now=now):
        blockers.append("public_discovery_state_expired_or_stale")
    if _truthy(_first_value(state_payload, data, keys=("synthetic_data", "synthetic_demo", "demo_data"))):
        blockers.append("synthetic_demo_not_production_approval")

    if grantex_state == agenticorg_state == "approved_for_sandbox_preview":
        missing = [key for key in REQUIRED_FUTURE_EVIDENCE if key not in set(evidence_keys)]
        if missing:
            blockers.append("public_discovery_evidence_missing")
    return _unique(blockers)


def _is_stale_or_expired(
    state_payload: Mapping[str, Any],
    data: Mapping[str, Any],
    *,
    now: datetime,
) -> bool:
    freshness = _safe_token(
        _first_value(state_payload, data, keys=("freshness_status", "state_freshness", "source_freshness"))
    )
    if freshness in {"stale", "expired", "blocked"}:
        return True
    expires_at = _parse_datetime(_first_value(state_payload, data, keys=("expires_at", "state_expires_at")))
    return expires_at <= now


def _safe_evidence_keys(value: Any) -> list[str]:
    entries: list[str] = []
    if isinstance(value, Mapping):
        iterable: Sequence[Any] = list(value.values())
    elif isinstance(value, Sequence) and not isinstance(value, str):
        iterable = value
    else:
        return []

    for item in iterable[:16]:
        if isinstance(item, str):
            token = _safe_token(item)
        elif isinstance(item, Mapping):
            token = _safe_token(item.get("key") or item.get("evidence_key") or item.get("type"))
            status = _safe_token(item.get("status"))
            if status and status not in {"pass", "passed", "available", "fresh", "current"}:
                continue
        else:
            continue
        if token and token not in entries:
            entries.append(token)
    return entries


def _buyer_safe_reason(code: str) -> str:
    messages = {
        "public_discovery_state_missing": "Public discovery state is incomplete, so discovery remains hidden.",
        "public_discovery_state_unsupported": "Public discovery state is unsupported, so discovery remains hidden.",
        "public_discovery_state_mismatch": "Grantex and AgenticOrg discovery states do not match.",
        "public_discovery_state_expired_or_stale": "Public discovery evidence is stale or expired.",
        "public_discovery_evidence_missing": "Required public discovery evidence is missing.",
        "synthetic_demo_not_production_approval": "Synthetic or demo data cannot approve public discovery.",
        "future_public_enabled_not_enabled_by_c6u5": "Future public discovery state is not active in C6U5.",
    }
    return messages.get(code, "Public discovery is not enabled for this merchant.")


def _data_object(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    data = payload.get("data")
    return data if isinstance(data, Mapping) else payload


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_value(*mappings: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for mapping in mappings:
        for key in keys:
            value = mapping.get(key)
            if value not in (None, ""):
                return value
    return None


def _normalize_state(value: Any) -> str:
    token = _safe_token(value)
    if not token:
        return "missing"
    return token if token in PUBLIC_DISCOVERY_STATES else "unsupported"


def _safe_token(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if not text or len(text) > 80:
        return ""
    if any(marker in text for marker in PRIVATE_STATE_MARKERS):
        return ""
    return "".join(ch for ch in text if ch.isalnum() or ch == "_")


def _parse_datetime(value: datetime | str | Any | None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return datetime.max.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "demo", "synthetic"}


def _unique(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
