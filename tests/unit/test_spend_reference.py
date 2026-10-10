# SPDX-License-Identifier: Apache-2.0
"""Spend reference data: the organisation tree, mappings, aliases, rate cards, commitments, FX, imports, audit,
the migration and the settings."""

from __future__ import annotations

import importlib.util
import json
import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.sql.elements import TextClause

from core.config import Settings, settings
from core.spend import audit, clock, commitments, fx, imports, jobs, locks, mappings, org, pricing, rates, vocab
from core.spend.errors import SpendError, require_actor
from core.tool_gateway.audit_logger import verify_audit_row
from tests.unit.spend_fakes import FakeSession

TENANT = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER_TENANT = uuid.UUID("22222222-2222-4222-8222-222222222222")
ACTOR = "33333333-3333-4333-8333-333333333333"
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)  # billing "today" for UTC providers is 2026-10-01
MIGRATION = Path("migrations/versions/v6_z79_spend_reference.py")


@pytest.fixture
def session(monkeypatch):
    import core.database

    store = FakeSession()
    monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: store)
    monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
    monkeypatch.setattr(settings, "spend_reporting_timezone", "Asia/Kolkata")
    monkeypatch.setattr(settings, "spend_provider_billing_timezones_json", "")
    monkeypatch.setattr(settings, "model_price_overrides_json", "")
    monkeypatch.setattr(clock, "now_utc", lambda: T0)  # a service called without now reads T0
    monkeypatch.setattr(jobs, "_dispatch", lambda tenant_id, job_id: None)  # follow-up jobs stay queued
    pricing._ALIAS_CACHE.clear()
    return store


async def node(code: str, kind: str, parent: str | None = None, **over):
    body = {"code": code, "name": f"Node {code}", "kind": kind}
    if parent:
        body["parent_code"] = parent
    body.update(over)
    return await org.create_node(TENANT, body, actor=ACTOR, now=T0)


def card_body(**over):
    body = {
        "provider": "openai",
        "usage_type": "llm_tokens",
        "model_sku": "gpt-4o",
        "unit": "1m_input_tokens",
        "unit_price": "2.5",
        "currency": "USD",
        "effective_from": "2026-01-01",
        "source": "list",
    }
    body.update(over)
    return body


def audit_rows(session: FakeSession, event_type: str | None = None):
    return [r for r in session.of("audit_log") if event_type is None or r.event_type == event_type]


def in_use_on(day: date | None):
    async def card_in_use(session, tenant_id, card_id):
        return day

    return card_in_use


# ---------------------------------------------------------------- vocabulary and clock


class TestVocabulary:
    def test_normalisers_refuse_rather_than_repair(self):
        assert vocab.norm_code(" cc-4120 ") == "CC-4120"
        for bad in ("", "-CC", "C" * 65, "CC 41"):
            with pytest.raises(SpendError) as info:
                vocab.norm_code(bad)
            assert info.value.code == "invalid_code"
        assert vocab.norm_sku(" GPT-4o ") == "gpt-4o" and vocab.norm_sku("", allow_empty=True) == ""
        with pytest.raises(SpendError):
            vocab.norm_sku("=cmd")
        assert vocab.norm_provider(" GPT ") == "openai" and vocab.norm_provider("Gemini") == "gemini"
        with pytest.raises(SpendError):
            vocab.norm_provider("")
        assert vocab.norm_currency("usd") == "USD"
        with pytest.raises(SpendError) as info:
            vocab.norm_currency("US")
        assert info.value.code == "invalid_currency"
        assert vocab.label(" Retail Lending ") == "retail-lending"
        assert vocab.parse_date("2026-10-01", field="d") == date(2026, 10, 1)
        for bad in ("2026-13-01", "01/10/2026", ""):
            with pytest.raises(SpendError) as info:
                vocab.parse_date(bad, field="d")
            assert info.value.code == "invalid_date"
        assert vocab.parse_bool("Yes", field="b") is True and vocab.parse_bool("0", field="b") is False
        assert vocab.parse_bool("", field="b") is None and vocab.parse_bool(False, field="b") is False
        with pytest.raises(SpendError):
            vocab.parse_bool("maybe", field="b")
        assert vocab.dec_str(Decimal("1E+3")) == "1000" and vocab.dec_str(None) is None

    def test_name_with_formula_lead_is_refused(self):
        for bad in ("=HYPERLINK(1)", "+1", "-1", "@SUM", "x\ty", "a\x00b", "x" * 201):
            with pytest.raises(SpendError) as info:
                vocab.free_text(bad, field="name", max_len=200)
            assert info.value.code == "invalid_text"
        assert vocab.free_text("  Retail ops ", field="name", max_len=200) == "Retail ops"
        with pytest.raises(SpendError):
            vocab.free_text("  ", field="name", max_len=200, required=True)

    def test_clock_calendars_and_bounds(self, monkeypatch):
        monkeypatch.setattr(settings, "spend_reporting_timezone", "Asia/Kolkata")
        monkeypatch.setattr(settings, "spend_provider_billing_timezones_json", '{"acme_ai": "America/New_York"}')
        assert str(clock.billing_zone("gemini")) == "America/Los_Angeles"
        assert str(clock.billing_zone("acme_ai")) == "America/New_York"
        assert str(clock.billing_zone("openai")) == "UTC"
        assert str(clock.billing_zone("ollama")) == "Asia/Kolkata"
        start, end = clock.day_bounds(date(2026, 10, 1), clock.reporting_zone())
        assert start == datetime(2026, 9, 30, 18, 30, tzinfo=UTC) and end == datetime(2026, 10, 1, 18, 30, tzinfo=UTC)
        first, following, a, b = clock.month_bounds("2026-12", clock.billing_zone("openai"))
        assert (first, following) == (date(2026, 12, 1), date(2027, 1, 1))
        assert a == datetime(2026, 12, 1, tzinfo=UTC) and b == datetime(2027, 1, 1, tzinfo=UTC)
        with pytest.raises(SpendError) as info:
            clock.month_bounds("2026-13", clock.reporting_zone())
        assert info.value.code == "invalid_period"
        assert clock.days_in_month(date(2028, 2, 3)) == 29
        assert clock.event_date_of(T0.replace(hour=20, tzinfo=None)) == date(2026, 10, 2)  # naive is read as UTC
        assert clock.today_in(clock.reporting_zone(), T0) == date(2026, 10, 1)
        assert clock.now_utc().tzinfo is not None
        monkeypatch.setattr(settings, "spend_provider_billing_timezones_json", "[]")
        with pytest.raises(ValueError):
            clock.billing_zone("openai")

    @pytest.mark.asyncio
    async def test_lock_keys_are_built_in_one_place(self):
        session = FakeSession()
        assert locks.rollup_day(TENANT, date(2026, 10, 1)) == f"spend:rollup:{TENANT}:2026-10-01"
        assert locks.rate_card(TENANT, "openai", "llm_tokens", "", "1m_input_tokens", "list").startswith(
            "spend:rate_card:"
        )
        assert (
            locks.commitment(TENANT, "openai", None, "", None, "money") == f"spend:commitment:{TENANT}:openai::::money"
        )
        assert locks.commitment_recompute(TENANT, "openai") == f"spend:commitment_recompute:{TENANT}:openai"
        await locks.xact_lock(session, "spend:a")
        await locks.xact_lock_shared(session, "spend:b")
        assert await locks.try_xact_lock(session, "spend:c") and await locks.try_xact_lock_shared(session, "spend:d")
        await locks.set_lock_timeout(session, 30_000)
        assert session.locks == ["spend:a", "spend:b", "spend:c", "spend:d"]

    def test_write_without_actor_is_refused(self):
        with pytest.raises(SpendError) as info:
            require_actor("  ")
        assert (info.value.status, info.value.code) == (401, "actor_required")
        assert require_actor("x" * 200) == "x" * 128


# ---------------------------------------------------------------- organisation tree


