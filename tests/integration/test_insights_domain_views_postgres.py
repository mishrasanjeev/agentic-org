# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The Domain and Back-office views against real Postgres without BYPASSRLS (the isolation test's schema).

An analyst assigned to HR lands on the Domain view and reads only HR: runs,
run confidence, approvals and feedback on HR agents; an analyst with no
assigned domain reads nothing. The analyst opens the agents, runs, approvals,
cost and quality explorers for HR and is refused the audit explorer. An
administrator's Back-office view counts overdue compliance deadlines (and those
due in 30 days) and the period's transaction findings by severity, open ones
included. The Finance view now carries the success and straight-through rates
and agents by status, as the legacy CFO dashboard did.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from core.insights.metrics import day_bounds, local_today
from tests.integration.test_insights_isolation_postgres import KOLKATA, World, _seed_tenant
from tests.integration.test_insights_isolation_postgres import world as world  # the shared fixture

pytestmark = pytest.mark.timeout(300)


def _analyst(domains: list[str]) -> SimpleNamespace:
    claims = {
        "sub": "analyst@example.test",
        "role": "analyst",
        "agenticorg:user_id": str(uuid.uuid4()),
        "agenticorg:domains": domains,
        "grantex:scopes": ["insights:read"],
    }
    return SimpleNamespace(
        state=SimpleNamespace(claims=claims, scopes=["insights:read"], auth_mode=None), query_params={}
    )


def _more(db: World, tenant: SimpleNamespace) -> None:
    """Confidence-scored HR runs, HR feedback, compliance deadlines and transaction findings for ``tenant``."""
    from sqlalchemy import select

    from core.models.agent import Agent
    from core.models.compliance_deadline import ComplianceDeadline
    from core.models.feedback import AgentFeedback
    from core.models.insights import RunFact
    from core.models.txn_finding import TxnFinding

    tid = uuid.UUID(tenant.tid)
    with db.admin.connect() as conn:
        conn.exec_driver_sql(f'SET search_path TO "{db.schema}"')
        hr_agent = conn.execute(select(Agent.id).where(Agent.tenant_id == tid, Agent.domain == "hr")).scalar_one()
        company_id = conn.exec_driver_sql(
            "SELECT id FROM companies WHERE tenant_id = %(t)s", {"t": tid}
        ).scalar_one()
    now = datetime.now(UTC)
    today = local_today(KOLKATA, now)
    start = day_bounds(today, KOLKATA)[0] + timedelta(minutes=30)
    common = {"tenant_id": tid, "entrypoint": "agents", "agent_id": hr_agent, "agent_type": "leave_bot",
              "domain": "hr", "status": "completed", "duration_ms": 800, "cost_usd": Decimal("0.01")}
    db.add(
        RunFact(run_key="agents:conf:" + uuid.uuid4().hex, confidence=0.9, confidence_floor=0.7, started_at=start,
                **common),
        RunFact(run_key="agents:conf:" + uuid.uuid4().hex, confidence=0.5, confidence_floor=0.7,
                started_at=start + timedelta(minutes=1), **common),
        AgentFeedback(tenant_id=tid, agent_id=hr_agent, run_id="hr-r1", feedback_type="thumbs_down",
                      created_at=start),
        AgentFeedback(tenant_id=tid, agent_id=hr_agent, run_id="hr-r2", feedback_type="thumbs_up",
                      created_at=start),
        ComplianceDeadline(tenant_id=tid, company_id=company_id, deadline_type="gstr3b", filing_period="2026-09",
                           due_date=today - timedelta(days=3)),
        ComplianceDeadline(tenant_id=tid, company_id=company_id, deadline_type="gstr3b", filing_period="2026-10",
                           due_date=today + timedelta(days=10)),
        ComplianceDeadline(tenant_id=tid, company_id=company_id, deadline_type="tds", filing_period="2026-Q2",
                           due_date=today - timedelta(days=1), filed=True),
        TxnFinding(tenant_id=tid, kind="structuring", entity_ref="acct-1", severity="high", status="open",
                   fingerprint=uuid.uuid4().hex, detected_at=start),
        TxnFinding(tenant_id=tid, kind="velocity", entity_ref="acct-2", severity="low", status="closed",
                   fingerprint=uuid.uuid4().hex, detected_at=start),
    )


