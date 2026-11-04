# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""What changed since a person last opened Insights (the strip on the landing views).

The last visit is kept per person in ``insights_user_state``. Changes since
then (at most 30 days back; 7 days on a first visit) are counted within the
caller's reach: agents created and agents whose status changed, approvals
decided and runs that failed (limited to the caller's domains), alerts that
fired for the caller's role, and, for administrators and auditors, audited
changes to configuration. Each item links to the rows behind it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

MAX_LOOKBACK = timedelta(days=30)
FIRST_VISIT_LOOKBACK = timedelta(days=7)
EXPLORE = "/dashboard/insights/explore"
CONFIG_EVENT_PREFIXES = ("config.", "agent.create", "agent.update", "agent.retire", "connector.", "policy.")


async def last_seen(session: Any, tenant_id: uuid.UUID, user_id: uuid.UUID) -> datetime | None:
    from sqlalchemy import select

    from core.models.insights import InsightsUserState as S

    return (
        await session.execute(select(S.last_seen_at).where(S.tenant_id == tenant_id, S.user_id == user_id))
    ).scalar_one_or_none()


async def mark_seen(session: Any, tenant_id: uuid.UUID, user_id: uuid.UUID, at: datetime | None = None) -> datetime:
    from sqlalchemy.dialects.postgresql import insert

    from core.models.insights import InsightsUserState as S

    moment = at or datetime.now(UTC)
    statement = insert(S).values(tenant_id=tenant_id, user_id=user_id, last_seen_at=moment, updated_at=moment)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[S.tenant_id, S.user_id], set_={"last_seen_at": moment, "updated_at": moment}
        )
    )
    return moment


def _agent_scope(agent: Any, tenant_id: uuid.UUID, domains: list[str] | None) -> list[Any]:
    clauses = [agent.tenant_id == tenant_id]
    if domains is not None:
        clauses += [agent.domain.in_(list(domains)), agent.visibility == "tenant"]
    return clauses


async def changes(
    session: Any,
    tenant_id: uuid.UUID,
    caller: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    from sqlalchemy import func, or_, select

    from core.models.agent import Agent, AgentLifecycleEvent
    from core.models.audit import AuditLog as A
    from core.models.hitl import HITLQueue as H
    from core.models.insights import InsightsAlert
    from core.models.insights import RunFact as R

    now = now or datetime.now(UTC)
    seen = await last_seen(session, tenant_id, caller.user_id) if caller.user_id else None
    first = seen is None
    since = max(seen or now - FIRST_VISIT_LOOKBACK, now - MAX_LOOKBACK)
    scope = _agent_scope(Agent, tenant_id, caller.domains)
    in_scope = select(Agent.id).where(*scope)

    async def count(statement: Any) -> int:
        return int((await session.execute(statement)).scalar_one() or 0)

    items: list[dict[str, Any]] = []

    def add(key: str, n: int, link: str) -> None:
        if n:
            items.append({"key": key, "count": n, "link": link})

    add(
        "agents_created",
        await count(select(func.count()).select_from(Agent).where(*scope, Agent.created_at > since)),
        f"{EXPLORE}/agents",
    )
    add(
        "agent_status_changes",
        await count(
            select(func.count()).where(
                AgentLifecycleEvent.tenant_id == tenant_id,
                AgentLifecycleEvent.agent_id.in_(in_scope),
                AgentLifecycleEvent.created_at > since,
            )
        ),
        f"{EXPLORE}/agents",
    )
    add(
        "approvals_decided",
        await count(
            select(func.count()).where(
                H.tenant_id == tenant_id, H.agent_id.in_(in_scope), H.decision_at.is_not(None), H.decision_at > since
            )
        ),
        f"{EXPLORE}/approvals?filter%5Bstatus%5D=decided",
    )
    run_clauses = [R.tenant_id == tenant_id, R.status == "failed", R.started_at > since]
    if caller.domains is not None:
        run_clauses.append(R.domain.in_(list(caller.domains)))
    failed = await count(select(func.count()).where(*run_clauses))
    add("runs_failed", failed, f"{EXPLORE}/runs?filter%5Bstatus%5D=failed")
    alert_clauses = [InsightsAlert.tenant_id == tenant_id, InsightsAlert.fired_at > since]
    if not caller.is_admin:
        alert_clauses.append(InsightsAlert.owner_role == caller.role)
    add("alerts_fired", await count(select(func.count()).where(*alert_clauses)), "/dashboard/insights")
    if caller.is_admin or caller.role == "auditor":
        add(
            "config_changes",
            await count(
                select(func.count()).where(
                    A.tenant_id == tenant_id,
                    A.created_at > since,
                    or_(*(A.event_type.startswith(prefix) for prefix in CONFIG_EVENT_PREFIXES)),
                )
            ),
            f"{EXPLORE}/audit",
        )
    return {"since": since.isoformat(), "first_visit": first, "items": items}