class TestOrgTree:
    @pytest.mark.asyncio
    async def test_create_node_normalises_code_and_refuses_duplicate(self, session):
        out = await node(" grp-1 ", "group")
        assert out["code"] == "GRP-1" and out["active"] is True and out["parent_code"] is None
        with pytest.raises(SpendError) as info:
            await node("grp-1", "group")
        assert (info.value.status, info.value.code) == (409, "code_taken")
        assert locks.org_tree(TENANT) in session.locks
        assert [r.event_type for r in audit_rows(session)] == ["spend.org_node.create"]
        with pytest.raises(SpendError) as info:
            await node("X1", "group", name="=SUM(A1)")
        assert info.value.code == "invalid_text"
        with pytest.raises(SpendError) as info:
            await node("X2", "division")
        assert info.value.code == "invalid_kind"
        with pytest.raises(SpendError) as info:
            await node("X3", "team", parent="NOPE")
        assert info.value.code == "invalid_reference"

    @pytest.mark.asyncio
    async def test_parent_kind_rules_refuse_team_under_business_unit(self, session):
        await node("G", "group")
        await node("BU", "business_unit", parent="G")
        with pytest.raises(SpendError) as info:
            await node("T", "team", parent="BU")
        assert (info.value.status, info.value.code) == (422, "invalid_parent_kind")
        with pytest.raises(SpendError) as info:
            await node("D", "department")
        assert info.value.code == "invalid_parent_kind"
        org.check_parent_kind("cost_centre", "team")
        org.check_parent_kind("group", None)

    @pytest.mark.asyncio
    async def test_reparent_into_own_descendant_is_refused_as_cycle(self, session):
        await node("G", "group")
        d1 = await node("D1", "department", parent="G")
        await node("D2", "department", parent="D1")
        with pytest.raises(SpendError) as info:
            await org.update_node(TENANT, uuid.UUID(d1["id"]), {"parent_code": "D2"}, actor=ACTOR, now=T0)
        assert (info.value.status, info.value.code) == (409, "cycle")
        with pytest.raises(SpendError) as info:
            await org.update_node(TENANT, uuid.UUID(d1["id"]), {"parent_code": "D1"}, actor=ACTOR, now=T0)
        assert info.value.code == "cycle"

    @pytest.mark.asyncio
    async def test_reparent_takes_the_org_tree_lock(self, session):
        await node("G1", "group")
        await node("G2", "group")
        d = await node("D", "department", parent="G1")
        session.locks.clear()
        out = await org.update_node(TENANT, uuid.UUID(d["id"]), {"parent_code": "g2"}, actor=ACTOR, now=T0)
        assert out["parent_code"] == "G2" and session.locks == [locks.org_tree(TENANT)]
        change = json.loads(json.dumps(audit_rows(session, "spend.org_node.update")[-1].details))["changes"][0]
        assert change["before"]["parent_code"] == "G1" and change["after"]["parent_code"] == "G2"
        session.locks.clear()
        await org.update_node(TENANT, uuid.UUID(d["id"]), {"name": "Renamed"}, actor=ACTOR, now=T0)
        assert session.locks == []  # no parent change, no tree lock
        g2 = next(r for r in session.of("spend_org_nodes") if r.code == "G2")
        out = await org.update_node(TENANT, g2.id, {"clear_parent": True}, actor=ACTOR, now=T0)
        assert out["parent_code"] is None
        with pytest.raises(SpendError) as info:
            await org.update_node(TENANT, uuid.UUID(d["id"]), {"clear_parent": True}, actor=ACTOR, now=T0)
        assert info.value.code == "invalid_parent_kind"
        with pytest.raises(SpendError) as info:
            await org.update_node(TENANT, uuid.uuid4(), {"name": "x"}, actor=ACTOR, now=T0)
        assert info.value.status == 404

    @pytest.mark.asyncio
    async def test_depth_beyond_sixteen_is_refused(self, session):
        await node("L0", "group")
        for level in range(1, vocab.MAX_DEPTH + 1):
            await node(f"L{level}", "group", parent=f"L{level - 1}")
        with pytest.raises(SpendError) as info:
            await node("L17", "group", parent="L16")
        assert info.value.code == "cycle"
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        chain = {a: None, b: a, c: b}
        assert not org.would_cycle(chain, c, None)
        assert org.would_cycle(chain, a, c) and org.would_cycle(chain, a, a)
        deep = {uuid.uuid4(): None}
        ids = list(deep)
        for _ in range(vocab.MAX_DEPTH):
            child = uuid.uuid4()
            deep[child] = ids[-1]
            ids.append(child)
        loose = uuid.uuid4()
        deep[loose] = None
        assert org.would_cycle(deep, loose, ids[-1])  # depth 17 below the root
        assert not org.would_cycle(deep, loose, ids[-2])

    @pytest.mark.asyncio
    async def test_deactivate_sets_deactivated_at_and_has_no_delete_route(self, session):
        from api.v1 import spend as api

        g = await node("G", "group")
        out = await org.update_node(TENANT, uuid.UUID(g["id"]), {"active": False}, actor=ACTOR, now=T0)
        assert out["active"] is False and out["deactivated_at"] == T0.isoformat()
        out = await org.update_node(TENANT, uuid.UUID(g["id"]), {"active": True}, actor=ACTOR, now=T0)
        assert out["active"] is True and out["deactivated_at"] is None
        assert not any("DELETE" in getattr(route, "methods", set()) for route in api.router.routes)

    @pytest.mark.asyncio
    async def test_business_unit_of_walks_to_nearest_business_unit(self, session):
        await node("G", "group")
        bu1 = await node("BU1", "business_unit", parent="G")
        bu2 = await node("BU2", "business_unit", parent="BU1")
        await node("D", "department", parent="BU2")
        team = await node("T", "team", parent="D")
        found = await org.business_unit_of(session, TENANT, uuid.UUID(team["id"]))
        assert str(found) == bu2["id"]
        assert await org.business_unit_of(session, TENANT, uuid.UUID(bu1["id"])) == uuid.UUID(bu1["id"])
        g = next(r for r in session.of("spend_org_nodes") if r.code == "G")
        assert await org.business_unit_of(session, TENANT, g.id) is None
        detail = await org.get_node(TENANT, uuid.UUID(team["id"]))
        assert [a["code"] for a in detail["ancestors"]] == ["D", "BU2", "BU1", "G"]
        assert detail["business_unit_node_id"] == bu2["id"] and detail["parent_code"] == "D"
        assert await org.ancestors(session, OTHER_TENANT, uuid.UUID(team["id"])) == []
        listed = await org.list_nodes(TENANT, kind="business_unit")
        assert [i["code"] for i in listed["items"]] == ["BU1", "BU2"] and listed["total"] == 2
        assert listed["items"][1]["parent_code"] == "BU1"
        page = await org.list_nodes(TENANT, active=True, limit=2, offset=1)
        assert [i["code"] for i in page["items"]] == ["BU2", "D"] and page["total"] == 5

    @pytest.mark.asyncio
    async def test_owner_must_belong_to_the_tenant(self, session):
        mine, theirs = uuid.uuid4(), uuid.uuid4()
        session.users = {(str(TENANT), str(mine)), (str(OTHER_TENANT), str(theirs))}
        with pytest.raises(SpendError) as info:
            await node("G", "group", owner_user_id=str(theirs))
        assert info.value.code == "invalid_reference"
        with pytest.raises(SpendError):
            await node("G", "group", owner_user_id="not-a-uuid")
        out = await node("G", "group", owner_user_id=mine)
        assert out["owner_user_id"] == str(mine)
        cleared = await org.update_node(TENANT, uuid.UUID(out["id"]), {"owner_user_id": None}, actor=ACTOR, now=T0)
        assert cleared["owner_user_id"] is None


# ---------------------------------------------------------------- mappings and aliases


class TestMappings:
    @pytest.mark.asyncio
    async def test_mapping_upsert_by_source_and_unknown_node_code_refused(self, session):
        await node("G", "group")
        await node("CC-1", "cost_centre", parent="G")
        agent = str(uuid.uuid4())
        first = await mappings.put_mapping(
            TENANT, {"source_type": "agent", "source_ref": agent.upper(), "org_node_code": "cc-1"}, actor=ACTOR, now=T0
        )
        assert first["outcome"] == "created" and first["source_ref"] == agent and first["org_node_code"] == "CC-1"
        second = await mappings.put_mapping(
            TENANT,
            {"source_type": "agent", "source_ref": agent, "org_node_code": "CC-1", "use_case": "KYC Review"},
            actor=ACTOR,
            now=T0,
        )
        assert second["outcome"] == "updated" and second["use_case"] == "kyc-review" and second["id"] == first["id"]
        again = await mappings.put_mapping(
            TENANT,
            {"source_type": "agent", "source_ref": agent, "org_node_code": "CC-1", "use_case": "kyc-review"},
            actor=ACTOR,
            now=T0,
        )
        assert again["outcome"] == "unchanged"
        assert [r.event_type for r in audit_rows(session)][-2:] == ["spend.mappings.create", "spend.mappings.update"]
        with pytest.raises(SpendError) as info:
            await mappings.put_mapping(
                TENANT, {"source_type": "agent", "source_ref": agent, "org_node_code": "NOPE"}, actor=ACTOR, now=T0
            )
        assert info.value.code == "invalid_reference"
        cleared = {"source_type": "agent", "source_ref": agent, "org_node_code": None, "use_case": None}
        with pytest.raises(SpendError) as info:
            await mappings.put_mapping(TENANT, cleared, actor=ACTOR, now=T0)
        assert info.value.code == "invalid_reference"  # names no node, product line or use case
        with pytest.raises(SpendError) as info:
            await mappings.put_mapping(TENANT, {"source_type": "application", "source_ref": "api"}, actor=ACTOR)
        assert info.value.code == "invalid_reference"  # a new mapping with no target
        off = await mappings.put_mapping(
            TENANT,
            {"source_type": "agent", "source_ref": agent, "org_node_code": "CC-1", "use_case": "kyc", "active": False},
            actor=ACTOR,
            now=T0,
        )
        assert off["active"] is False
        listed = await mappings.list_mappings(TENANT, source_type="agent", active=False)
        assert listed["total"] == 1 and listed["items"][0]["org_node_code"] == "CC-1"

    @pytest.mark.asyncio
    async def test_application_mapping_requires_known_application(self, session):
        out = await mappings.put_mapping(
            TENANT, {"source_type": "application", "source_ref": "Chat", "product_line": "cards"}, actor=ACTOR, now=T0
        )
        assert out["source_ref"] == "chat" and out["org_node_id"] is None
        for body in (
            {"source_type": "application", "source_ref": "telepathy", "product_line": "x"},
            {"source_type": "workflow", "source_ref": "not-a-uuid", "product_line": "x"},
            {"source_type": "printer", "source_ref": "x", "product_line": "x"},
        ):
            with pytest.raises(SpendError) as info:
                await mappings.put_mapping(TENANT, body, actor=ACTOR, now=T0)
            assert info.value.code == "invalid_reference"
        assert mappings.check_source_ref("cost_center", str(uuid.UUID(int=5))) == str(uuid.UUID(int=5))

    @pytest.mark.asyncio
    async def test_alias_upsert_and_canonical_model(self, session):
        pricing._ALIAS_CACHE[str(TENANT)] = (0.0, {})
        out = await mappings.put_alias(
            TENANT, {"provider": "openai", "alias": "GPT-4o-2024-08-06", "model_sku": "gpt-4o"}, actor=ACTOR, now=T0
        )
        assert out["alias"] == "gpt-4o-2024-08-06" and str(TENANT) not in pricing._ALIAS_CACHE
        await mappings.put_alias(
            TENANT, {"provider": "openai", "alias": "gpt-4o-2024-08-06", "model_sku": "gpt-4o-x"}, actor=ACTOR, now=T0
        )
        await mappings.put_alias(
            TENANT, {"provider": "openai", "alias": "gpt-4o-2024-08-06", "model_sku": "gpt-4o-x"}, actor=ACTOR, now=T0
        )
        assert len(audit_rows(session, "spend.model_aliases.put")) == 2  # the repeat changed nothing
        assert locks.model_alias(TENANT, "openai") in session.locks
        for body, code in (
            ({"provider": "openai", "alias": "same", "model_sku": "same"}, "invalid_sku"),
            ({"provider": "openai", "alias": "other", "model_sku": "gpt-4o-2024-08-06"}, "invalid_sku"),
            ({"provider": "openai", "alias": "gpt-4o-x", "model_sku": "gpt-4o"}, "invalid_sku"),
        ):
            with pytest.raises(SpendError) as info:
                await mappings.put_alias(TENANT, body, actor=ACTOR, now=T0)
            assert info.value.code == code
        aliases = await mappings.alias_map(session, TENANT)
        assert aliases == {("openai", "gpt-4o-2024-08-06"): "gpt-4o-x"}
        assert mappings.canonical_model("openai", "GPT-4o-2024-08-06", aliases) == "gpt-4o-x"
        assert mappings.canonical_model("openai", "gpt-4o-x", aliases) == "gpt-4o-x"  # applying twice changes nothing
        listed = await mappings.list_aliases(TENANT, provider="openai")
        assert listed["total"] == 1 and listed["items"][0]["model_sku"] == "gpt-4o-x"
        assert await pricing.cached_aliases(session, TENANT) == aliases
        session.rows = [r for r in session.rows if r.__tablename__ != "spend_model_aliases"]
        assert await pricing.cached_aliases(session, TENANT) == aliases  # served from the 60 s cache
        pricing.invalidate_aliases(TENANT)
        assert await pricing.cached_aliases(session, TENANT) == {}


