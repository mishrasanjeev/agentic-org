# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights entity pages and search against real Postgres without BYPASSRLS (the isolation test's schema).

An administrator opens each kind of entity (agent, workflow, connector,
company, user, cost centre) and gets its facts, scoped metric cards and
timeline; the other tenant's entities are not found. A CHRO opens the HR agent
but not the finance one, and may not open workflows. Search finds the
tenant's own entities by name among the kinds the caller may open, a run by
its id, and treats LIKE wildcards in the text literally.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from tests.integration.test_insights_isolation_postgres import World, _seed_tenant
from tests.integration.test_insights_isolation_postgres import world as world  # the shared fixture

pytestmark = pytest.mark.timeout(300)


def _ids(db: World, tenant: SimpleNamespace) -> SimpleNamespace:
    """The seeded entities' ids, looked up by their names, plus a cost centre holding the finance agent."""
    from core.models.organization import CostCenter

    label = tenant.label
    with db.admin.begin() as conn:
        conn.exec_driver_sql(f'SET search_path TO "{db.schema}"')

        def one(sql: str, **params):
            return conn.execute(text(sql), {"t": tenant.tid, **params}).scalar_one()

        ids = SimpleNamespace(
            fin=one("SELECT id FROM agents WHERE tenant_id = :t AND domain = 'finance'"),
            hr=one("SELECT id FROM agents WHERE tenant_id = :t AND domain = 'hr'"),
            workflow=one("SELECT id FROM workflow_definitions WHERE tenant_id = :t"),
            connector=one("SELECT id FROM connectors WHERE tenant_id = :t"),
            company=one("SELECT id FROM companies WHERE tenant_id = :t"),
            admin=one("SELECT id FROM users WHERE tenant_id = :t AND role = 'admin'"),
            centre=uuid.uuid4(),
        )
    db.add(CostCenter(id=ids.centre, tenant_id=uuid.UUID(tenant.tid), company_id=ids.company, code=f"{label[:2]}-01",
                      name=f"{label} finance ops", budget_limit=Decimal("10")))
    with db.admin.begin() as conn:
        conn.exec_driver_sql(f'SET search_path TO "{db.schema}"')
        conn.execute(text("UPDATE agents SET cost_center_id = :c WHERE id = :a"), {"c": ids.centre, "a": ids.fin})
    return ids


def _caller(role: str, domains: list[str] | None) -> SimpleNamespace:
    claims = {"sub": f"{role}@example.test", "role": role, "agenticorg:user_id": str(uuid.uuid4()),
              "agenticorg:domains": domains, "grantex:scopes": ["insights:read"]}
    return SimpleNamespace(state=SimpleNamespace(claims=claims, scopes=["insights:read"], auth_mode=None),
                           query_params={})


def _facts(page: dict) -> dict:
    return {f["key"]: f["value"] for f in page["facts"]}


def _cards(page: dict) -> dict:
    return {c["key"]: c for c in page["cards"]}


