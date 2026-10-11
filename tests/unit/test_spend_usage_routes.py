# SPDX-License-Identifier: Apache-2.0
"""Spend usage routes, jobs, partitions, the migration, the Celery tasks and the hooks into reference data."""

from __future__ import annotations

import asyncio
import importlib.util
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from api.deps import ActiveHumanAdmin
from api.v1 import spend as api
from core.config import settings
from core.models.spend_usage import SpendJob, SpendMeterGap, SpendUsageRecord, SpendUsageRollup
from core.ownership import Caller
from core.spend import fx, jobs, locks, meter, partitions, rates, rollups
from core.spend.errors import SpendError
from tests.unit.spend_usage_fakes import ACTOR, T0, TENANT, install
from tests.unit.test_spend_usage import card, event, hints

TID = str(TENANT)
DAY = date(2026, 10, 1)
ADMIN = ActiveHumanAdmin(user_id=uuid.UUID(ACTOR), tenant_id=TENANT, email="admin@example.com", role="admin")
ADMIN_CALLER = Caller(user_id=uuid.UUID(ACTOR), role="admin", domains=None, is_admin=True, is_machine=False)
MIGRATION = Path("migrations/versions/v6_z80_spend_usage.py")
MODELS = (SpendUsageRecord, SpendUsageRollup, SpendMeterGap, SpendJob)


@pytest.fixture
def store(monkeypatch):
    return install(monkeypatch)


def _usage_calls():
    """One direct call of every PR B route."""
    job = uuid.uuid4()
    return [
        api.list_usage(DAY, DAY, caller=ADMIN_CALLER, tenant_id=TID),
        api.list_rollups(DAY, DAY, caller=ADMIN_CALLER, tenant_id=TID),
        api.spend_coverage(DAY, DAY, caller=ADMIN_CALLER, tenant_id=TID),
        api.ledger_comparison(DAY, DAY, caller=ADMIN_CALLER, tenant_id=TID),
        api.list_gaps(DAY, DAY, caller=ADMIN_CALLER, tenant_id=TID),
        api.rebuild_rollups(api.RebuildIn(start=DAY, end=DAY), ADMIN, tenant_id=TID),
        api.backfill_usage(api.BackfillIn(start=DAY, end=DAY), ADMIN, tenant_id=TID),
        api.restate_usage(
            api.RestateIn(provider="openai", start=DAY, end=DAY, reason="a long enough reason"), ADMIN, tenant_id=TID
        ),
        api.reattribute_usage(api.ReattributeIn(start=DAY, end=DAY), ADMIN, tenant_id=TID),
        api.settle_fx_rates(api.SettleIn(start=DAY, end=DAY), ADMIN, tenant_id=TID),
        api.recompute_commitments(api.RecomputeIn(), ADMIN, tenant_id=TID),
        api.list_jobs(caller=ADMIN_CALLER, tenant_id=TID),
        api.get_job(job, caller=ADMIN_CALLER, tenant_id=TID),
    ]