# ---------------------------------------------------------------- rate cards


class TestRateCards:
    @pytest.mark.asyncio
    async def test_rate_card_overlap_refused_for_same_key(self, session):
        first = await rates.create_card(TENANT, card_body(), actor=ACTOR, now=T0)
        assert first["status"] == "active" and first["superseded_id"] is None and first["restate_job_id"] is None
        with pytest.raises(SpendError) as info:
            await rates.create_card(TENANT, card_body(effective_from="2026-06-01"), actor=ACTOR, now=T0)
        assert (info.value.status, info.value.code) == (409, "rate_card_overlap") and first["id"] in info.value.message
        key = locks.rate_card(TENANT, "openai", "llm_tokens", "gpt-4o", "1m_input_tokens", "list")
        assert key in session.locks
        other = await rates.create_card(TENANT, card_body(model_sku="gpt-4o-mini"), actor=ACTOR, now=T0)
        assert other["model_sku"] == "gpt-4o-mini"
        for bad, code in (
            (card_body(unit="1m_embedding_tokens"), "invalid_unit"),
            (card_body(usage_type="vibes"), "invalid_unit"),
            (card_body(unit="1m_output_tokens", cached_unit_price="1"), "invalid_unit"),
            (card_body(effective_to="2026-01-01"), "invalid_period"),
            (card_body(unit_price="1e10"), "invalid_number"),
            (card_body(source="rumour"), "invalid_value"),
            (card_body(reference="=cmd"), "invalid_text"),
        ):
            with pytest.raises(SpendError) as info:
                await rates.create_card(TENANT, bad, actor=ACTOR, now=T0)
            assert info.value.code == code

    @pytest.mark.asyncio
    async def test_list_and_contract_for_same_key_may_overlap(self, session):
        await rates.create_card(TENANT, card_body(), actor=ACTOR, now=T0)
        contract = await rates.create_card(TENANT, card_body(source="contract", unit_price="2.0"), actor=ACTOR, now=T0)
        assert contract["source"] == "contract"
        listed = await rates.list_cards(TENANT, provider="openai", usage_type="llm_tokens", as_of=date(2026, 5, 1))
        assert listed["total"] == 2
        assert (await rates.list_cards(TENANT, as_of=date(2025, 5, 1)))["total"] == 0
        assert (await rates.list_cards(TENANT, status="retired"))["total"] == 0

    @pytest.mark.asyncio
    async def test_extending_effective_to_into_a_successor_is_refused(self, session):
        a = await rates.create_card(TENANT, card_body(effective_to="2026-06-01"), actor=ACTOR, now=T0)
        await rates.create_card(TENANT, card_body(effective_from="2026-06-01", unit_price="3"), actor=ACTOR, now=T0)
        with pytest.raises(SpendError) as info:
            await rates.update_card(TENANT, uuid.UUID(a["id"]), {"effective_to": "2026-07-01"}, actor=ACTOR, now=T0)
        assert info.value.code == "rate_card_overlap"
        with pytest.raises(SpendError) as info:
            await rates.update_card(TENANT, uuid.UUID(a["id"]), {"effective_to": "2025-12-01"}, actor=ACTOR, now=T0)
        assert info.value.code == "invalid_period"
        out = await rates.update_card(TENANT, uuid.UUID(a["id"]), {"reference": "MSA-7"}, actor=ACTOR, now=T0)
        assert out["reference"] == "MSA-7" and out["restate_job_id"] is None
        same = await rates.update_card(TENANT, uuid.UUID(a["id"]), {"reference": "MSA-7"}, actor=ACTOR, now=T0)
        assert same["reference"] == "MSA-7"
        assert len(audit_rows(session, "spend.rate_cards.update")) == 1
        with pytest.raises(SpendError) as info:
            await rates.update_card(TENANT, uuid.uuid4(), {"reference": "x"}, actor=ACTOR, now=T0)
        assert info.value.status == 404

    @pytest.mark.asyncio
    async def test_supersede_closes_open_predecessor_and_audits_it(self, session):
        a = await rates.create_card(TENANT, card_body(), actor=ACTOR, now=T0)
        b = await rates.create_card(
            TENANT, card_body(effective_from="2026-11-01", unit_price="3", supersede=True), actor=ACTOR, now=T0
        )
        assert b["superseded_id"] == a["id"]
        old = next(r for r in session.of("spend_rate_cards") if str(r.id) == a["id"])
        assert old.effective_to == date(2026, 11, 1)
        changes = audit_rows(session, "spend.rate_cards.create")[-1].details["changes"]
        assert len(changes) == 2
        assert changes[0]["before"]["effective_to"] is None and changes[0]["after"]["effective_to"] == "2026-11-01"
        assert changes[1]["before"] is None and changes[1]["after"]["unit_price"] == "3"

    @pytest.mark.asyncio
    async def test_backdated_supersede_over_priced_records_needs_restate(self, session, monkeypatch):
        await rates.create_card(TENANT, card_body(), actor=ACTOR, now=T0)
        monkeypatch.setattr(rates, "card_in_use", in_use_on(date(2026, 9, 15)))
        backdated = card_body(effective_from="2026-09-01", unit_price="3", supersede=True)
        with pytest.raises(SpendError) as info:
            await rates.create_card(TENANT, backdated, actor=ACTOR, now=T0)
        assert info.value.code == "restate_required" and "2026-09-01" in info.value.message
        out = await rates.create_card(TENANT, {**backdated, "restate": True}, actor=ACTOR, now=T0)
        # The supersede cut priced history, so a restatement of the predecessor's records is queued.
        assert out["superseded_id"] and out["restate_job_id"]
        job = next(r for r in session.of("spend_jobs") if str(r.id) == out["restate_job_id"])
        assert job.kind == "restate" and job.status == "queued"
        assert job.params["card_ids"] == [out["superseded_id"]] and job.params["provider"] == "openai"
        assert (job.params["start"], job.params["end"]) == ("2026-09-01", "2026-10-01")

    @pytest.mark.asyncio
    async def test_price_edit_refused_when_card_in_use(self, session, monkeypatch):
        future = await rates.create_card(TENANT, card_body(effective_from="2026-12-01"), actor=ACTOR, now=T0)
        monkeypatch.setattr(rates, "card_in_use", in_use_on(date(2026, 12, 2)))
        with pytest.raises(SpendError) as info:
            await rates.update_card(TENANT, uuid.UUID(future["id"]), {"unit_price": "3"}, actor=ACTOR, now=T0)
        assert info.value.code == "card_in_use" and "/correct" in info.value.message

    @pytest.mark.asyncio
    async def test_price_edit_refused_when_card_already_in_force(self, session):
        started = await rates.create_card(TENANT, card_body(), actor=ACTOR, now=T0)
        with pytest.raises(SpendError) as info:
            await rates.update_card(TENANT, uuid.UUID(started["id"]), {"unit_price": "3"}, actor=ACTOR, now=T0)
        assert info.value.code == "card_in_use"
        future = await rates.create_card(
            TENANT, card_body(model_sku="gpt-4o-mini", effective_from="2026-12-01"), actor=ACTOR, now=T0
        )
        out = await rates.update_card(
            TENANT,
            uuid.UUID(future["id"]),
            {
                "unit_price": "3",
                "cached_unit_price": "1.5",
                "batch_discount_pct": "50",
                "volume_tiers": [
                    {"from_quantity": "0", "unit_price": "3"},
                    {"from_quantity": "100", "unit_price": "2"},
                ],
                "tier_mode": "all_units",
                "currency": "EUR",
                "effective_from": "2026-11-15",
            },
            actor=ACTOR,
            now=T0,
        )
        assert out["unit_price"] == "3" and out["cached_unit_price"] == "1.5" and out["currency"] == "EUR"
        assert (
            out["tier_mode"] == "all_units" and len(out["volume_tiers"]) == 2 and out["effective_from"] == "2026-11-15"
        )

    @pytest.mark.asyncio
    async def test_referenced_card_cannot_be_retired_by_patch(self, session, monkeypatch):
        card = await rates.create_card(TENANT, card_body(), actor=ACTOR, now=T0)
        monkeypatch.setattr(rates, "card_in_use", in_use_on(date(2026, 9, 1)))
        with pytest.raises(SpendError) as info:
            await rates.update_card(TENANT, uuid.UUID(card["id"]), {"status": "retired"}, actor=ACTOR, now=T0)
        assert info.value.code == "card_in_use"
        monkeypatch.setattr(rates, "card_in_use", in_use_on(None))
        out = await rates.update_card(TENANT, uuid.UUID(card["id"]), {"status": "retired"}, actor=ACTOR, now=T0)
        assert out["status"] == "retired" and out["retired_at"] == T0.isoformat()
        with pytest.raises(SpendError) as info:
            await rates.update_card(TENANT, uuid.UUID(card["id"]), {"reference": "x"}, actor=ACTOR, now=T0)
        assert info.value.code == "card_in_use"

    @pytest.mark.asyncio
    async def test_correction_retires_and_replaces_with_same_effective_from(self, session):
        old = await rates.create_card(TENANT, card_body(reference="MSA-1"), actor=ACTOR, now=T0)
        with pytest.raises(SpendError) as info:
            await rates.correct_card(
                TENANT, uuid.UUID(old["id"]), {"unit_price": "2"}, reason="short", actor=ACTOR, now=T0
            )
        assert info.value.code == "reason_required"
        out = await rates.correct_card(
            TENANT,
            uuid.UUID(old["id"]),
            {"unit_price": "2.25", "cached_unit_price": "1.1"},
            reason="Contract rate was keyed wrong",
            actor=ACTOR,
            now=T0,
        )
        new = out["card"]
        assert out["retired_id"] == old["id"] and out["restate_job_id"] is None
        assert new["effective_from"] == "2026-01-01" and new["replaces_id"] == old["id"]
        assert new["unit_price"] == "2.25" and new["cached_unit_price"] == "1.1" and new["reference"] == "MSA-1"
        stored = {str(r.id): r for r in session.of("spend_rate_cards")}
        assert stored[old["id"]].status == "retired" and stored[old["id"]].retired_at == T0
        manifest = audit_rows(session, "spend.rate_cards.correct")
        assert len(manifest) == 2 and manifest[0].details["reason"] == "Contract rate was keyed wrong"
        assert [c["before"] is None for c in manifest[1].details["changes"]] == [False, True]
        with pytest.raises(SpendError) as info:
            await rates.correct_card(
                TENANT, uuid.UUID(old["id"]), {}, reason="Already retired card", actor=ACTOR, now=T0
            )
        assert info.value.code == "card_in_use"
        ended = await rates.correct_card(
            TENANT,
            uuid.UUID(new["id"]),
            {"effective_to": "2026-12-01"},
            reason="Contract ends in December",
            actor=ACTOR,
        )
        assert ended["card"]["effective_to"] == "2026-12-01" and ended["card"]["unit_price"] == "2.25"
        with pytest.raises(SpendError) as info:
            await rates.correct_card(
                TENANT,
                uuid.UUID(ended["card"]["id"]),
                {"effective_to": "2025-01-01"},
                reason="A bad end date",
                actor=ACTOR,
            )
        assert info.value.code == "invalid_period"

    @pytest.mark.asyncio
    async def test_effective_to_cannot_cut_referenced_history_without_restate(self, session, monkeypatch):
        card = await rates.create_card(TENANT, card_body(), actor=ACTOR, now=T0)
        monkeypatch.setattr(rates, "card_in_use", in_use_on(date(2026, 9, 20)))
        with pytest.raises(SpendError) as info:
            await rates.update_card(TENANT, uuid.UUID(card["id"]), {"effective_to": "2026-09-01"}, actor=ACTOR, now=T0)
        assert info.value.code == "restate_required"
        out = await rates.update_card(
            TENANT, uuid.UUID(card["id"]), {"effective_to": "2026-09-01", "restate": True}, actor=ACTOR, now=T0
        )
        assert out["effective_to"] == "2026-09-01"
        later = await rates.update_card(
            TENANT, uuid.UUID(card["id"]), {"effective_to": "2026-12-01"}, actor=ACTOR, now=T0
        )
        assert later["effective_to"] == "2026-12-01"
        reopened = await rates.update_card(TENANT, uuid.UUID(card["id"]), {"effective_to": None}, actor=ACTOR, now=T0)
        assert reopened["effective_to"] is None

    @pytest.mark.asyncio
    async def test_card_writes_take_the_key_lock_before_any_row_lock(self, session):
        """A superseding create and an import update the predecessor row while holding the key's lock, so a
        PATCH or a correction must take the key's lock before its row lock too, or the two deadlock."""
        card = await rates.create_card(TENANT, card_body(), actor=ACTOR, now=T0)
        key = locks.rate_card(TENANT, "openai", "llm_tokens", "gpt-4o", "1m_input_tokens", "list")

        def row_locked(statement) -> bool:
            return getattr(statement, "_for_update_arg", None) is not None

        for write in (
            lambda: rates.update_card(TENANT, uuid.UUID(card["id"]), {"reference": "MSA-2"}, actor=ACTOR, now=T0),
            lambda: rates.correct_card(
                TENANT, uuid.UUID(card["id"]), {"unit_price": "2"}, reason="Keyed wrong", actor=ACTOR, now=T0
            ),
        ):
            session.statements.clear()
            session.locks.clear()
            await write()
            lock_at = next(
                i for i, s in enumerate(session.statements) if isinstance(s, TextClause) and "advisory" in str(s)
            )
            row_lock_at = next(i for i, s in enumerate(session.statements) if row_locked(s))
            assert session.locks == [key] and lock_at < row_lock_at
        # The superseding create and the import take no row lock at all: the key's lock serialises them.
        for write in (
            lambda: rates.create_card(
                TENANT, card_body(effective_from="2026-12-01", supersede=True), actor=ACTOR, now=T0
            ),
            lambda: rates.import_cards(
                TENANT,
                [card_body(effective_from="2027-03-01", supersede="true")],
                actor=ACTOR,
                dry_run=False,
                file_sha256="0" * 64,
                now=T0,
            ),
        ):
            session.statements.clear()
            await write()
            assert not any(row_locked(s) for s in session.statements)
        active = sorted(
            (r.effective_from, r.effective_to) for r in session.of("spend_rate_cards") if r.status == "active"
        )
        assert active == [  # both writes cut their open predecessor's row under the key's lock
            (date(2026, 1, 1), date(2026, 12, 1)),
            (date(2026, 12, 1), date(2027, 3, 1)),
            (date(2027, 3, 1), None),
        ]

    @pytest.mark.asyncio
    async def test_card_is_read_again_under_the_key_lock(self, session, monkeypatch):
        """A correction that committed while this write waited for the key's lock is seen: the retired card
        is refused instead of being retired a second time."""
        card = await rates.create_card(TENANT, card_body(), actor=ACTOR, now=T0)
        stored = next(r for r in session.of("spend_rate_cards") if str(r.id) == card["id"])
        original = rates._lock

        async def lock_after_a_concurrent_correction(s, tenant_id, key):
            await original(s, tenant_id, key)
            stored.status, stored.retired_at = "retired", T0

        monkeypatch.setattr(rates, "_lock", lock_after_a_concurrent_correction)
        for write in (
            lambda: rates.correct_card(
                TENANT, uuid.UUID(card["id"]), {}, reason="A second correction", actor=ACTOR, now=T0
            ),
            lambda: rates.update_card(TENANT, uuid.UUID(card["id"]), {"reference": "x"}, actor=ACTOR, now=T0),
        ):
            stored.status, stored.retired_at = "active", None
            with pytest.raises(SpendError) as info:
                await write()
            assert info.value.code == "card_in_use"
        assert len(session.of("spend_rate_cards")) == 1
        with pytest.raises(SpendError) as info:
            await rates.update_card(TENANT, uuid.uuid4(), {"reference": "x"}, actor=ACTOR, now=T0)
        assert info.value.code == "not_found"

    def test_overlaps_is_half_open(self):
        assert rates.overlaps(date(2026, 1, 1), None, date(2026, 6, 1), None)
        assert not rates.overlaps(date(2026, 1, 1), date(2026, 6, 1), date(2026, 6, 1), None)
        assert rates.overlaps(date(2026, 6, 1), date(2026, 7, 1), date(2026, 1, 1), date(2026, 6, 2))


