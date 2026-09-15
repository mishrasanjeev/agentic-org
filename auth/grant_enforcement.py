# SPDX-License-Identifier: Apache-2.0
"""Grant enforcement for agent tool calls (``grants.enforce_closed``).

Every agent tool call is checked against the run's Grantex grant. What happens
when the check fails depends on the enforcement mode:

``off``
    The legacy behaviour. Callers keep their existing code path; nothing in
    this module runs for them.
``warn``
    The call goes ahead. Each call that *would* be denied is logged as a
    ``grant_enforcement_would_deny`` event and counted.
``deny``
    The call is refused with a reason code, logged as
    ``grant_enforcement_denied`` and counted.

The mode is the deployment default (``AGENTICORG_GRANTS_ENFORCE_CLOSED``)
raised by the tenant's ``grants.enforce_closed.warn`` /
``grants.enforce_closed.deny`` feature flags; deny wins over warn. Tenant flags
are managed by tenant admins, so they can only make enforcement stricter than
the deployment default, never weaker. See
``docs/operations/grant-enforcement.md``.

Reasons come from a fixed vocabulary (PRD Appendix B plus ``grant_missing``,
``token_invalid`` and ``enforcement_unavailable``) so they can be metric
labels. Sub-reasons, grant ids, tools and tenants are log fields only. The
grant token itself is never logged.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import structlog

from core.config import settings

logger = structlog.get_logger()

FLAG_WARN = "grants.enforce_closed.warn"
FLAG_DENY = "grants.enforce_closed.deny"

# Deny mode is switched on by a separate change that also surfaces the reason
# code in the run result and audit trail. Until then a requested ``deny``
# (deployment default or tenant flag) runs as ``warn`` and says so.
DENY_MODE_AVAILABLE = False


class EnforcementMode(StrEnum):
    OFF = "off"
    WARN = "warn"
    DENY = "deny"


_STRICTNESS = {EnforcementMode.OFF: 0, EnforcementMode.WARN: 1, EnforcementMode.DENY: 2}


class DenialReason(StrEnum):
    """Why a tool call is (or would be) denied. Low cardinality by design."""

    GRANT_MISSING = "grant_missing"
    TOKEN_INVALID = "token_invalid"
    GRANT_REVOKED = "grant_revoked"
    TOOL_NOT_GRANTED = "tool_not_granted"
    PERMISSION_INSUFFICIENT = "permission_insufficient"
    CAP_EXCEEDED = "cap_exceeded"
    MANIFEST_UNKNOWN_TOOL = "manifest_unknown_tool"
    ENFORCEMENT_UNAVAILABLE = "enforcement_unavailable"


@dataclass(frozen=True)
class Denial:
    reason: DenialReason
    sub_reason: str = ""
    grant_id: str = ""


@dataclass(frozen=True)
class GrantCheck:
    """Outcome of checking one tool call in ``warn`` or ``deny`` mode.

    ``dispatch_allowed`` is what the caller must obey. ``denial`` is set
    whenever the grant does not cover the call, including in warn mode where
    the call is still allowed.
    """

    dispatch_allowed: bool
    denial: Denial | None = None


@dataclass(frozen=True)
class GrantCallContext:
    """Log context for a tool-call check. Never carries the token."""

    tenant_id: str = ""
    agent_id: str = ""
    agent_type: str = ""
    runtime: str = ""
    grant_source: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def parse_mode(value: Any) -> EnforcementMode:
    """Read a mode carried in agent state or config.

    A missing value is ``off``: state written before this feature existed, or
    by a caller that never resolved a mode, keeps the legacy behaviour. An
    unrecognised value is ``deny`` — it cannot be trusted to mean anything
    weaker.
    """
    if value is None or value == "":
        return EnforcementMode.OFF
    try:
        return EnforcementMode(str(value).strip().lower())
    except ValueError:
        logger.error("grant_enforcement_mode_unrecognised", value=str(value)[:32])
        return EnforcementMode.DENY


def _stricter(a: EnforcementMode, b: EnforcementMode) -> EnforcementMode:
    return a if _STRICTNESS[a] >= _STRICTNESS[b] else b


_LAST_KNOWN_TTL_SECONDS = 3_600
_LAST_KNOWN_MAX = 4_096
# Last successfully resolved mode per tenant, used only when the flag store
# cannot be read so a tenant that was in warn/deny does not silently drop to a
# weaker deployment default during a database blip.
# enterprise-gate: process-local-ok reason=bounded-ttl-last-known-mode-used-only-when-flag-store-unreadable
_last_known: dict[str, tuple[EnforcementMode, float]] = {}


def _remember(tenant_key: str, mode: EnforcementMode) -> None:
    now = time.monotonic()
    if tenant_key not in _last_known and len(_last_known) >= _LAST_KNOWN_MAX:
        oldest = min(_last_known, key=lambda key: _last_known[key][1])
        _last_known.pop(oldest, None)
    _last_known[tenant_key] = (mode, now)


def _recall(tenant_key: str) -> EnforcementMode | None:
    entry = _last_known.get(tenant_key)
    if entry is None or time.monotonic() - entry[1] > _LAST_KNOWN_TTL_SECONDS:
        return None
    return entry[0]


def clear_mode_cache() -> None:
    """Testing helper."""
    _last_known.clear()


def deployment_default_mode() -> EnforcementMode:
    return EnforcementMode(settings.grants_enforce_closed)


def _cap_deny(mode: EnforcementMode, *, tenant_id: str) -> EnforcementMode:
    if mode is EnforcementMode.DENY and not DENY_MODE_AVAILABLE:
        logger.error(
            "grant_enforcement_deny_unavailable",
            tenant_id=tenant_id,
            effective_mode=EnforcementMode.WARN.value,
        )
        return EnforcementMode.WARN
    return mode


async def resolve_enforcement_mode(tenant_id: str | uuid.UUID | None) -> EnforcementMode:
    """Effective ``grants.enforce_closed`` mode for a tenant.

    The stricter of the deployment default and the tenant's flags (``deny``
    flag, else ``warn`` flag). When the flag store cannot be read the result
    is the stricter of the deployment default and the tenant's last resolved
    mode, and the failure is logged. Never raises.
    """
    from core.feature_flags import FeatureFlagLookupError, is_enabled_strict

    default = deployment_default_mode()
    tenant_key = str(tenant_id or "")
    try:
        tid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(tenant_key)
    except ValueError:
        # No tenant to look flags up for (internal callers, tests).
        return _cap_deny(default, tenant_id=tenant_key)

    try:
        if await is_enabled_strict(FLAG_DENY, tenant_id=tid):
            requested = EnforcementMode.DENY
        elif await is_enabled_strict(FLAG_WARN, tenant_id=tid):
            requested = EnforcementMode.WARN
        else:
            requested = EnforcementMode.OFF
        mode = _stricter(default, requested)
    except FeatureFlagLookupError:
        remembered = _recall(tenant_key)
        mode = _stricter(default, remembered) if remembered is not None else default
        logger.warning(
            "grant_enforcement_mode_lookup_failed",
            tenant_id=tenant_key,
            deployment_default=default.value,
            last_known_mode=remembered.value if remembered is not None else "",
            effective_mode=mode.value,
        )
        return _cap_deny(mode, tenant_id=tenant_key)

    _remember(tenant_key, mode)
    return _cap_deny(mode, tenant_id=tenant_key)


def classify_enforce_reason(reason: str) -> tuple[DenialReason, str]:
    """Map a Grantex ``EnforceResult.reason`` to ``(reason, sub_reason)``.

    Unrecognised text is still a denial (``tool_not_granted`` /
    ``unclassified``); it is never read as an allow.
    """
    text = (reason or "").strip().lower()
    if "revoked" in text:
        return DenialReason.GRANT_REVOKED, ""
    if text.startswith("token verification failed") or any(
        marker in text for marker in ("expired", "signature", "invalid", "malformed")
    ):
        return DenialReason.TOKEN_INVALID, "expired" if "expired" in text else "verification_failed"
    if "no manifest loaded" in text:
        return DenialReason.MANIFEST_UNKNOWN_TOOL, "connector_unknown"
    if "unknown tool" in text or "not found in manifest" in text:
        return DenialReason.MANIFEST_UNKNOWN_TOOL, "tool_unknown"
    if "no scope grants access" in text:
        return DenialReason.TOOL_NOT_GRANTED, ""
    if "does not permit" in text:
        return DenialReason.PERMISSION_INSUFFICIENT, ""
    if "exceeds budget cap" in text:
        return DenialReason.CAP_EXCEEDED, ""
    return DenialReason.TOOL_NOT_GRANTED, "unclassified"


def record_denial(
    mode: EnforcementMode,
    denial: Denial,
    *,
    connector: str,
    tool: str,
    context: GrantCallContext,
) -> None:
    """Log and count one denied (or would-be-denied) tool call."""
    event = "grant_enforcement_would_deny" if mode is EnforcementMode.WARN else "grant_enforcement_denied"
    log = logger.warning if mode is EnforcementMode.WARN else logger.error
    log(
        event,
        mode=mode.value,
        reason=denial.reason.value,
        sub_reason=denial.sub_reason,
        grant_id=denial.grant_id,
        tenant_id=context.tenant_id,
        agent_id=context.agent_id,
        agent_type=context.agent_type,
        runtime=context.runtime,
        grant_source=context.grant_source,
        connector=connector,
        tool=tool,
        **context.extra,
    )
    try:
        from observability.metrics import grant_enforcement_denials_total

        grant_enforcement_denials_total.labels(mode=mode.value, reason=denial.reason.value).inc()
    except (ImportError, ValueError) as exc:
        logger.warning("grant_enforcement_metric_failed", error_type=type(exc).__name__)


async def check_tool_grant(
    *,
    mode: EnforcementMode,
    grant_token: str | None,
    connector: str,
    tool: str,
    context: GrantCallContext,
    amount: float | None = None,
    missing_sub_reason: str = "",
    client_factory: Callable[[], Any] | None = None,
) -> GrantCheck:
    """Check one tool call against the run grant in ``warn`` or ``deny`` mode.

    ``off`` is rejected: callers keep their legacy path for it. Any failure
    to evaluate the grant (no token, client unavailable, enforce raising) is a
    denial with a reason, never an allow; in warn mode it is recorded and the
    call proceeds.
    """
    if mode is EnforcementMode.OFF:
        raise ValueError("check_tool_grant is only for warn and deny modes")

    denial: Denial | None = None
    if not grant_token:
        denial = Denial(DenialReason.GRANT_MISSING, missing_sub_reason or "no_grant")
    else:
        if client_factory is None:
            from core.langgraph.grantex_auth import get_grantex_client

            client_factory = get_grantex_client
        try:
            client = client_factory()
            # ``enforce`` may fetch the JWKS synchronously; keep it off the loop.
            result = await asyncio.to_thread(
                client.enforce,
                grant_token=grant_token,
                connector=connector,
                tool=tool,
                amount=amount,
            )
        # enterprise-gate: broad-except-ok reason=enforcement-failure-is-recorded-as-a-denial-never-an-allow
        except Exception as exc:
            denial = Denial(DenialReason.ENFORCEMENT_UNAVAILABLE, type(exc).__name__)
        else:
            if not bool(getattr(result, "allowed", False)):
                reason, sub_reason = classify_enforce_reason(str(getattr(result, "reason", "") or ""))
                denial = Denial(reason, sub_reason, str(getattr(result, "grant_id", "") or ""))

    if denial is None:
        return GrantCheck(dispatch_allowed=True)

    record_denial(mode, denial, connector=connector, tool=tool, context=context)
    return GrantCheck(dispatch_allowed=mode is EnforcementMode.WARN, denial=denial)
