# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights platform view: who the platform operator is, cross-tenant aggregates and the platform posture.

The operator is an active ``admin`` user of the tenant named by
``settings.platform_operator_tenant_id`` (``AGENTICORG_PLATFORM_OPERATOR_TENANT_ID``),
signed in with a human session (no API key, Grantex or agent token) and
enrolled in MFA, loaded from the database on every request. Anyone else gets
404, so the surface is not revealed; an empty setting means nobody.

Cross-tenant figures come only from the aggregate functions of migration
``v6z107_insights_platform``, ``public.insights_platform_daily`` and
``public.insights_platform_tenants``: SECURITY DEFINER, owned by a NOLOGIN
NOBYPASSRLS role that reads a few columns of ``insights_daily``,
``insights_rollup_state``, ``tenants`` and ``agents``. The application never
reads another tenant's rows. Tenant ids are returned to the operator but are
never logged or used as metric labels.

The posture (``posture``) reads the Alembic version table, the system
catalogue, the connected role's attributes and ``health_check_history``
(a platform table without tenant rows).
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from fastapi import HTTPException, Request
from sqlalchemy import func, select, text

from core.config import settings

logger = structlog.get_logger()

# The functions refuse anything longer (migration v6z107_insights_platform).
MAX_RANGE_DAYS = 92
DEFAULT_RANGE_DAYS = 30
# A tenant's rollups must reconcile this many consecutive nights before its
# figures are trusted for decisions (shown as a progress bullet).
RECONCILED_GATE_NIGHTS = 14
MAX_TENANT_ROWS = 2000
HEALTH_WINDOW_HOURS = 24
STATEMENT_TIMEOUT = "10s"

DAILY_SQL = text(
    "SELECT day, tenants_active, runs, failed, tokens, cost_usd "
    "FROM public.insights_platform_daily(:p_from, :p_to)"
)
TENANTS_SQL = text(
    "SELECT tenant_id, tenant_name, plan, runs, failed, cost_usd, active_agents, last_active_day, "
    "reconciled_nights, last_rollup_status FROM public.insights_platform_tenants(:p_from, :p_to)"
)


class PlatformRangeError(ValueError):
    """The requested range is reversed or longer than the functions allow."""


@dataclass(frozen=True)
class PlatformOperator:
    user_id: uuid.UUID
    tenant_id: uuid.UUID


def _not_found() -> HTTPException:
    return HTTPException(404, "Not Found")


def _operator_tenant() -> uuid.UUID | None:
    raw = str(getattr(settings, "platform_operator_tenant_id", "") or "").strip()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        logger.warning("platform_operator_tenant_invalid")
        return None


def _human_session(request: Request) -> dict[str, Any] | None:
    """The caller's claims when they come from a human session; None for API keys, Grantex and agent tokens."""
    claims = getattr(request.state, "claims", None)
    if not isinstance(claims, dict) or not claims:
        return None
    auth_mode = getattr(request.state, "auth_mode", None)
    subject = str(claims.get("sub") or "")
    if auth_mode in {"api_key", "grantex"} or subject.startswith("apikey:") or claims.get("grantex:grant_id"):
        return None
    if auth_mode not in {None, "legacy"}:
        return None
    if claims.get("agenticorg:agent_id") or getattr(request.state, "agent_id", None):
        return None
    return claims


async def platform_operator(request: Request) -> PlatformOperator | None:
    """The platform operator making this request, or None (never an HTTP error)."""
    operator_tenant = _operator_tenant()
    if operator_tenant is None:
        return None
    claims = _human_session(request)
    if claims is None:
        return None
    try:
        tenant_id = uuid.UUID(str(getattr(request.state, "tenant_id", "") or ""))
    except ValueError:
        return None
    if tenant_id != operator_tenant:
        return None

    user_id: uuid.UUID | None = None
    raw_user_id = claims.get("agenticorg:user_id")
    if raw_user_id:
        try:
            user_id = uuid.UUID(str(raw_user_id))
        except ValueError:
            return None
    email = str(claims.get("email") or claims.get("sub") or "").strip().lower()
    if user_id is None and not email:
        return None

    from api.v1.auth import mfa_enrolled
    from core.database import get_tenant_session
    from core.models.user import User

    identity = User.id == user_id if user_id is not None else func.lower(User.email) == email
    async with get_tenant_session(tenant_id) as session:
        user = (
            await session.execute(
                select(User).where(
                    User.tenant_id == tenant_id,
                    identity,
                    User.status == "active",
                    User.role == "admin",
                )
            )
        ).scalar_one_or_none()
    if user is None or not mfa_enrolled(user):
        return None
    return PlatformOperator(user_id=user.id, tenant_id=user.tenant_id)