class TestRoutes:
    @pytest.mark.asyncio
    async def test_usage_routes_not_found_while_off(self, monkeypatch):
        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
        calls = _usage_calls()
        assert len(calls) == 13
        for call in calls:
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404 and info.value.detail["error"] == "spend_disabled"
        out = await api.spend_status(tenant_id=TID)
        assert out["enabled"] is False and out["writer"] == {"started": False}

    @pytest.mark.asyncio
    async def test_usage_route_flow_while_on(self, store):
        store.add(card())
        await meter.write_events(store, TENANT, [event(), event(provider="mystery", model="m")], now=T0)
        listed = await api.list_usage(DAY, DAY, limit=1, caller=ADMIN_CALLER, tenant_id=TID)
        assert len(listed["items"]) == 1 and listed["next_cursor"]
        rest = await api.list_usage(DAY, DAY, cursor=listed["next_cursor"], caller=ADMIN_CALLER, tenant_id=TID)
        assert len(rest["items"]) == 1 and rest["next_cursor"] is None
        assert {listed["items"][0]["id"], rest["items"][0]["id"]} == {
            str(r.id) for r in store.of("spend_usage_records")
        }
        unpriced = await api.list_usage(DAY, DAY, unpriced=True, caller=ADMIN_CALLER, tenant_id=TID)
        assert [i["flags"] for i in unpriced["items"]] == [["unpriced"]]
        filtered = await api.list_usage(
            DAY, DAY, provider="openai", usage_type="llm_tokens", unattributed=True, caller=ADMIN_CALLER, tenant_id=TID
        )
        assert len(filtered["items"]) == 1 and filtered["items"][0]["price_source"] == "contract"
        assert (await api.list_usage(DAY, DAY, unattributed=False, caller=ADMIN_CALLER, tenant_id=TID))["items"] == []
        rolled = await api.list_rollups(
            DAY, DAY, group_by="provider", provider="openai", caller=ADMIN_CALLER, tenant_id=TID
        )
        assert [r["provider"] for r in rolled["rows"]] == ["openai"]
        coverage = await api.spend_coverage(DAY, DAY, caller=ADMIN_CALLER, tenant_id=TID)
        assert coverage["period"]["records"] == 2
        assert (await api.ledger_comparison(DAY, DAY, caller=ADMIN_CALLER, tenant_id=TID))["days"][0]["usage"][
            "calls"
        ] == 2
        await meter.upsert_gaps(store, TENANT, {(DAY, "llm_tokens", "queue_full", ""): 2})
        gaps = await api.list_gaps(DAY, DAY, caller=ADMIN_CALLER, tenant_id=TID)
        assert gaps["items"] == [
            {"day": "2026-10-01", "usage_type": "llm_tokens", "reason": "queue_full", "detail": "", "count": 2}
        ]
        with pytest.raises(HTTPException) as info:
            await api.list_usage(DAY, DAY + timedelta(days=31), caller=ADMIN_CALLER, tenant_id=TID)
        assert info.value.status_code == 422 and info.value.detail["error"] == "range_too_long"
        with pytest.raises(HTTPException) as info:
            await api.list_usage(DAY, DAY, cursor="nonsense", caller=ADMIN_CALLER, tenant_id=TID)
        assert info.value.status_code == 422
        status = await api.spend_status(tenant_id=TID)
        assert status["partition_horizon"] == {"last_month": "2028-12", "months_ahead": 26, "low": False}

    @pytest.mark.asyncio
    async def test_usage_reads_filter_personal_agents_and_redact_user_ids(self, store):
        from core.models.agent import Agent

        owner = uuid.uuid4()
        personal = uuid.uuid4()
        store.add(
            Agent(
                id=personal,
                tenant_id=TENANT,
                name="p",
                agent_type="t",
                domain="finance",
                visibility="personal",
                owner_user_id=owner,
            )
        )
        store.agents[str(personal)] = ("1", "t", "active", None, None, None, None, None)
        user = str(uuid.uuid4())
        await meter.write_events(
            store,
            TENANT,
            [
                event(hints=hints(agent_id=str(personal), initiating_user_id=user)),
                event(hints=hints(initiating_user_id=user)),
            ],
            now=T0,
        )
        admin = await api.list_usage(DAY, DAY, caller=ADMIN_CALLER, tenant_id=TID)
        assert len(admin["items"]) == 2 and {i["initiating_user_id"] for i in admin["items"]} == {user}
        domain_head = Caller(user_id=uuid.uuid4(), role="cfo", domains=["finance"], is_admin=False, is_machine=False)
        theirs = await api.list_usage(DAY, DAY, caller=domain_head, tenant_id=TID)
        assert len(theirs["items"]) == 1 and theirs["items"][0]["agent_id"] is None
        assert theirs["items"][0]["initiating_user_id"] is None  # user ids are for administrators and auditors
        owner_view = Caller(user_id=owner, role="cfo", domains=["finance"], is_admin=False, is_machine=False)
        assert len((await api.list_usage(DAY, DAY, caller=owner_view, tenant_id=TID))["items"]) == 2

    @pytest.mark.asyncio
    async def test_usage_reads_show_contract_terms_to_commercial_readers_only(self, store):
        store.add(card())
        await meter.write_events(store, TENANT, [event()], now=T0)
        record = store.of("spend_usage_records")[0]
        record.commitment_id, record.overage, record.overage_quantity = uuid.uuid4(), True, Decimal("5")
        admin = (await api.list_usage(DAY, DAY, caller=ADMIN_CALLER, tenant_id=TID))["items"][0]
        assert admin["rate_card_id"] == str(record.rate_card_id) and admin["unit_price"] == "2.5"
        assert admin["commitment_id"] == str(record.commitment_id) and admin["overage_quantity"] == "5"
        assert "overage" in admin["flags"]
        for reader in (
            Caller(user_id=uuid.uuid4(), role="cfo", domains=["finance"], is_admin=False, is_machine=False),
            Caller(user_id=None, role="agent", domains=None, is_admin=False, is_machine=True),
        ):
            item = (await api.list_usage(DAY, DAY, caller=reader, tenant_id=TID))["items"][0]
            assert item["rate_card_id"] is None and item["unit_price"] is None
            assert item["commitment_id"] is None and item["overage_quantity"] is None
            assert "overage" not in item["flags"] and item["amount"] == admin["amount"]
            for group in ("rate_card_id", "commitment_id"):
                with pytest.raises(HTTPException) as info:
                    await api.list_rollups(DAY, DAY, group_by=group, caller=reader, tenant_id=TID)
                assert info.value.status_code == 403 and info.value.detail["error"] == "commercial_read_refused"
            rows = (await api.list_rollups(DAY, DAY, group_by="provider", caller=reader, tenant_id=TID))["rows"]
            assert rows[0]["provider"] == "openai"
        by_card = await api.list_rollups(DAY, DAY, group_by="rate_card_id", caller=ADMIN_CALLER, tenant_id=TID)
        assert by_card["rows"][0]["rate_card_id"] == str(record.rate_card_id)

    @pytest.mark.asyncio
    async def test_job_routes_answer_202_with_a_job_id(self, store):
        rebuild = await api.rebuild_rollups(api.RebuildIn(start=DAY, end=DAY), ADMIN, tenant_id=TID)
        assert rebuild["status"] == "queued" and uuid.UUID(rebuild["job_id"])
        with pytest.raises(HTTPException) as info:
            await api.rebuild_rollups(api.RebuildIn(start=DAY, end=DAY), ADMIN, tenant_id=TID)
        assert info.value.status_code == 409 and info.value.detail["error"] == "job_running"
        with pytest.raises(HTTPException) as info:
            await api.rebuild_rollups(api.RebuildIn(start=DAY, end=DAY + timedelta(days=40)), ADMIN, tenant_id=TID)
        assert info.value.status_code == 422
        restate = await api.restate_usage(
            api.RestateIn(provider="GPT", start=DAY, end=DAY, card_ids=[uuid.uuid4()], reason="A corrected contract"),
            ADMIN,
            tenant_id=TID,
        )
        job = next(r for r in store.of("spend_jobs") if str(r.id) == restate["job_id"])
        assert job.params["provider"] == "openai" and job.params["include_unpriced"] is True
        for call in (
            api.backfill_usage(api.BackfillIn(start=DAY, end=DAY), ADMIN, tenant_id=TID),
            api.reattribute_usage(api.ReattributeIn(start=DAY, end=DAY), ADMIN, tenant_id=TID),
            api.settle_fx_rates(api.SettleIn(start=DAY, end=DAY), ADMIN, tenant_id=TID),
            api.recompute_commitments(api.RecomputeIn(provider="openai"), ADMIN, tenant_id=TID),
        ):
            assert (await call)["status"] == "queued"
        listed = await api.list_jobs(caller=ADMIN_CALLER, tenant_id=TID)
        assert len(listed["items"]) == 6
        rebuilds = await api.list_jobs(kind="rebuild", caller=ADMIN_CALLER, tenant_id=TID)
        assert rebuilds["items"][0]["kind"] == "rebuild"
        one = await api.get_job(uuid.UUID(rebuild["job_id"]), caller=ADMIN_CALLER, tenant_id=TID)
        assert one["params"] == {"start": "2026-10-01", "end": "2026-10-01"} and one["requested_by"] == ACTOR
        with pytest.raises(HTTPException) as info:
            await api.get_job(uuid.uuid4(), caller=ADMIN_CALLER, tenant_id=TID)
        assert info.value.status_code == 404
        audits = [r.event_type for r in store.of("audit_log")]
        assert audits.count("spend.job.enqueue") == 6

    def test_routes_are_registered_with_their_scopes(self):
        from api.main import app
        from api.route_metadata import ROUTE_METADATA_ATTR

        paths = set(app.openapi()["paths"])
        for path in (
            "/api/v1/spend/usage",
            "/api/v1/spend/rollups",
            "/api/v1/spend/coverage",
            "/api/v1/spend/coverage/ledgers",
            "/api/v1/spend/gaps",
            "/api/v1/spend/rollups/rebuild",
            "/api/v1/spend/usage/backfill",
            "/api/v1/spend/usage/restate",
            "/api/v1/spend/usage/reattribute",
            "/api/v1/spend/fx-rates/settle",
            "/api/v1/spend/commitments/recompute",
            "/api/v1/spend/jobs",
            "/api/v1/spend/jobs/{job_id}",
        ):
            assert path in paths, path
        scopes = {r.path: getattr(r.endpoint, ROUTE_METADATA_ATTR)["scope"] for r in api.router.routes}
        assert scopes["/spend/usage"] == "spend.usage.read" and scopes["/spend/jobs"] == "spend.jobs.read"
        assert scopes["/spend/rollups/rebuild"] == "spend.rollups.sensitive.write"
        assert scopes["/spend/fx-rates/settle"] == "spend.fx.sensitive.write"

    def test_bodies_are_bounded(self):
        from pydantic import ValidationError

        for bad in (
            {"provider": "", "start": DAY, "end": DAY, "reason": "x" * 20},
            {"provider": "p", "start": DAY, "end": DAY, "reason": "short"},
            {"provider": "p", "start": DAY, "end": DAY, "reason": "x" * 20, "card_ids": [str(uuid.uuid4())] * 51},
            {"provider": "p", "start": DAY, "end": DAY, "reason": "x" * 20, "extra": 1},
        ):
            with pytest.raises(ValidationError):
                api.RestateIn(**bad)
        with pytest.raises(ValidationError):
            api.RebuildIn(start=DAY, end=DAY, extra=1)


# ---------------------------------------------------------------- who reads which usage figures

