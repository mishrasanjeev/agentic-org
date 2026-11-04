# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights explorers: query parsing, in-memory paging, the cost driver split, the shadow gate and the registry."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from core.insights import explore_registry, explore_sources
from core.insights.explorers import (
    DOMAIN_HEADS,
    ExploreQuery,
    ExploreQueryError,
    Filter,
    apply_query,
    parse_query,
)

RUNS = explore_registry.EXPLORERS["runs"]
AGENTS = explore_registry.EXPLORERS["agents"]


def test_parse_query_reads_filters_sort_and_page() -> None:
    query = parse_query(
        RUNS,
        {
            "filter[status]": "failed,completed",
            "filter[error_class]": "Timeout",
            "filter[duration_ms]": "1000",
            "filter[hitl_required]": "TRUE",
            "sort": "duration_ms",
            "order": "asc",
            "page": "3",
            "page_size": "25",
            "from": "2026-10-01",
        },
    )
    assert query.filters["status"].values == ("failed", "completed")
    assert query.filters["error_class"].text == "Timeout"
    assert query.filters["duration_ms"].minimum == 1000
    assert query.filters["hitl_required"].flag is True
    assert (query.sort, query.order, query.page, query.page_size, query.offset) == ("duration_ms", "asc", 3, 25, 50)
    assert parse_query(RUNS, {}).sort == "started_at" and parse_query(RUNS, {}).order == "desc"


@pytest.mark.parametrize(
    ("params", "error"),
    [
        ({"filter[nope]": "x"}, "filter_not_allowed"),
        ({"filter[agent]": "x"}, "filter_not_allowed"),  # declared, but not filterable
        ({"filter[status]": "exploded"}, "invalid_filter"),
        ({"filter[status]": " , "}, "invalid_filter"),
        ({"filter[status]": ",".join(f"v{i}" for i in range(21))}, "invalid_filter"),
        ({"filter[duration_ms]": "-1"}, "invalid_filter"),
        ({"filter[duration_ms]": "nan"}, "invalid_filter"),
        ({"filter[hitl_required]": "yes"}, "invalid_filter"),
        ({"filter[task_type]": "x" * 101}, "invalid_filter"),
        ({"filters": "status=failed"}, "invalid_filter"),
        ({"sort": "agent"}, "sort_not_allowed"),
        ({"sort": "drop table"}, "sort_not_allowed"),
        ({"order": "sideways"}, "invalid_order"),
        ({"page": "0"}, "invalid_page"),
        ({"page_size": "201"}, "invalid_page"),
        ({"page_size": "ten"}, "invalid_page"),
    ],
)
def test_parse_query_refuses_what_is_not_declared(params, error) -> None:
    with pytest.raises(ExploreQueryError) as refused:
        parse_query(RUNS, params)
    assert refused.value.error == error


def test_apply_query_filters_sorts_with_nulls_last_and_pages() -> None:
    rows = [
        {"agent": "A", "spend": 5.0, "status": "active", "idle_days": 0},
        {"agent": "b", "spend": None, "status": "paused", "idle_days": 40},
        {"agent": "C", "spend": 9.0, "status": "active", "idle_days": 31},
        {"agent": "d", "spend": 1.0, "status": "shadow", "idle_days": None},
    ]
    page, total = apply_query(rows, ExploreQuery(sort="spend", order="desc", page_size=2))
    assert [r["agent"] for r in page] == ["C", "A"] and total == 4
    page, _ = apply_query(rows, ExploreQuery(sort="spend", order="asc", page=2, page_size=2))
    assert [r["agent"] for r in page] == ["C", "b"]  # nulls last in both orders
    idle = ExploreQuery(filters={"idle_days": Filter("idle_days", "min", minimum=30)}, sort="agent", order="asc")
    assert [r["agent"] for r in apply_query(rows, idle)[0]] == ["b", "C"]
    active = ExploreQuery(filters={"status": Filter("status", "enum", values=("active",))}, sort="agent", order="asc")
    assert [r["agent"] for r in apply_query(rows, active)[0]] == ["A", "C"]
    text = ExploreQuery(filters={"agent": Filter("agent", "text", text="B")}, sort="agent")
    assert [r["agent"] for r in apply_query(rows, text)[0]] == ["b"]


def test_decompose_splits_a_change_into_volume_price_and_mix() -> None:
    d = Decimal
    # Twice the tokens at the same price: all volume.
    parts = explore_sources.decompose({"m|tok": (d(100), d(10))}, {"m|tok": (d(200), d(20))})
    assert parts == {"change": d(10), "volume": d(10), "price": d(0), "mix": d(0)}
    assert explore_sources.driver_of(parts) == "volume"
    # Same tokens at twice the price: all price.
    parts = explore_sources.decompose({"m|tok": (d(100), d(10))}, {"m|tok": (d(100), d(20))})
    assert parts["price"] == d(10) and parts["volume"] == 0 and explore_sources.driver_of(parts) == "price"
    # A new model replaces the old one: all mix.
    parts = explore_sources.decompose({"old|tok": (d(100), d(10))}, {"new|tok": (d(100), d(30))})
    assert parts["mix"] == d(20) and explore_sources.driver_of(parts) == "mix"
    # Both move: volume at the old price, price on the old quantity, the interaction in mix; they add up.
    parts = explore_sources.decompose({"m": (d(100), d(10))}, {"m": (d(150), d(30))})
    assert parts["volume"] == d(5) and parts["price"] == d(10) and parts["mix"] == d(5)
    assert parts["volume"] + parts["price"] + parts["mix"] == parts["change"]
    assert explore_sources.driver_of(explore_sources.decompose({}, {})) == "none"


