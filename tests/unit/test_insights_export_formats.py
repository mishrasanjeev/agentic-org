# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights PowerPoint decks and Parquet tables: typed columns and metadata, chart data, summaries and slides."""

from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from core.insights import export as exports
from core.insights import export_jobs, slides


def test_parquet_column_types() -> None:
    assert exports.parquet_type([1, 2, None]) == "int64"
    assert exports.parquet_type([1, 2.5]) == "float64"
    assert exports.parquet_type([True, None, False]) == "bool"
    assert exports.parquet_type([True, 1]) == "string"  # a flag mixed with numbers is not silently a number
    assert exports.parquet_type(["2026-11-01", None]) == "date32"
    assert exports.parquet_type(["2026-13-01"]) == "string"
    assert exports.parquet_type(["2026-11-01T10:00:00+00:00"]) == "string"
    assert exports.parquet_type([None, None]) == "string" and exports.parquet_type([]) == "string"
    assert exports.parquet_type(["a", 1]) == "string"


def test_a_parquet_export_keeps_types_definitions_and_filters() -> None:
    import pyarrow.parquet as pq

    rows = [
        {"t": "2026-11-01", "group": "=cmd", "value": 1.5, "runs": 3, "ok": True, "detail": {"a": 1}},
        {"t": "2026-11-02", "group": "b", "value": 2, "runs": None, "ok": False, "detail": None},
    ]
    response = {"key": "cost.spend", "title": "Spend", "data_quality": "measured", "period": {"from": "2026-11-01"}}
    content = exports.render("parquet", rows, {"from": "2026-11-01", "pii": "masked"}, response)
    assert content[:4] == b"PAR1" and exports.MEDIA_TYPES["parquet"] == "application/vnd.apache.parquet"
    table = pq.read_table(io.BytesIO(content))
    assert [str(t) for t in table.schema.types] == ["date32[day]", "string", "double", "int64", "bool", "string"]
    assert table.to_pylist()[0] == {
        "t": date(2026, 11, 1),
        "group": "=cmd",  # Parquet is not a spreadsheet: values are not formula-guarded
        "value": 1.5,
        "runs": 3,
        "ok": True,
        "detail": '{"a": 1}',
    }
    meta = {k.decode(): v.decode() for k, v in table.schema.metadata.items()}
    assert meta["insights.key"] == "cost.spend" and meta["insights.data_quality"] == "measured"
    assert meta["insights.period_from"] == "2026-11-01" and meta["insights.hint"] == ""
    assert json.loads(meta["insights.filters"]) == {"from": "2026-11-01", "pii": "masked"}
    empty = pq.read_table(io.BytesIO(exports.render("parquet", [], {}, {"key": "x"})))
    assert empty.num_rows == 0


def test_parquet_is_a_row_format_of_metric_and_explorer_jobs() -> None:
    assert "parquet" in exports.FORMATS
    assert export_jobs.FORMATS_BY_KIND["metric"] == ("csv", "xlsx", "json", "parquet")
    assert export_jobs.FORMATS_BY_KIND["explorer"] == ("csv", "xlsx", "json", "parquet")
    assert export_jobs.FORMATS_BY_KIND["view_pptx"] == ("pptx",)
    assert export_jobs.MEDIA_TYPES["pptx"].endswith("presentationml.presentation")
    with pytest.raises(ValueError, match="format is one of"):
        exports.render("ods", [], {}, {})


def test_a_view_pptx_job_follows_the_view_rules() -> None:
    caller = SimpleNamespace(role="cfo", is_admin=False, user_id=None, domains=["finance"])
    clean = export_jobs.validate("view_pptx", "pptx", {"view": "finance", "compare": "none"}, caller)
    assert clean == {"view": "finance", "compare": "none"}
    with pytest.raises(export_jobs.ExportJobError) as forbidden:
        export_jobs.validate("view_pptx", "pptx", {"view": "compliance"}, caller)
    assert forbidden.value.status == 403
    with pytest.raises(export_jobs.ExportJobError) as unknown:
        export_jobs.validate("view_pptx", "pptx", {"view": "nope"}, caller)
    assert unknown.value.status == 404
    with pytest.raises(export_jobs.ExportJobError) as fmt:
        export_jobs.validate("view_pptx", "pdf", {"view": "finance"}, caller)
    assert fmt.value.error == "invalid_format"