OWNER = uuid.UUID("44444444-4444-4444-8444-444444444444")
# Each record's quantity is 1,000 times a distinct power of two, so a total names exactly the records it summed.
NO_AGENT, SHARED_FINANCE, SHARED_HR, PERSONAL = 1000, 2000, 4000, 8000
READERS = {
    # A tenant administrator: every record.
    "admin": Caller(user_id=uuid.UUID(ACTOR), role="admin", domains=None, is_admin=True, is_machine=False),
    # A domain-scoped reader: shared agents of finance, and records with no agent.
    "domain": Caller(user_id=uuid.uuid4(), role="cfo", domains=["finance"], is_admin=False, is_machine=False),
    # An auditor (unrestricted domains), who does not own the personal agent: every record, as on GET /audit.
    "auditor": Caller(user_id=uuid.uuid4(), role="auditor", domains=None, is_admin=False, is_machine=False),
    # The personal agent's owner, a domain-scoped reader too.
    "owner": Caller(user_id=OWNER, role="cfo", domains=["finance"], is_admin=False, is_machine=False),
    # A machine credential with audit:read: shared agents only.
    "machine": Caller(user_id=None, role="", domains=None, is_admin=False, is_machine=True),
}
VISIBLE = {
    "admin": NO_AGENT + SHARED_FINANCE + SHARED_HR + PERSONAL,
    "domain": NO_AGENT + SHARED_FINANCE,
    "auditor": NO_AGENT + SHARED_FINANCE + SHARED_HR + PERSONAL,
    "owner": NO_AGENT + SHARED_FINANCE + PERSONAL,
    "machine": NO_AGENT + SHARED_FINANCE + SHARED_HR,
}
TENANT_WIDE = {"admin", "auditor"}  # unrestricted domains: an administrator or an auditor


