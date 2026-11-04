# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights Phase 3 (part A): why a run failed, span summaries, the bundle checksum, change-log kinds, late data."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from core.insights import evidence
from core.insights.freshness import rollup_late
from core.insights.metrics_governance import CHANGE_LABELS, _change_kind


def _span(span_id: str, status: str, offset: int, *, parent: str | None = None, depth: int = 0, events=()):
    return {
        "span_id": span_id,
        "parent_span_id": parent,
        "depth": depth,
        "name": f"agenticorg.{span_id}",
        "status": status,
        "offset_ms": offset,
        "summary": f"summary {span_id}",
        "events": list(events),
    }


def test_the_failure_is_the_error_span_without_an_erroring_child() -> None:
    # An error marks every span above it: the root and the tool call both say error; the tool call is the cause.
    spans = [
        _span("root", "error", 0),
        _span("reason", "ok", 100, parent="root", depth=1),
        _span(
            "tool",
            "error",
            400,
            parent="root",
            depth=1,
            events=[{"name": "exception", "offset_ms": 990, "label": "exception TimeoutError"}],
        ),
        _span("later", "error", 900, parent="root", depth=1),
    ]
    failure = evidence.first_failure({"status": "failed", "error_class": "ToolError"}, spans)
    assert failure == {
        "source": "span",
        "span_id": "tool",
        "name": "agenticorg.tool",
        "summary": "summary tool",
        "exception": "exception TimeoutError",
        "offset_ms": 400,
        "error_class": "ToolError",
    }


def test_a_failed_run_without_error_spans_names_its_error_class() -> None:
    assert evidence.first_failure({"status": "failed", "error_class": "Timeout"}, [_span("root", "ok", 0)]) == {
        "source": "run",
        "error_class": "Timeout",
        "status": "failed",
    }
    for status in ("refused", "limit_exceeded", "rejected"):
        assert evidence.first_failure({"status": status}, [])["status"] == status
    for status in ("completed", "hitl_triggered", "paused"):
        assert evidence.first_failure({"status": status, "error_class": None}, []) is None


def test_span_summaries_show_ids_and_counts_only() -> None:
    assert (
        evidence.span_summary(
            {"llm.provider": "gemini", "llm.model": "gemini-2.5-flash", "llm.input_tokens": 7, "llm.output_tokens": 3}
        )
        == "gemini/gemini-2.5-flash · 7 in / 3 out"
    )
    assert evidence.span_summary({"tool.name": "get_payslip", "connector.id": "zoho", "tool.outcome": "failed"}) == (
        "zoho:get_payslip failed"
    )
    assert evidence.span_summary({"search.results": 0}) == "0 results"
    assert evidence.span_summary({}) == ""
    assert evidence._event_label({"name": "exception", "attributes": {"exception.type": "KeyError"}}) == (
        "exception KeyError"
    )
    assert (
        evidence._event_label(
            {"name": "guardrail.outcome", "attributes": {"stage": "pre_llm", "detector": "pii", "action": "redact"}}
        )
        == "guardrail pre_llm pii redact"
    )


def test_the_bundle_checksum_is_the_sha256_of_its_bytes() -> None:
    document = {"b": [1, 2], "a": "ā", "when": "2026-11-03T00:00:00+00:00"}
    body = evidence.canonical_json(document)
    assert body == '{"a":"ā","b":[1,2],"when":"2026-11-03T00:00:00+00:00"}'.encode()
    assert evidence.canonical_sha256(document) == hashlib.sha256(body).hexdigest()
    # Key order never changes the checksum.
    assert evidence.canonical_sha256(dict(reversed(list(document.items())))) == evidence.canonical_sha256(document)


def test_change_log_kinds() -> None:
    assert _change_kind("agent.create") == "agents"
    assert _change_kind("agent.run") is None  # a run is not a change
    assert _change_kind("governance_config.change") == "governance"
    assert _change_kind("residency_attestation.revoke") == "governance"
    assert _change_kind("model_gateway_limit.update") == "governance"
    assert _change_kind("spend.budget_update") == "spend_controls"
    assert _change_kind("config.reporting.write") == "settings"
    assert _change_kind("insights.bi_access.create") == "access"
    assert _change_kind("insights.export") is None
    assert _change_kind("pack.install") == "packs"
    assert _change_kind("hitl.decided") is None
    assert set(CHANGE_LABELS) >= {"agents", "prompts", "governance", "delegations"}


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    async def execute(self, _statement):
        self.calls += 1
        return _Result(self.value)


async def test_late_data_shows_from_03_00_local_until_yesterday_is_rolled() -> None:
    zone = ZoneInfo("Asia/Kolkata")
    tenant = uuid.uuid4()
    early = datetime(2026, 11, 3, 2, 45, tzinfo=zone)
    late = datetime(2026, 11, 3, 3, 15, tzinfo=zone)
    # Before 03:00 local nothing is late and nothing is read.
    session = _Session(None)
    assert await rollup_late(session, tenant, zone, early.astimezone(UTC)) is False
    assert session.calls == 0
    # No rollup for yesterday, or one made before the day closed: late.
    assert await rollup_late(_Session(None), tenant, zone, late.astimezone(UTC)) is True
    before_close = datetime(2026, 11, 2, 23, 0, tzinfo=zone).astimezone(UTC)
    assert await rollup_late(_Session(before_close), tenant, zone, late.astimezone(UTC)) is True
    # Rolled after it closed: in.
    rolled = datetime(2026, 11, 3, 2, 10, tzinfo=zone).astimezone(UTC)
    assert await rollup_late(_Session(rolled), tenant, zone, late.astimezone(UTC)) is False
    assert date(2026, 11, 2) == (late - timedelta(days=1)).date()


def test_the_debug_reference_comes_from_the_root_span() -> None:
    from types import SimpleNamespace

    from core.insights.evidence import debug_ref

    root = SimpleNamespace(parent_span_id=None, attributes={"agent.thread_id": "thread-9", "agent.id": "a1"})
    child = SimpleNamespace(parent_span_id="s1", attributes={"agent.thread_id": "other"})
    assert debug_ref([child, root], None) == {"agent_id": "a1", "thread_id": "thread-9"}
    # The run's own agent stands in when the span does not name one; no thread, no reference.
    no_agent = SimpleNamespace(parent_span_id=None, attributes={"agent.thread_id": "t"})
    assert debug_ref([no_agent], "a7") == {"agent_id": "a7", "thread_id": "t"}
    assert debug_ref([SimpleNamespace(parent_span_id=None, attributes={})], "a7") is None
    assert debug_ref([], "a7") is None
