# SPDX-License-Identifier: Apache-2.0
"""Spend usage: events and keys from a model call, token details, scopes, billing accounts and the write path."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from core.config import settings
from core.governance.model_gateway_records import ModelCallRecord
from core.models.spend import SpendCommitment, SpendRateCard
from core.spend import billing, context, ids, meter, pricing, resolver, rollups, tokens, writer
from core.spend.meter import UsageEvent
from core.spend.resolver import Hints
from tests.unit.spend_usage_fakes import ACTOR, OTHER_TENANT, T0, TENANT, install

TID = str(TENANT)


def record(**over) -> ModelCallRecord:
    base = {
        "tenant_id": TID,
        "correlation_id": "req-123",
        "use_case": "completion",
        "agent_id": None,
        "policy_id": None,
        "access_policy_id": None,
        "requested_provider": None,
        "requested_model": None,
        "provider": "gpt",
        "model": "gpt-4o",
        "fallback_from": None,
        "restricted": False,
        "outcome": "completed",
        "error_type": None,
        "latency_ms": 120,
        "admission_wait_ms": None,
        "tokens": 1500,
        "input_tokens": 1000,
        "output_tokens": 500,
        "cost_usd": 0.0,
        "tokens_per_second": None,
        "created_at": T0,
    }
    base.update(over)
    return ModelCallRecord(**base)


def hints(**over) -> Hints:
    base = {
        "agent_id": None,
        "agent_version": None,
        "application": "api",
        "default_use_case": "completion",
        "workflow_id": None,
        "workflow_run_id": None,
        "run_id": None,
        "initiating_user_id": None,
        "origin": "hook",
    }
    base.update(over)
    return Hints(**base)


def event(**over) -> UsageEvent:
    base = {
        "tenant_id": TID,
        "usage_type": "llm_tokens",
        "unit": "input_token",
        "quantity": Decimal("1000"),
        "provider": "openai",
        "model": "gpt-4o",
        "event_time": T0,
        "idempotency_key": f"llm:{uuid.uuid4().hex}:input_token",
        "source_ref": uuid.uuid4().hex[:40],
        "correlation_ref": "c" * 32,
        "hints": hints(),
        "calls": 1,
        "billing_account": "tenant_key",
    }
    base.update(over)
    return UsageEvent(**base)


def card(**over) -> SpendRateCard:
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "provider": "openai",
        "usage_type": "llm_tokens",
        "model_sku": "gpt-4o",
        "unit": "1m_input_tokens",
        "unit_price": Decimal("2.5"),
        "currency": "USD",
        "cached_unit_price": None,
        "batch_discount_pct": Decimal("0"),
        "volume_tiers": [],
        "tier_mode": "graduated",
        "effective_from": date(2026, 1, 1),
        "effective_to": None,
        "source": "contract",
        "status": "active",
        "reference": "",
    }
    base.update(over)
    return SpendRateCard(**base)


@pytest.fixture
def store(monkeypatch):
    return install(monkeypatch)


@pytest.fixture
def queued(monkeypatch):
    """Events and gaps the hook hands the writer, captured instead of queued."""
    got: dict[str, list] = {"events": [], "gaps": []}
    monkeypatch.setattr(writer, "submit", lambda events: got["events"].extend(events))
    monkeypatch.setattr(writer, "add_gap", lambda *args, **kw: got["gaps"].append(args))
    monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
    return got


# ---------------------------------------------------------------- events and keys


class TestEvents:
    def test_model_call_gives_input_cached_and_output_records(self):
        details = tokens.UsageDetails(cached_input_tokens=400)
        events = meter.model_call_events(record(), details=details)
        assert [(e.unit, e.quantity) for e in events] == [
            ("input_token", Decimal(600)),
            ("cached_input_token", Decimal(400)),
            ("output_token", Decimal(500)),
        ]
        assert {e.provider for e in events} == {"openai"} and {e.source_ref for e in events} == {events[0].source_ref}
        assert [e.idempotency_key for e in events] == [f"llm:{events[0].source_ref}:{e.unit}" for e in events]

    def test_first_record_of_a_call_carries_calls_one(self):
        events = meter.model_call_events(record())
        assert [e.calls for e in events] == [1, 0]

    def test_unknown_split_gives_one_estimated_token_record(self):
        events = meter.model_call_events(record(input_tokens=None, output_tokens=None, tokens=900))
        assert [(e.unit, e.quantity, e.quantity_estimated) for e in events] == [("token", Decimal(900), True)]

    def test_one_missing_side_is_derived_from_total(self):
        events = meter.model_call_events(record(input_tokens=None, output_tokens=300, tokens=1000))
        assert [(e.unit, e.quantity) for e in events] == [("input_token", Decimal(700)), ("output_token", 300)]
        events = meter.model_call_events(record(input_tokens=800, output_tokens=None, tokens=1000))
        assert [(e.unit, e.quantity) for e in events] == [("input_token", Decimal(800)), ("output_token", 200)]

    def test_failed_call_without_tokens_writes_nothing_and_is_counted_as_a_gap(self, queued):
        failed = record(outcome="failed", tokens=0, input_tokens=None, output_tokens=None, error_type="X")
        plan = meter.plan_model_call(failed)
        assert plan.events == [] and plan.gap == ("failed_no_usage", "openai") and plan.unmetered == "failed_no_usage"
        meter.meter_model_call(failed)
        assert queued["events"] == []
        assert queued["gaps"] == [(TID, date(2026, 10, 1), "llm_tokens", "failed_no_usage", "openai")]

    def test_router_timeout_writes_one_estimated_input_record_from_prompt_length(self, queued):
        failed = record(outcome="failed", tokens=0, input_tokens=None, output_tokens=None, error_type="TimeoutError")
        messages = [{"role": "system", "content": "x" * 10}, {"role": "user", "content": "y" * 31}]
        usage = context.call_usage("router", response=None, error=TimeoutError(), messages=messages, tenant_id=TID)
        meter.meter_model_call(failed, usage=usage)
        assert [(e.unit, e.quantity, e.quantity_estimated) for e in queued["events"]] == [
            ("input_token", Decimal(11), True)
        ]
        assert queued["gaps"][0][3] == "timeout_estimated"
        assert tokens.from_router(None, ValueError(), messages) is None

    def test_gemini_router_thoughts_are_billed_as_output(self):
        response = SimpleNamespace(
            raw={"usage": {"cached_content_token_count": 100, "thoughts_token_count": 250}}, content="x"
        )
        details = tokens.from_router(response, None, [])
        assert details.cached_input_tokens == 100 and details.extra_output_tokens == 250
        events = meter.model_call_events(record(provider="gemini", model="gemini-2.5-pro"), details=details)
        assert [(e.unit, e.quantity) for e in events] == [
            ("input_token", Decimal(900)),
            ("cached_input_token", Decimal(100)),
            ("output_token", Decimal(750)),
        ]

    def test_openai_cached_tokens_read_from_raw_usage(self):
        response = SimpleNamespace(
            raw={
                "usage": {
                    "prompt_tokens_details": {"cached_tokens": 256},
                    "completion_tokens_details": {"reasoning_tokens": 64},
                }
            }
        )
        details = tokens.from_router(response, None, [])
        assert (
            details.cached_input_tokens == 256 and details.reasoning_tokens == 64 and not details.cached_outside_input
        )
        anthropic = tokens.from_router(SimpleNamespace(raw={"usage": {"cache_read_input_tokens": 50}}), None, [])
        assert anthropic.cached_outside_input and anthropic.cached_input_tokens == 50
        events = meter.model_call_events(record(provider="claude", model="claude-sonnet"), details=anthropic)
        assert [(e.unit, e.quantity) for e in events] == [
            ("input_token", Decimal(1000)),
            ("cached_input_token", Decimal(50)),
            ("output_token", Decimal(500)),
        ]
        assert tokens.from_router(SimpleNamespace(raw={"usage": {}}), None, []) is None
        assert tokens.from_router(SimpleNamespace(raw="text"), None, []) is None
        assert tokens._count("x") is None and tokens._count(True) is None and tokens._count(-1) is None

    def test_router_provider_aliases_are_normalised_for_pricing(self):
        assert meter.pricing_provider(record(provider="claude")) == "anthropic"
        assert meter.pricing_provider(record(provider="gpt")) == "openai"
        assert meter.pricing_provider(record(provider="azure_openai")) == "azure_openai"

    def test_prefixed_local_model_is_in_house_zero(self):
        assert meter.pricing_provider(record(model="ollama:llama3", provider="unknown")) == "ollama"
        assert meter.pricing_provider(record(model="m"), decision=SimpleNamespace(provider="vllm")) == "vllm"
        events = meter.model_call_events(record(model="vllm:mistral", provider="unknown"))
        assert events[0].billing_account == "in_house"
        usage = pricing.Usage(
            "ollama", "llm_tokens", "input_token", Decimal(10), "llama3", date(2026, 10, 1), date(2026, 10, 1)
        )
        priced = pricing.price_with(usage, [], lambda c: None)
        assert priced.price_source == "in_house" and priced.amount == 0

    def test_unprefixed_local_model_detected_from_endpoint(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.internal:11434")
        monkeypatch.setenv("VLLM_BASE_URL", "http://vllm.internal:8000")
        llm = SimpleNamespace(openai_api_base="http://ollama.internal:11434/v1", model_name="llama3")
        bound = SimpleNamespace(bound=llm)
        assert tokens.serving_provider_of(bound) == "ollama"
        assert tokens.serving_provider_of(SimpleNamespace(openai_api_base="http://vllm.internal:8000/v1")) == "vllm"
        assert tokens.serving_provider_of(SimpleNamespace(openai_api_base="https://api.example.com/v1")) is None
        assert tokens.serving_provider_of(SimpleNamespace()) is None
        details = tokens.from_message(SimpleNamespace(usage_metadata={}), llm=bound)
        assert details.serving_provider == "ollama"
        assert meter.pricing_provider(record(provider="openai"), details=details) == "ollama"

    def test_call_hash_matches_a_gateway_row_for_the_same_call(self):
        call = record(tenant_id=TID.upper())
        events = meter.model_call_events(call)
        row = SimpleNamespace(
            tenant_id=TENANT,
            correlation_id="req-123",
            created_at=T0.astimezone(UTC),
            provider="gpt",
            model="gpt-4o",
            outcome="completed",
            tokens=1500,
            input_tokens=1000,
            output_tokens=500,
        )
        expected = meter.call_hash(
            str(row.tenant_id),
            row.correlation_id,
            row.created_at,
            row.provider,
            row.model,
            row.outcome,
            row.tokens,
            row.input_tokens,
            row.output_tokens,
        )
        assert events[0].source_ref == expected and len(expected) == 40
        other = meter.call_hash(
            TID, "req-123", T0 + timedelta(microseconds=1), "gpt", "gpt-4o", "completed", 1500, 1000, 500
        )
        assert other != expected
        assert meter.call_hash(TID, "c", T0, "gpt", "m", "failed", 0, None, None) != meter.call_hash(
            TID, "c", T0, "gpt", "m", "failed", 0, 0, 0
        )

    def test_correlation_is_stored_as_a_hash_never_raw(self):
        events = meter.model_call_events(record(correlation_id="client-supplied-request-id-123"))
        wire = events[0].to_wire()
        assert events[0].correlation_ref == meter.correlation_ref("client-supplied-request-id-123")
        assert len(events[0].correlation_ref) == 32
        assert "client-supplied-request-id-123" not in str(wire)

    def test_event_and_billing_dates_follow_their_zones(self, store):
        evening = datetime(2026, 9, 30, 20, 0, tzinfo=UTC)
        from core.spend import clock

        assert clock.event_date_of(evening) == date(2026, 10, 1)
        assert clock.billing_date_of("openai", evening) == date(2026, 9, 30)
        assert clock.billing_date_of("gemini", evening) == date(2026, 9, 30)
        row = meter._record_row(
            TENANT,
            event(event_time=evening),
            "gpt-4o",
            resolver.failed(hints()),
            pricing.unpriced(),
            None,
        )
        assert (row["event_date"], row["billing_date"]) == (date(2026, 10, 1), date(2026, 9, 30))

    def test_no_tenant_is_counted_never_written(self, queued):
        meter.meter_model_call(record(tenant_id=None))
        assert queued["events"] == []
        with context.scope(tenant_id=TID):
            meter.meter_model_call(record(tenant_id=None))
        assert len(queued["events"]) == 2 and queued["events"][0].tenant_id == TID

    def test_wire_round_trip_keeps_everything_but_resolution_and_price(self):
        original = event(hints=hints(agent_id="a", run_id="run_1"), allocated=True, allocated_from="ref")
        back = UsageEvent.from_wire(original.to_wire())
        assert back == original
        naive = original.to_wire() | {"event_time": "2026-10-01T09:00:00"}
        assert UsageEvent.from_wire(naive).event_time == T0

    def test_hook_failure_inside_the_meter_is_counted(self, monkeypatch, queued):
        monkeypatch.setattr(meter, "plan_model_call", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
        meter.meter_model_call(record())  # never raises
        assert queued["events"] == []


# ---------------------------------------------------------------- scopes and hints


class TestContext:
    def test_scopes_are_no_ops_while_off(self, monkeypatch):
        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
        assert context.bind_scope(application="chat") is None
        with context.scope(application="chat"):
            assert context.current_scope() is None
        assert context.call_usage("router", response=1) is None
        context.note_credential("gemini", "tenant")
        assert context.current_credential() is None
        assert context.billing_account_of({}, TID, "gpt-4o", None) is None

    def test_first_binder_wins_and_nested_scopes_fill_gaps(self, monkeypatch):
        monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
        with context.scope(application="speech", default_use_case="speech.summary"):
            with context.scope(application="content", default_use_case="content", agent_id="a1", unknown="x"):
                scope = context.current_scope()
                assert scope.application == "speech" and scope.default_use_case == "speech.summary"
                assert scope.agent_id == "a1"
            assert context.current_scope().agent_id is None
        assert context.current_scope() is None
        token = context.bind_scope(run_id="r" * 300)
        assert len(context.current_scope().run_id) == 128
        context.reset_scope(token)
        context.reset_scope(None)

    def test_hints_come_from_scope_route_and_identity(self, monkeypatch):
        from core.governance import caller_identity

        monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
        user = str(uuid.uuid4())
        token = caller_identity.bind_identity(caller_identity.CallerIdentity(principal=f"user:{user}", auth_mode="jwt"))
        try:
            found = meter.hints_from_context(
                record_agent_id="agent-x", record_use_case="completion", default_application="", default_use_case=""
            )
            assert found.application == "console" and found.initiating_user_id == user and found.agent_id == "agent-x"
            assert found.default_use_case == "completion"
            with context.scope(application="workflows", agent_id="agent-y", workflow_id="wf", default_use_case="d"):
                inside = meter.hints_from_context(
                    record_agent_id="agent-x", record_use_case="agent_run", default_application="", default_use_case=""
                )
                assert (inside.application, inside.agent_id, inside.workflow_id) == ("workflows", "agent-y", "wf")
                assert inside.default_use_case == "d"
        finally:
            caller_identity.reset_identity(token)
        api_token = caller_identity.bind_identity(
            caller_identity.CallerIdentity(principal="api_key:k", auth_mode="api_key")
        )
        try:
            assert (
                meter.hints_from_context(
                    record_agent_id=None, record_use_case="", default_application="", default_use_case=""
                ).application
                == "api"
            )
        finally:
            caller_identity.reset_identity(api_token)
        assert (
            meter.hints_from_context(
                record_agent_id=None, record_use_case="agent_run", default_application="", default_use_case=""
            ).application
            == "agents"
        )
        assert (
            meter.hints_from_context(
                record_agent_id=None, record_use_case="", default_application="knowledge", default_use_case="x"
            ).application
            == "knowledge"
        )
        assert (
            meter.hints_from_context(
                record_agent_id=None, record_use_case="", default_application="", default_use_case=""
            ).application
            == "system"
        )

    def test_caller_payload_labels_are_never_used(self, monkeypatch):
        """The hints are built from server context only: nothing a caller sends in a body is read."""
        import inspect

        source = inspect.getsource(meter.hints_from_context) + inspect.getsource(resolver.load_facts)
        assert "business_unit" not in source and "payload" not in source and "body" not in source


# ---------------------------------------------------------------- billing accounts


class TestBilling:
    def test_router_records_the_credential_source(self, monkeypatch):
        monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
        context.note_credential("gemini", "tenant")
        assert context.current_credential() == ("gemini", "tenant")
        assert context.account_for("gemini", context.current_credential()) == "tenant_key"
        assert context.account_for("openai", context.current_credential()) is None
        context.note_credential("claude", "platform_env")
        events = meter.model_call_events(record(provider="claude", model="claude-sonnet"))
        assert events[0].billing_account == "platform_key"

    def test_graph_takes_the_billing_account_from_the_prefetched_credential(self, monkeypatch):
        monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
        snapshot = {(TID, "openai"): SimpleNamespace(source="tenant")}
        assert context.billing_account_of(snapshot, TID, "gpt-4o", None) == "tenant_key"
        assert (
            context.billing_account_of({(TID, "gemini"): SimpleNamespace(source="platform_env")}, TID, "", None)
            == "platform_key"
        )
        assert context.billing_account_of(snapshot, TID, "llama3", "ollama") == "in_house"
        assert context.billing_account_of(None, TID, "gpt-4o", None) is None
        usage = context.call_usage("message", response=SimpleNamespace(), billing_account="tenant_key", tenant_id=TID)
        details = tokens.details_of(usage)
        assert details.billing_account == "tenant_key" and details.tenant_id == TID
        assert meter.model_call_events(record(), details=details)[0].billing_account == "tenant_key"

    @pytest.mark.asyncio
    async def test_writer_infers_billing_for_direct_callers(self, store):
        from core.models.tenant_ai_credential import TenantAICredential

        store.add(
            TenantAICredential(
                id=uuid.uuid4(), tenant_id=TENANT, provider="openai", credential_kind="llm", status="active"
            )
        )
        found = await billing.infer(store, TENANT, {"openai", "gemini", "ollama", "toolco"}, now=1.0)
        assert found == {"openai": "tenant_key", "gemini": "platform_key", "ollama": "in_house"}
        billing.invalidate(TENANT)
        assert await billing.infer(store, TENANT, {"tesseract"}) == {"tesseract": "in_house"}

    @pytest.mark.asyncio
    async def test_billing_inference_failure_leaves_the_account_unknown(self, store, monkeypatch):
        async def broken(*args, **kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(billing, "_tenant_credentials", broken)
        assert await billing.infer(store, TENANT, {"openai"}) == {}

    def test_in_house_and_storage_are_in_house_accounts(self):
        assert billing.fixed_account("ollama") == "in_house"
        assert billing.fixed_account("platform_storage") == "in_house"
        assert billing.fixed_account("openai") is None


# ---------------------------------------------------------------- the write path


async def _write(store, events, **kw):
    return await meter.write_events(store, TENANT, events, now=kw.pop("now", T0), **kw)


class TestWritePath:
    @pytest.mark.asyncio
    async def test_written_records_carry_price_attribution_and_rollup(self, store):
        store.add(card())
        from core.models.spend import SpendFxRate

        store.add(
            SpendFxRate(
                id=uuid.uuid4(),
                tenant_id=TENANT,
                rate_date=date(2026, 10, 1),
                currency="USD",
                rate_to_inr=Decimal("83.5"),
                source="manual",
            )
        )
        result = await _write(store, [event()])
        assert (result.written, result.duplicates, result.busy) == (1, 0, False)
        row = store.of("spend_usage_records")[0]
        assert row.amount == Decimal("0.0025000000") and row.currency == "USD" and row.price_source == "contract"
        assert row.amount_inr == Decimal("0.2087500000") and not row.fx_estimated
        assert row.unattributed_reason == "no_mapping" and row.application == "api"
        assert ids.unix_ms_of(row.id) > 0 and row.billing_account == "tenant_key"
        rollup = store.of("spend_usage_rollups")[0]
        assert rollup.record_count == 1 and rollup.call_count == 1 and rollup.amount_inr == row.amount_inr
        assert any(k.startswith("spend:rollup:") and "2026-10-01" in k for k in store.locks)

    @pytest.mark.asyncio
    async def test_event_for_another_tenant_is_refused(self, store):
        result = await _write(store, [event(tenant_id=str(OTHER_TENANT))])
        assert (result.written, result.refused) == (0, 1)
        assert store.of("spend_usage_records") == []
        gap = store.of("spend_meter_gaps")[0]
        assert gap.reason == "tenant_mismatch" and gap.tenant_id == TENANT and gap.count == 1

    @pytest.mark.asyncio
    async def test_retried_write_is_not_double_counted(self, store):
        same = event()
        await _write(store, [same])
        again = await _write(store, [same])
        assert (again.written, again.duplicates) == (0, 1)
        assert len(store.of("spend_usage_records")) == 1
        assert store.of("spend_usage_rollups")[0].record_count == 1

    @pytest.mark.asyncio
    async def test_on_conflict_skips_concurrent_duplicate_without_rolling_back_the_batch(self, store):
        first = event()
        await _write(store, [first])
        result = await _write(store, [first, event(unit="output_token", calls=0)])
        assert (result.written, result.duplicates) == (1, 1)
        assert len(store.of("spend_usage_records")) == 2
        assert store.of("spend_usage_rollups")[0].record_count + store.of("spend_usage_rollups")[-1].record_count >= 2

    @pytest.mark.asyncio
    async def test_insert_chunks_stay_under_the_bind_parameter_limit(self, store, monkeypatch):
        from core.models.spend_usage import SpendUsageRecord

        columns = len(SpendUsageRecord.__table__.columns)
        assert meter.INSERT_CHUNK * columns < 32_767
        monkeypatch.setattr(meter, "INSERT_CHUNK", 2)
        statements_before = len(store.statements)
        await _write(store, [event(idempotency_key=f"llm:{i}:input_token") for i in range(5)])
        inserts = [
            s
            for s in store.statements[statements_before:]
            if type(s).__name__ == "Insert" and s.table.name == "spend_usage_records"
        ]
        assert len(inserts) == 3 and len(store.of("spend_usage_records")) == 5

    @pytest.mark.asyncio
    async def test_unpriced_record_has_null_amount_and_counts_in_rollup(self, store):
        result = await _write(store, [event(provider="unknownco", model="mystery-1")])
        row = store.of("spend_usage_records")[0]
        assert result.unpriced == 1 and row.unpriced and row.amount is None and row.price_source == "none"
        rollup = store.of("spend_usage_rollups")[0]
        assert rollup.unpriced_count == 1 and rollup.unpriced_quantity == Decimal(1000) and rollup.amount == 0

    @pytest.mark.asyncio
    async def test_unconverted_record_has_null_inr_and_counts_unconverted_amount(self, store):
        store.add(card(currency="EUR"))
        await _write(store, [event()])
        row = store.of("spend_usage_records")[0]
        assert row.unconverted and row.amount_inr is None and row.amount == Decimal("0.0025000000")
        rollup = store.of("spend_usage_rollups")[0]
        assert rollup.unconverted_count == 1 and rollup.unconverted_amount == Decimal("0.0025000000")

    @pytest.mark.asyncio
    async def test_late_event_marks_commitments_for_full_recompute(self, store):
        commitment = SpendCommitment(
            id=uuid.uuid4(), tenant_id=TENANT, provider="openai", kind="money", committed_amount=Decimal(10),
            currency="USD", period_start=date(2026, 9, 1), period_end=date(2026, 11, 1), status="active",
            needs_full_recompute=False, recomputed_through=T0 - timedelta(hours=1),
        )  # fmt: skip
        store.add(commitment)
        await _write(store, [event(event_time=T0 - timedelta(hours=1, minutes=30))])
        assert commitment.needs_full_recompute is False  # inside the append grace: the append pass draws it
        await _write(store, [event(event_time=T0 - timedelta(hours=5))])
        assert commitment.needs_full_recompute is True

    @pytest.mark.asyncio
    async def test_busy_rollup_day_answers_busy_with_nothing_written(self, store, monkeypatch):
        from core.spend import locks

        async def held(session, key):
            return False

        monkeypatch.setattr(locks, "try_xact_lock_shared", held)
        result = await _write(store, [event()])
        assert result.busy and store.of("spend_usage_records") == []

    @pytest.mark.asyncio
    async def test_wait_mode_sets_a_lock_timeout(self, store):
        await _write(store, [event()], lock="wait")
        assert any(type(s).__name__ == "TextClause" and "lock_timeout" in str(s) for s in store.statements)

    @pytest.mark.asyncio
    async def test_skip_if_unpriced_drops_and_gaps(self, store):
        result = await _write(
            store,
            [
                event(
                    usage_type="tool_calls",
                    unit="call",
                    provider="toolco",
                    model="lookup",
                    quantity=Decimal(1),
                    skip_if_unpriced=True,
                )
            ],
        )
        assert result.skipped == 1 and store.of("spend_usage_records") == []
        gap = store.of("spend_meter_gaps")[0]
        assert (gap.reason, gap.detail) == ("unpriced_tool", "toolco:lookup")

    @pytest.mark.asyncio
    async def test_precomputed_resolution_and_price_are_kept(self, store):
        day = date(2026, 10, 1)
        usage = pricing.Usage("vllm", "gpu_hours", "gpu_node_hour", Decimal(1), "pool", day, day)
        priced = pricing.price_with(usage, [], lambda c: None)
        fixed = replace(resolver.failed(hints()), unattributed_reason="no_source")
        await _write(
            store,
            [
                event(
                    usage_type="gpu_hours",
                    unit="gpu_node_hour",
                    quantity=Decimal(1),
                    provider="vllm",
                    model="pool",
                    resolved=fixed,
                    priced=priced,
                    billing_account=None,
                )
            ],
        )
        row = store.of("spend_usage_records")[0]
        assert row.unattributed_reason == "no_source" and row.unpriced and row.billing_account == "in_house"

    @pytest.mark.asyncio
    async def test_aliases_canonicalise_the_stored_model(self, store):
        from core.models.spend import SpendModelAlias

        store.add(
            SpendModelAlias(
                id=uuid.uuid4(), tenant_id=TENANT, provider="openai", alias="gpt-4o-2024-08-06", model_sku="gpt-4o"
            )
        )
        store.add(card())
        await _write(store, [event(model="GPT-4o-2024-08-06")])
        row = store.of("spend_usage_records")[0]
        assert row.model == "gpt-4o" and row.price_source == "contract"

    @pytest.mark.asyncio
    async def test_gaps_passed_in_are_written_with_the_records(self, store):
        await _write(store, [event()], gaps={(date(2026, 10, 1), "llm_tokens", "queue_full", ""): 3})
        await _write(store, [], gaps={(date(2026, 10, 1), "llm_tokens", "queue_full", ""): 2})
        gap = store.of("spend_meter_gaps")[0]
        assert gap.count == 5

    def test_rollup_contribution_of_a_written_row(self):
        row = meter._record_row(TENANT, event(), "gpt-4o", resolver.failed(hints()), pricing.unpriced(), None)
        day, dims, delta = rollups.contribution(row)
        assert day == date(2026, 10, 1) and delta.unpriced_count == 1 and delta.call_count == 1
        assert dims[rollups.ROLLUP_DIMS.index("unattributed_reason")] == "resolver_failed"
        assert ACTOR  # the module's fixtures are importable
