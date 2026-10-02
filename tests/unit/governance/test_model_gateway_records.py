# SPDX-License-Identifier: Apache-2.0
"""Routing records: token and cost extraction, signing, metering and the best-effort write."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from core.governance import model_gateway as gw
from core.governance import model_gateway_records as rec

TENANT = str(uuid.uuid4())


def _decision(**over) -> gw.RouteDecision:
    base = {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "correlation_id": "req-1",
        "reason": "policy p",
        "applied": True,
        "policy_id": "p1",
        "access_policy_id": "a1",
        "gated": True,
        "tenant_id": TENANT,
        "use_case": "agent_run",
        "requested_provider": "openai",
        "requested_model": "gpt-4o",
    }
    base.update(over)
    return gw.RouteDecision(**base)


class TestTokens:
    def test_langchain_usage_metadata(self):
        message = AIMessage(content="x", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
        assert rec.message_tokens(message) == (10, 5, 15)

    def test_google_response_metadata(self):
        message = AIMessage(
            content="x",
            response_metadata={"usage_metadata": {"prompt_token_count": 7, "candidates_token_count": 3}},
        )
        assert rec.message_tokens(message) == (7, 3, 10)
        message = AIMessage(content="x", response_metadata={"token_usage": {"total_tokens": 4}})
        assert rec.message_tokens(message) == (None, None, 4)

    def test_no_metadata(self):
        assert rec.message_tokens(AIMessage(content="x")) == (None, None, 0)
        assert rec.message_tokens(SimpleNamespace()) == (None, None, 0)

    def test_cost_uses_the_list_price_where_known_and_the_blended_estimate_otherwise(self):
        from core.langgraph.runner import _BLENDED_COST_PER_1K_TOKENS_USD
        from core.llm.router import gemini_cost_usd

        gemini = rec.estimate_cost_usd("gemini", "gemini-2.5-flash", input_tokens=1000, output_tokens=500, tokens=1500)
        assert gemini == round(gemini_cost_usd("gemini-2.5-flash", 1000, 500), 6) and gemini > 0
        blended = rec.estimate_cost_usd("openai", "gpt-4o", input_tokens=None, output_tokens=None, tokens=2000)
        assert blended == round(2000 * _BLENDED_COST_PER_1K_TOKENS_USD / 1000, 6)
        assert (
            rec.estimate_cost_usd("gemini", "gemini-2.5-flash", input_tokens=None, output_tokens=None, tokens=0) == 0.0
        )


class TestSigning:
    def _record(self) -> rec.ModelCallRecord:
        return rec.ModelCallRecord(
            tenant_id=TENANT,
            correlation_id="req-1",
            use_case="agent_run",
            agent_id="a1",
            policy_id="p1",
            access_policy_id=None,
            requested_provider="openai",
            requested_model="gpt-4o",
            provider="gemini",
            model="gemini-2.5-flash",
            fallback_from=None,
            restricted=False,
            outcome="completed",
            error_type=None,
            latency_ms=120,
            admission_wait_ms=3,
            tokens=15,
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.000123,
            tokens_per_second=41.67,
            created_at=datetime(2026, 10, 2, 12, 0, tzinfo=UTC),
        )

    def test_a_row_verifies_until_a_field_changes(self):
        record = self._record()
        secret = b"ci-test-secret-key-minimum-16"
        signature = rec.sign_record(record, secret)
        row = {**record.to_dict(), "created_at": record.created_at, "signature": signature}
        assert rec.verify_record(row, secret) is True
        assert rec.verify_record({**row, "cost_usd": 9.0}, secret) is False
        assert rec.verify_record({**row, "signature": ""}, secret) is False
        assert rec.verify_record(SimpleNamespace(**row), secret) is True

    def test_the_canonical_payload_is_the_same_for_a_dataclass_a_dict_and_a_row(self):
        record = self._record()
        as_dict = {**record.to_dict(), "created_at": record.created_at}
        assert rec.canonical_record_payload(record) == rec.canonical_record_payload(as_dict)
        assert rec.canonical_record_payload(SimpleNamespace(**as_dict)) == rec.canonical_record_payload(record)
        assert "tokens_per_second" not in rec.canonical_record_payload(record)


class TestRecordModelCall:
    @pytest.fixture
    def captured(self):
        metered: list[rec.ModelCallRecord] = []
        written: list[rec.ModelCallRecord] = []

        async def fake_write(record):
            written.append(record)
            return True

        with (
            patch.object(rec, "_meter", lambda record: metered.append(record)),
            patch.object(rec, "_write", fake_write),
        ):
            yield metered, written

    def test_a_routed_call_is_metered_and_written_with_its_decision(self, captured):
        metered, written = captured
        record = asyncio.run(
            rec.record_model_call(
                _decision(),
                provider="gemini",
                model="gemini-2.5-flash",
                outcome="completed",
                latency_ms=500,
                tokens=150,
                input_tokens=100,
                output_tokens=50,
                admission_wait_ms=2,
            )
        )
        assert metered == [record] and written == [record]
        assert record.tenant_id == TENANT and record.correlation_id == "req-1" and record.use_case == "agent_run"
        assert record.policy_id == "p1" and record.access_policy_id == "a1"
        assert record.requested_provider == "openai" and record.requested_model == "gpt-4o"
        assert record.tokens_per_second == 100.0 and record.cost_usd > 0 and record.admission_wait_ms == 2

    def test_an_unrouted_call_is_metered_but_not_written(self, captured):
        metered, written = captured
        record = asyncio.run(rec.record_model_call(provider=None, model="gpt-4o", outcome="completed", latency_ms=0))
        assert metered == [record] and written == []
        assert record.provider == "unknown" and record.tenant_id is None and record.tokens_per_second is None
        assert len(record.correlation_id) == 32

    def test_throughput_needs_the_output_count_never_the_total(self, captured):
        _metered, _written = captured
        only_total = asyncio.run(
            rec.record_model_call(
                _decision(), provider="gemini", model="m", outcome="completed", latency_ms=1000, tokens=500
            )
        )
        assert only_total.tokens_per_second is None and only_total.tokens == 500
        split = asyncio.run(
            rec.record_model_call(
                _decision(),
                provider="gemini",
                model="m",
                outcome="completed",
                latency_ms=1000,
                tokens=500,
                input_tokens=400,
                output_tokens=100,
            )
        )
        assert split.tokens_per_second == 100.0

    def test_a_call_under_a_gateway_that_was_off_is_not_written(self, captured):
        _metered, written = captured
        asyncio.run(
            rec.record_model_call(
                _decision(gated=False), provider="gemini", model="m", outcome="completed", latency_ms=1
            )
        )
        assert written == []

    def test_the_bound_route_supplies_the_decision_use_case_and_agent(self, captured):
        _metered, written = captured
        decision = _decision(use_case="agent_resume")
        token = gw.bind_route(decision, use_case="agent_resume", agent_id="a-9")
        try:
            record = asyncio.run(rec.record_model_call(provider="gemini", model="m", outcome="completed", latency_ms=1))
        finally:
            gw.reset_route(token)
        assert record.use_case == "agent_resume" and record.agent_id == "a-9" and written == [record]
        assert record.correlation_id == "req-1"

    def test_a_failure_records_the_error_type_and_an_unknown_outcome_is_a_failure(self, captured):
        metered, _written = captured
        record = asyncio.run(
            rec.record_model_call(
                _decision(), provider="gemini", model="m", outcome="failed", latency_ms=5, error_type="TimeoutError"
            )
        )
        assert record.outcome == "failed" and record.error_type == "TimeoutError" and record.cost_usd == 0.0
        odd = asyncio.run(
            rec.record_model_call(_decision(), provider="gemini", model="m", outcome="weird", latency_ms=5)
        )
        assert odd.outcome == "failed" and len(metered) == 2

    def test_a_fallback_keeps_the_model_it_fell_back_from(self, captured):
        _metered, written = captured
        record = asyncio.run(
            rec.record_model_call(
                _decision(),
                provider="gemini",
                model="gemini-2.5-flash",
                outcome="completed",
                latency_ms=5,
                fallback_from="gemini-2.5-pro",
                cost_usd=0.01,
            )
        )
        assert record.fallback_from == "gemini-2.5-pro" and record.cost_usd == 0.01 and written == [record]


class TestWrite:
    def _record(self, tenant=TENANT) -> rec.ModelCallRecord:
        return rec.ModelCallRecord(
            tenant_id=tenant,
            correlation_id="req-1",
            use_case="agent_run",
            agent_id=None,
            policy_id=None,
            access_policy_id=None,
            requested_provider=None,
            requested_model=None,
            provider="gemini",
            model="gemini-2.5-flash",
            fallback_from=None,
            restricted=False,
            outcome="completed",
            error_type=None,
            latency_ms=10,
            admission_wait_ms=None,
            tokens=0,
            input_tokens=None,
            output_tokens=None,
            cost_usd=0.0,
            tokens_per_second=None,
            created_at=datetime.now(UTC),
        )

    def test_the_row_is_added_with_a_verifying_signature(self, monkeypatch):
        monkeypatch.setattr(rec.settings, "secret_key", "ci-test-secret-key-minimum-16")
        monkeypatch.setattr(rec.settings, "model_gateway_records_enabled", True)
        added: list = []
        session = SimpleNamespace(add=added.append)

        @contextlib.asynccontextmanager
        async def _ctx(_tid):
            yield session

        monkeypatch.setattr("core.database.get_tenant_session", _ctx)
        assert asyncio.run(rec._write(self._record())) is True
        [row] = added
        assert type(row).__name__ == "ModelGatewayRecord" and str(row.tenant_id) == TENANT
        assert rec.verify_record(row, b"ci-test-secret-key-minimum-16") is True

    def test_records_are_off_by_default_and_a_call_without_a_tenant_writes_nothing(self, monkeypatch):
        assert rec.settings.model_gateway_records_enabled is False
        with patch("core.database.get_tenant_session") as sessions:
            assert asyncio.run(rec._write(self._record())) is False
        sessions.assert_not_called()
        monkeypatch.setattr(rec.settings, "model_gateway_records_enabled", True)
        assert asyncio.run(rec._write(self._record(tenant=None))) is False

    def test_a_database_failure_is_logged_never_raised(self, monkeypatch):
        monkeypatch.setattr(rec.settings, "model_gateway_records_enabled", True)

        @contextlib.asynccontextmanager
        async def _ctx(_tid):
            raise RuntimeError("db down")
            yield  # pragma: no cover

        monkeypatch.setattr("core.database.get_tenant_session", _ctx)
        assert asyncio.run(rec._write(self._record())) is False

    def test_the_metrics_are_best_effort(self):
        with patch("observability.metrics.model_calls_total", None):
            rec._meter(self._record())  # a missing metric never changes a record


class TestCorrelationId:
    def test_the_bound_request_id_is_the_correlation_id(self, monkeypatch):
        import structlog

        monkeypatch.setattr(gw.settings, "model_gateway_enabled", True)
        monkeypatch.setattr(gw.settings, "env", "test")
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id="req-abc")
        try:
            with patch.object(gw, "active_policy_set", AsyncMock(return_value=gw.PolicySet())):
                decision = asyncio.run(
                    gw.decide(
                        gw.RouteRequest(tenant_id=TENANT, use_case="completion", requested_model="gemini-2.5-flash")
                    )
                )
                explicit = asyncio.run(
                    gw.decide(
                        gw.RouteRequest(
                            tenant_id=TENANT,
                            use_case="completion",
                            requested_model="gemini-2.5-flash",
                            correlation_id="mine",
                        )
                    )
                )
        finally:
            structlog.contextvars.clear_contextvars()
        assert decision.correlation_id == "req-abc" and explicit.correlation_id == "mine"
        assert decision.requested_model == "gemini-2.5-flash" and decision.requested_provider is None
        with patch.object(gw, "active_policy_set", AsyncMock(return_value=gw.PolicySet())):
            fresh = asyncio.run(
                gw.decide(gw.RouteRequest(tenant_id=TENANT, use_case="completion", requested_model="gemini-2.5-flash"))
            )
        assert len(fresh.correlation_id) == 32
        assert gw.request_correlation_id() is None