async def require_platform_operator(request: Request) -> PlatformOperator:
    """FastAPI dependency: the platform operator, else 404 (the surface is not revealed)."""
    operator = await platform_operator(request)
    if operator is None:
        raise _not_found()
    return operator


async def is_platform_operator(request: Request) -> bool:
    """For ``GET /insights/status``: cheap (no query unless the tenant matches) and never raises."""
    if _operator_tenant() is None:
        return False
    try:
        return await platform_operator(request) is not None
    # enterprise-gate: broad-except-ok reason=platform-operator-flag-read-failure-reads-as-false
    except Exception as exc:
        logger.warning("platform_operator_check_failed", error_type=type(exc).__name__)
        return False


# ------------------------------------------------------------------ range


def platform_range(start: date | None, end: date | None, *, today: date | None = None) -> tuple[date, date]:
    """The requested UTC-day range (default: the last 30 days, today included), at most 92 days apart."""
    today = today or datetime.now(UTC).date()
    end = end or (today if start is None else min(today, start + timedelta(days=DEFAULT_RANGE_DAYS - 1)))
    start = start or end - timedelta(days=DEFAULT_RANGE_DAYS - 1)
    if start > end:
        raise PlatformRangeError("from must be on or before to")
    if (end - start).days > MAX_RANGE_DAYS:
        raise PlatformRangeError(f"to may be at most {MAX_RANGE_DAYS} days after from")
    return start, end


# ------------------------------------------------------------------ aggregates


def _money(value: Any) -> float:
    return float(round(Decimal(value or 0), 6))


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


async def _bounded(session: Any) -> None:
    await session.execute(text("SELECT set_config('statement_timeout', :v, true)"), {"v": STATEMENT_TIMEOUT})


async def platform_daily(session: Any, start: date, end: date) -> list[dict[str, Any]]:
    """One row per day with runs: tenants with runs, runs, failed runs, tokens and cost (USD)."""
    await _bounded(session)
    rows = (await session.execute(DAILY_SQL, {"p_from": start, "p_to": end})).mappings().all()
    return [
        {
            "day": row["day"].isoformat(),
            "tenants_active": int(row["tenants_active"] or 0),
            "runs": int(row["runs"] or 0),
            "failed": int(row["failed"] or 0),
            "tokens": int(row["tokens"] or 0),
            "cost_usd": _money(row["cost_usd"]),
        }
        for row in rows
    ]


async def platform_tenants(session: Any, start: date, end: date) -> list[dict[str, Any]]:
    """One row per live tenant: name, plan, usage in the range, active agents and the rollup gate."""
    await _bounded(session)
    rows = (await session.execute(TENANTS_SQL, {"p_from": start, "p_to": end})).mappings().all()
    out: list[dict[str, Any]] = []
    for row in rows:
        runs = int(row["runs"] or 0)
        failed = int(row["failed"] or 0)
        nights = int(row["reconciled_nights"] or 0)
        out.append(
            {
                "tenant_id": str(row["tenant_id"]),
                "tenant_name": row["tenant_name"] or "",
                "plan": row["plan"] or "",
                "runs": runs,
                "failed": failed,
                "failure_rate": _rate(failed, runs),
                "cost_usd": _money(row["cost_usd"]),
                "active_agents": int(row["active_agents"] or 0),
                "last_active_day": row["last_active_day"].isoformat() if row["last_active_day"] else None,
                "reconciled_nights": nights,
                "gate_met": nights >= RECONCILED_GATE_NIGHTS,
                "last_rollup_status": row["last_rollup_status"],
            }
        )
    return out


def totals(daily: list[dict[str, Any]], tenants: list[dict[str, Any]]) -> dict[str, Any]:
    """Range totals: distinct tenants with runs (from the tenant rows), runs, failure rate, tokens and cost."""
    runs = sum(d["runs"] for d in daily)
    failed = sum(d["failed"] for d in daily)
    return {
        "tenants_active": sum(1 for t in tenants if t["runs"] > 0),
        "tenants_total": len(tenants),
        "runs": runs,
        "failed": failed,
        "failure_rate": _rate(failed, runs),
        "tokens": sum(d["tokens"] for d in daily),
        "cost_usd": round(sum(d["cost_usd"] for d in daily), 6),
    }


