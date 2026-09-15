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
    The call is refused with a reason code that reaches the run result and
    audit trail, logged as ``grant_enforcement_denied`` and counted.

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

    def as_dict(self, *, connector: str, tool: str) -> dict[str, str]:
        """The reason code as surfaced in run results and audit rows."""
        return {
            "reason": self.reason.value,
            "sub_reason": self.sub_reason,
            "grant_id": self.grant_id,
            "connector": connector,
            "tool": tool,
        }


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
        return default

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
        return mode

    _remember(tenant_key, mode)
    return mode


def _count_mode_fallback(outcome: str) -> None:
    try:
        from observability.metrics import grant_enforcement_mode_fallbacks_total

        grant_enforcement_mode_fallbacks_total.labels(outcome=outcome).inc()
    except (ImportError, ValueError) as exc:
        logger.warning("grant_enforcement_metric_failed", error_type=type(exc).__name__)


def classify_enforce_result(result: Any) -> tuple[DenialReason, str]:
    """Map a denied Grantex ``EnforceResult`` to ``(reason, sub_reason)``.

    Uses the SDK's ``reason_code`` / ``sub_reason`` exactly. A result without a
    code (an SDK older than reason codes) or with a code this module does not
    know is ``unclassified`` - still a denial, never read as an allow.
    """
    code = str(getattr(result, "reason_code", "") or "").strip()
    sub_reason = str(getattr(result, "sub_reason", "") or "").strip()[:_SUB_REASON_MAX]
    if not code:
        return DenialReason.UNCLASSIFIED, "no_reason_code"
    if code in _SDK_REASON_CODES:
        return DenialReason(code), sub_reason
    return DenialReason.UNCLASSIFIED, "unknown_reason_code"


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
                reason, sub_reason = classify_enforce_result(result)
                detail = str(getattr(result, "reason", "") or "")[:200] if reason is DenialReason.UNCLASSIFIED else ""
                denial = Denial(reason, sub_reason, str(getattr(result, "grant_id", "") or ""), detail)

    if denial is None:
        return GrantCheck(dispatch_allowed=True)

    record_denial(mode, denial, connector=connector, tool=tool, context=context)
    return GrantCheck(dispatch_allowed=mode is EnforcementMode.WARN, denial=denial)
