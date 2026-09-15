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

The mode is the strictest of the deployment default
(``AGENTICORG_GRANTS_ENFORCE_CLOSED``), the global rows and the tenant's rows
of the ``grants.enforce_closed.warn`` / ``grants.enforce_closed.deny`` feature
flags; deny wins over warn. Global and tenant rows are read separately, so a
tenant row can never hide an operator's global row, and the keys are reserved
for platform operators (``api/v1/feature_flags.py`` refuses them). See
``docs/operations/grant-enforcement.md``.

Reasons come from a fixed vocabulary - the Grantex SDK's ``reason_code`` values
(PRD Appendix B) plus ``grant_missing``, ``enforcement_unavailable`` and
``unclassified`` - so they can be metric labels. Sub-reasons, grant ids, tools
and tenants are log fields only. The grant token itself is never logged.
"""

from __future__ import annotations

import asyncio
import re
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
    """Why a tool call is (or would be) denied. Low cardinality by design.

    The first ten are the Grantex SDK's ``DenialReason`` codes, used verbatim.
    """

    PURPOSE_NOT_ALLOWED = "purpose_not_allowed"
    TOOL_NOT_GRANTED = "tool_not_granted"
    PERMISSION_INSUFFICIENT = "permission_insufficient"
    CAP_EXCEEDED = "cap_exceeded"
    DECISION_REQUIRED = "decision_required"
    DECISION_INVALID = "decision_invalid"
    GRANT_REVOKED = "grant_revoked"
    REGION_MISMATCH = "region_mismatch"
    MANIFEST_UNKNOWN_TOOL = "manifest_unknown_tool"
    TOKEN_INVALID = "token_invalid"
    GRANT_MISSING = "grant_missing"
    ENFORCEMENT_UNAVAILABLE = "enforcement_unavailable"
    UNCLASSIFIED = "unclassified"


# Grantex SDK codes this module maps one to one.
_SDK_REASON_CODES = frozenset(
    {
        DenialReason.PURPOSE_NOT_ALLOWED,
        DenialReason.TOOL_NOT_GRANTED,
        DenialReason.PERMISSION_INSUFFICIENT,
        DenialReason.CAP_EXCEEDED,
        DenialReason.DECISION_REQUIRED,
        DenialReason.DECISION_INVALID,
        DenialReason.GRANT_REVOKED,
        DenialReason.REGION_MISMATCH,
        DenialReason.MANIFEST_UNKNOWN_TOOL,
        DenialReason.TOKEN_INVALID,
    }
)
_SUB_REASON_MAX = 64


@dataclass(frozen=True)
class Denial:
    reason: DenialReason
    sub_reason: str = ""
    grant_id: str = ""
    # Grantex's human-readable reason, kept only for unclassified denials so
    # operators can see what the SDK said. Never contains the token.
    detail: str = ""


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
    """Effective ``grants.enforce_closed`` mode for a tenant. Never raises.

    The strictest of the deployment default, the global flag rows and the
    tenant's flag rows. Rows are evaluated independently (a tenant row cannot
    hide a global row). When the flag store cannot be read the result is the
    stricter of the deployment default and the tenant's last mode resolved in
    the past hour by this process; with no such mode it is ``deny``
    (``flag_store_unreadable``). Every fallback is logged and counted.
    """
    from core.feature_flags import FeatureFlagLookupError, load_flag_rows_strict, row_enabled

    default = deployment_default_mode()
    tenant_key = str(tenant_id or "")
    try:
        tid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(tenant_key)
    except ValueError:
        # No tenant to look flags up for (internal callers, tests).
        return _cap_deny(default, tenant_id=tenant_key)

    try:
        mode = default
        for flag_key, flag_mode in ((FLAG_WARN, EnforcementMode.WARN), (FLAG_DENY, EnforcementMode.DENY)):
            rows = await load_flag_rows_strict(flag_key, tenant_id=tid)
            if row_enabled(flag_key, rows.global_row, subject_id=tenant_key) or row_enabled(
                flag_key, rows.tenant_row, subject_id=tenant_key
            ):
                mode = _stricter(mode, flag_mode)
    except FeatureFlagLookupError:
        remembered = _recall(tenant_key)
        if remembered is not None:
            mode, outcome = _stricter(default, remembered), "last_known"
        else:
            mode, outcome = EnforcementMode.DENY, "deny"
        logger.error(
            "grant_enforcement_mode_lookup_failed",
            reason_code="flag_store_unreadable",
            tenant_id=tenant_key,
            deployment_default=default.value,
            last_known_mode=remembered.value if remembered is not None else "",
            effective_mode=mode.value,
        )
        _count_mode_fallback(outcome)
        return _cap_deny(mode, tenant_id=tenant_key)

    _remember(tenant_key, mode)
    return _cap_deny(mode, tenant_id=tenant_key)


def _count_mode_fallback(outcome: str) -> None:
    try:
        from observability.metrics import grant_enforcement_mode_fallbacks_total

        grant_enforcement_mode_fallbacks_total.labels(outcome=outcome).inc()
    except (ImportError, ValueError) as exc:
        logger.warning("grant_enforcement_metric_failed", error_type=type(exc).__name__)


# ── Compatibility: Grantex SDKs without reason codes (0.5.x) ─────────────
# Grantex 0.5.0 and 0.5.1 return only a message in ``EnforceResult.reason``.
# Each pattern below matches exactly one message ``Grantex.enforce`` builds in
# those releases (grantex/_client.py), anchored on the connector and tool of
# the call being checked, and maps it to the reason and sub-reason the 0.6 SDK
# returns as ``reason_code``/``sub_reason`` for the same denial. Remove this
# table once the pinned SDK returns reason codes.
_SDK_05_MESSAGES: tuple[tuple[str, DenialReason, str], ...] = (
    (r"Token verification failed: Signature has expired", DenialReason.TOKEN_INVALID, "expired"),
    (r"Token verification failed: .+", DenialReason.TOKEN_INVALID, ""),
    (
        r"No manifest loaded for connector '{connector}'\. Load a manifest first\.",
        DenialReason.MANIFEST_UNKNOWN_TOOL,
        "unknown_connector",
    ),
    (
        r"Unknown tool '{tool}' on connector '{connector}'\. Tool not found in manifest\.",
        DenialReason.MANIFEST_UNKNOWN_TOOL,
        "unknown_tool",
    ),
    (r"No scope grants access to connector '{connector}'\.", DenialReason.TOOL_NOT_GRANTED, ""),
    (
        r"(read|write|delete|admin) scope does not permit (read|write|delete|admin) operations on {connector}\.",
        DenialReason.PERMISSION_INSUFFICIENT,
        "",
    ),
    (r"Amount \S+ exceeds budget cap of \S+ on {connector}\.", DenialReason.CAP_EXCEEDED, "amount_cap"),
    # 0.5.1 only:
    (
        r"Amount must be a finite number to enforce a budget cap on {connector}\.",
        DenialReason.CAP_EXCEEDED,
        "invalid_amount",
    ),
    (
        r"A capped scope on {connector} carries a malformed cap; refusing to authorize amount \S+\.",
        DenialReason.CAP_EXCEEDED,
        "malformed_cap",
    ),
)


def _classify_sdk_05_message(message: str, *, connector: str, tool: str) -> tuple[DenialReason, str]:
    """Compatibility fallback for SDKs without reason codes: exact 0.5.x messages only."""
    for pattern, reason, sub_reason in _SDK_05_MESSAGES:
        compiled = pattern.format(connector=re.escape(connector), tool=re.escape(tool))
        if re.fullmatch(compiled, message, flags=re.DOTALL):
            return reason, sub_reason
    return DenialReason.UNCLASSIFIED, "unknown_message"


def classify_enforce_result(result: Any, *, connector: str = "", tool: str = "") -> tuple[DenialReason, str]:
    """Map a denied Grantex ``EnforceResult`` to ``(reason, sub_reason)``.

    Primary source: the SDK's ``reason_code`` / ``sub_reason``, used exactly
    (Grantex 0.6 SDK onwards); an unknown code is ``unclassified``. An SDK
    without reason codes (0.5.x) falls back to matching the exact messages it
    builds (``_SDK_05_MESSAGES``); a message that matches none is
    ``unclassified``. Every outcome is still a denial, never read as an allow.
    """
    has_code_field = hasattr(result, "reason_code")
    code = str(getattr(result, "reason_code", "") or "").strip()
    if code:
        sub_reason = str(getattr(result, "sub_reason", "") or "").strip()[:_SUB_REASON_MAX]
        if code in _SDK_REASON_CODES:
            return DenialReason(code), sub_reason
        return DenialReason.UNCLASSIFIED, "unknown_reason_code"
    if has_code_field:
        # A reason-code SDK that denied without a code: do not guess from text.
        return DenialReason.UNCLASSIFIED, "no_reason_code"
    return _classify_sdk_05_message(str(getattr(result, "reason", "") or ""), connector=connector, tool=tool)


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
        **({"sdk_reason": denial.detail} if denial.detail else {}),
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
                reason, sub_reason = classify_enforce_result(result, connector=connector, tool=tool)
                detail = str(getattr(result, "reason", "") or "")[:200] if reason is DenialReason.UNCLASSIFIED else ""
                denial = Denial(reason, sub_reason, str(getattr(result, "grant_id", "") or ""), detail)

    if denial is None:
        return GrantCheck(dispatch_allowed=True)

    record_denial(mode, denial, connector=connector, tool=tool, context=context)
    return GrantCheck(dispatch_allowed=mode is EnforcementMode.WARN, denial=denial)
