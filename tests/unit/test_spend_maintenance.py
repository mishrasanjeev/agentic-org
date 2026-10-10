# SPDX-License-Identifier: Apache-2.0
"""Rollups, coverage, the maintenance jobs (FX settlement, restatement, re-attribution, commitment recompute),
the ledger comparison and backfill."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from core.models.model_gateway_record import ModelGatewayRecord
from core.models.spend import SpendCommitment, SpendFxRate, SpendOrgNode, SpendSourceMapping
from core.spend import access, commitments, ledgers, locks, maintenance, meter, rollups
from core.spend.errors import SpendError
from tests.unit.spend_usage_fakes import ACTOR, T0, TENANT, install
from tests.unit.test_spend_usage import card, event, hints

TID = str(TENANT)
DAY = date(2026, 10, 1)
NEXT_DAY = date(2026, 10, 2)
ADMIN_VIEW = access.ReadView(agent_clause=None, show_user_ids=True, commercial=True)


@pytest.fixture
def store(monkeypatch):
    return install(monkeypatch)


async def write(store, *events, now=T0):
    return await meter.write_events(store, TENANT, list(events), now=now)


def rollup_state(store) -> dict:
    out = {}
    for row in store.of("spend_usage_rollups"):
        out[(row.day, row.dims_hash)] = tuple(
            Decimal(getattr(row, f)) for f in (*rollups.SUM_FIELDS, *rollups.COUNT_FIELDS)
        )
    return out


def fx_rate(store, day: date, rate: str, currency: str = "USD") -> None:
    store.add(
        SpendFxRate(
            id=uuid.uuid4(), tenant_id=TENANT, rate_date=day, currency=currency, rate_to_inr=Decimal(rate),
            source="reference",
        )
    )  # fmt: skip


def records(store):
    return sorted(store.of("spend_usage_records"), key=lambda r: (r.event_time, r.id))


class TestRollups:
    @pytest.mark.asyncio
    async def test_rollup_incremental_equals_rebuild(self, store):
        store.add(card())
        fx_rate(store, DAY, "83")
        await write(
            store,
            event(),
            event(unit="output_token", calls=0),
            event(provider="unknownco", model="x"),
            event(event_time=T0 + timedelta(days=1), quantity=Decimal("7")),
            event(event_time=T0 + timedelta(days=1), hints=hints(application="chat")),
        )
        incremental = rollup_state(store)
        assert {day for day, _h in incremental} == {DAY, NEXT_DAY}
        for day in (DAY, NEXT_DAY):
            await rollups.rebuild_day(TENANT, day)
        assert rollup_state(store) == incremental

    @pytest.mark.asyncio
    async def test_rebuild_is_idempotent_and_day_scoped(self, store):
        await write(store, event(), event(event_time=T0 + timedelta(days=1)))
        other_day = [r for r in store.of("spend_usage_rollups") if r.day == NEXT_DAY]
        first = await rollups.rebuild_day(TENANT, DAY)
        second = await rollups.rebuild_day(TENANT, DAY)
        assert first == second == {"rows": 1, "records": 1}
        assert [r for r in store.of("spend_usage_rollups") if r.day == NEXT_DAY] == other_day

    @pytest.mark.asyncio
    async def test_rebuild_locks_one_day_exclusively(self, store):
        await write(store, event())
        store.locks.clear()
        before = len(store.statements)
        await rollups.rebuild_day(TENANT, DAY)
        assert store.locks == [locks.rollup_day(TENANT, DAY)]
        texts = [str(s) for s in store.statements[before:] if type(s).__name__ == "TextClause"]
        assert any("pg_advisory_xact_lock(hashtextextended" in t for t in texts)
        assert not any("lock_shared" in t for t in texts)

    @pytest.mark.asyncio
    async def test_rebuild_range_over_31_days_refused(self, store):
        with pytest.raises(SpendError) as info:
            await rollups.rebuild(TENANT, start=DAY, end=DAY + timedelta(days=31), actor=ACTOR, now=T0)
        assert info.value.code == "range_too_long"
        with pytest.raises(SpendError) as info:
            rollups.check_range(NEXT_DAY, DAY, max_days=31)
        assert info.value.code == "invalid_period"
        out = await rollups.rebuild(TENANT, start=DAY, end=NEXT_DAY, actor=ACTOR, now=T0)
        assert out == {"days": 2, "rows": 0, "records": 0}
        assert [r.event_type for r in store.of("audit_log")] == ["spend.rollups.rebuild"]

    @pytest.mark.asyncio
    async def test_lock_keys_come_from_one_helper(self, store):
        await write(store, event())
        writer_keys = [k for k in store.locks if k.startswith("spend:rollup:")]
        store.locks.clear()
        await rollups.rebuild_day(TENANT, DAY)
        assert writer_keys == store.locks == [f"spend:rollup:{TENANT}:{DAY.isoformat()}"]

    @pytest.mark.asyncio
    async def test_deltas_move_records_between_rows_and_drop_empty_rows(self, store):
        await write(store, event())
        row = store.of("spend_usage_records")[0]
        before = maintenance.record_dict(row)
        row.org_node_id, row.attribution_path, row.unattributed_reason = uuid.uuid4(), "agent_mapping", None
        deltas: rollups.Deltas = {}
        rollups.move(deltas, before, maintenance.record_dict(row))
        await rollups.apply_deltas(store, TENANT, deltas)
        rows = store.of("spend_usage_rollups")
        assert len(rows) == 1 and rows[0].org_node_id == row.org_node_id and rows[0].record_count == 1
        await rollups.apply_deltas(store, TENANT, {})
        assert rollups.ZERO_DELTA.is_zero() and rollups.ZERO_DELTA.negate() == rollups.ZERO_DELTA

    @pytest.mark.asyncio
    async def test_query_groups_amounts_per_currency_and_filters(self, store):
        store.add(card())
        store.add(card(provider="anthropic", model_sku="claude", currency="EUR"))
        fx_rate(store, DAY, "83")
        await write(store, event(), event(provider="anthropic", model="claude"), event(provider="nobody", model="m"))
        out = await rollups.query(TENANT, start=DAY, end=DAY, group_by="provider", filters={}, view=ADMIN_VIEW)
        by = {r["provider"]: r for r in out["rows"]}
        assert (
            by["openai"]["amount_by_currency"] == {"USD": "0.0025000000"}
            and by["openai"]["amount_inr"] == "0.2075000000"
        )
        assert by["anthropic"]["amount_inr"] is None and by["anthropic"]["unconverted_amount_by_currency"] == {
            "EUR": "0.0025000000"
        }
        assert by["nobody"]["amount_inr"] is None and by["nobody"]["unpriced"] == 1
        assert out["totals"]["records"] == 3 and out["totals"]["calls"] == 3
        filtered = await rollups.query(
            TENANT, start=DAY, end=DAY, group_by="day", filters={"provider": "openai"}, view=ADMIN_VIEW
        )
        assert [r["day"] for r in filtered["rows"]] == ["2026-10-01"] and filtered["totals"]["records"] == 1
        with pytest.raises(SpendError):
            await rollups.query(TENANT, start=DAY, end=DAY, group_by="nonsense", filters={}, view=ADMIN_VIEW)

    @pytest.mark.asyncio
    async def test_query_by_agent_applies_the_read_view(self, store):
        from core.models.agent import Agent
        from core.ownership import Caller

        shared = uuid.uuid4()
        personal = uuid.uuid4()
        owner = uuid.uuid4()
        store.add(
            Agent(
                id=shared,
                tenant_id=TENANT,
                name="s",
                agent_type="t",
                domain="finance",
                visibility="tenant",
                owner_user_id=None,
            )
        )
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
        store.agents[str(shared)] = ("1", "t", "active", None, None, None, None, None)
        store.agents[str(personal)] = ("1", "t", "active", None, None, None, None, None)
        await write(
            store, event(hints=hints(agent_id=str(shared))), event(hints=hints(agent_id=str(personal))), event()
        )
        auditor = Caller(user_id=uuid.uuid4(), role="auditor", domains=None, is_admin=False, is_machine=False)
        view = access.read_view(auditor)
        out = await rollups.query(TENANT, start=DAY, end=DAY, group_by="agent_id", filters={}, view=view)
        assert {r["agent_id"] for r in out["rows"]} == {str(shared), None}
        admin = await rollups.query(TENANT, start=DAY, end=DAY, group_by="agent_id", filters={}, view=ADMIN_VIEW)
        assert {r["agent_id"] for r in admin["rows"]} == {str(shared), str(personal), None}


class TestCoverage:
    @pytest.fixture
    def tree(self, store):
        group = SpendOrgNode(id=uuid.uuid4(), tenant_id=TENANT, code="GRP", name="G", kind="group", active=True)
        unit = SpendOrgNode(
            id=uuid.uuid4(),
            tenant_id=TENANT,
            code="BU",
            name="B",
            kind="business_unit",
            parent_id=group.id,
            active=True,
        )
        for row in (group, unit):
            store.add(row)
        store.add(
            SpendSourceMapping(
                id=uuid.uuid4(),
                tenant_id=TENANT,
                source_type="application",
                source_ref="chat",
                org_node_id=unit.id,
                active=True,
            )
        )
        store.add(
            SpendSourceMapping(
                id=uuid.uuid4(),
                tenant_id=TENANT,
                source_type="application",
                source_ref="voice",
                org_node_id=group.id,
                active=True,
            )
        )
        return group, unit

    @pytest.mark.asyncio
    async def test_coverage_unattributed_share_by_amount_and_count(self, store, tree):
        store.add(card(model_sku="", unit="1m_input_tokens", unit_price=Decimal("10"), currency="INR"))
        await write(
            store,
            event(hints=hints(application="chat"), quantity=Decimal(300000)),  # 3 INR to a business unit
            event(hints=hints(application="voice"), quantity=Decimal(100000)),  # 1 INR to the group: not countable
            event(hints=hints(application="api"), quantity=Decimal(100000)),  # 1 INR unattributed
        )
        out = await rollups.coverage(TENANT, start=DAY, end=DAY, now=T0)
        period = out["period"]
        assert period["amount_inr"] == "5.0000000000" and period["attributed_amount_inr"] == "4.0000000000"
        assert period["countable_amount_inr"] == "3.0000000000"
        assert period["attributed_share"] == "0.600000" and period["group_share"] == "0.200000"
        assert period["unattributed_share"] == "0.200000" and period["unattributed_count_share"] == "0.333334"
        assert period["attributed_count_share"] == "0.666666"
        assert out["days"][0]["day"] == "2026-10-01" and out["days"][0]["records"] == 3

    @pytest.mark.asyncio
    async def test_coverage_reports_reasons_paths_gaps_fx_pending_and_top_unpriced(self, store, tree):
        store.add(card())  # USD
        fx_rate(store, date(2026, 9, 30), "83")  # older than the records: they are estimated and pending
        await write(
            store,
            event(hints=hints(application="chat")),
            event(provider="mystery", model="m1"),
            event(provider="mystery", model="m1", unit="output_token", calls=0),
            event(provider="other", model="m2"),
        )
        await meter.upsert_gaps(store, TENANT, {(DAY, "llm_tokens", "queue_full", ""): 4})
        out = await rollups.coverage(TENANT, start=DAY, end=DAY, now=T0)
        period = out["period"]
        assert period["fx_estimated_count"] == 1 and period["fx_pending_count"] == 1
        assert period["unpriced_count"] == 3
        assert period["unpriced_quantity"] == {"input_token": "2000.000000", "output_token": "1000.000000"}
        assert period["gaps"] == {"queue_full": 4}
        assert {r["reason"] for r in out["by_reason"]} == {"no_mapping"}
        assert out["by_path"] == [
            {
                "attribution_path": "application_mapping",
                "node_kind": "business_unit",
                "records": 1,
                "amount_inr": "0.2075000000",
            }
        ]
        assert out["top_unpriced"][0] == {
            "provider": "mystery",
            "model": "m1",
            "usage_type": "llm_tokens",
            "unit": "input_token",
            "unpriced_count": 1,
        }
        fx_rate(store, DAY, "84")
        settled = await rollups.coverage(TENANT, start=DAY, end=DAY, now=T0)
        assert settled["period"]["fx_pending_count"] == 0  # a rate for the day exists: settlement will convert it

    @pytest.mark.asyncio
    async def test_all_unpriced_day_reports_no_inr_amount(self, store, tree):
        await write(store, event(provider="mystery", model="m"))
        out = await rollups.coverage(TENANT, start=DAY, end=DAY, now=T0)
        assert out["period"]["amount_inr"] is None and out["period"]["unattributed_share"] is None
        assert out["period"]["unattributed_count_share"] == "1.000000"


class TestFxSettlement:
    @pytest.mark.asyncio
    async def test_settle_fx_converts_estimated_and_unconverted_when_the_rate_arrives(self, store):
        store.add(card())
        await write(store, event())  # no rate at all: unconverted
        fx_rate(store, date(2026, 9, 28), "82")
        await write(store, event(unit="output_token", calls=0))  # an earlier rate: estimated
        unconverted, estimated = records(store)
        assert unconverted.unconverted and estimated.fx_estimated and estimated.fx_rate_date == date(2026, 9, 28)
        fx_rate(store, DAY, "83")
        result = await maintenance.settle_fx(TENANT, start=DAY, end=DAY, actor=ACTOR, now=T0)
        assert result == {"days": 1, "scanned": 2, "changed": 2}
        for row in records(store):
            assert not row.fx_estimated and not row.unconverted and row.fx_rate_date == DAY and row.revised_at == T0
        assert sum(r.amount_inr for r in store.of("spend_usage_rollups")) == sum(r.amount_inr for r in records(store))
        assert rollup_state(store) == await _rebuilt(store)
        audit = [r for r in store.of("audit_log") if r.event_type == "spend.fx.settle"][0]
        assert audit.details["by_currency"]["USD"]["records"] == 2
        again = await maintenance.settle_fx(TENANT, start=DAY, end=DAY, actor=ACTOR, now=T0)
        assert again["changed"] == 0

    @pytest.mark.asyncio
    async def test_settle_fx_keeps_holiday_records_estimated(self, store):
        store.add(card())
        fx_rate(store, date(2026, 9, 30), "83")
        await write(store, event())
        result = await maintenance.settle_fx(TENANT, start=DAY, end=DAY, actor=ACTOR, now=T0)
        row = records(store)[0]
        assert result["changed"] == 0 and row.fx_estimated and row.fx_rate_date == date(2026, 9, 30)

    @pytest.mark.asyncio
    async def test_forced_date_reconverts_settled_records_and_marks_money_commitments(self, store):
        store.add(card())
        fx_rate(store, DAY, "83")
        commitment = SpendCommitment(
            id=uuid.uuid4(), tenant_id=TENANT, provider="openai", kind="money", committed_amount=Decimal(5),
            currency="USD", period_start=DAY, period_end=date(2026, 11, 1), status="active",
            needs_full_recompute=False,
        )  # fmt: skip
        store.add(commitment)
        await write(store, event())
        store.of("spend_fx_rates")[0].rate_to_inr = Decimal("84")
        plain = await maintenance.settle_fx(TENANT, start=DAY, end=DAY, actor=ACTOR, now=T0)
        assert plain["changed"] == 0  # settled records are not pending
        forced = await maintenance.settle_fx(
            TENANT, start=DAY, end=DAY, force_dates=[("usd", DAY)], actor=ACTOR, now=T0
        )
        assert forced["changed"] == 1 and records(store)[0].fx_rate == Decimal("84")
        assert commitment.needs_full_recompute is True


async def _rebuilt(store) -> dict:
    snapshot = list(store.rows)
    days = sorted({r.event_date for r in store.of("spend_usage_records")})
    for day in days:
        await rollups.rebuild_day(TENANT, day)
    state = rollup_state(store)
    store.rows = snapshot
    return state


class TestRestatement:
    @pytest.mark.asyncio
    async def test_restate_moves_records_to_the_corrected_card_and_moves_the_rollup(self, store):
        old = card(unit_price=Decimal("2.5"))
        store.add(old)
        fx_rate(store, DAY, "83")
        await write(store, event())
        assert records(store)[0].rate_card_id == old.id
        old.status, old.retired_at = "retired", T0
        new = card(unit_price=Decimal("3"), replaces_id=old.id)
        store.add(new)
        result = await maintenance.restate(
            TENANT, provider="openai", start=DAY, end=DAY, card_ids=[old.id], include_unpriced=True,
            actor=ACTOR, reason="Corrected contract price", now=T0,
        )  # fmt: skip
        row = records(store)[0]
        assert result["changed"] == 1 and row.rate_card_id == new.id and row.amount == Decimal("0.0030000000")
        assert row.amount_inr == Decimal("0.2490000000") and row.revised_at == T0
        rollup_cards = {r.rate_card_id for r in store.of("spend_usage_rollups")}
        assert rollup_cards == {new.id}
        assert rollup_state(store) == await _rebuilt(store)
        audit = [r for r in store.of("audit_log") if r.event_type == "spend.usage.restate"][0]
        assert audit.details["reason"] == "Corrected contract price" and audit.details["changed"] == 1
        with pytest.raises(SpendError):
            maintenance.check_reason("short")

    @pytest.mark.asyncio
    async def test_restate_keeps_the_stored_fallback_price(self, store, monkeypatch):
        from core.config import settings

        monkeypatch.setattr(settings, "model_price_overrides_json", '{"openai/gpt-4o": {"input": 9, "output": 9}}')
        await write(store, event())
        row = records(store)[0]
        assert row.price_source == "fallback_override" and row.unit_price == Decimal("9")
        monkeypatch.setattr(settings, "model_price_overrides_json", '{"openai/gpt-4o": {"input": 1, "output": 1}}')
        result = await maintenance.restate(
            TENANT, provider="openai", start=DAY, end=DAY, include_unpriced=True, actor=ACTOR,
            reason="Periodic restatement", now=T0,
        )  # fmt: skip
        assert result["changed"] == 0 and records(store)[0].unit_price == Decimal("9")

    @pytest.mark.asyncio
    async def test_restate_prices_unpriced_records_once_a_card_exists(self, store):
        await write(store, event(model="new-model"))
        assert records(store)[0].unpriced
        store.add(card(model_sku="new-model", currency="INR", unit_price=Decimal("100")))
        result = await maintenance.restate(
            TENANT, provider="openai", start=DAY, end=DAY, include_unpriced=True, actor=ACTOR,
            reason="Card added after the usage", now=T0,
        )  # fmt: skip
        row = records(store)[0]
        assert result["changed"] == 1 and not row.unpriced and row.amount_inr == Decimal("0.1000000000")
        with pytest.raises(SpendError) as info:
            await maintenance.restate(
                TENANT, provider="openai", start=DAY, end=DAY + timedelta(days=maintenance.JOB_MAX_DAYS),
                actor=ACTOR, reason="x" * 12, now=T0,
            )  # fmt: skip
        assert info.value.code == "range_too_long"


class TestReattribution:
    @pytest.mark.asyncio
    async def test_reattribute_changes_only_unattributed_records(self, store):
        unit = SpendOrgNode(id=uuid.uuid4(), tenant_id=TENANT, code="BU", name="B", kind="business_unit", active=True)
        store.add(unit)
        await write(store, event(hints=hints(application="chat")), event(hints=hints(application="api")))
        api, chat = sorted(records(store), key=lambda r: r.application)
        assert chat.unattributed_reason == api.unattributed_reason == "no_mapping"
        store.add(
            SpendSourceMapping(
                id=uuid.uuid4(),
                tenant_id=TENANT,
                source_type="application",
                source_ref="chat",
                org_node_id=unit.id,
                product_line="retail",
                active=True,
            )
        )
        result = await maintenance.reattribute(TENANT, start=DAY, end=DAY, actor=ACTOR, now=T0)
        assert result == {"days": 1, "scanned": 2, "changed": 1}
        assert chat.org_node_id == unit.id and chat.attribution_path == "application_mapping"
        assert (
            chat.business_unit_node_id == unit.id and chat.product_line == "retail" and chat.unattributed_reason is None
        )
        assert api.org_node_id is None and api.unattributed_reason == "no_mapping"
        audit = [r for r in store.of("audit_log") if r.event_type == "spend.usage.reattribute"][0]
        assert audit.details["before"] == {"no_mapping": 2} and audit.details["after"] == {
            "attributed": 1,
            "no_mapping": 1,
        }
        assert rollup_state(store) == await _rebuilt(store)
        hinted = maintenance.hints_of(chat)
        assert hinted.origin == "reattribute" and hinted.application == "chat"


def commitment(**over) -> SpendCommitment:
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "provider": "openai",
        "usage_type": "llm_tokens",
        "model_sku": "",
        "unit": "1m_input_tokens",
        "kind": "quantity",
        "committed_quantity": Decimal("0.0015"),  # 1500 input tokens
        "committed_amount": None,
        "currency": None,
        "period_start": date(2026, 9, 1),
        "period_end": date(2026, 11, 1),
        "status": "active",
        "needs_full_recompute": True,
        "drawn_quantity": Decimal(0),
        "drawn_amount": Decimal(0),
        "undrawn_records": 0,
        "recomputed_through": None,
    }
    base.update(over)
    return SpendCommitment(**base)


LATER = T0 + timedelta(days=1)  # the recompute runs a day after the usage, past the append grace


class TestCommitmentRecompute:
    @pytest.mark.asyncio
    async def test_recompute_draws_in_event_order_whatever_the_arrival_order(self, store):
        target = commitment()
        store.add(target)
        late, early = event(event_time=T0 + timedelta(minutes=5)), event(event_time=T0)
        await write(store, late)
        await write(store, early)  # arrives second, happened first
        result = await commitments.recompute(TENANT, now=LATER)
        assert result["providers"]["openai"]["mode"] == "full"
        first, second = records(store)
        assert first.event_time == T0 and first.commitment_id == target.id and not first.overage
        assert second.overage and second.overage_quantity == Decimal(500)
        assert target.drawn_quantity == Decimal(2000) and target.needs_full_recompute is False
        assert target.recomputed_through == LATER - commitments.APPEND_GRACE
        assert sum(r.overage_count for r in store.of("spend_usage_rollups")) == 1
        assert rollup_state(store) == await _rebuilt(store)

    @pytest.mark.asyncio
    async def test_recompute_counts_usage_before_a_commitment_was_created(self, store):
        await write(store, event())
        target = commitment()
        store.add(target)
        await commitments.recompute(TENANT, provider="openai", now=LATER)
        assert records(store)[0].commitment_id == target.id and target.drawn_quantity == Decimal(1000)

    @pytest.mark.asyncio
    async def test_recompute_draws_quantity_in_record_units(self, store):
        target = commitment(committed_quantity=Decimal("0.001"))
        store.add(target)
        await write(store, event(quantity=Decimal(1000)), event(unit="output_token", calls=0, quantity=Decimal(1)))
        await commitments.recompute(TENANT, now=LATER)
        inp = next(r for r in records(store) if r.unit == "input_token")
        out = next(r for r in records(store) if r.unit == "output_token")
        assert inp.commitment_id == target.id and not inp.overage and out.commitment_id is None
        assert target.drawn_quantity == Decimal(1000)

    @pytest.mark.asyncio
    async def test_money_commitment_converts_through_inr_or_counts_undrawn(self, store):
        store.add(card(currency="EUR", unit_price=Decimal("1000")))  # 1000 EUR per 1M: 1 EUR per 1000 tokens
        fx_rate(store, DAY, "90", currency="EUR")
        fx_rate(store, DAY, "80", currency="USD")
        money = commitment(
            kind="money",
            unit=None,
            usage_type=None,
            committed_quantity=None,
            committed_amount=Decimal("1"),
            currency="USD",
        )
        store.add(money)
        await write(store, event())  # 1 EUR = 90 INR = 1.125 USD: draws 1.125, 0.125 over
        await commitments.recompute(TENANT, now=LATER)
        row = records(store)[0]
        assert money.drawn_amount == Decimal("1.1250000000") and row.overage
        assert row.overage_quantity == Decimal("111.111111")
        store.rows = [r for r in store.rows if not (r.__tablename__ == "spend_fx_rates" and r.currency == "USD")]
        money.needs_full_recompute = True
        await commitments.recompute(TENANT, now=LATER)
        assert money.undrawn_records == 1 and money.drawn_amount == 0 and records(store)[0].commitment_id is None

    @pytest.mark.asyncio
    async def test_append_pass_continues_from_the_watermark(self, store):
        target = commitment()
        store.add(target)
        await write(store, event())
        await commitments.recompute(TENANT, now=LATER)
        assert target.drawn_quantity == Decimal(1000)
        await write(store, event(event_time=LATER), now=LATER)
        later = LATER + timedelta(days=1)
        result = await commitments.recompute(TENANT, now=later)
        assert result["providers"]["openai"]["mode"] == "append" and result["providers"]["openai"]["scanned"] == 1
        assert target.drawn_quantity == Decimal(2000) and records(store)[1].overage_quantity == Decimal(500)

    def test_most_specific_commitment_wins(self):
        from types import SimpleNamespace

        rec = SimpleNamespace(
            provider="openai", billing_date=DAY, usage_type="llm_tokens", unit="input_token", model="gpt-4o"
        )
        model_specific = commitment(model_sku="gpt-4o")
        default = commitment()
        money_typed = commitment(kind="money", unit=None, committed_amount=Decimal(1), currency="USD")
        money_any = commitment(kind="money", unit=None, usage_type=None, committed_amount=Decimal(1), currency="USD")
        assert commitments.best_commitment(rec, [money_any, money_typed, default, model_specific]) is model_specific
        assert commitments.best_commitment(rec, [money_any, money_typed]) is money_typed
        assert commitments.match_rank(rec, commitment(status="closed")) is None
        assert commitments.match_rank(rec, commitment(period_end=DAY)) is None
        assert commitments.match_rank(rec, commitment(unit="1m_output_tokens")) is None
        assert commitments.match_rank(rec, commitment(model_sku="other")) is None
        assert commitments.match_rank(rec, commitment(usage_type="ocr_pages")) is None
        assert commitments.match_rank(rec, commitment(kind="money", usage_type="ocr_pages")) is None


def gateway_row(**over) -> ModelGatewayRecord:
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "correlation_id": "req-1",
        "use_case": "agent_run",
        "agent_id": None,
        "provider": "gpt",
        "model": "gpt-4o",
        "restricted": False,
        "outcome": "completed",
        "latency_ms": 10,
        "tokens": 1500,
        "input_tokens": 1000,
        "output_tokens": 500,
        "cost_usd": 0.0,
        "created_at": T0,
    }
    base.update(over)
    return ModelGatewayRecord(**base)


class TestLedgersAndBackfill:
    @pytest.mark.asyncio
    async def test_ledger_comparison_reads_rollups_and_reports_notes(self, store):
        from core.models.agent import AgentCostLedger
        from core.models.finops_ledger import FinopsCostLedger

        store.add(card(currency="USD"))
        await write(
            store, event(hints=hints(application="agents")), event(hints=hints(application="chat"), unit="output_token")
        )
        agent = uuid.uuid4()
        store.add(
            AgentCostLedger(
                id=uuid.uuid4(),
                tenant_id=TENANT,
                agent_id=agent,
                period_date=DAY,
                token_count=1200,
                cost_usd=Decimal("0.4"),
                task_count=1,
            )
        )
        store.add(
            FinopsCostLedger(
                id=uuid.uuid4(),
                tenant_id=TENANT,
                period_date=DAY,
                agent_id=agent,
                use_case="x",
                application="agents",
                tokens=1200,
                cost_usd=0.4,
                calls=1,
            )
        )
        store.add(gateway_row())
        out = await ledgers.compare(TENANT, start=DAY, end=DAY, view=ADMIN_VIEW)
        day = out["days"][0]
        assert day["usage"]["tokens"] == {"agents": "1000.000000", "chat": "1000.000000", "other": "0"}
        assert day["agent_cost_ledger"]["tokens"] == 1200 and day["token_delta"] == "-200.000000"
        assert day["finops_cost_ledger"]["calls"] == 1 and day["model_gateway_records"] == {
            "calls": 1,
            "completed_tokens": 1500,
        }
        assert day["amount_ratio"] == "0.031250" and len(out["notes"]) == 8 and out["backfill_source"] == "none"
        with pytest.raises(SpendError):
            await ledgers.compare(TENANT, start=DAY, end=DAY + timedelta(days=31), view=ADMIN_VIEW)

    @pytest.mark.asyncio
    async def test_backfill_skips_a_call_with_any_existing_record(self, store):
        """A fully cached prompt: the hook wrote one cached-input record and no uncached one."""
        from core.governance.model_gateway_records import ModelCallRecord
        from core.spend import tokens

        row = gateway_row()
        store.add(row)
        hook_record = ModelCallRecord(
            tenant_id=TID, correlation_id=row.correlation_id, use_case=row.use_case, agent_id=None, policy_id=None,
            access_policy_id=None, requested_provider=None, requested_model=None, provider=row.provider,
            model=row.model, fallback_from=None, restricted=False, outcome="completed", error_type=None,
            latency_ms=10, admission_wait_ms=None, tokens=1500, input_tokens=1000, output_tokens=500, cost_usd=0.0,
            tokens_per_second=None, created_at=T0,
        )  # fmt: skip
        hook_events = meter.model_call_events(hook_record, details=tokens.UsageDetails(cached_input_tokens=1000))
        assert [e.unit for e in hook_events] == ["cached_input_token", "output_token"]
        await write(store, hook_events[0])  # only the cached-input record made it
        result = await ledgers.backfill_model_calls(TENANT, start=DAY, end=DAY, actor=ACTOR, now=LATER)
        assert result["skipped_calls"] == 1 and result["written"] == 0
        assert [r.unit for r in records(store)] == ["cached_input_token"]

    @pytest.mark.asyncio
    async def test_backfill_from_gateway_rows_is_idempotent_against_hook_records(self, store):
        store.add(gateway_row())
        store.add(gateway_row(correlation_id="req-2", created_at=T0 + timedelta(seconds=1), agent_id=str(uuid.uuid4())))
        store.add(
            gateway_row(correlation_id="req-3", outcome="failed", tokens=0, input_tokens=None, output_tokens=None)
        )
        first = await ledgers.backfill_model_calls(TENANT, start=DAY, end=DAY, actor=ACTOR, now=LATER)
        assert first["scanned"] == 3 and first["written"] == 4 and first["unpriced"] == 0
        second = await ledgers.backfill_model_calls(TENANT, start=DAY, end=DAY, actor=ACTOR, now=LATER)
        assert second["written"] == 0 and second["skipped_calls"] == 2
        backfilled = records(store)
        assert {r.application for r in backfilled} == {"agents"} and len(backfilled) == 4
        assert any(r.agent_id is not None for r in backfilled)  # an absent agent keeps its id on backfill
        audit = [r for r in store.of("audit_log") if r.event_type == "spend.usage.backfill"]
        assert len(audit) == 2

    @pytest.mark.asyncio
    async def test_backfill_pages_through_gateway_rows(self, store, monkeypatch):
        monkeypatch.setattr(ledgers, "PAGE", 2)
        monkeypatch.setattr(ledgers, "WRITE_BATCH", 1)
        for index in range(3):
            store.add(
                gateway_row(correlation_id=f"r{index}", created_at=T0 + timedelta(seconds=index), use_case="completion")
            )
        result = await ledgers.backfill_model_calls(TENANT, start=DAY, end=DAY, actor=ACTOR, now=LATER)
        assert result["scanned"] == 3 and result["written"] == 6
        assert {r.application for r in records(store)} == {"api"}

    def test_backfill_source_follows_the_gateway_settings(self, monkeypatch):
        from core.config import settings

        monkeypatch.setattr(settings, "model_gateway_enabled", True)
        monkeypatch.setattr(settings, "model_gateway_records_enabled", True)
        assert ledgers.backfill_source() == "model_gateway_records"
        monkeypatch.setattr(settings, "model_gateway_records_enabled", False)
        assert ledgers.backfill_source() == "none"


def test_utc_constant():
    assert T0.tzinfo is UTC and datetime(2026, 10, 1, tzinfo=UTC) < T0