def _by_key(view: dict) -> dict:
    return {m["key"]: m for m in view["tiles"] + view["charts"]}


@pytest.mark.asyncio
async def test_the_domain_view_keeps_an_analyst_to_their_domain(world) -> None:
    from api.v1 import insights as api
    from api.v1 import insights_explore as explore

    tenant = _seed_tenant(world, "Alpha")
    _more(world, tenant)
    hr = _analyst(["hr"])

    status = await api.insights_status(hr, tenant_id=tenant.tid)
    assert status["views"] == ["domain"]
    for refused in ("executive", "finance", "back_office"):
        with pytest.raises(HTTPException) as forbidden:
            await api.insights_view(refused, hr, tenant_id=tenant.tid)
        assert forbidden.value.status_code == 403

    view = _by_key(await api.insights_view("domain", hr, tenant_id=tenant.tid))
    # The seed's HR run plus the two scored ones; the two finance runs are not the analyst's.
    assert view["runs.volume"]["value"] == 3
    confidence = view["runs.confidence"]
    assert confidence["value"] == pytest.approx(70.0)
    assert {r["label"]: r["value"] for r in confidence["rows"]} == {"below_floor": 1, "scored_runs": 2}
    assert confidence["state"] == "act"  # 70 is the act level (watch below 80)
    feedback = view["quality.feedback"]
    # The finance agent's correction is not the analyst's.
    assert feedback["value"] == 2 and {r["label"] for r in feedback["rows"]} == {"thumbs_down", "thumbs_up"}
    assert view["approvals.pending"]["value"] == 1  # the leave approval, not the finance one

    readable = [e["key"] for e in (await explore.insights_explorers(hr, tenant_id=tenant.tid))["explorers"]]
    assert readable == ["agents", "runs", "approvals", "cost", "quality"]
    runs = await explore.insights_explore("runs", hr, tenant_id=tenant.tid)
    assert {row["domain"] for row in runs["rows"]} == {"hr"}
    with pytest.raises(HTTPException) as refused_audit:
        await explore.insights_explore("audit", hr, tenant_id=tenant.tid)
    assert refused_audit.value.status_code == 403

    # No assigned domain: the view opens and every figure is no_data.
    nowhere = _by_key(await api.insights_view("domain", _analyst([]), tenant_id=tenant.tid))
    assert {m["data_quality"] for m in nowhere.values() if m["key"] != "agents.by_status"} == {"no_data"}


@pytest.mark.asyncio
async def test_back_office_and_finance_views(world) -> None:
    from api.v1 import insights as api

    tenant = _seed_tenant(world, "Alpha")
    _more(world, tenant)

    back_office = _by_key(await api.insights_view("back_office", tenant.admin, tenant_id=tenant.tid))
    deadlines = back_office["backoffice.deadlines"]
    assert deadlines["value"] == 1 and deadlines["state"] == "watch"
    assert deadlines["rows"] == [{"label": "gstr3b", "overdue": 1, "due_30d": 1}]
    findings = back_office["backoffice.txn_findings"]
    assert findings["value"] == 2
    assert findings["rows"] == [{"label": "high", "value": 1, "open": 1}, {"label": "low", "value": 1, "open": 0}]
    # Back office reads the backoffice, legal, risk, corporate and comms domains: no runs in this seed.
    assert back_office["runs.volume"]["data_quality"] == "no_data"

    finance = _by_key(await api.insights_view("finance", tenant.admin, tenant_id=tenant.tid))
    assert {"runs.success_rate", "runs.straight_through_rate", "agents.by_status"} <= set(finance)
    assert finance["agents.by_status"]["value"] == 2
