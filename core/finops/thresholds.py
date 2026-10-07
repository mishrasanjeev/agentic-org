# SPDX-License-Identifier: Apache-2.0
"""Cost thresholds with actions: alert, throttle or suspend a scope of spend.

A threshold names a scope (the whole organisation, one application, one use
case or one business unit), a period (``daily`` or ``monthly``), an amount
in USD and an action. While ``AGENTICORG_FINOPS_THRESHOLDS_ENABLED`` is on,
every run through the agents API is checked before it executes
(``check_run``): the attributed ledger's spend for the period is compared
with each threshold that matches the run's attribution, and the strongest
breached action wins:

* ``alert``: the run proceeds; the owner is notified once per period;
* ``throttle``: the run proceeds after a short delay (the threshold's
  ``throttle_seconds``, at most 30), and says so in its answer;
* ``suspend``: the run is refused (``threshold_suspended``) until the period
  resets, the threshold is disabled, or an administrator lifts it until a
  time.

The scope a run is matched against is server-owned: while thresholds are
on, the run's use case and business unit come from the agent's configuration,
type and domain, never from the caller's request, so a caller cannot relabel a
run (or its ledger row) out of a scoped threshold.

A breach is claimed on the threshold (period, time, spend) by one conditional
update, committed before anyone is told, so concurrent runs notify the owner
once per period through the threshold's channels (``email`` to the tenant's
earliest active administrator, ``log``); a notification that fails never
touches the run. Every enabled threshold is evaluated; a tenant holds at most
``MAX_THRESHOLDS``. ``status`` shows every threshold with its spend and share,
for the console.

Thresholds read the ledger that attribution writes, so they run only while
``AGENTICORG_FINOPS_ATTRIBUTION_ENABLED`` is on too; settings refuse to load
with thresholds on and attribution off.

Off, nothing here runs: no run is checked, delayed or refused.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any

import structlog

from core.config import settings
from core.finops import attribution as cost_attribution

logger = structlog.get_logger()

SCOPES: tuple[str, ...] = ("organisation", "application", "use_case", "business_unit")
PERIODS: tuple[str, ...] = ("daily", "monthly")
ACTIONS: tuple[str, ...] = ("alert", "throttle", "suspend")
CHANNELS: tuple[str, ...] = ("email", "log")
ACTION_RANK: dict[str, int] = {"alert": 0, "throttle": 1, "suspend": 2}
SCOPE_COLUMN: dict[str, str] = {"application": "application", "use_case": "use_case", "business_unit": "business_unit"}
MAX_THRESHOLDS = 200
MAX_THRESHOLD_USD = 1_000_000_000.0
DEFAULT_THROTTLE_SECONDS = 5
MAX_THROTTLE_SECONDS = 30
ERROR_CODE = "E1009"


class ThresholdError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def enabled() -> bool:
    """On only with attribution on too: without the attributed ledger every spend would read zero."""
    return bool(settings.finops_thresholds_enabled) and bool(settings.finops_attribution_enabled)


def period_start(period: str, now: datetime) -> date:
    return now.date().replace(day=1) if period == "monthly" else now.date()


def period_key(period: str, now: datetime) -> str:
    return now.strftime("%Y-%m") if period == "monthly" else now.date().isoformat()


def parse_fields(raw: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """The fields of a threshold as they are stored; every value checked, unknown keys refused."""
    allowed = {
        "name",
        "scope_kind",
        "scope_value",
        "period",
        "threshold_usd",
        "action",
        "throttle_seconds",
        "enabled",
        "notify_channels",
        "lifted_until",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ThresholdError(422, "unknown_field", f"unknown threshold fields: {', '.join(unknown)}")
    fields: dict[str, Any] = {}
    if "name" in raw or not partial:
        name = str(raw.get("name") or "").strip()
        if not 1 <= len(name) <= 120:
            raise ThresholdError(422, "name", "name is 1 to 120 characters")
        fields["name"] = name
    if "scope_kind" in raw or not partial:
        kind = str(raw.get("scope_kind") or "").strip().lower()
        if kind not in SCOPES:
            raise ThresholdError(422, "scope_kind", f"scope_kind is one of {', '.join(SCOPES)}")
        fields["scope_kind"] = kind
        value = cost_attribution.label(raw.get("scope_value"))
        if kind != "organisation" and not value:
            raise ThresholdError(422, "scope_value", f"a {kind} threshold names the {kind}")
        fields["scope_value"] = "" if kind == "organisation" else value
    elif "scope_value" in raw:
        raise ThresholdError(422, "scope_value", "scope_value changes with scope_kind")
    if "period" in raw or not partial:
        period = str(raw.get("period") or "monthly").strip().lower()
        if period not in PERIODS:
            raise ThresholdError(422, "period", f"period is one of {', '.join(PERIODS)}")
        fields["period"] = period
    if "threshold_usd" in raw or not partial:
        try:
            amount = float(raw.get("threshold_usd"))
        except (TypeError, ValueError):
            raise ThresholdError(422, "threshold_usd", "threshold_usd is an amount in USD") from None
        if not 0 < amount <= MAX_THRESHOLD_USD:
            raise ThresholdError(422, "threshold_usd", f"threshold_usd is above 0 and at most {MAX_THRESHOLD_USD:.0f}")
        fields["threshold_usd"] = round(amount, 6)
    if "action" in raw or not partial:
        action = str(raw.get("action") or "alert").strip().lower()
        if action not in ACTIONS:
            raise ThresholdError(422, "action", f"action is one of {', '.join(ACTIONS)}")
        fields["action"] = action
    if "throttle_seconds" in raw or not partial:
        seconds = raw.get("throttle_seconds", DEFAULT_THROTTLE_SECONDS)
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= MAX_THROTTLE_SECONDS:
            raise ThresholdError(422, "throttle_seconds", f"throttle_seconds is 1 to {MAX_THROTTLE_SECONDS}")
        fields["throttle_seconds"] = seconds
    if "enabled" in raw or not partial:
        fields["enabled"] = bool(raw.get("enabled", True))
    if "notify_channels" in raw or not partial:
        channels = raw.get("notify_channels", ["email"])
        if isinstance(channels, str):
            channels = channels.split(",")
        cleaned = [str(c).strip().lower() for c in (channels or []) if str(c).strip()]
        if any(c not in CHANNELS for c in cleaned):
            raise ThresholdError(422, "notify_channels", f"notify_channels are among {', '.join(CHANNELS)}")
        fields["notify_channels"] = ",".join(dict.fromkeys(cleaned))
    if "lifted_until" in raw:
        value = raw.get("lifted_until")
        if value is None:
            fields["lifted_until"] = None
        else:
            try:
                lifted = datetime.fromisoformat(str(value))
            except ValueError:
                raise ThresholdError(422, "lifted_until", "lifted_until is an ISO 8601 time") from None
            fields["lifted_until"] = lifted if lifted.tzinfo else lifted.replace(tzinfo=UTC)
    return fields


def matches(threshold: Any, attribution: cost_attribution.Attribution) -> bool:
    kind = getattr(threshold, "scope_kind", "")
    if kind == "organisation":
        return True
    value = str(getattr(threshold, "scope_value", "") or "")
    return bool(value) and getattr(attribution, kind, None) == value


def row_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "scope_kind": row.scope_kind,
        "scope_value": row.scope_value or "",
        "period": row.period,
        "threshold_usd": float(row.threshold_usd),
        "action": row.action,
        "throttle_seconds": int(row.throttle_seconds or DEFAULT_THROTTLE_SECONDS),
        "enabled": bool(row.enabled),
        "notify_channels": [c for c in str(row.notify_channels or "").split(",") if c],
        "last_breach_period": row.last_breach_period,
        "last_breach_at": row.last_breach_at.isoformat() if row.last_breach_at else None,
        "last_breach_spend_usd": float(row.last_breach_spend_usd) if row.last_breach_spend_usd is not None else None,
        "lifted_until": row.lifted_until.isoformat() if row.lifted_until else None,
    }


async def spend(session: Any, tenant_id: uuid.UUID, threshold: Any, *, now: datetime | None = None) -> float:
    """The attributed ledger's spend for the threshold's scope over its period."""
    from sqlalchemy import text as sqltext

    now = now or datetime.now(UTC)
    column = SCOPE_COLUMN.get(threshold.scope_kind)
    scope_sql = f" AND {column} = :value" if column else ""
    params: dict[str, Any] = {"tid": str(tenant_id), "since": period_start(threshold.period, now)}
    if column:
        params["value"] = threshold.scope_value or ""
    row = (
        await session.execute(
            sqltext(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM finops_cost_ledger "  # noqa: S608  # nosec B608 — the column is one of three fixed names, the value is bound
                f"WHERE tenant_id = :tid AND period_date >= :since{scope_sql}"
            ),
            params,
        )
    ).fetchone()
    return float((row[0] if row else 0) or 0)


@dataclass(frozen=True)
class Decision:
    action: str | None = None
    threshold_id: str | None = None
    name: str | None = None
    scope_kind: str | None = None
    scope_value: str | None = None
    period: str | None = None
    spend_usd: float = 0.0
    threshold_usd: float = 0.0
    delay_seconds: int = 0
    notified: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


NONE = Decision()


async def _recipient(session: Any, tenant_id: uuid.UUID) -> str | None:
    from sqlalchemy import select

    from core.models.user import User

    row = await session.execute(
        select(User.email)
        .where(User.tenant_id == tenant_id, User.role == "admin", User.status == "active")
        .order_by(User.created_at)
        .limit(1)
    )
    return (row.scalar_one_or_none() or "").strip() or None


async def notify(session: Any, tenant_id: uuid.UUID, row: Any, spend_usd: float) -> bool:
    """Tell the owner once; best effort, never raises."""
    channels = [c for c in str(row.notify_channels or "").split(",") if c]
    subject = f"Cost threshold {row.name} reached: ${spend_usd:.2f} of ${float(row.threshold_usd):.2f} ({row.period})"
    body = (
        f"The {row.scope_kind} threshold {row.name!r}"
        f"{f' for {row.scope_value}' if row.scope_value else ''} has reached ${spend_usd:.2f} "
        f"of its ${float(row.threshold_usd):.2f} {row.period} limit; the action is {row.action}."
    )
    delivered = False
    for channel in channels:
        try:
            if channel == "log":
                logger.warning("finops_threshold_breached", threshold_id=str(row.id), action=row.action)
                delivered = True
            elif channel == "email":
                to = await _recipient(session, tenant_id)
                if not to:
                    logger.warning("finops_threshold_notification_skipped_no_recipient", threshold_id=str(row.id))
                    continue
                from core.email import send_email

                delivered = bool(await asyncio.to_thread(send_email, to, subject, f"<h2>{subject}</h2><p>{body}</p>"))
        # enterprise-gate: broad-except-ok reason=a-failed-notification-never-touches-the-run
        except Exception as exc:
            logger.warning("finops_threshold_notification_failed", channel=channel, error=type(exc).__name__)
    return delivered


async def enabled_rows(session: Any, tenant_id: uuid.UUID) -> list[Any]:
    """Every enabled threshold of the tenant; none is cut off (creation is capped at MAX_THRESHOLDS)."""
    from sqlalchemy import select

    from core.models.finops_threshold import FinopsThreshold

    return list(
        (
            await session.execute(
                select(FinopsThreshold)
                .where(FinopsThreshold.tenant_id == tenant_id, FinopsThreshold.enabled.is_(True))
                .order_by(FinopsThreshold.name)
            )
        )
        .scalars()
        .all()
    )


async def count_rows(session: Any, tenant_id: uuid.UUID) -> int:
    """How many thresholds the tenant holds, enabled or not."""
    from sqlalchemy import func, select

    from core.models.finops_threshold import FinopsThreshold

    found = (
        await session.execute(
            select(func.count()).select_from(FinopsThreshold).where(FinopsThreshold.tenant_id == tenant_id)
        )
    ).scalar()
    return int(found or 0)


async def claim_breach(session: Any, tenant_id: uuid.UUID, row: Any, key: str, *, now: datetime, spent: float) -> bool:
    """Record the breach for the period unless another run already has; True for the one run that did.

    One conditional update: a concurrent run blocks on the row until this
    transaction ends, then finds the period recorded and updates nothing.
    """
    from sqlalchemy import text as sqltext

    claimed = (
        await session.execute(
            sqltext(
                "UPDATE finops_thresholds SET last_breach_period = :key, last_breach_at = :at, "
                "last_breach_spend_usd = :spent WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid) "
                "AND last_breach_period IS DISTINCT FROM :key RETURNING id"
            ),
            {"key": key, "at": now, "spent": spent, "id": str(row.id), "tid": str(tenant_id)},
        )
    ).fetchone()
    if claimed is None:
        return False
    row.last_breach_period = key
    row.last_breach_at = now
    row.last_breach_spend_usd = spent
    return True


async def check_run(
    session: Any, tenant_id: uuid.UUID, attribution: cost_attribution.Attribution | None, *, now: datetime | None = None
) -> Decision:
    """The strongest breached action for the run; a breach is recorded and notified once a period."""
    if not enabled():
        return NONE
    now = now or datetime.now(UTC)
    given = attribution or cost_attribution.Attribution()
    breached: list[tuple[Any, float]] = []
    for row in await enabled_rows(session, tenant_id):
        if not matches(row, given):
            continue
        spent = await spend(session, tenant_id, row, now=now)
        if spent >= float(row.threshold_usd):
            breached.append((row, spent))
    if not breached:
        return NONE
    claimed: list[tuple[Any, float]] = []
    for row, spent in breached:
        row.notified = False  # transient attribute, not a column
        key = period_key(row.period, now)
        if row.last_breach_period != key and await claim_breach(session, tenant_id, row, key, now=now, spent=spent):
            claimed.append((row, spent))
    if claimed:
        # The claim is committed before anyone is told, so the row lock is not held while notifying.
        await session.commit()
        for row, spent in claimed:
            row.notified = await notify(session, tenant_id, row, spent)

    def effective(row: Any) -> str:
        lifted = getattr(row, "lifted_until", None)
        if row.action == "suspend" and lifted is not None and now < lifted:
            return "alert"
        return row.action

    row, spent = max(
        breached, key=lambda item: (ACTION_RANK[effective(item[0])], item[1] - float(item[0].threshold_usd))
    )
    action = effective(row)
    return Decision(
        action=action,
        threshold_id=str(row.id),
        name=row.name,
        scope_kind=row.scope_kind,
        scope_value=row.scope_value or "",
        period=row.period,
        spend_usd=round(spent, 6),
        threshold_usd=float(row.threshold_usd),
        delay_seconds=int(row.throttle_seconds or DEFAULT_THROTTLE_SECONDS) if action == "throttle" else 0,
        notified=bool(getattr(row, "notified", False)),
    )


def refusal(decision: Decision, *, agent_id: str) -> dict[str, Any]:
    """The run result for a suspended run, in the shape a budget refusal takes."""
    kind = str(decision.scope_kind or "").replace("_", " ")
    scope = kind if decision.scope_kind == "organisation" else f"{kind} {decision.scope_value}"
    message = (
        f"Cost threshold {decision.name!r} exceeded: ${decision.spend_usd:.2f} / ${decision.threshold_usd:.2f} "
        f"({decision.period}); runs for the {scope} are suspended."
    )
    return {
        "task_id": f"msg_{uuid.uuid4().hex[:12]}",
        "agent_id": agent_id,
        "status": "threshold_suspended",
        "error": {"code": ERROR_CODE, "message": message},
        "output": {},
        "confidence": 0,
        "reasoning_trace": [f"Threshold check: ${decision.spend_usd:.2f} >= ${decision.threshold_usd:.2f}"],
        "finops": decision.as_dict(),
    }


async def status(session: Any, tenant_id: uuid.UUID, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Every threshold of the tenant with its spend and share over its period."""
    from sqlalchemy import select

    from core.models.finops_threshold import FinopsThreshold

    now = now or datetime.now(UTC)
    rows = list(
        (
            await session.execute(
                select(FinopsThreshold).where(FinopsThreshold.tenant_id == tenant_id).order_by(FinopsThreshold.name)
            )
        )
        .scalars()
        .all()
    )
    out = []
    for row in rows:
        spent = await spend(session, tenant_id, row, now=now)
        limit = float(row.threshold_usd) or 1.0
        out.append(
            {
                **row_dict(row),
                "spend_usd": round(spent, 6),
                "share": round(spent / limit, 4),
                "breached": spent >= float(row.threshold_usd),
                "period_key": period_key(row.period, now),
            }
        )
    return out