@pytest.mark.asyncio
async def test_entity_pages_keep_to_the_tenant_and_the_callers_reach(world) -> None:
    from api.v1 import insights_entities as api

    alpha, bravo = _seed_tenant(world, "Alpha"), _seed_tenant(world, "Bravo")
    a, b = _ids(world, alpha), _ids(world, bravo)

    async def page(kind, entity_id, caller=alpha.admin, tenant=alpha):
        return await api.insights_entity(kind, entity_id, caller, tenant_id=tenant.tid)

    agent = await page("agent", a.fin)
    assert agent["title"] == "Alpha invoice agent" and _facts(agent)["domain"] == "finance"
    assert _facts(agent)["spend_usd"] == 2.0
    cards = _cards(agent)
    assert cards["runs.volume"]["value"] == 2 and cards["runs.success_rate"]["value"] == 50.0
    assert agent["links"]["runs"].endswith(str(a.fin))
    assert {row["event"] for row in agent["timeline"]} >= {"agent.run"}

    workflow = await page("workflow", a.workflow)
    assert workflow["title"] == "Alpha invoice to pay"
    assert _cards(workflow)["workflow.runs"]["value"] == 1
    assert _cards(workflow)["workflow.runs"]["rows"] == [{"label": "failed", "value": 1}]

    connector = await page("connector", a.connector)
    health = _cards(connector)["connector.health"]
    assert (health["value"], health["state"]) == (1, "act")

    company = await page("company", a.company)
    assert company["title"] == "Alpha Books" and _cards(company)["runs.volume"]["value"] == 3

    user = await page("user", a.admin)
    assert user["title"] == "Alpha Admin" and _cards(user)["user.decisions"]["value"] == 1
    assert any(row["event"] == "hitl.decided" for row in user["timeline"])

    centre = await page("cost_centre", a.centre)
    assert _facts(centre)["spend_usd"] == 2.0 and _facts(centre)["budget_used_pct"] == 20.0
    assert [x["name"] for x in centre["agents"]] == ["Alpha invoice agent"]
    assert _cards(centre)["runs.volume"]["value"] == 2

    # The other tenant's entities are not found, whatever the kind.
    for kind, entity_id in (("agent", b.fin), ("workflow", b.workflow), ("connector", b.connector),
                            ("company", b.company), ("user", b.admin), ("cost_centre", b.centre)):
        with pytest.raises(HTTPException) as missing:
            await page(kind, entity_id)
        assert missing.value.status_code == 404, kind

    # A CHRO opens the HR agent, not the finance one, and may not open a workflow.
    chro = _caller("chro", ["hr"])
    assert (await page("agent", a.hr, chro))["title"] == "Alpha leave agent"
    with pytest.raises(HTTPException) as outside:
        await page("agent", a.fin, chro)
    assert outside.value.status_code == 404
    with pytest.raises(HTTPException) as forbidden:
        await page("workflow", a.workflow, chro)
    assert forbidden.value.status_code == 403
    with pytest.raises(HTTPException) as unknown:
        await page("planet", a.fin)
    assert unknown.value.status_code == 404


@pytest.mark.asyncio
async def test_search_finds_what_the_caller_may_open(world) -> None:
    from api.v1 import insights_entities as api

    alpha, bravo = _seed_tenant(world, "Alpha"), _seed_tenant(world, "Bravo")
    _ids(world, alpha)
    _ids(world, bravo)

    found = (await api.insights_search(alpha.admin, tenant_id=alpha.tid, q="alpha"))["results"]
    kinds = {r["kind"] for r in found}
    assert kinds == {"agent", "workflow", "connector", "company", "user", "cost_centre"}
    assert all("Bravo" not in (r["title"] or "") and "bravo" not in (r["title"] or "") for r in found)
    assert [r["title"] for r in (await api.insights_search(alpha.admin, tenant_id=alpha.tid, q="ledger"))["results"]
            ] == ["alpha-ledger"]

    chro = _caller("chro", ["hr"])
    for_chro = (await api.insights_search(chro, tenant_id=alpha.tid, q="alpha"))["results"]
    assert [(r["kind"], r["title"]) for r in for_chro] == [("agent", "Alpha leave agent")]

    by_id = (await api.insights_search(alpha.auditor, tenant_id=alpha.tid, q=str(alpha.failed_run)))["results"]
    assert [r["kind"] for r in by_id] == ["run"] and by_id[0]["at"]
    assert (await api.insights_search(alpha.admin, tenant_id=alpha.tid, q=str(bravo.failed_run)))["results"] == []
    # Wildcards are literal; one character is too short to search.
    assert (await api.insights_search(alpha.admin, tenant_id=alpha.tid, q="%%"))["results"] == []
    assert (await api.insights_search(alpha.admin, tenant_id=alpha.tid, q="a"))["results"] == []