# ---------------------------------------------------------------- commitments and FX


class TestCommitmentsAndFx:
    QUANTITY = {
        "provider": "openai",
        "kind": "quantity",
        "usage_type": "llm_tokens",
        "unit": "1m_input_tokens",
        "committed_quantity": "500",
        "period_start": "2026-10-01",
        "period_end": "2026-11-01",
    }

    @pytest.mark.asyncio
    async def test_commitment_overlap_refused_on_create_and_on_period_change(self, session):
        first = await commitments.create_commitment(TENANT, self.QUANTITY, actor=ACTOR, now=T0)
        assert first["needs_full_recompute"] and first["remaining"] == "500" and first["drawn_quantity"] == "0"
        with pytest.raises(SpendError) as info:
            await commitments.create_commitment(TENANT, {**self.QUANTITY, "period_start": "2026-10-15"}, actor=ACTOR)
        assert info.value.code == "commitment_overlap"
        nov = await commitments.create_commitment(
            TENANT, {**self.QUANTITY, "period_start": "2026-11-01", "period_end": "2026-12-01"}, actor=ACTOR, now=T0
        )
        with pytest.raises(SpendError) as info:
            await commitments.update_commitment(
                TENANT, uuid.UUID(first["id"]), {"period_end": "2026-11-15"}, actor=ACTOR
            )
        assert info.value.code == "commitment_overlap"
        closed = await commitments.update_commitment(
            TENANT, uuid.UUID(nov["id"]), {"status": "closed", "reference": "PO-9"}, actor=ACTOR, now=T0
        )
        assert closed["status"] == "closed" and closed["reference"] == "PO-9"
        extended = await commitments.update_commitment(
            TENANT, uuid.UUID(first["id"]), {"period_end": "2026-11-15"}, actor=ACTOR, now=T0
        )
        assert extended["period_end"] == "2026-11-15"
        with pytest.raises(SpendError) as info:
            await commitments.update_commitment(
                TENANT, uuid.UUID(first["id"]), {"period_end": "2026-09-01"}, actor=ACTOR
            )
        assert info.value.code == "invalid_period"
        with pytest.raises(SpendError) as info:
            await commitments.update_commitment(TENANT, uuid.uuid4(), {"status": "closed"}, actor=ACTOR)
        assert info.value.status == 404
        listed = await commitments.list_commitments(TENANT, provider="openai", active=True)
        assert listed["total"] == 1 and listed["items"][0]["id"] == first["id"]
        assert len(audit_rows(session, "spend.commitments.update")) == 2

    @pytest.mark.asyncio
    async def test_gb_month_quantity_commitment_refused(self, session):
        storage = {**self.QUANTITY, "provider": "platform_storage", "usage_type": "storage", "unit": "gb_month"}
        with pytest.raises(SpendError) as info:
            await commitments.create_commitment(TENANT, storage, actor=ACTOR, now=T0)
        assert info.value.code == "invalid_unit"
        out = await commitments.create_commitment(TENANT, {**storage, "unit": "gb_day"}, actor=ACTOR, now=T0)
        assert out["unit"] == "gb_day"
        assert commitments.quantity_units("storage") == ("gb_day",)

    @pytest.mark.asyncio
    async def test_money_commitments_and_their_shape(self, session):
        money = {
            "provider": "openai",
            "kind": "money",
            "committed_amount": "10000",
            "currency": "USD",
            "period_start": "2026-10-01",
            "period_end": "2027-10-01",
        }
        out = await commitments.create_commitment(TENANT, money, actor=ACTOR, now=T0)
        assert out["committed_amount"] == "10000" and out["remaining"] == "10000" and out["unit"] is None
        for bad, code in (
            ({**money, "unit": "call"}, "invalid_unit"),
            ({**money, "model_sku": "gpt-4o"}, "invalid_sku"),
            ({**money, "overage_unit_price": "1", "overage_currency": "USD"}, "invalid_number"),
            ({**money, "committed_amount": "0"}, "invalid_number"),
            ({**self.QUANTITY, "usage_type": None}, "invalid_unit"),
            ({**self.QUANTITY, "overage_unit_price": "1"}, "invalid_number"),
            ({**self.QUANTITY, "period_end": "2026-10-01"}, "invalid_period"),
            ({**self.QUANTITY, "currency": "USD"}, "invalid_number"),
            ({**money, "committed_quantity": "5"}, "invalid_number"),
        ):
            with pytest.raises(SpendError) as info:
                await commitments.create_commitment(TENANT, bad, actor=ACTOR, now=T0)
            assert info.value.code == code
        overage = await commitments.create_commitment(
            TENANT,
            {**self.QUANTITY, "model_sku": "gpt-4o", "overage_unit_price": "3", "overage_currency": "usd"},
            actor=ACTOR,
            now=T0,
        )
        assert overage["overage_unit_price"] == "3" and overage["overage_currency"] == "USD"

    @pytest.mark.asyncio
    async def test_fx_put_upserts_and_audits_previous_rate(self, session):
        first = await fx.put_rate(
            TENANT, {"rate_date": "2026-10-01", "currency": "usd", "rate_to_inr": "83.1"}, actor=ACTOR
        )
        assert first["previous_rate"] is None and first["outcome"] == "created" and first["source"] == "manual"
        second = await fx.put_rate(
            TENANT,
            {"rate_date": "2026-10-01", "currency": "USD", "rate_to_inr": "83.25", "source": "reference"},
            actor=ACTOR,
            now=T0,
        )
        # Each change queues an FX settlement; the second folds into the one still queued.
        assert first["settle_job_id"] and second["settle_job_id"] == first["settle_job_id"]
        assert second["previous_rate"] == "83.1" and second["rate_to_inr"] == "83.25"
        same = await fx.put_rate(
            TENANT,
            {"rate_date": "2026-10-01", "currency": "USD", "rate_to_inr": "83.25", "source": "reference"},
            actor=ACTOR,
        )
        assert same["outcome"] == "unchanged"
        update = audit_rows(session, "spend.fx_rates.update")[-1].details["changes"][0]
        assert update["before"] == {"rate_to_inr": "83.1", "source": "manual"}
        assert update["after"] == {"rate_to_inr": "83.25", "source": "reference"}
        for bad, code in (
            ({"rate_date": "2026-10-01", "currency": "INR", "rate_to_inr": "1"}, "invalid_currency"),
            ({"rate_date": "2026-10-01", "currency": "USD", "rate_to_inr": "0"}, "invalid_number"),
            ({"rate_date": "2026-10-01", "currency": "USD", "rate_to_inr": "1.123456789"}, "invalid_number"),
            ({"rate_date": "2026-10-01", "currency": "USD", "rate_to_inr": "1", "source": "rumour"}, "invalid_value"),
        ):
            with pytest.raises(SpendError) as info:
                await fx.put_rate(TENANT, bad, actor=ACTOR)
            assert info.value.code == code
        await fx.put_rate(TENANT, {"rate_date": "2026-09-28", "currency": "EUR", "rate_to_inr": "90"}, actor=ACTOR)
        found = await fx.rate_on(session, TENANT, "USD", date(2026, 10, 5))
        assert found.rate_date == date(2026, 10, 1) and found.rate_to_inr == Decimal("83.25")
        assert await fx.rate_on(session, TENANT, "USD", date(2026, 9, 30)) is None
        assert await fx.latest_rate_dates(session, TENANT) == {"USD": date(2026, 10, 1), "EUR": date(2026, 9, 28)}
        listed = await fx.list_rates(TENANT, currency="usd", start=date(2026, 9, 1), end=date(2026, 10, 31))
        assert listed["total"] == 1 and listed["items"][0]["rate_to_inr"] == "83.25"

    @pytest.mark.asyncio
    async def test_fx_writes_take_the_currency_lock_before_reading_the_rate(self, session):
        """``FOR UPDATE`` locks nothing while a new rate's row does not exist, so two writers of the same new
        rate are serialised by the currency's advisory lock instead of both inserting it."""

        def first(predicate) -> int:
            return next(i for i, s in enumerate(session.statements) if predicate(s))

        def is_lock(statement) -> bool:
            return isinstance(statement, TextClause) and "advisory" in str(statement)

        def reads_rates(statement) -> bool:
            return not isinstance(statement, TextClause) and "spend_fx_rates" in str(statement)

        await fx.put_rate(TENANT, {"rate_date": "2026-10-01", "currency": "USD", "rate_to_inr": "83"}, actor=ACTOR)
        assert session.locks == [locks.fx_rate(TENANT, "USD")]
        assert first(is_lock) < first(reads_rates)
        for dry_run in (True, False):
            session.statements.clear()
            session.locks.clear()
            report = await fx.import_rates(
                TENANT,
                [
                    {"rate_date": "2026-10-02", "currency": "USD", "rate_to_inr": "83.1"},
                    {"rate_date": "2026-10-02", "currency": "EUR", "rate_to_inr": "90.2"},
                    {"rate_date": "2026-10-03", "currency": "USD", "rate_to_inr": "83.2"},
                ],
                actor=ACTOR,
                dry_run=dry_run,
                file_sha256="0" * 64,
                now=T0,
            )
            assert report["created"] == 3
            # Every currency of the file is locked once, in sorted order, before the first rate is read.
            assert session.locks == [locks.fx_rate(TENANT, "EUR"), locks.fx_rate(TENANT, "USD")]
            last_lock = max(i for i, s in enumerate(session.statements) if is_lock(s))
            assert last_lock < first(reads_rates)
        assert len(session.of("spend_fx_rates")) == 4