# ------------------------------------------------------------------ posture

TENANT_TABLES_SQL = text(
    """
    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
      AND a.attname = 'tenant_id' AND a.attnum > 0 AND NOT a.attisdropped
    ORDER BY c.relname
    """
)
ROLE_SQL = text(
    "SELECT rolname, rolsuper, rolbypassrls FROM pg_catalog.pg_roles WHERE rolname = current_user"
)
HEALTH_COUNTS_SQL = text(
    "SELECT status, count(*) AS n FROM health_check_history WHERE recorded_at >= :since GROUP BY status"
)
HEALTH_LATEST_SQL = text(
    "SELECT recorded_at, status, version, commit FROM health_check_history "
    "WHERE recorded_at >= :since ORDER BY recorded_at DESC LIMIT 1"
)


def _health_bucket(status: str | None) -> str:
    """``healthy``, ``unhealthy`` or ``other`` (snapshot statuses may carry an error suffix)."""
    value = str(status or "").strip().lower()
    if value == "healthy":
        return "healthy"
    if value.startswith("unhealthy"):
        return "unhealthy"
    return "other"


async def _schema(session: Any) -> dict[str, Any]:
    from core.database import get_expected_alembic_heads

    code_heads = sorted(get_expected_alembic_heads())
    exists = (await session.execute(text("SELECT to_regclass('public.alembic_version') IS NOT NULL"))).scalar()
    database_heads: list[str] = []
    if exists:
        database_heads = sorted(
            str(v) for v in (await session.execute(text("SELECT version_num FROM alembic_version"))).scalars() if v
        )
    return {"ok": bool(database_heads) and database_heads == code_heads, "database_heads": database_heads,
            "code_heads": code_heads}


async def _rls(session: Any) -> dict[str, Any]:
    rows = (await session.execute(TENANT_TABLES_SQL)).all()
    unforced = sorted({str(name) for name, enabled, forced in rows if not (enabled and forced)})
    tables = {str(name) for name, _enabled, _forced in rows}
    return {
        "ok": bool(tables) and not unforced,
        "tenant_tables": len(tables),
        "forced": len(tables) - len(unforced),
        "unforced": unforced,
    }


async def _role(session: Any) -> dict[str, Any]:
    row = (await session.execute(ROLE_SQL)).first()
    if row is None:  # pragma: no cover - current_user always resolves
        return {"ok": False, "role": None, "superuser": None, "bypass_rls": None}
    superuser, bypass = bool(row[1]), bool(row[2])
    return {"ok": not (superuser or bypass), "role": str(row[0]), "superuser": superuser, "bypass_rls": bypass}


async def _health(session: Any, now: datetime) -> dict[str, Any]:
    since = now - timedelta(hours=HEALTH_WINDOW_HOURS)
    counts: Counter[str] = Counter({"healthy": 0, "unhealthy": 0, "other": 0})
    for status, n in (await session.execute(HEALTH_COUNTS_SQL, {"since": since})).all():
        counts[_health_bucket(status)] += int(n)
    latest = (await session.execute(HEALTH_LATEST_SQL, {"since": since})).mappings().first()
    snapshots = sum(counts.values())
    latest_status = _health_bucket(latest["status"]) if latest else None
    return {
        "ok": snapshots > 0 and latest_status == "healthy",
        "window_hours": HEALTH_WINDOW_HOURS,
        "snapshots": snapshots,
        "counts": dict(counts),
        "latest_status": latest_status,
        "latest_at": latest["recorded_at"].isoformat() if latest and latest["recorded_at"] else None,
        "latest_version": latest["version"] if latest else None,
        "latest_commit": latest["commit"] if latest else None,
    }


async def posture(session: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """The platform posture checklist: schema at head, forced tenant RLS, an RLS-bound role, recent health."""
    now = now or datetime.now(UTC)
    await _bounded(session)
    sections = {
        "schema": await _schema(session),
        "rls": await _rls(session),
        "role": await _role(session),
        "health": await _health(session, now),
    }
    return {
        "computed_at": now.isoformat(),
        "ok": all(section["ok"] for section in sections.values()),
        "checks": [{"key": key, "ok": section["ok"]} for key, section in sections.items()],
        **sections,
    }
