# SPDX-License-Identifier: Apache-2.0
"""Spend attribution: server-owned resolution through source mappings and the organisation tree."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from core.models.spend import SpendOrgNode, SpendSourceMapping
from core.spend import mappings, org, resolver
from core.spend.resolver import Hints
from tests.unit.spend_usage_fakes import ACTOR, T0, TENANT, install

AGENT = uuid.UUID("44444444-4444-4444-8444-444444444444")
COST_CENTRE = uuid.UUID("55555555-5555-4555-8555-555555555555")
WORKFLOW = uuid.UUID("66666666-6666-4666-8666-666666666666")
USER = uuid.UUID("77777777-7777-4777-8777-777777777777")
DEPARTMENT = uuid.UUID("88888888-8888-4888-8888-888888888888")


def hints(**over) -> Hints:
    base = {
        "agent_id": None,
        "agent_version": None,
        "application": "api",
        "default_use_case": "",
        "workflow_id": None,
        "workflow_run_id": None,
        "run_id": None,
        "initiating_user_id": None,
        "origin": "hook",
    }
    base.update(over)
    return Hints(**base)


@pytest.fixture
def store(monkeypatch):
    return install(monkeypatch)


def node(store, code: str, kind: str, parent: SpendOrgNode | None = None, active: bool = True) -> SpendOrgNode:
    row = SpendOrgNode(
        id=uuid.uuid4(), tenant_id=TENANT, code=code, name=code, kind=kind,
        parent_id=parent.id if parent else None, active=active,
    )  # fmt: skip
    store.add(row)
    return row


def mapping(store, source_type: str, source_ref, target: SpendOrgNode | None = None, **labels) -> None:
    store.add(
        SpendSourceMapping(
            id=uuid.uuid4(), tenant_id=TENANT, source_type=source_type, source_ref=str(source_ref),
            org_node_id=target.id if target else None, active=labels.pop("active", True), **labels,
        )
    )  # fmt: skip


def agent_row(store, *, status="active", cost_centre=COST_CENTRE, cc_code="CC-4120", risk="high", use_case=None):
    store.agents[str(AGENT)] = ("2.1.0", "kyc_analyst", status, cost_centre, cc_code, None, risk, use_case)


@pytest.fixture
def tree(store):
    group = node(store, "GRP", "group")
    unit = node(store, "BU-RETAIL", "business_unit", group)
    dept = node(store, "DEPT-OPS", "department", unit)
    centre = node(store, "CC-4120", "cost_centre", dept)
    return SimpleNamespace(group=group, unit=unit, dept=dept, centre=centre)


class TestResolution:
    @pytest.mark.asyncio
    async def test_agent_mapping_wins_over_cost_centre_code(self, store, tree):
        agent_row(store)
        mapping(store, "agent", AGENT, tree.dept, product_line="cards", use_case="kyc")
        found = await resolver.resolve(store, TENANT, hints(agent_id=str(AGENT)), now=1.0)
        assert (found.org_node_id, found.attribution_path) == (tree.dept.id, "agent_mapping")
        assert found.business_unit_node_id == tree.unit.id and found.product_line == "cards" and found.use_case == "kyc"
        assert found.agent_id == AGENT and found.agent_version == "2.1.0" and found.risk_tier == "high"

    @pytest.mark.asyncio
    async def test_cost_centre_code_resolves_to_node(self, store, tree):
        agent_row(store, cc_code="cc-4120")
        found = await resolver.resolve(store, TENANT, hints(agent_id=str(AGENT)), now=1.0)
        assert (found.org_node_id, found.attribution_path) == (tree.centre.id, "cost_centre_code")
        assert found.use_case == "kyc_analyst"  # the agent type, with no mapping or registry use case
        mapping(store, "cost_center", COST_CENTRE, tree.dept)
        resolver.invalidate(TENANT)
        again = await resolver.resolve(store, TENANT, hints(agent_id=str(AGENT)), now=1.0)
        assert (again.org_node_id, again.attribution_path) == (tree.dept.id, "cost_centre_mapping")

    @pytest.mark.asyncio
    async def test_unknown_cost_centre_code_is_unattributed_unknown_label_never_dropped(self, store, tree):
        agent_row(store, cc_code="CC-9999")
        mapping(store, "application", "api", tree.unit)
        found = await resolver.resolve(store, TENANT, hints(agent_id=str(AGENT)), now=1.0)
        assert found.org_node_id is None and found.unattributed_reason == "unknown_label"
        assert found.attribution_path is None and found.agent_id == AGENT  # the label stops resolution

    @pytest.mark.asyncio
    async def test_inactive_node_is_unattributed_inactive_node(self, store, tree):
        closed = node(store, "CC-OLD", "cost_centre", tree.dept, active=False)
        agent_row(store)
        mapping(store, "agent", AGENT, closed)
        found = await resolver.resolve(store, TENANT, hints(agent_id=str(AGENT)), now=1.0)
        assert found.org_node_id is None and found.unattributed_reason == "inactive_node"

    @pytest.mark.asyncio
    async def test_non_uuid_agent_falls_back_to_application_mapping(self, store, tree):
        mapping(store, "application", "mcp", tree.unit, use_case="mcp-tools")
        found = await resolver.resolve(store, TENANT, hints(agent_id="mcp_abc123", application="mcp"), now=1.0)
        assert (found.org_node_id, found.attribution_path) == (tree.unit.id, "application_mapping")
        assert found.agent_id is None and found.use_case == "mcp-tools" and found.business_unit_node_id == tree.unit.id

    @pytest.mark.asyncio
    async def test_retired_or_missing_agent_hint_is_ignored_on_the_hook_path(self, store, tree):
        agent_row(store, status="retired")
        mapping(store, "agent", AGENT, tree.centre)
        mapping(store, "application", "workflows", tree.unit)
        found = await resolver.resolve(store, TENANT, hints(agent_id=str(AGENT), application="workflows"), now=1.0)
        assert found.agent_id is None and found.attribution_path == "application_mapping"
        missing = await resolver.resolve(
            store, TENANT, hints(agent_id=str(uuid.uuid4()), application="workflows"), now=1.0
        )
        assert missing.agent_id is None and missing.org_node_id == tree.unit.id

    @pytest.mark.asyncio
    async def test_backfill_keeps_a_missing_agent_id(self, store, tree):
        ghost = uuid.uuid4()
        mapping(store, "agent", ghost, tree.centre)
        found = await resolver.resolve(store, TENANT, hints(agent_id=str(ghost), origin="backfill"), now=1.0)
        assert found.agent_id == ghost and found.attribution_path == "agent_mapping"
        agent_row(store, status="retired")
        kept = await resolver.resolve(store, TENANT, hints(agent_id=str(AGENT), origin="backfill"), now=1.0)
        assert kept.agent_id == AGENT and kept.attribution_path == "cost_centre_code"

    @pytest.mark.asyncio
    async def test_workflow_mapping_then_user_department(self, store, tree):
        mapping(store, "workflow", WORKFLOW, tree.dept, product_line="loans")
        found = await resolver.resolve(
            store, TENANT, hints(workflow_id=str(WORKFLOW), application="workflows"), now=1.0
        )
        assert (found.attribution_path, found.workflow_id, found.product_line) == (
            "workflow_mapping",
            WORKFLOW,
            "loans",
        )
        store.user_departments[str(USER)] = (DEPARTMENT, "dept-ops")
        other = uuid.uuid4()
        by_user = await resolver.resolve(
            store, TENANT, hints(workflow_id=str(other), application="workflows", initiating_user_id=str(USER)), now=1.0
        )
        assert (by_user.org_node_id, by_user.attribution_path) == (tree.dept.id, "department_code")

    @pytest.mark.asyncio
    async def test_service_call_uses_user_department_when_no_application_mapping(self, store, tree):
        store.user_departments[str(USER)] = (DEPARTMENT, None)
        mapping(store, "department", DEPARTMENT, tree.centre)
        found = await resolver.resolve(
            store, TENANT, hints(application="content", initiating_user_id=str(USER)), now=1.0
        )
        assert (found.org_node_id, found.attribution_path) == (tree.centre.id, "department_mapping")
        assert found.initiating_user_id == USER
        store.user_departments[str(USER)] = (DEPARTMENT, "NOT-A-NODE")
        store.rows = [r for r in store.rows if r.__tablename__ != "spend_source_mappings"]
        resolver.invalidate(TENANT)
        unknown = await resolver.resolve(
            store, TENANT, hints(application="content", initiating_user_id=str(USER)), now=1.0
        )
        assert unknown.unattributed_reason == "unknown_label"

    @pytest.mark.asyncio
    async def test_no_source_and_no_mapping(self, store, tree):
        system = await resolver.resolve(store, TENANT, hints(application="system"), now=1.0)
        assert system.unattributed_reason == "no_source" and system.use_case == "unattributed"
        api = await resolver.resolve(store, TENANT, hints(application="api", default_use_case="Completion"), now=1.0)
        assert api.unattributed_reason == "no_mapping" and api.use_case == "completion"
        odd = await resolver.resolve(store, TENANT, hints(application="not-an-app"), now=1.0)
        assert odd.application == "system"

    @pytest.mark.asyncio
    async def test_workflow_initiator_comes_from_the_run(self, store, tree, monkeypatch):
        from core.models.workflow import WorkflowRun

        run_id = uuid.uuid4()
        store.add(WorkflowRun(id=run_id, tenant_id=TENANT, context={"initiated_by_user_id": str(USER)}))
        found = await resolver.resolve(
            store, TENANT, hints(application="workflows", workflow_run_id=str(run_id)), now=1.0
        )
        assert found.initiating_user_id == USER
        none = await resolver.resolve(store, TENANT, hints(application="workflows", workflow_run_id="wfr_x"), now=1.0)
        assert none.initiating_user_id is None

    def test_resolver_joins_carry_the_tenant_predicate(self):
        for statement in (resolver._AGENT_SQL, resolver._USER_DEPARTMENT_SQL):
            sql = " ".join(str(statement.compile(dialect=postgresql.dialect())).split())
            assert sql.count("tenant_id = %(tid)s") >= 2
        agent_sql = " ".join(str(resolver._AGENT_SQL).split())
        assert (
            "cc.tenant_id = :tid" in agent_sql
            and "r.tenant_id = :tid" in agent_sql
            and "a.tenant_id = :tid" in agent_sql
        )
        assert "d.tenant_id = :tid" in str(resolver._USER_DEPARTMENT_SQL) and "u.tenant_id = :tid" in str(
            resolver._USER_DEPARTMENT_SQL
        )

    @pytest.mark.asyncio
    async def test_use_case_order_mapping_registry_agent_type_default(self, store, tree):
        agent_row(store, use_case="Customer Onboarding")
        found = await resolver.resolve(store, TENANT, hints(agent_id=str(AGENT), default_use_case="x"), now=1.0)
        assert found.use_case == "customer-onboarding"
        mapping(store, "application", "api", None, use_case="app-case", product_line="pl-app")
        resolver.invalidate(TENANT)
        found = await resolver.resolve(store, TENANT, hints(agent_id=str(AGENT)), now=1.0)
        assert found.use_case == "app-case" and found.product_line == "pl-app"
        mapping(store, "agent", AGENT, None, use_case="agent-case")
        resolver.invalidate(TENANT)
        found = await resolver.resolve(store, TENANT, hints(agent_id=str(AGENT)), now=1.0)
        assert found.use_case == "agent-case"

    @pytest.mark.asyncio
    async def test_business_unit_is_nearest_business_unit_ancestor(self, store, tree):
        team = node(store, "TEAM-A", "team", tree.dept)
        mapping(store, "application", "chat", team)
        found = await resolver.resolve(store, TENANT, hints(application="chat"), now=1.0)
        assert found.org_node_id == team.id and found.business_unit_node_id == tree.unit.id
        mapping(store, "application", "voice", tree.group)
        top = await resolver.resolve(store, TENANT, hints(application="voice"), now=1.0)
        assert top.org_node_id == tree.group.id and top.business_unit_node_id is None

    @pytest.mark.asyncio
    async def test_attribution_path_recorded_for_each_rule(self, store, tree):
        paths = set()
        agent_row(store)
        mapping(store, "agent", AGENT, tree.centre)
        paths.add((await resolver.resolve(store, TENANT, hints(agent_id=str(AGENT)), now=1.0)).attribution_path)
        mapping(store, "application", "chat", tree.unit)
        paths.add((await resolver.resolve(store, TENANT, hints(application="chat"), now=1.0)).attribution_path)
        mapping(store, "workflow", WORKFLOW, tree.dept)
        paths.add((await resolver.resolve(store, TENANT, hints(workflow_id=str(WORKFLOW)), now=1.0)).attribution_path)
        assert paths == {"agent_mapping", "application_mapping", "workflow_mapping"}

    @pytest.mark.asyncio
    async def test_resolver_error_records_resolver_failed_and_still_writes(self, store, tree):
        from core.spend import meter
        from tests.unit.test_spend_usage import event

        store.fail_on.add("FROM agents a")
        failed = await resolver.resolve(store, TENANT, hints(agent_id=str(AGENT), run_id="run_1"), now=1.0)
        assert failed.unattributed_reason == "resolver_failed" and failed.run_id == "run_1" and failed.agent_id is None
        resolver.invalidate(TENANT)
        result = await meter.write_events(store, TENANT, [event(hints=hints(agent_id=str(AGENT)))], now=T0)
        assert result.written == 1
        assert store.of("spend_usage_records")[0].unattributed_reason == "resolver_failed"

    @pytest.mark.asyncio
    async def test_resolution_cache_expires_and_is_invalidated_by_mapping_write(self, store, tree):
        first = await resolver.resolve(store, TENANT, hints(application="chat"), now=100.0)
        assert first.unattributed_reason == "no_mapping"
        mapping(store, "application", "chat", tree.unit)
        cached = await resolver.resolve(store, TENANT, hints(application="chat", run_id="r2"), now=110.0)
        assert cached.unattributed_reason == "no_mapping" and cached.run_id == "r2"
        expired = await resolver.resolve(store, TENANT, hints(application="chat"), now=200.0)
        assert expired.org_node_id == tree.unit.id
        resolver._RESOLUTION_CACHE.clear()
        await resolver.resolve(store, TENANT, hints(application="voice"), now=300.0)
        assert resolver._RESOLUTION_CACHE
        await mappings.put_mapping(
            TENANT, {"source_type": "application", "source_ref": "voice", "org_node_code": "BU-RETAIL"}, actor=ACTOR
        )
        assert not [k for k in resolver._RESOLUTION_CACHE if k[0] == str(TENANT)]
        await resolver.resolve(store, TENANT, hints(application="voice"), now=300.0)
        await org.create_node(
            TENANT, {"code": "TEAM-Z", "name": "Z", "kind": "team", "parent_code": "DEPT-OPS"}, actor=ACTOR
        )
        assert not resolver._RESOLUTION_CACHE

    def test_cache_is_bounded(self, monkeypatch):
        monkeypatch.setattr(resolver, "CACHE_MAX", 0)
        resolver._RESOLUTION_CACHE[("x",)] = (0.0, resolver.failed(hints()))
        assert resolver.as_uuid(" ") is None and resolver.as_uuid(AGENT) == AGENT and resolver.as_uuid(None) is None

    @pytest.mark.asyncio
    async def test_region_and_environment_come_from_the_deployment_and_tenant(self, store, tree, monkeypatch):
        from core.config import settings
        from core.models.governance_config import GovernanceConfig

        monkeypatch.setattr(settings, "env", "Production")
        found = await resolver.resolve(store, TENANT, hints(), now=1.0)
        assert found.environment == "production" and found.region == (settings.data_region or "").upper()[:8]
        store.add(GovernanceConfig(tenant_id=TENANT, data_region="eu"))
        resolver.invalidate(TENANT)
        assert (await resolver.resolve(store, TENANT, hints(), now=1.0)).region == "EU"