def test_chart_data_lines_bars_and_groups() -> None:
    line = {"title": "Spend", "series": [{"t": "2026-11-02", "value": 2}, {"t": "2026-11-01", "value": "1"}]}
    assert slides.chart_data(line) == ("line", ["2026-11-01", "2026-11-02"], [("Spend", [1.0, 2.0])])
    grouped = {
        "series": [
            {"t": "d1", "group": "a", "value": 1},
            {"t": "d1", "group": "b", "value": 2},
            {"t": "d2", "group": "a", "value": 3},
        ]
    }
    kind, categories, series = slides.chart_data(grouped)
    assert kind == "line" and categories == ["d1", "d2"] and series == [("a", [1.0, 3.0]), ("b", [2.0, None])]
    bars = {
        "title": "By agent",
        "rows": [{"label": "x", "value": 3}, {"name": "y", "count": 1}, {"label": "z"}, "junk"],
    }
    assert slides.chart_data(bars) == ("bar", ["x", "y"], [("By agent", [3.0, 1.0])])
    assert slides.chart_data({"rows": []}) == ("bar", [], [])
    many = {"series": [{"t": f"d{i:02}", "value": i} for i in range(40)]}
    assert len(slides.chart_data(many)[1]) == slides.CHART_POINTS
    assert slides._number(True) is None and slides._number("x") is None


def test_summaries_say_what_the_chart_shows() -> None:
    assert slides.summary(
        {"title": "Spend", "value": 1200, "unit": "usd", "compare_value": 1000, "state": "watch",
         "data_quality": "estimated"},
        "USD",
    ) == "Spend: USD 1,200.00; change +20.0% on the previous period; state watch; data quality estimated, not measured."
    rows = {"title": "By agent", "rows": [{"label": "a", "value": 2}, {"label": "b", "value": 5}],
            "data_quality": "measured"}
    assert slides.summary(rows, None).startswith("By agent: 2 rows, the largest b at 5")
    series = {"title": "Runs", "series": [{"t": "d1", "value": 4}, {"t": "d2", "value": 6}], "data_quality": "measured"}
    assert slides.summary(series, None).startswith("Runs: 2 points, the latest (d2) 6")
    assert slides.summary({"title": "Empty", "data_quality": "no_data"}, None).startswith("Empty: No data")
    assert slides.table_rows({"rows": [{"label": "a", "value": 2}], "unit": "percent"}, None) == [["a", "2.0%"]]
    assert slides.table_rows({"series": [{"t": "d1", "group": "g", "value": 1}]}, None) == [["d1 g", "1"]]


def test_a_view_deck_has_title_tiles_charts_and_data_slides() -> None:
    from pptx import Presentation

    tiles = [{"key": f"t{i}", "title": f"Tile {i}", "value": i, "data_quality": "measured"} for i in range(12)]
    tiles.append({"key": "e", "title": "Empty tile", "value": None, "data_quality": "no_data", "hint": "Add data."})
    charts = [
        {"key": "c", "title": "Chart", "data_quality": "measured", "definition": "Def.", "source": "src",
         "rows": [{"label": f"r{i}", "value": i} for i in range(20)]},
        {"key": "n", "title": "Nothing", "data_quality": "no_data", "hint": "Record runs."},
        {"key": "z", "title": "No rows", "data_quality": "measured", "rows": []},
    ]
    content = slides.view_pptx(title="Insights - Finance", organisation="Acme", period={"from": "a", "to": "b"},
                               filters={"compare": "previous", "company": None}, tiles=tiles, charts=charts,
                               currency="INR", computed_at=datetime(2026, 11, 4, tzinfo=UTC))
    deck = list(Presentation(io.BytesIO(content)).slides)
    headings = [s.shapes[0].text_frame.text.split("\n")[0] for s in deck]
    assert headings == [
        "Insights - Finance",
        "Headline figures",
        "Headline figures (continued)",
        "Why some figures are empty",
        "Chart",
        "Chart: data",
        "Nothing",
        "No rows",
    ]
    assert any(shape.has_chart for shape in deck[4].shapes)
    assert deck[4].notes_slide.notes_text_frame.text == "Def. Source: src."
    data_text = " ".join(shape.text_frame.text for shape in deck[5].shapes if shape.has_text_frame)
    assert "6 more rows in the data export" in data_text
    assert "No data. Record runs." in deck[6].shapes[1].text_frame.text
    assert "Filters: compare: previous" in deck[0].shapes[1].text_frame.text
    empty = slides.view_pptx(title="T", organisation="O", period={}, filters={}, tiles=[], charts=[],
                             currency=None, computed_at=datetime(2026, 11, 4, tzinfo=UTC))
    texts = [sh.text_frame.text for s in Presentation(io.BytesIO(empty)).slides for sh in s.shapes if sh.has_text_frame]
    assert "This view has no headline figures." in texts and "Filters: none\n" in texts[1] + "\n"