async def agents_usage(store) -> dict[str, uuid.UUID]:
    """Four records: no agent, a shared finance agent, a shared HR agent and a personal agent; each agent with an
    agent ledger row of a tenth of its tokens."""
    from core.models.agent import Agent, AgentCostLedger

    made = {}
    for name, domain, visibility, owner in (
        ("shared_finance", "finance", "tenant", None),
        ("shared_hr", "hr", "tenant", None),
        ("personal", "finance", "personal", OWNER),
    ):
        agent_id = uuid.uuid4()
        store.add(
            Agent(id=agent_id, tenant_id=TENANT, name=name, agent_type="t", domain=domain, visibility=visibility,
                  owner_user_id=owner)
        )  # fmt: skip
        store.agents[str(agent_id)] = ("1", "t", "active", None, None, None, None, None)
        made[name] = agent_id
    store.add(card())
    quantities = {None: NO_AGENT, "shared_finance": SHARED_FINANCE, "shared_hr": SHARED_HR, "personal": PERSONAL}
    events = [
        event(quantity=Decimal(q), hints=hints(agent_id=str(made[n]) if n else None)) for n, q in quantities.items()
    ]
    await meter.write_events(store, TENANT, events, now=T0)
    for name, agent_id in made.items():
        store.add(
            AgentCostLedger(id=uuid.uuid4(), tenant_id=TENANT, agent_id=agent_id, period_date=DAY,
                            token_count=quantities[name] // 10, cost_usd=Decimal("0.1"), task_count=1)
        )  # fmt: skip
    await meter.upsert_gaps(store, TENANT, {(DAY, "llm_tokens", "queue_full", ""): 2})
    await jobs.enqueue(TENANT, kind="rebuild", params={"start": DAY, "end": DAY}, actor=ACTOR)
    return made


def _refused_tenant_wide(info) -> bool:
    return info.value.status_code == 403 and info.value.detail["error"] == "tenant_wide_read_refused"


def _sql(clause, *, literal: bool = False) -> str:
    kwargs = {"compile_kwargs": {"literal_binds": True}} if literal else {}
    return " ".join(str(clause.compile(dialect=postgresql.dialect(), **kwargs)).split())


def _subquery(sql: str) -> str:
    """The agent visibility subquery of a compiled statement: the parenthesised text from ``(SELECT agents.id``."""
    start = sql.index("(SELECT agents.id")
    depth = 0
    for index in range(start, len(sql)):
        depth += {"(": 1, ")": -1}.get(sql[index], 0)
        if depth == 0:
            return sql[start : index + 1]
    raise AssertionError(sql)


class TestReadVisibility:
    """The finding: the agent visibility clause applied only to rollups grouped by agent, so a domain-scoped
    reader or a non-owner grouping by use case, provider or node got the usage of agents /spend/usage hides."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reader", sorted(READERS))
    async def test_usage_lists_the_records_the_reader_may_see(self, store, reader):
        await agents_usage(store)
        items = (await api.list_usage(DAY, DAY, caller=READERS[reader], tenant_id=TID))["items"]
        assert sum(Decimal(i["quantity"]) for i in items) == VISIBLE[reader]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reader", sorted(READERS))
    async def test_every_rollup_grouping_applies_the_readers_agent_visibility(self, store, reader):
        made = await agents_usage(store)
        caller = READERS[reader]
        groupings = [g for g in rollups.GROUP_BYS if g not in rollups.COMMERCIAL_GROUP_BYS]
        assert {"use_case", "provider", "org_node_id", "day", "agent_id"} <= set(groupings)
        for group_by in groupings:
            out = await api.list_rollups(DAY, DAY, group_by=group_by, caller=caller, tenant_id=TID)
            assert Decimal(out["totals"]["quantity"]) == VISIBLE[reader], group_by
            assert sum(Decimal(r["quantity"]) for r in out["rows"]) == VISIBLE[reader], group_by
        by_agent = await api.list_rollups(DAY, DAY, group_by="agent_id", caller=caller, tenant_id=TID)
        shown = {r["agent_id"] for r in by_agent["rows"]}
        assert None in shown  # records with no agent stay visible to every reader
        assert (str(made["personal"]) in shown) is (reader in ("admin", "auditor", "owner"))
        assert (str(made["shared_hr"]) in shown) is (reader in ("admin", "auditor", "machine"))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reader", sorted(READERS))
    async def test_the_ledger_comparison_filters_every_source_by_agent_visibility(self, store, reader):
        await agents_usage(store)
        day = (await api.ledger_comparison(DAY, DAY, caller=READERS[reader], tenant_id=TID))["days"][0]
        assert sum(Decimal(v) for v in day["usage"]["tokens"].values()) == VISIBLE[reader]
        assert day["agent_cost_ledger"]["tokens"] == (VISIBLE[reader] - NO_AGENT) // 10

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reader", sorted(READERS))
    async def test_coverage_is_for_tenant_wide_readers_only(self, store, reader):
        await agents_usage(store)
        if reader not in TENANT_WIDE:
            with pytest.raises(HTTPException) as info:
                await api.spend_coverage(DAY, DAY, caller=READERS[reader], tenant_id=TID)
            assert _refused_tenant_wide(info)
            return
        out = await api.spend_coverage(DAY, DAY, caller=READERS[reader], tenant_id=TID)
        assert out["period"]["records"] == 4 and out["period"]["gaps"] == {"queue_full": 2}  # the whole tenant

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reader", sorted(READERS))
    async def test_gaps_are_for_tenant_wide_readers_only(self, store, reader):
        await agents_usage(store)
        if reader not in TENANT_WIDE:
            with pytest.raises(HTTPException) as info:
                await api.list_gaps(DAY, DAY, caller=READERS[reader], tenant_id=TID)
            assert _refused_tenant_wide(info)
            return
        items = (await api.list_gaps(DAY, DAY, caller=READERS[reader], tenant_id=TID))["items"]
        assert [i["count"] for i in items] == [2]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reader", sorted(READERS))
    async def test_jobs_are_for_tenant_wide_readers_only(self, store, reader):
        await agents_usage(store)
        queued = store.of("spend_jobs")[0]
        caller = READERS[reader]
        if reader not in TENANT_WIDE:
            for call in (
                api.list_jobs(caller=caller, tenant_id=TID),
                api.get_job(queued.id, caller=caller, tenant_id=TID),
                api.get_job(uuid.uuid4(), caller=caller, tenant_id=TID),  # refused before the lookup: no 404
            ):
                with pytest.raises(HTTPException) as info:
                    await call
                assert _refused_tenant_wide(info)
            return
        assert [j["id"] for j in (await api.list_jobs(caller=caller, tenant_id=TID))["items"]] == [str(queued.id)]
        assert (await api.get_job(queued.id, caller=caller, tenant_id=TID))["kind"] == "rebuild"

    def test_an_administrator_credential_is_a_tenant_wide_reader_and_a_domain_role_is_not(self):
        from core.spend import access

        machine_admin = Caller(user_id=None, role="", domains=None, is_admin=True, is_machine=True)
        assert access.is_tenant_wide_reader(machine_admin)  # it reads every record unfiltered anyway
        assert access.read_view(machine_admin).agent_clause is None
        assert sorted(name for name, caller in READERS.items() if access.is_tenant_wide_reader(caller)) == sorted(
            TENANT_WIDE
        )
        with pytest.raises(SpendError) as info:
            access.require_tenant_wide(READERS["domain"])
        assert info.value.status == 403 and info.value.code == "tenant_wide_read_refused"

    def test_unfiltered_readers_and_tenant_wide_readers_are_one_set(self):
        """The finding: an auditor read coverage, gaps and jobs (every agent's usage) but had the personal agent
        filtered out of the records and rollups, so two views of one tenant disagreed. One rule now: a reader of
        tenant-wide figures reads every record; anyone filtered by agent is refused tenant-wide figures."""
        from core.spend import access

        callers = [
            *READERS.values(),
            Caller(user_id=None, role="", domains=None, is_admin=True, is_machine=True),
            Caller(user_id=uuid.uuid4(), role="analyst", domains=[], is_admin=False, is_machine=False),
            Caller(user_id=None, role="auditor", domains=None, is_admin=False, is_machine=False),  # no user id
        ]
        for caller in callers:
            assert (access.read_view(caller).agent_clause is None) is access.is_tenant_wide_reader(caller), caller
        auditor = access.read_view(READERS["auditor"])
        assert auditor.show_user_ids and auditor.commercial  # unchanged: user ids and contract terms
        machine_admin = access.read_view(callers[len(READERS)])
        assert not machine_admin.show_user_ids and not machine_admin.commercial

    @pytest.mark.asyncio
    async def test_the_auditor_reads_the_personal_agent_everywhere_and_a_domain_reader_nowhere(self, store):
        made = await agents_usage(store)
        personal = str(made["personal"])
        for reader, sees in (("auditor", True), ("domain", False)):
            caller = READERS[reader]
            items = (await api.list_usage(DAY, DAY, caller=caller, tenant_id=TID, agent_id=made["personal"]))["items"]
            assert (len(items) == 1) is sees
            rows = await api.list_rollups(DAY, DAY, group_by="agent_id", caller=caller, tenant_id=TID)
            assert (personal in {r["agent_id"] for r in rows["rows"]}) is sees
            day = (await api.ledger_comparison(DAY, DAY, caller=caller, tenant_id=TID))["days"][0]
            assert (day["agent_cost_ledger"]["tokens"] >= PERSONAL // 10) is sees
            for figure in (
                api.spend_coverage(DAY, DAY, caller=caller, tenant_id=TID),
                api.list_gaps(DAY, DAY, caller=caller, tenant_id=TID),
                api.list_jobs(caller=caller, tenant_id=TID),
            ):
                if sees:
                    assert await figure
                    continue
                with pytest.raises(HTTPException) as info:
                    await figure
                assert _refused_tenant_wide(info)

    @pytest.mark.asyncio
    async def test_status_never_gives_the_process_wide_pending_count(self, store, monkeypatch):
        """The pending count is every tenant's queued events in the process, so no tenant's reader gets it."""
        import sys
        from types import SimpleNamespace

        def refuse() -> int:
            raise AssertionError("the status route must not read the process-wide pending count")

        fake = SimpleNamespace(started=lambda: True, pending=refuse)
        monkeypatch.setitem(sys.modules, "core.spend.writer", fake)
        assert (await api.spend_status(tenant_id=TID))["writer"] == {"started": True}
        monkeypatch.delitem(sys.modules, "core.spend.writer")
        assert (await api.spend_status(tenant_id=TID))["writer"] == {"started": False}

    @pytest.mark.asyncio
    async def test_usage_filters_bind_the_tenant_and_never_correlate_on_the_outer_table(self, store):
        """The finding: the visibility subquery compared agents.tenant_id with the outer table's tenant_id, so
        the database re-ran it for every rollup row (43.6 s on 219,600 rows against 0.165 s with the tenant
        bound)."""
        from sqlalchemy.sql.selectable import Select

        from core.models.agent import Agent
        from core.spend import access

        await agents_usage(store)
        view = access.read_view(READERS["domain"])
        for model in (SpendUsageRollup, SpendUsageRecord):
            sql = _sql(access.usage_filter(view, model.__table__, Agent.__table__, tenant_id=TENANT))
            subquery = _subquery(sql)
            assert "FROM agents" in subquery and model.__tablename__ not in subquery, model
            assert "agents.tenant_id = %(tenant_id_1)s::UUID" in subquery
        store.statements.clear()
        await api.list_rollups(DAY, DAY, group_by="use_case", caller=READERS["domain"], tenant_id=TID)
        await api.list_usage(DAY, DAY, caller=READERS["domain"], tenant_id=TID)
        selects = [s for s in store.statements if isinstance(s, Select)]
        tables = [s.get_final_froms()[0].name for s in selects]
        assert tables == ["spend_usage_rollups", "spend_usage_records"]
        for select_statement, table in zip(selects, tables, strict=True):
            subquery = _subquery(_sql(select_statement))
            assert table not in subquery and "agents.tenant_id = " in subquery, table


# ---------------------------------------------------------------- job audit rows in GET /audit

JOB_AUDIT_PREFIXES = ("spend.job.", "spend.usage.", "spend.fx.", "spend.rollups.")


def _audit_request(role, *, user_id=True, auth_mode="legacy", scopes=None, domains=None):
    from types import SimpleNamespace

    claims = {"sub": f"{role}@example.com", "role": role}
    if user_id:
        claims["agenticorg:user_id"] = str(uuid.uuid4())
    if domains is not None:
        claims["agenticorg:domains"] = domains
    scopes = scopes or (["agenticorg:admin"] if role == "admin" else ["agents:read", "audit:read"])
    return SimpleNamespace(state=SimpleNamespace(claims=claims, scopes=scopes, auth_mode=auth_mode))


class _AuditSession:
    """The audit route's session: the spend-rows check answers ``kept``; the count and page answer ``entries``."""

    def __init__(self, *, kept: bool, entries=()):
        self.statements: list = []
        self.kept = kept
        self.entries = list(entries)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, statement):
        from types import SimpleNamespace

        self.statements.append(statement)
        if "audit_log" not in _sql(statement, literal=True):
            return SimpleNamespace(scalar=lambda: self.kept)
        entries = self.entries
        return SimpleNamespace(scalar=lambda: len(entries), scalars=lambda: SimpleNamespace(all=lambda: entries))


class TestJobAuditRows:
    """The finding: spend job audit rows carry no agent and only rate-card and commitment rows were hidden, so
    any audit:read holder read through GET /audit a job's parameters (reasons, card ids, ranges), restated and
    re-attributed amounts per billing date and card, settled INR totals per currency, and rebuild and backfill
    counts."""

    @pytest.mark.asyncio
    async def test_every_audit_row_a_job_writes_is_hidden_from_readers_refused_commercial_reads(self, store):
        from sqlalchemy import Column, MetaData, String, Table, create_engine, insert, select

        from core.spend import access

        store.add(card(effective_to=date(2026, 12, 1)))
        await meter.write_events(store, TENANT, [event()], now=T0)
        span = {"start": "2026-10-01", "end": "2026-10-01"}
        restate = {**span, "provider": "openai", "card_ids": [], "include_unpriced": False, "reason": "A new contract"}
        for kind, params in (
            ("rebuild", span),
            ("backfill", span),
            ("reattribute", span),
            ("settle_fx", {**span, "force_dates": []}),
            ("restate", restate),
        ):
            queued = await jobs.enqueue(TENANT, kind=kind, params=params, actor=ACTOR)
            assert (await jobs.run(TENANT, uuid.UUID(queued["job_id"]), now=T0))["status"] == "succeeded", kind
        await jobs.queue_followup(store, TENANT, kind="settle_fx", params={**span, "force_dates": []}, actor=ACTOR)
        await jobs.queue_followup(
            store, TENANT, kind="settle_fx", params={**span, "force_dates": [["USD", "2026-10-01"]]}, actor=ACTOR
        )
        written = {row.event_type for row in store.of("audit_log")}
        assert written == {
            "spend.job.enqueue", "spend.job.merge", "spend.rollups.rebuild", "spend.usage.backfill",
            "spend.usage.reattribute", "spend.fx.settle", "spend.usage.restate",
        }  # fmt: skip
        assert all(access.is_commercial_audit_event(event_type) for event_type in written)
        # Every action core/spend writes from a job, chunks included; FX reference data stays visible.
        hidden = sorted(
            written | {"spend.usage.restate.chunk", "spend.usage.reattribute.chunk", "spend.fx.settle.chunk"}
        )
        kept = ["spend.fx_rates.create", "spend.fx_rates.import", "spend.org_node.create", "spend.mappings.import",
                "spend.jobXenqueue", "agent.run"]  # fmt: skip
        rows = Table("audit_rows", MetaData(), Column("event_type", String(100)))
        engine = create_engine("sqlite://")
        rows.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(insert(rows), [{"event_type": name} for name in kept + hidden])
        for reader in ("domain", "owner", "machine"):
            clause = access.commercial_audit_clause(READERS[reader], rows.c.event_type)
            with engine.begin() as connection:
                seen = [r[0] for r in connection.execute(select(rows.c.event_type).where(clause))]
            assert sorted(seen) == sorted(kept), reader
            assert not [e for e in kept if access.is_commercial_audit_event(e)]
        for reader in ("admin", "auditor"):
            assert access.commercial_audit_clause(READERS[reader], rows.c.event_type) is None
        assert set(JOB_AUDIT_PREFIXES) <= set(access.COMMERCIAL_AUDIT_PREFIXES)  # every job prefix is hidden

    @pytest.mark.asyncio
    async def test_general_audit_read_hides_job_rows_from_a_domain_reader_and_a_machine_credential(self, monkeypatch):
        from types import SimpleNamespace

        from api.v1 import audit as audit_api

        job_rows = [
            SimpleNamespace(event_type=name)
            for name in ("spend.job.enqueue", "spend.job.merge", "spend.usage.restate", "spend.usage.reattribute.chunk",
                         "spend.fx.settle", "spend.rollups.rebuild", "spend.usage.backfill")
        ]  # fmt: skip
        others = [SimpleNamespace(event_type="spend.fx_rates.create"), SimpleNamespace(event_type="agent.run.resumed")]
        monkeypatch.setattr(audit_api, "_audit_to_dict", lambda entry: {"event_type": entry.event_type})
        assert settings.spend_intelligence_enabled is False  # the filter does not depend on the flag
        refused = [
            (_audit_request("cfo", domains=["finance"]), "cfo"),
            (_audit_request("", user_id=False, auth_mode="grantex", scopes=["audit:read"]), ""),
            (_audit_request("admin", user_id=False, auth_mode="api_key"), "admin"),
        ]
        for req, role in refused:
            session = _AuditSession(kept=True, entries=job_rows + others)
            monkeypatch.setattr(audit_api, "get_tenant_session", lambda tenant_id, s=session: s)
            out = await audit_api.query_audit(request=req, tenant_id=TID, user_role=role)
            assert [item["event_type"] for item in out.items] == ["spend.fx_rates.create", "agent.run.resumed"], role
            kept_sql = _sql(session.statements[0], literal=True)
            assert "audit_log" not in kept_sql and f"tenant_id = '{TID}'" in kept_sql
            assert [kept_sql.count(f"FROM {t}") for t in ("spend_rate_cards", "spend_commitments", "spend_jobs")] == [
                1, 1, 1,
            ]  # fmt: skip
            for statement in session.statements[1:]:  # the count and the page
                sql = _sql(statement, literal=True)
                for prefix in JOB_AUDIT_PREFIXES:
                    assert f"NOT LIKE '{prefix}'" in sql, (role, prefix)
                assert "NOT LIKE 'spend.fx/_rates.'" not in sql
        for req, role in (
            (_audit_request("admin"), "admin"),
            (_audit_request("auditor", scopes=["audit:read"]), "auditor"),
        ):
            session = _AuditSession(kept=True, entries=job_rows + others)
            monkeypatch.setattr(audit_api, "get_tenant_session", lambda tenant_id, s=session: s)
            out = await audit_api.query_audit(request=req, tenant_id=TID, user_role=role)
            assert len(out.items) == len(job_rows) + len(others) and len(session.statements) == 2, role
        # A tenant with no rate card, commitment or spend job runs the audit query it ran before.
        before = _AuditSession(kept=False, entries=others)
        monkeypatch.setattr(audit_api, "get_tenant_session", lambda tenant_id, s=before: s)
        out = await audit_api.query_audit(request=refused[0][0], tenant_id=TID, user_role="cfo")
        assert len(before.statements) == 3 and len(out.items) == 2
        assert all("NOT LIKE" not in _sql(s, literal=True) for s in before.statements[1:])


# ---------------------------------------------------------------- jobs


class TestJobs:
    @pytest.mark.asyncio
    async def test_job_enqueue_refuses_a_second_active_job_of_the_kind(self, store):
        first = await jobs.enqueue(TENANT, kind="rebuild", params={"start": DAY, "end": DAY}, actor=ACTOR)
        with pytest.raises(SpendError) as info:
            await jobs.enqueue(TENANT, kind="rebuild", params={}, actor=ACTOR)
        assert info.value.code == "job_running" and first["job_id"] in info.value.message
        other = await jobs.enqueue(TENANT, kind="reattribute", params={}, actor="")
        assert other["status"] == "queued"
        assert next(r for r in store.of("spend_jobs") if str(r.id) == other["job_id"]).requested_by == jobs.SYSTEM_ACTOR
        # A follow-up is never folded into a job that does not cover it: other parameters, its own job.
        followup = await jobs.enqueue_followup(TENANT, kind="rebuild", params={}, actor=ACTOR)
        assert followup["job_id"] != first["job_id"] and followup["merged"] is False
        assert followup["status"] == "queued"
        same = await jobs.enqueue_followup(TENANT, kind="rebuild", params={}, actor=ACTOR)
        assert same == {"job_id": followup["job_id"], "status": "queued", "kind": "rebuild", "merged": True}
        assert await jobs.enqueue_followup(TENANT, kind="nonsense", params={}, actor=ACTOR) is None
        assert locks.job_kind(TENANT, "rebuild") in store.locks

    @pytest.mark.asyncio
    async def test_dispatch_failure_fails_the_job_so_the_kind_is_not_blocked(self, store, monkeypatch):
        def broken(tenant_id, job_id):
            raise ConnectionError("broker down")

        monkeypatch.setattr(jobs, "_dispatch", broken)
        out = await jobs.enqueue(TENANT, kind="rebuild", params={}, actor=ACTOR)
        assert out["status"] == "failed"
        row = store.of("spend_jobs")[0]
        assert row.status == "failed" and row.error_code == "dispatch_failed"
        monkeypatch.setattr(jobs, "_dispatch", lambda tenant_id, job_id: None)
        assert (await jobs.enqueue(TENANT, kind="rebuild", params={}, actor=ACTOR))["status"] == "queued"

    @pytest.mark.asyncio
    async def test_job_claim_takes_over_a_stale_running_job(self, store):
        queued = await jobs.enqueue(
            TENANT, kind="rebuild", params={"start": "2026-10-01", "end": "2026-10-01"}, actor=ACTOR
        )
        job_id = uuid.UUID(queued["job_id"])
        row = store.of("spend_jobs")[0]
        row.status, row.started_at = "running", datetime(2026, 10, 1, 9, 0, tzinfo=UTC)  # started three hours ago
        row.heartbeat_at = datetime(2026, 10, 1, 11, 55, tzinfo=UTC)  # but its heartbeat is five minutes old
        assert (await jobs.run(TENANT, job_id))["skipped"] == "not_claimable"
        row.heartbeat_at = datetime(2026, 10, 1, 11, 40, tzinfo=UTC)  # silent for twenty minutes: the worker was lost
        done = await jobs.run(TENANT, job_id, now=T0)
        assert done["status"] == "succeeded" and row.status == "succeeded" and row.result["days"] == 1
        assert (await jobs.run(TENANT, job_id))["skipped"] == "not_claimable"

    @pytest.mark.asyncio
    async def test_job_failure_stores_error_code_only(self, store, monkeypatch):
        from core.spend import maintenance

        async def broken(*args, **kwargs):
            raise RuntimeError("secret detail that must not be stored")

        monkeypatch.setattr(maintenance, "reattribute", broken)
        queued = await jobs.enqueue(
            TENANT, kind="reattribute", params={"start": "2026-10-01", "end": "2026-10-01"}, actor=ACTOR
        )
        out = await jobs.run(TENANT, uuid.UUID(queued["job_id"]))
        row = store.of("spend_jobs")[0]
        assert out["status"] == "failed" and row.error_code == "RuntimeError" and "secret" not in str(row.result)

    @pytest.mark.asyncio
    async def test_each_kind_dispatches_to_its_job(self, store, monkeypatch):
        from core.spend import commitments, ledgers, maintenance

        seen: list[str] = []

        def recorder(name):
            async def run(*args, **kwargs):
                seen.append(name)
                return {"ok": name}

            return run

        monkeypatch.setattr(ledgers, "backfill_model_calls", recorder("backfill"))
        monkeypatch.setattr(maintenance, "restate", recorder("restate"))
        monkeypatch.setattr(maintenance, "settle_fx", recorder("settle_fx"))
        monkeypatch.setattr(commitments, "recompute", recorder("recompute_commitments"))
        span = {"start": "2026-10-01", "end": "2026-10-01"}
        await jobs._execute(TENANT, "backfill", span, ACTOR, T0)
        await jobs._execute(
            TENANT, "restate", {**span, "provider": "openai", "card_ids": [str(uuid.uuid4())]}, ACTOR, T0
        )
        await jobs._execute(TENANT, "settle_fx", {**span, "force_dates": [["USD", "2026-10-01"]]}, ACTOR, T0)
        await jobs._execute(TENANT, "recompute_commitments", {}, ACTOR, T0)
        assert seen == ["backfill", "restate", "settle_fx", "recompute_commitments"]
        with pytest.raises(SpendError):
            await jobs._execute(TENANT, "nonsense", {}, ACTOR, T0)


# ---------------------------------------------------------------- partitions and the migration


def _migration():
    spec = importlib.util.spec_from_file_location("_v6_z80_spend_usage", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Op:
    def __init__(self):
        self.sql: list[str] = []

    def execute(self, sql):
        self.sql.append(" ".join(str(sql).split()))


def _strip_ws(text: str) -> str:
    return re.sub(r"\s+", "", text)


class TestPartitionsAndMigration:
    def test_static_partitions_cover_thirty_months_and_have_policies(self, monkeypatch):
        assert len(partitions.STATIC_MONTHS) == 30 and partitions.STATIC_MONTHS[0] == (2026, 7)
        assert partitions.STATIC_MONTHS[-1] == (2028, 12) and len(partitions.STATIC_PARTITIONS) == 31
        assert partitions.partition_name(2026, 7) == "spend_usage_records_y2026m07"
        assert "TO ('2027-01-01 00:00:00+00')" in partitions.partition_ddl(2026, 12)[0]
        migration = _migration()
        op = _Op()
        monkeypatch.setattr(migration, "op", op)
        migration.upgrade()
        created = [s for s in op.sql if "PARTITION OF spend_usage_records" in s]
        assert len(created) == 31 and created[0] == partitions.partition_ddl(2026, 7)[0]
        assert (
            created[-1]
            == "CREATE TABLE IF NOT EXISTS spend_usage_records_default PARTITION OF spend_usage_records DEFAULT;"
        )
        for name in (*partitions.STATIC_PARTITIONS, *migration.TABLES):
            assert f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY;" in op.sql, name
            assert any(s.startswith(f"CREATE POLICY {name}_tenant_isolation") for s in op.sql), name
        assert "PARTITION BY RANGE (event_time)" in " ".join(op.sql)
        assert not any(s.startswith("ALTER TABLE") and "ROW LEVEL" not in s for s in op.sql)

    @pytest.mark.asyncio
    async def test_partition_horizon_reports_months_ahead(self):
        assert await partitions.horizon(datetime(2026, 10, 1, tzinfo=UTC)) == {
            "last_month": "2028-12", "months_ahead": 26, "low": False,
        }  # fmt: skip
        assert (await partitions.horizon(datetime(2028, 8, 1, tzinfo=UTC)))["low"] is True
        assert partitions.months_ahead(datetime(2029, 3, 1, tzinfo=UTC)) == 0
        assert partitions.months_ahead() >= 0

    def test_migration_v6z80_chain_partitioning_rls_and_fk_indexes(self, monkeypatch):
        migration = _migration()
        assert migration.revision == "v6z80_spend_usage" and len(migration.revision) <= 32
        assert migration.down_revision == "v6z79_spend_reference"
        assert migration.TABLES == tuple(model.__tablename__ for model in MODELS)
        assert migration._months() == list(partitions.STATIC_MONTHS)
        for model in MODELS:
            table = model.__table__
            leading = [tuple(c.name for c in index.columns) for index in table.indexes]
            for fk in table.foreign_key_constraints:
                columns = tuple(c.name for c in fk.columns)
                assert columns[0] == "tenant_id" and fk.ondelete == "RESTRICT"
                assert any(cols[: len(columns)] == columns for cols in leading), (table.name, columns)
        assert SpendUsageRecord.__table__.dialect_options["postgresql"]["partition_by"] == "RANGE (event_time)"
        down = _Op()
        monkeypatch.setattr(migration, "op", down)
        migration.downgrade()
        assert down.sql == [f"DROP TABLE IF EXISTS {t};" for t in reversed(migration.TABLES)]

    def test_models_compile_to_the_migration_ddl(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        flat = _strip_ws(sql.replace('"\n        "', "").replace('" "', ""))
        ddl = ""
        for model in MODELS:
            ddl += str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
            for index in model.__table__.indexes:
                ddl += str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        flat_ddl = _strip_ws(ddl)
        names = set(re.findall(r"CONSTRAINT (\w+)", sql)) | set(re.findall(r"INDEX IF NOT EXISTS (\w+)", sql))
        assert len(names) > 25
        for name in names:
            assert name in ddl, name
        for match in re.finditer(r"(?<!WITH )CHECK \(", sql):
            depth, start, index = 1, match.end(), match.end()
            while depth:
                depth += {"(": 1, ")": -1}.get(sql[index], 0)
                index += 1
            body = _strip_ws(sql[start : index - 1])
            assert f"CHECK({body})" in flat_ddl, body
        for predicate in (
            "rate_card_id IS NOT NULL",
            "org_node_id IS NOT NULL",
            "fx_estimated OR unconverted",
            "status IN ('queued','running')",
            "status = 'running'",
        ):
            assert f"WHERE {predicate}" in ddl, predicate
        for model in MODELS:
            table_sql = sql[sql.index(f"CREATE TABLE IF NOT EXISTS {model.__tablename__} (") :]
            table_sql = table_sql[: table_sql.index(");")]
            for column in model.__table__.columns:
                assert re.search(rf"\b{column.name} ", table_sql), (model.__tablename__, column.name)
            for name, _default in re.findall(r"(\w+) [A-Z(),0-9 ]+? NOT NULL DEFAULT ([^,\n]+)", table_sql):
                assert model.__table__.c[name].server_default is not None, (model.__tablename__, name)
        assert str(SpendUsageRecord.__table__.c.correlation_ref.type) == "CHAR(32)"
        assert "pk_spend_usage_records" in flat and "PARTITIONBYRANGE(event_time)" in flat_ddl.replace(")\n", ")")

    def test_rollup_fillfactor_is_set_on_the_orm_table_too(self):
        from core.models import spend_usage

        assert "fillfactor = 70" in str(spend_usage.ROLLUP_FILLFACTOR_DDL.statement)
        assert "WITH (fillfactor = 70)" in MIGRATION.read_text(encoding="utf-8")

    def test_every_tenant_table_is_named_by_an_rls_migration(self):
        from tests.unit.test_rls_tenant_coverage import _rls_tables_declared_in_migrations

        assert {model.__tablename__ for model in MODELS} <= _rls_tables_declared_in_migrations()

    def test_drift_allowlist_lists_the_partitions(self):
        from tests.integration.alembic_schema_drift_allowlist import MIGRATION_OWNED_TABLES

        assert set(partitions.STATIC_PARTITIONS) <= set(MIGRATION_OWNED_TABLES)


# ---------------------------------------------------------------- Celery


class TestTasks:
    def test_spend_tasks_registered_and_scheduled(self):
        from core.tasks.celery_app import app

        app.loader.import_default_modules()
        for name in ("persist_usage", "run_job", "check_partitions", "settle_fx_daily", "recompute_commitments"):
            assert f"core.tasks.spend_tasks.{name}" in app.tasks, name
        assert "core.tasks.spend_tasks" in app.conf.include
        beat = app.conf.beat_schedule
        assert beat["spend-check-partitions"]["task"] == "core.tasks.spend_tasks.check_partitions"
        assert beat["spend-settle-fx"]["task"] == "core.tasks.spend_tasks.settle_fx_daily"
        assert beat["spend-recompute-commitments"]["schedule"] == 900.0
        assert {
            beat[k]["options"]["queue"]
            for k in ("spend-check-partitions", "spend-settle-fx", "spend-recompute-commitments")
        } == {"maintenance"}

    def test_tasks_skip_while_off_and_when_sweeps_are_off(self, monkeypatch):
        import core.tasks.spend_tasks as tasks

        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
        for task in (tasks.check_partitions, tasks.settle_fx_daily, tasks.recompute_commitments):
            assert task.run() == {"skipped": "spend_intelligence_disabled"}
        assert tasks.run_job.run(TID, str(uuid.uuid4())) == {"skipped": "spend_intelligence_disabled"}
        monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
        monkeypatch.setattr(settings, "spend_sweeps_enabled", False)
        for task in (tasks.check_partitions, tasks.settle_fx_daily, tasks.recompute_commitments):
            assert task.run() == {"skipped": "spend_sweeps_disabled"}

    def test_beat_bodies_queue_jobs_per_tenant(self, store, monkeypatch):
        import core.tasks.spend_tasks as tasks
        from core.models.spend import SpendCommitment
        from core.spend import tenants

        async def two_tenants():
            return [TENANT, uuid.UUID("99999999-9999-4999-8999-999999999999")]

        monkeypatch.setattr(tenants, "active_tenant_ids", two_tenants)
        monkeypatch.setattr(tasks, "run_async", _run)
        monkeypatch.setattr(settings, "spend_sweeps_enabled", True)
        assert tasks.check_partitions.run()["months_ahead"] == 26
        assert tasks.settle_fx_daily.run() == {"queued": 0}
        store.add(card(currency="EUR"))
        _run(meter.write_events(store, TENANT, [event()], now=T0))  # unconverted: pending FX
        assert tasks.settle_fx_daily.run() == {"queued": 1}  # only the tenant with pending records
        assert tasks.recompute_commitments.run() == {"queued": 0}
        store.add(
            SpendCommitment(
                id=uuid.uuid4(),
                tenant_id=TENANT,
                provider="openai",
                kind="money",
                committed_amount=Decimal(1),
                currency="USD",
                period_start=DAY,
                period_end=date(2026, 11, 1),
                status="active",
            )
        )
        assert tasks.recompute_commitments.run() == {"queued": 1}  # only the tenant with a commitment
        assert tasks.recompute_commitments.run() == {"queued": 0}  # already queued: folded in

    def test_beat_isolates_a_failing_tenant(self, store, monkeypatch):
        import core.tasks.spend_tasks as tasks
        from core.spend import tenants

        async def one_tenant():
            return [TENANT]

        def broken(tenant_id):
            raise RuntimeError("tenant session down")

        import core.database

        monkeypatch.setattr(tenants, "active_tenant_ids", one_tenant)
        monkeypatch.setattr(tasks, "run_async", _run)
        monkeypatch.setattr(core.database, "get_tenant_session", broken)
        assert tasks.settle_fx_daily.run() == {"queued": 0}
        assert tasks.recompute_commitments.run() == {"queued": 0}

    def test_run_job_task_runs_the_job(self, store, monkeypatch):
        import core.tasks.spend_tasks as tasks

        monkeypatch.setattr(tasks, "run_async", _run)
        queued = _run(
            jobs.enqueue(TENANT, kind="rebuild", params={"start": "2026-10-01", "end": "2026-10-01"}, actor=ACTOR)
        )
        assert tasks.run_job.run(TID, queued["job_id"])["status"] == "succeeded"

    @pytest.mark.asyncio
    async def test_active_tenants_skip_deleted_ones(self, monkeypatch):
        import core.database
        from core.models.tenant import Tenant
        from core.spend import tenants
        from tests.unit.spend_usage_fakes import UsageSession

        session = UsageSession()
        live, gone = uuid.uuid4(), uuid.uuid4()
        session.add(Tenant(id=live, name="a", slug="a", deleted_at=None))
        session.add(Tenant(id=gone, name="b", slug="b", deleted_at=T0))
        monkeypatch.setattr(core.database, "async_session_factory", lambda: session)
        assert await tenants.active_tenant_ids() == [live]


def _run(awaitable):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(awaitable)
    finally:
        loop.close()


# ---------------------------------------------------------------- hooks into the reference data


class TestReferenceHooks:
    @pytest.mark.asyncio
    async def test_card_in_use_reads_direct_and_blend_references(self, store):
        priced = card()
        output = card(unit="1m_output_tokens", unit_price=Decimal("10"))
        store.add(priced)
        store.add(output)
        assert await rates.card_in_use(store, TENANT, priced.id) is None
        await meter.write_events(store, TENANT, [event()], now=T0)
        assert await rates.card_in_use(store, TENANT, priced.id) == DAY
        assert await rates.card_in_use(store, TENANT, output.id) is None
        blend_input = card(model_sku="blend-model")
        blend_output = card(model_sku="blend-model", unit="1m_output_tokens", effective_to=date(2027, 1, 1))
        store.add(blend_input)
        store.add(blend_output)
        await meter.write_events(
            store, TENANT, [event(unit="token", model="blend-model", event_time=T0 + timedelta(days=2))], now=T0
        )
        assert await rates.card_in_use(store, TENANT, blend_output.id) == date(2026, 10, 3)

    @pytest.mark.asyncio
    async def test_correction_of_a_used_card_queues_its_restatement(self, store):
        used = card(effective_to=date(2026, 12, 1))
        store.add(used)
        await meter.write_events(store, TENANT, [event()], now=T0)
        out = await rates.correct_card(
            TENANT, used.id, {"unit_price": "3"}, reason="Contract price was wrong", actor=ACTOR, now=T0
        )
        job = next(r for r in store.of("spend_jobs") if str(r.id) == out["restate_job_id"])
        assert job.kind == "restate" and job.params["card_ids"] == [str(used.id)]
        assert (job.params["start"], job.params["end"]) == ("2026-01-01", "2026-10-01")
        assert job.params["reason"] == "Contract price was wrong"

    @pytest.mark.asyncio
    async def test_a_queued_correction_restatement_runs_over_its_whole_range(self, store):
        """A card in force since January: the restatement covers nine months, past the route's 92 days."""
        used = card()
        store.add(used)
        await meter.write_events(store, TENANT, [event()], now=T0)
        out = await rates.correct_card(
            TENANT, used.id, {"unit_price": "3"}, reason="Contract price was wrong", actor=ACTOR, now=T0
        )
        done = await jobs.run(TENANT, uuid.UUID(out["restate_job_id"]), now=T0)
        assert done["status"] == "succeeded" and done["result"]["changed"] == 1 and done["result"]["days"] == 274
        record = store.of("spend_usage_records")[0]
        assert record.rate_card_id == uuid.UUID(out["card"]["id"]) and record.amount == Decimal("0.0030000000")
        with pytest.raises(HTTPException) as info:
            await api.restate_usage(
                api.RestateIn(
                    provider="openai", start=DAY, end=DAY + timedelta(days=92), reason="a long enough reason"
                ),
                ADMIN,
                tenant_id=TID,
            )
        assert info.value.status_code == 422 and info.value.detail["error"] == "range_too_long"

    @pytest.mark.asyncio
    async def test_moving_effective_to_over_priced_records_queues_a_restatement(self, store):
        used = card()
        store.add(used)
        await meter.write_events(store, TENANT, [event()], now=T0)
        with pytest.raises(SpendError) as info:
            await rates.update_card(TENANT, used.id, {"effective_to": "2026-09-15"}, actor=ACTOR, now=T0)
        assert info.value.code == "restate_required"
        out = await rates.update_card(
            TENANT, used.id, {"effective_to": "2026-09-15", "restate": True}, actor=ACTOR, now=T0
        )
        job = next(r for r in store.of("spend_jobs") if str(r.id) == out["restate_job_id"])
        assert (job.params["start"], job.params["end"]) == ("2026-09-15", "2026-10-01")

    @pytest.mark.asyncio
    async def test_rate_card_import_queues_one_restatement_per_provider(self, store):
        old = card(source="list", effective_from=date(2026, 1, 1))
        store.add(old)
        await meter.write_events(store, TENANT, [event()], now=T0)
        rows = [
            {"provider": "openai", "usage_type": "llm_tokens", "model_sku": "gpt-4o", "unit": "1m_input_tokens",
             "unit_price": "3", "currency": "USD", "effective_from": "2026-09-01", "source": "list",
             "supersede": "true", "restate": "true"},
        ]  # fmt: skip
        report = await rates.import_cards(TENANT, rows, actor=ACTOR, dry_run=False, file_sha256="0" * 64, now=T0)
        assert report["created"] == 1 and report["restate_jobs"][0]["provider"] == "openai"
        assert rates._merge_plans([("p", DAY, DAY, old.id), ("p", date(2026, 9, 1), DAY, old.id)])[0][0][1] == date(
            2026, 9, 1
        )

    @pytest.mark.asyncio
    async def test_fx_rate_in_use_needs_restate_and_reconverts(self, store):
        store.add(card())
        await fx.put_rate(
            TENANT, {"rate_date": "2026-10-01", "currency": "USD", "rate_to_inr": "83"}, actor=ACTOR, now=T0
        )
        await meter.write_events(store, TENANT, [event()], now=T0)
        assert not store.of("spend_usage_records")[0].fx_estimated
        with pytest.raises(SpendError) as info:
            await fx.put_rate(
                TENANT, {"rate_date": "2026-10-01", "currency": "USD", "rate_to_inr": "84"}, actor=ACTOR, now=T0
            )
        assert info.value.code == "fx_in_use"
        for job in store.of("spend_jobs"):
            job.status = "succeeded"
        out = await fx.put_rate(
            TENANT,
            {"rate_date": "2026-10-01", "currency": "USD", "rate_to_inr": "84", "restate": True},
            actor=ACTOR,
            now=T0,
        )
        job = next(r for r in store.of("spend_jobs") if str(r.id) == out["settle_job_id"])
        assert job.params["force_dates"] == [["USD", "2026-10-01"]]
        assert (job.params["start"], job.params["end"]) == ("2026-10-01", "2026-11-01")
        report = await fx.import_rates(
            TENANT, [{"rate_date": "2026-10-01", "currency": "USD", "rate_to_inr": "85"}], actor=ACTOR, dry_run=False,
            file_sha256="0" * 64, now=T0,
        )  # fmt: skip
        assert report["rejected"] == [{"row": 2, "key": "USD:2026-10-01", "reason": "fx_in_use"}]
        later = await fx.import_rates(
            TENANT, [{"rate_date": "2026-10-05", "currency": "USD", "rate_to_inr": "85"}], actor=ACTOR, dry_run=False,
            file_sha256="0" * 64, now=T0,
        )  # fmt: skip
        assert later["created"] == 1 and later["settle_job_id"]
        window = await fx.settle_window(store, TENANT, "USD", date(2026, 10, 1))
        assert window == (date(2026, 10, 1), date(2026, 10, 4))

    @pytest.mark.asyncio
    async def test_an_fx_import_settles_its_whole_window(self, store):
        """Rates nine months apart: the settlement runs from the first to the end of the last one's window,
        not 92 days."""
        rows = [
            {"rate_date": "2026-01-01", "currency": "USD", "rate_to_inr": "82"},
            {"rate_date": "2026-09-30", "currency": "USD", "rate_to_inr": "83"},
        ]
        report = await fx.import_rates(TENANT, rows, actor=ACTOR, dry_run=False, file_sha256="0" * 64, now=T0)
        job = next(r for r in store.of("spend_jobs") if str(r.id) == report["settle_job_id"])
        assert (job.params["start"], job.params["end"]) == ("2026-01-01", "2026-10-31")

    @pytest.mark.asyncio
    async def test_commitment_changes_queue_a_recompute(self, store):
        from core.spend import commitments

        body = {"provider": "openai", "kind": "money", "committed_amount": "100", "currency": "USD",
                "period_start": "2026-10-01", "period_end": "2026-11-01"}  # fmt: skip
        created = await commitments.create_commitment(TENANT, body, actor=ACTOR, now=T0)
        job = store.of("spend_jobs")[0]
        assert job.kind == "recompute_commitments" and job.params == {"provider": "openai"}
        job.status = "succeeded"
        await commitments.update_commitment(
            TENANT, uuid.UUID(created["id"]), {"reference": "PO-1"}, actor=ACTOR, now=T0
        )
        assert len([j for j in store.of("spend_jobs") if j.kind == "recompute_commitments"]) == 2


def test_rollups_constants():
    assert rollups.MAX_USAGE_DAYS == 31 and rollups.MAX_GAP_DAYS == 92