def _shadow(**overrides):
    agent = {
        "status": "shadow",
        "shadow_min_samples": 10,
        "shadow_sample_count": 12,
        "shadow_scored_sample_count": 12,
        "shadow_accuracy_current": Decimal("0.9"),
        "shadow_accuracy_floor": Decimal("0.8"),
        "shadow_feedback_count": 5,
        "shadow_human_confidence_current": Decimal("0.9"),
    }
    agent.update(overrides)
    return SimpleNamespace(**agent)


def test_shadow_gate_mirrors_the_promote_checks() -> None:
    from api.v1 import agents

    # The explorer's floor is the promote endpoint's floor.
    assert explore_sources.MIN_PRODUCTION_SHADOW_ACCURACY == agents._MIN_PRODUCTION_SHADOW_ACCURACY
    ok, reason = explore_sources.shadow_gate(_shadow())
    assert ok and "connectors" in reason
    ok, reason = explore_sources.shadow_gate(_shadow(shadow_scored_sample_count=4, shadow_sample_count=6))
    assert not ok and reason.startswith("4/10 scored samples (6 total)")
    ok, reason = explore_sources.shadow_gate(_shadow(shadow_accuracy_current=None))
    assert not ok and "not computed" in reason
    ok, reason = explore_sources.shadow_gate(_shadow(shadow_accuracy_current=Decimal("0.7")))
    assert not ok and "below the floor 0.8" in reason
    # A row floor under the production floor is raised to it.
    ok, reason = explore_sources.shadow_gate(
        _shadow(shadow_accuracy_floor=Decimal("0.1"), shadow_accuracy_current=Decimal("0.5"))
    )
    assert not ok and "below the floor 0.600" in reason
    ok, reason = explore_sources.shadow_gate(_shadow(shadow_feedback_count=0))
    assert not ok and reason
    assert explore_sources.shadow_gate(_shadow(shadow_min_samples=0))[0] is True


def test_run_links_encode_the_instant() -> None:
    link = explore_sources.run_link("abc", datetime(2026, 11, 2, 4, 30, tzinfo=UTC))
    assert link == "/dashboard/insights/runs/abc?at=2026-11-02T04%3A30%3A00%2B00%3A00"


def test_parse_at_accepts_offsets_and_a_decoded_plus() -> None:
    from core.insights.evidence import parse_at

    moment = datetime(2026, 11, 2, 4, 30, tzinfo=UTC)
    assert parse_at("2026-11-02T04:30:00+00:00") == moment
    assert parse_at("2026-11-02T04:30:00 00:00") == moment
    assert parse_at("2026-11-02T04:30:00Z") == moment
    assert parse_at("2026-11-02T04:30:00") is None  # no offset
    assert parse_at("yesterday") is None and parse_at(None) is None


def test_registry_declarations_are_consistent() -> None:
    assert explore_registry.explorer_keys() == sorted(
        ["agents", "runs", "workflows", "approvals", "cost", "value", "quality", "connectors", "audit"]
    )
    for explorer in explore_registry.EXPLORERS.values():
        keys = [c.key for c in explorer.columns]
        assert len(keys) == len(set(keys)), explorer.key
        default = explorer.column(explorer.default_sort)
        assert default is not None and default.sortable, explorer.key
        for column in explorer.columns:
            assert column.options == () or column.filter == "enum", (explorer.key, column.key)
        assert explorer.title and explorer.definition and explorer.source
    # Administrators read everything; the audit log is theirs and the auditor's; a role claim of admin is not
    # enough.
    assert [e.key for e in explore_registry.readable_explorers("", is_admin=True)] == [
        e.key for e in explore_registry.EXPLORERS.values()
    ]
    assert explore_registry.readable_explorers("admin", is_admin=False) == []
    assert "audit" not in {e.key for e in explore_registry.readable_explorers("cfo", is_admin=False)}
    assert [e.key for e in explore_registry.readable_explorers("coo", is_admin=False)] == [
        "agents",
        "runs",
        "workflows",
        "approvals",
        "cost",
        "quality",
        "connectors",
    ]
    assert [e.key for e in explore_registry.readable_explorers("auditor", is_admin=False)] == [
        "runs",
        "approvals",
        "audit",
    ]
    assert [e.key for e in explore_registry.readable_explorers("developer", is_admin=False)] == [
        "agents",
        "runs",
        "quality",
        "connectors",
    ]
    for role in ("analyst", "domain_lead"):
        assert [e.key for e in explore_registry.readable_explorers(role, is_admin=False)] == [
            "agents",
            "runs",
            "approvals",
            "cost",
            "quality",
        ], role
    for role in ("merchant", ""):
        assert explore_registry.readable_explorers(role, is_admin=False) == [], role
    assert DOMAIN_HEADS == {"cfo", "chro", "cmo", "coo"}


def test_explorer_domains_add_the_operations_alias() -> None:
    from api.v1.insights_explore import explorer_domains

    assert explorer_domains(None) is None
    assert explorer_domains([]) == []
    assert explorer_domains(["ops"]) == ["ops", "operations"]
    assert explorer_domains(["finance", "ops", "ops"]) == ["finance", "ops", "operations"]


def test_explorer_exports_describe_their_columns() -> None:
    from core.insights import export
    from core.insights.explorers import export_rows

    rows = export_rows(RUNS, {"rows": [{"started_at": "x", "status": "failed", "links": {"run": "/r"}, "extra": 1}]})
    assert list(rows[0]) == [c.key for c in RUNS.columns] and rows[0]["status"] == "failed"
    pairs = dict(
        export.definition_rows({"key": "explore.runs", "columns": [("status", "Status", ""), ("a", "A", "d.")]})
    )
    assert pairs["column:status"] == "Status" and pairs["column:a"] == "A. d."