# ---------------------------------------------------------------- pricing loaders


class TestPricingLoaders:
    @pytest.mark.asyncio
    async def test_price_many_loads_cards_once_and_one_rate_per_currency_day(self, session):
        await rates.create_card(TENANT, card_body(source="contract", unit_price="2"), actor=ACTOR, now=T0)
        await rates.create_card(TENANT, card_body(unit="1m_output_tokens", unit_price="8"), actor=ACTOR, now=T0)
        await rates.create_card(
            TENANT,
            card_body(model_sku="gpt-4o", source="list", effective_from="2025-01-01", effective_to="2025-06-01"),
            actor=ACTOR,
            now=T0,
        )
        await fx.put_rate(TENANT, {"rate_date": "2026-10-01", "currency": "USD", "rate_to_inr": "83"}, actor=ACTOR)
        await mappings.put_alias(
            TENANT, {"provider": "openai", "alias": "gpt-4o-2024-08-06", "model_sku": "gpt-4o"}, actor=ACTOR, now=T0
        )
        session.statements.clear()
        on = date(2026, 10, 1)
        batch = [
            pricing.Usage("openai", "llm_tokens", "input_token", Decimal("1000000"), "GPT-4o-2024-08-06", on, on),
            pricing.Usage("openai", "llm_tokens", "output_token", Decimal("500000"), "gpt-4o", on, on),
            pricing.Usage("openai", "llm_tokens", "input_token", Decimal("0"), "gpt-4o", on, on),
            pricing.Usage("acme_ai", "llm_tokens", "input_token", Decimal("10"), "m1", on, on),
        ]
        priced = await pricing.price_many(session, TENANT, batch)
        assert [p.amount for p in priced[:3]] == [Decimal("2.0000000000"), Decimal("4.0000000000"), 0]
        assert priced[0].price_source == "contract" and priced[0].amount_inr == Decimal("166.0000000000")
        assert priced[3].unpriced
        fx_reads = [s for s in session.statements if "spend_fx_rates" in str(s)]
        card_reads = [s for s in session.statements if "spend_rate_cards" in str(s)]
        assert len(fx_reads) == 1 and len(card_reads) == 1
        assert await pricing.price_many(session, TENANT, []) == []
        assert (
            await pricing.load_cards(session, TENANT, providers=[], usage_types=["llm_tokens"], start=on, end=on) == []
        )
        quoted = await pricing.quote(
            TENANT,
            provider="OpenAI",
            usage_type="llm_tokens",
            unit="input_token",
            quantity="1000000",
            model="gpt-4o",
            on=on,
            fx_on=None,
        )
        assert quoted["amount"] == "2.0000000000" and quoted["price_source"] == "contract"
        for bad in ({"usage_type": "vibes"}, {"unit": "ocr_page"}, {"quantity": "-1"}):
            args = {
                "provider": "openai",
                "usage_type": "llm_tokens",
                "unit": "input_token",
                "quantity": "1",
                "model": "",
            }
            args.update(bad)
            with pytest.raises(SpendError):
                await pricing.quote(TENANT, on=on, fx_on=None, **args)

    @pytest.mark.asyncio
    async def test_price_many_falls_back_to_the_called_name_when_the_aliased_sku_has_no_price(self, session):
        await mappings.put_alias(
            TENANT, {"provider": "openai", "alias": "gpt-4o", "model_sku": "acme-gpt-4o"}, actor=ACTOR, now=T0
        )
        on = date(2026, 10, 1)
        (priced,) = await pricing.price_many(
            session,
            TENANT,
            [pricing.Usage("openai", "llm_tokens", "input_token", Decimal("1000000"), "GPT-4o", on, on)],
        )
        assert priced.price_source == "fallback_list" and priced.amount == Decimal("2.5000000000")
        await rates.create_card(TENANT, card_body(model_sku="acme-gpt-4o", unit_price="2"), actor=ACTOR, now=T0)
        (carded,) = await pricing.price_many(
            session,
            TENANT,
            [pricing.Usage("openai", "llm_tokens", "input_token", Decimal("1000000"), "gpt-4o", on, on)],
        )
        assert carded.price_source == "list" and carded.amount == Decimal("2.0000000000")
        aliases = {("openai", "gpt-4o"): "acme-gpt-4o"}
        through = pricing._through_aliases(
            pricing.Usage("openai", "llm_tokens", "input_token", Decimal("1"), " GPT-4o ", on, on), aliases
        )
        assert (through.model, through.called_model) == ("acme-gpt-4o", "gpt-4o")
        # A caller that applied the aliases already passes the called name; it is kept.
        again = pricing._through_aliases(through, aliases)
        assert (again.model, again.called_model) == ("acme-gpt-4o", "gpt-4o")
        plain = pricing._through_aliases(
            pricing.Usage("openai", "llm_tokens", "input_token", Decimal("1"), "o1", on, on), aliases
        )
        assert (plain.model, plain.called_model) == ("o1", "")


# ---------------------------------------------------------------- imports


def _write(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


class TestImports:
    def test_parse_rows_csv_bom_latin1_json_and_missing_columns(self, tmp_path):
        req, opt = ("code", "name", "kind"), ("parent_code", "active")
        csv_path = _write(tmp_path, "a.csv", "﻿Code , NAME,kind,extra\nCC-1,Ops,team,zz\nCC-2,,department\n".encode())
        rows = imports.parse_rows(csv_path, filename="a.csv", content_type="text/csv", required=req, optional=opt)
        assert rows == [
            {"code": "CC-1", "name": "Ops", "kind": "team"},
            {"code": "CC-2", "name": "", "kind": "department"},
        ]
        latin = _write(tmp_path, "b.csv", "code,name,kind,parent_code\nCC-3,Caf\xe9,team,\n".encode("latin-1"))
        rows = imports.parse_rows(latin, filename="b.csv", content_type="", required=req, optional=opt)
        assert rows == [{"code": "CC-3", "name": "Café", "kind": "team", "parent_code": ""}]
        body = {"rows": [{"code": "CC-4", "name": "Ops", "kind": "team", "active": False, "tiers": [1]}]}
        json_path = _write(tmp_path, "c.json", json.dumps(body).encode())
        rows = imports.parse_rows(json_path, filename="c.json", content_type="", required=req, optional=opt)
        assert rows == [{"code": "CC-4", "name": "Ops", "kind": "team", "active": "false"}]
        typed = _write(tmp_path, "upload", json.dumps([{"code": 1, "name": None, "kind": "team"}]).encode())
        rows = imports.parse_rows(typed, filename="upload", content_type="application/json", required=req, optional=opt)
        assert rows == [{"code": "1", "name": "", "kind": "team"}]
        nested = _write(
            tmp_path, "d.json", json.dumps([{"code": "x", "name": "y", "kind": "z", "parent_code": {"a": 1}}]).encode()
        )
        assert (
            imports.parse_rows(nested, filename="d.json", content_type="", required=req, optional=opt)[0]["parent_code"]
            == '{"a":1}'
        )
        missing = _write(tmp_path, "e.csv", b"code,name\nCC-1,Ops\n")
        with pytest.raises(SpendError) as info:
            imports.parse_rows(missing, filename="e.csv", content_type="", required=req, optional=opt)
        assert (info.value.status, info.value.code) == (400, "missing_columns")
        empty_json = _write(tmp_path, "f.json", b"[]")
        with pytest.raises(SpendError) as info:
            imports.parse_rows(empty_json, filename="f.json", content_type="", required=req, optional=opt)
        assert info.value.code == "missing_columns"
        assert len(imports.file_sha256(csv_path)) == 64

    def test_parse_rows_stops_counting_at_the_row_limit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(imports, "MAX_IMPORT_ROWS", 3)
        produced = []
        original = imports.iter_rows

        def counting(path, **kw):
            for row in original(path, **kw):
                produced.append(row)
                yield row

        monkeypatch.setattr(imports, "iter_rows", counting)
        path = _write(tmp_path, "many.csv", b"code\n" + b"".join(b"%d\n" % i for i in range(1000)))
        with pytest.raises(SpendError) as info:
            imports.parse_rows(path, filename="many.csv", content_type="", required=("code",), optional=())
        assert (info.value.status, info.value.code) == (413, "too_many_rows")
        assert len(produced) == 4  # stopped at the limit + 1, not after reading 1000 rows

    def test_parse_rows_maps_csv_and_json_parser_errors_to_bad_file(self, tmp_path):
        cases = {
            "bad.json": b"{not json",
            "deep.json": b"[" * 200_000 + b"]" * 200_000,
            "scalar.json": b"42",
            "rows.json": b'{"rows": [1, 2]}',
            "wrong.json": b'{"lines": []}',
            "big.csv": b"code\n" + b"x" * (200 * 1024) + b"\n",
        }
        for name, content in cases.items():
            path = _write(tmp_path, name, content)
            with pytest.raises(SpendError) as info:
                imports.parse_rows(path, filename=name, content_type="", required=("code",), optional=())
            assert (info.value.status, info.value.code) == (400, "bad_file"), name
        lines = _write(tmp_path, "inv.json", b'{"lines": [{"code": "a"}]}')
        rows = imports.parse_rows(
            lines,
            filename="inv.json",
            content_type="",
            required=("code",),
            optional=(),
            envelope_keys=("rows", "lines"),
        )
        assert rows == [{"code": "a"}]

    def test_report_numbers_rows_from_two(self):
        report = imports.new_report(dry_run=True, received=3)
        imports.reject(report, row=2, key="k" * 300, reason="invalid_code")
        assert report["rejected"] == [{"row": 2, "key": "k" * 200, "reason": "invalid_code"}]
        assert report["dry_run"] is True and report["created"] == 0

    def test_json_import_row_limit_is_checked_before_rows_are_copied(self, tmp_path, monkeypatch):
        monkeypatch.setattr(imports, "MAX_IMPORT_ROWS", 3)
        checked = []
        monkeypatch.setattr(imports, "_check_columns", lambda present, required: checked.append(present))
        # Empty objects lack every column; the size refusal comes first, before any row dict is built.
        for content in (b"[" + b",".join([b"{}"] * 1000) + b"]", b'{"rows": [' + b",".join([b"{}"] * 4) + b"]}"):
            path = _write(tmp_path, "tiny.json", content)
            with pytest.raises(SpendError) as info:
                imports.parse_rows(path, filename="tiny.json", content_type="", required=("code",), optional=())
            assert (info.value.status, info.value.code) == (413, "too_many_rows")
        assert checked == []
        exact = _write(tmp_path, "three.json", json.dumps([{"Code": "a"}, {"code": "b"}, {"code": "c"}]).encode())
        rows = imports.parse_rows(exact, filename="three.json", content_type="", required=("code",), optional=())
        assert [r["code"] for r in rows] == ["a", "b", "c"] and checked == [{"code"}]

    @pytest.mark.asyncio
    async def test_json_import_numbers_keep_every_decimal_place(self, session, tmp_path):
        """JSON numbers are read as decimals: a binary float would silently change a precise price or tier."""
        body = (
            b'[{"provider": "openai", "usage_type": "llm_tokens", "model_sku": "gpt-4o", "unit": "1m_input_tokens",'
            b' "unit_price": 999999999.1234567891, "currency": "USD", "effective_from": "2026-12-01",'
            b' "source": "contract", "volume_tiers": [{"from_quantity": 0, "unit_price": 2.5},'
            b' {"from_quantity": 1000000000000.123456, "unit_price": 0.1234567891}]}]'
        )
        path = _write(tmp_path, "cards.json", body)
        rows = imports.parse_rows(
            path, filename="cards.json", content_type="", required=rates.IMPORT_REQUIRED, optional=rates.IMPORT_OPTIONAL
        )
        assert rows[0]["unit_price"] == "999999999.1234567891"
        assert "1000000000000.123456" in rows[0]["volume_tiers"]
        report = await rates.import_cards(TENANT, rows, actor=ACTOR, dry_run=False, file_sha256="", now=T0)
        assert report["created"] == 1 and report["rejected"] == []
        (stored,) = session.of("spend_rate_cards")
        assert stored.unit_price == Decimal("999999999.1234567891")
        assert stored.volume_tiers == [
            {"from_quantity": "0", "unit_price": "2.5"},
            {"from_quantity": "1000000000000.123456", "unit_price": "0.1234567891"},
        ]
        # A tiers cell given as JSON text (CSV) keeps its decimals too.
        tiers = rates.validate_tiers('[{"from_quantity": 0, "unit_price": 999999999.1234567891}]', mode="graduated")
        assert tiers == [{"from_quantity": "0", "unit_price": "999999999.1234567891"}]
        for bad in ("[" * 100_000 + "]" * 100_000, "[{"):
            with pytest.raises(SpendError) as info:
                rates.validate_tiers(bad, mode="graduated")
            assert (info.value.status, info.value.code) == (422, "invalid_number")

    @pytest.mark.asyncio
    async def test_org_import_two_pass_parents_reports_created_updated_rejected(self, session):
        await node("G", "group")
        owner = uuid.uuid4()
        session.users = {(str(TENANT), str(owner))}
        rows = [
            {"code": "cc-9", "name": "Cards CC", "kind": "cost_centre", "parent_code": "D-1"},  # child before parent
            {"code": "D-1", "name": "Cards", "kind": "department", "parent_code": "G", "owner_user_id": str(owner)},
            {"code": "G", "name": "Group renamed", "kind": "group", "parent_code": ""},
            {"code": "X", "name": "Bad", "kind": "division"},
            {"code": "Y", "name": "Orphan", "kind": "team", "parent_code": "NOWHERE"},
            {"code": "D-1", "name": "Again", "kind": "department"},
            {"code": "Z", "name": "Owner", "kind": "group", "owner_user_id": str(uuid.uuid4())},
            {"code": "W", "name": "Flag", "kind": "group", "active": "perhaps"},
            {"code": "V", "name": "Bad owner", "kind": "group", "owner_user_id": "nope"},
            {"code": "U", "name": "Bad parent code", "kind": "team", "parent_code": "-x"},
        ]
        report = await org.import_nodes(TENANT, rows, actor=ACTOR, dry_run=False, file_sha256="ab" * 32, now=T0)
        assert (report["created"], report["updated"], report["unchanged"]) == (2, 1, 0)
        assert [(r["row"], r["reason"]) for r in report["rejected"]] == [
            (5, "invalid_kind"),
            (6, "unknown_parent"),
            (7, "duplicate_in_file"),
            (8, "invalid_owner"),
            (9, "invalid_text"),
            (10, "invalid_owner"),
            (11, "unknown_parent"),
        ]
        by_code = {r.code: r for r in session.of("spend_org_nodes")}
        assert by_code["CC-9"].parent_id == by_code["D-1"].id and by_code["D-1"].parent_id == by_code["G"].id
        assert by_code["G"].name == "Group renamed" and by_code["D-1"].owner_user_id == owner
        manifest = audit_rows(session, "spend.org_node.import")
        assert manifest[0].details["file_sha256"] == "ab" * 32 and manifest[0].details["changed"] == 3
        again = await org.import_nodes(TENANT, rows[:3], actor=ACTOR, dry_run=False, file_sha256="cd" * 32, now=T0)
        assert (again["created"], again["updated"], again["unchanged"]) == (0, 0, 3)

    @pytest.mark.asyncio
    async def test_org_import_rejects_child_of_rejected_parent(self, session):
        rows = [
            {"code": "G", "name": "Group", "kind": "group"},
            {"code": "T1", "name": "Team", "kind": "team", "parent_code": "G"},  # a team cannot sit under a group
            {"code": "T2", "name": "Sub team", "kind": "team", "parent_code": "T1"},
            {"code": "C1", "name": "Loop a", "kind": "group", "parent_code": "C2"},
            {"code": "C2", "name": "Loop b", "kind": "group", "parent_code": "C1"},
        ]
        report = await org.import_nodes(TENANT, rows, actor=ACTOR, dry_run=False, file_sha256="", now=T0)
        assert report["created"] == 1
        assert [(r["row"], r["reason"]) for r in report["rejected"]] == [
            (3, "invalid_parent_kind"),
            (4, "parent_rejected"),
            (5, "cycle"),
            (6, "cycle"),
        ]

    @pytest.mark.asyncio
    async def test_org_import_kind_change_must_keep_existing_children_nested(self, session):
        await node("G", "group")
        await node("D", "department", parent="G")
        await node("T", "team", parent="D")
        report = await org.import_nodes(
            TENANT,
            [{"code": "D", "name": "Now a BU", "kind": "business_unit"}],
            actor=ACTOR,
            dry_run=False,
            file_sha256="",
        )
        assert report["rejected"] == [{"row": 2, "key": "D", "reason": "invalid_parent_kind"}]
        session.fail_flush_for = {"NEW"}
        report = await org.import_nodes(
            TENANT, [{"code": "NEW", "name": "Fails", "kind": "group"}], actor=ACTOR, dry_run=False, file_sha256=""
        )
        assert report["rejected"][0]["reason"] == "write_failed" and "NEW" not in {
            r.code for r in session.of("spend_org_nodes")
        }

    @pytest.mark.asyncio
    async def test_org_import_depth_is_bounded(self, session):
        rows = [{"code": "L0", "name": "Root", "kind": "group"}]
        rows += [
            {"code": f"L{i}", "name": f"Level {i}", "kind": "group", "parent_code": f"L{i - 1}"} for i in range(1, 19)
        ]
        report = await org.import_nodes(TENANT, rows, actor=ACTOR, dry_run=False, file_sha256="", now=T0)
        assert report["created"] == vocab.MAX_DEPTH + 1
        # L17 and L18 are both deeper than the bound: each is the deepest row of its own path
        assert [(r["row"], r["reason"]) for r in report["rejected"]] == [(19, "cycle"), (20, "cycle")]

    @pytest.mark.asyncio
    async def test_org_import_dry_run_writes_nothing(self, session):
        rows = [
            {"code": "G", "name": "Group", "kind": "group"},
            {"code": "D", "name": "Dept", "kind": "department", "parent_code": "G"},
        ]
        report = await org.import_nodes(TENANT, rows, actor=ACTOR, dry_run=True, file_sha256="", now=T0)
        assert report["dry_run"] and report["created"] == 2 and report["rejected"] == []
        assert session.of("spend_org_nodes") == [] and audit_rows(session) == []

    @pytest.mark.asyncio
    async def test_mapping_and_fx_imports(self, session):
        await node("G", "group")
        agent = str(uuid.uuid4())
        rows = [
            {"source_type": "agent", "source_ref": agent, "org_node_code": "G"},
            {"source_type": "agent", "source_ref": agent, "org_node_code": "G"},
            {"source_type": "agent", "source_ref": str(uuid.uuid4()), "org_node_code": "NOPE"},
            {"source_type": "application", "source_ref": "chat", "use_case": "Support"},
            {"source_type": "application", "source_ref": "telepathy", "use_case": "x"},
        ]
        report = await mappings.import_mappings(TENANT, rows, actor=ACTOR, dry_run=False, file_sha256="", now=T0)
        assert report["created"] == 2
        assert [(r["row"], r["reason"]) for r in report["rejected"]] == [
            (3, "duplicate_in_file"),
            (4, "invalid_reference"),
            (6, "invalid_reference"),
        ]
        dry = await mappings.import_mappings(TENANT, rows[3:4], actor=ACTOR, dry_run=True, file_sha256="", now=T0)
        assert dry["unchanged"] == 1
        # an absent column keeps the field; an empty cell clears it; a row left with no target is refused
        kept = await mappings.import_mappings(
            TENANT,
            [
                {"source_type": "application", "source_ref": "chat", "product_line": "Cards"},
                {"source_type": "agent", "source_ref": agent, "org_node_code": ""},
            ],
            actor=ACTOR,
            dry_run=False,
            file_sha256="",
            now=T0,
        )
        assert kept["updated"] == 1 and [r["reason"] for r in kept["rejected"]] == ["invalid_reference"]
        chat = next(r for r in session.of("spend_source_mappings") if r.source_ref == "chat")
        assert (chat.product_line, chat.use_case, chat.active) == ("cards", "support", True)
        fx_rows = [
            {"rate_date": "2026-10-01", "currency": "USD", "rate_to_inr": "83"},
            {"rate_date": "2026-10-01", "currency": "usd", "rate_to_inr": "84"},
            {"rate_date": "bad", "currency": "USD", "rate_to_inr": "83"},
        ]
        report = await fx.import_rates(TENANT, fx_rows, actor=ACTOR, dry_run=False, file_sha256="", now=T0)
        assert report["created"] == 1 and [r["reason"] for r in report["rejected"]] == [
            "duplicate_in_file",
            "invalid_date",
        ]
        assert session.of("spend_fx_rates")[0].source == "import"
        dry = await fx.import_rates(
            TENANT,
            [{"rate_date": "2026-10-02", "currency": "USD", "rate_to_inr": "83"}],
            actor=ACTOR,
            dry_run=True,
            file_sha256="",
        )
        assert dry["created"] == 1 and len(session.of("spend_fx_rates")) == 1

    @pytest.mark.asyncio
    async def test_rate_card_import_rejects_overlap_within_file(self, session, monkeypatch):
        rows = [
            {**card_body(), "unit_price": "2.5"},
            {**card_body(effective_from="2026-06-01"), "unit_price": "3"},  # overlaps the open row above
            {
                **card_body(model_sku="gpt-4o-mini", effective_from="2026-12-01"),
                "volume_tiers": '[{"from_quantity": 0, "unit_price": 1}]',
            },
            {**card_body(), "unit_price": "9"},  # duplicate key and start in the file
            {**card_body(usage_type="vibes")},
            {**card_body(model_sku="o1"), "supersede": "maybe"},
        ]
        report = await rates.import_cards(TENANT, rows, actor=ACTOR, dry_run=False, file_sha256="ef" * 32, now=T0)
        assert report["created"] == 2
        assert [(r["row"], r["reason"]) for r in report["rejected"]] == [
            (3, "rate_card_overlap"),
            (5, "duplicate_in_file"),
            (6, "invalid_unit"),
            (7, "invalid_boolean"),
        ]
        identical = await rates.import_cards(TENANT, rows[:1], actor=ACTOR, dry_run=False, file_sha256="", now=T0)
        assert (identical["unchanged"], identical["updated"], identical["created"]) == (1, 0, 0)
        again = [
            {**card_body(model_sku="gpt-4o-mini", effective_from="2026-12-01"), "unit_price": "2", "reference": "MSA"},
            {**card_body(), "unit_price": "2.75"},  # a price edit on a card in force
        ]
        report = await rates.import_cards(TENANT, again, actor=ACTOR, dry_run=False, file_sha256="", now=T0)
        assert (report["unchanged"], report["updated"]) == (0, 1)
        assert report["rejected"] == [
            {"row": 3, "key": "openai:llm_tokens:gpt-4o:1m_input_tokens:list", "reason": "card_in_use"}
        ]
        dry = await rates.import_cards(
            TENANT, [card_body(model_sku="o1-mini")], actor=ACTOR, dry_run=True, file_sha256="", now=T0
        )
        assert dry["created"] == 1 and not any(r.model_sku == "o1-mini" for r in session.of("spend_rate_cards"))

    @pytest.mark.asyncio
    async def test_import_update_is_overlap_checked(self, session):
        await rates.create_card(
            TENANT, card_body(effective_from="2026-12-01", effective_to="2027-06-01"), actor=ACTOR, now=T0
        )
        await rates.create_card(TENANT, card_body(effective_from="2027-06-01", unit_price="3"), actor=ACTOR, now=T0)
        rows = [{**card_body(effective_from="2026-12-01"), "effective_to": "2027-07-01"}]
        report = await rates.import_cards(TENANT, rows, actor=ACTOR, dry_run=False, file_sha256="", now=T0)
        assert report["rejected"][0]["reason"] == "rate_card_overlap" and report["updated"] == 0


# ---------------------------------------------------------------- audit


class TestAudit:
    @pytest.mark.asyncio
    async def test_every_write_adds_a_signed_audit_row(self, session):
        await node("G", "group")
        await mappings.put_mapping(
            TENANT, {"source_type": "application", "source_ref": "chat", "use_case": "x"}, actor=ACTOR
        )
        await mappings.put_alias(TENANT, {"provider": "openai", "alias": "a1", "model_sku": "gpt-4o"}, actor=ACTOR)
        card = await rates.create_card(TENANT, card_body(effective_from="2026-12-01"), actor=ACTOR, now=T0)
        await rates.update_card(TENANT, uuid.UUID(card["id"]), {"unit_price": "3"}, actor=ACTOR, now=T0)
        await commitments.create_commitment(TENANT, TestCommitmentsAndFx.QUANTITY, actor=ACTOR)
        await fx.put_rate(TENANT, {"rate_date": "2026-10-01", "currency": "USD", "rate_to_inr": "83"}, actor=ACTOR)
        rows = audit_rows(session)
        assert [r.event_type for r in rows] == [
            "spend.org_node.create",
            "spend.mappings.create",
            "spend.model_aliases.put",
            "spend.rate_cards.create",
            "spend.rate_cards.update",
            "spend.commitments.create",
            "spend.job.enqueue",  # the commitment's drawdown recompute
            "spend.fx_rates.create",
            "spend.job.enqueue",  # the new rate's FX settlement
        ]
        for row in rows:
            assert row.tenant_id == TENANT and row.actor_id == ACTOR and row.actor_type == "user"
            assert row.outcome == "success" and verify_audit_row(row)
            json.dumps(row.details)  # identifiers, codes and values only, all JSON

    @pytest.mark.asyncio
    async def test_import_manifest_records_every_changed_row_with_before_and_after(self, session):
        start = date(2025, 1, 1).toordinal()
        rows = [
            {"rate_date": date.fromordinal(start + i).isoformat(), "currency": "USD", "rate_to_inr": "80"}
            for i in range(450)
        ]
        await fx.import_rates(TENANT, rows, actor=ACTOR, dry_run=False, file_sha256="aa" * 32, now=T0)
        manifest = audit_rows(session, "spend.fx_rates.import")
        assert len(manifest) == 4 and manifest[0].details["parts"] == 3 and manifest[0].details["created"] == 450
        assert sum(len(r.details["changes"]) for r in manifest[1:]) == 450
        assert {r.resource_id for r in manifest} == {"aa" * 32}
        changed = [{**row, "rate_to_inr": "81"} for row in rows[:2]]
        await fx.import_rates(TENANT, changed, actor=ACTOR, dry_run=False, file_sha256="bb" * 32, now=T0)
        part = audit_rows(session, "spend.fx_rates.import")[-1].details
        assert [c["before"]["rate_to_inr"] for c in part["changes"]] == ["80", "80"]
        assert [c["after"]["rate_to_inr"] for c in part["changes"]] == ["81", "81"]

    def test_jsonable_and_resource_ids(self):
        out = audit.jsonable(
            {"d": Decimal("1.50"), "day": date(2026, 1, 1), "at": T0, "id": uuid.UUID(int=1), "l": (1, 2)}
        )
        assert out == {"d": "1.50", "day": "2026-01-01", "at": T0.isoformat(), "id": str(uuid.UUID(int=1)), "l": [1, 2]}
        rows = audit.audit_changes(
            TENANT, actor_id=ACTOR, action="x.import", resource_type="r", changes=[], summary={}, file_sha256=None
        )
        assert len(rows) == 1 and rows[0].resource_id == "manifest" and rows[0].details["parts"] == 0


# ---------------------------------------------------------------- migration and models


def _migration():
    spec = importlib.util.spec_from_file_location("_v6_z79_spend_reference", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Op:
    def __init__(self):
        self.sql: list[str] = []

    def execute(self, sql):
        self.sql.append(" ".join(str(sql).split()))


def _models():
    from core.models.spend import (
        SpendCommitment,
        SpendFxRate,
        SpendModelAlias,
        SpendOrgNode,
        SpendRateCard,
        SpendSourceMapping,
    )

    return (SpendOrgNode, SpendSourceMapping, SpendRateCard, SpendModelAlias, SpendCommitment, SpendFxRate)


def _squash(text: str) -> str:
    text = " ".join(text.split())
    return re.sub(r"\( ", "(", re.sub(r" \)", ")", text))


def _checks(sql: str) -> list[str]:
    """The body of every CHECK (...) in ``sql``, with balanced parentheses."""
    out = []
    for match in re.finditer(r"(?<!WITH )CHECK \(", sql):
        depth, start = 1, match.end()
        index = start
        while depth:
            depth += {"(": 1, ")": -1}.get(sql[index], 0)
            index += 1
        out.append(sql[start : index - 1])
    return out


class TestMigration:
    def test_migration_revision_chain_and_length(self):
        migration = _migration()
        assert migration.revision == "v6z79_spend_reference" and len(migration.revision) <= 32
        assert migration.down_revision == "v6z78_security_release_merge"
        assert migration.branch_labels is None and migration.depends_on is None
        assert migration.TABLES == tuple(model.__tablename__ for model in _models())

    def test_migration_forces_row_level_security_on_every_table(self, monkeypatch):
        migration = _migration()
        op = _Op()
        monkeypatch.setattr(migration, "op", op)
        migration.upgrade()
        for table in migration.TABLES:
            assert f"CREATE TABLE IF NOT EXISTS {table}" in " ".join(op.sql)
            assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;" in op.sql
            assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;" in op.sql
            policy = next(s for s in op.sql if s.startswith(f"CREATE POLICY {table}_tenant_isolation"))
            assert "USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))" in policy
            assert "WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true))" in policy
        assert not any(
            s.startswith("ALTER TABLE") and "ROW LEVEL" not in s for s in op.sql
        )  # no existing table altered
        down = _Op()
        monkeypatch.setattr(migration, "op", down)
        migration.downgrade()
        assert down.sql == [f"DROP TABLE IF EXISTS {t};" for t in reversed(migration.TABLES)]

    def test_every_foreign_key_has_a_leading_index(self):
        text = MIGRATION.read_text(encoding="utf-8")
        for model in _models():
            table = model.__table__
            leading = [tuple(c.name for c in index.columns) for index in table.indexes]
            for fk in table.foreign_key_constraints:
                columns = tuple(c.name for c in fk.columns)
                assert any(cols[: len(columns)] == columns for cols in leading), (table.name, columns)
        for name, cols in (
            ("ix_spend_org_nodes_parent", "spend_org_nodes(tenant_id, parent_id)"),
            ("ix_spend_org_nodes_owner", "spend_org_nodes(owner_user_id)"),
            ("ix_spend_source_mappings_org_node", "spend_source_mappings(tenant_id, org_node_id)"),
            ("ix_spend_rate_cards_replaces", "spend_rate_cards(tenant_id, replaces_id)"),
        ):
            assert f"{name} ON {cols}" in " ".join(text.split()).replace('" "', "")

    def test_spend_foreign_keys_are_tenant_composite(self):
        for model in _models():
            for fk in model.__table__.foreign_key_constraints:
                target = fk.referred_table.name
                columns = [c.name for c in fk.columns]
                if target == "users":
                    assert columns == ["owner_user_id"] and fk.ondelete == "SET NULL"
                    continue
                assert target.startswith("spend_") and columns[0] == "tenant_id" and len(columns) == 2
                assert [e.column.name for e in fk.elements] == ["tenant_id", "id"] and fk.ondelete == "RESTRICT"
                assert any(
                    {c.name for c in u.columns} == {"tenant_id", "id"}
                    for u in fk.referred_table.constraints
                    if u.__class__.__name__ == "UniqueConstraint"
                )

    def test_models_compile_to_the_migration_ddl(self):
        sql = _squash(MIGRATION.read_text(encoding="utf-8").replace('"\n        "', "").replace('" "', " "))
        ddl = ""
        for model in _models():
            ddl += str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
            for index in model.__table__.indexes:
                ddl += str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        ddl = _squash(ddl)
        names = set(re.findall(r"CONSTRAINT (\w+)", sql)) | set(re.findall(r"INDEX IF NOT EXISTS (\w+)", sql))
        assert len(names) > 40
        for name in names:
            assert name in ddl, name
        for body in _checks(sql):
            assert f"CHECK ({body})" in ddl, body
        for default in set(re.findall(r"DEFAULT ('\[\]'::jsonb|'[a-z]*'|true|0|now\(\))", sql)):
            assert f"DEFAULT {default}" in ddl, default
        assert "WHERE status = 'active'" in ddl
        for model in _models():
            table_sql = sql[sql.index(f"CREATE TABLE IF NOT EXISTS {model.__tablename__} (") :]
            table_sql = table_sql[: table_sql.index(");")]
            for column in model.__table__.columns:
                assert re.search(rf"\b{column.name} ", table_sql), (model.__tablename__, column.name)
            for line in re.findall(r"(\w+) [A-Z(),0-9 ]+? NOT NULL DEFAULT ([^,]+)", table_sql):
                assert model.__table__.c[line[0]].server_default is not None, line
        assert str(_models()[2].__table__.c.currency.type) == "CHAR(3)"

    def test_every_tenant_table_is_named_by_an_rls_migration(self):
        from tests.unit.test_rls_tenant_coverage import _rls_tables_declared_in_migrations

        covered = _rls_tables_declared_in_migrations()
        assert {model.__tablename__ for model in _models()} <= covered


# ---------------------------------------------------------------- settings


class TestSettings:
    def test_spend_settings_validator_checks_zones_only_while_on(self):
        assert Settings(spend_intelligence_enabled=False, spend_reporting_timezone="Nowhere/Land").spend_sweeps_enabled
        on = Settings(
            spend_intelligence_enabled=True,
            spend_reporting_timezone="Asia/Kolkata",
            spend_provider_billing_timezones_json='{"openai": "UTC", "gemini": "America/Los_Angeles"}',
        )
        assert on.spend_intelligence_enabled
        for over in (
            {"spend_reporting_timezone": "Nowhere/Land"},
            {"spend_reporting_timezone": ""},
            {"spend_provider_billing_timezones_json": "{not json"},
            {"spend_provider_billing_timezones_json": '["UTC"]'},
            {"spend_provider_billing_timezones_json": '{"openai": "Mars/Base"}'},
            {"spend_provider_billing_timezones_json": '{"": "UTC"}'},
            {"spend_provider_billing_timezones_json": '{"openai": 5}'},
        ):
            with pytest.raises(ValidationError) as info:
                Settings(spend_intelligence_enabled=True, **over)
            assert "spend_" in str(info.value)

    def test_the_flag_defaults_off(self):
        assert Settings.model_fields["spend_intelligence_enabled"].default is False
        assert Settings.model_fields["spend_reporting_timezone"].default == "Asia/Kolkata"
