# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights views as PowerPoint decks (python-pptx, 16:9, light theme).

A deck says what the screen says: a title slide (view, organisation, period,
filters, when it was computed and the data quality of its figures), the tiles
as a table (value, state, change, data quality), and for each chart a native
PowerPoint chart with its text summary under it, followed by the chart's data
as a table. ``estimated`` and ``no_data`` are written out; a chart with no data
is a slide that says so and why, with nothing drawn. Each chart slide's notes
carry the metric's definition and source.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any

from core.insights.pdf import NO_DATA, _change, format_value

TILE_ROWS_PER_SLIDE = 10
TABLE_ROWS_PER_SLIDE = 14
CHART_POINTS = 24
MAX_SERIES = 6


def _quality(metric: dict[str, Any]) -> str:
    quality = str(metric.get("data_quality") or "no_data")
    return {"estimated": "estimated, not measured", "no_data": "no data"}.get(quality, quality.replace("_", " "))


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def chart_data(metric: dict[str, Any]) -> tuple[str, list[str], list[tuple[str, list[float | None]]]]:
    """``(kind, categories, series)`` for a metric: ``line`` over its series (one line per group), else ``bar``
    over its rows. Empty categories mean there is nothing to draw."""
    points = [p for p in metric.get("series") or [] if isinstance(p, dict)]
    if points:
        groups: list[str] = []
        for point in points:
            group = str(point.get("group") or "")
            if group not in groups:
                groups.append(group)
        categories = sorted({str(p.get("t") or "") for p in points})[-CHART_POINTS:]
        values: dict[tuple[str, str], float | None] = {
            (str(p.get("t") or ""), str(p.get("group") or "")): _number(p.get("value")) for p in points
        }
        series = [
            (
                group or str(metric.get("title") or metric.get("key") or "value"),
                [values.get((t, group)) for t in categories],
            )
            for group in groups[:MAX_SERIES]
        ]
        return "line", categories, series
    labels: list[str] = []
    numbers: list[float | None] = []
    for row in metric.get("rows") or []:
        if not isinstance(row, dict):
            continue
        number = _number(row.get("value", row.get("count")))
        if number is None:
            continue
        labels.append(str(row.get("label") or row.get("name") or row.get("key") or row.get("t") or ""))
        numbers.append(number)
    labels, numbers = labels[:CHART_POINTS], numbers[:CHART_POINTS]
    return "bar", labels, [(str(metric.get("title") or "value"), numbers)] if labels else []


def table_rows(metric: dict[str, Any], currency: str | None) -> list[list[str]]:
    """The chart's data as (label, value) rows, as the data export carries them."""
    unit = metric.get("unit")
    out: list[list[str]] = []
    for row in metric.get("rows") or []:
        if isinstance(row, dict):
            label = row.get("label") or row.get("name") or row.get("key") or row.get("t") or ""
            out.append([str(label), format_value(row.get("value", row.get("count")), unit, currency)])
    if not out:
        for point in metric.get("series") or []:
            if isinstance(point, dict):
                label = str(point.get("t") or "") + (f" {point['group']}" if point.get("group") else "")
                out.append([label, format_value(point.get("value"), unit, currency)])
    return out


def summary(metric: dict[str, Any], currency: str | None) -> str:
    """The chart's text summary: its value (or its largest row), change and state, and how it was measured."""
    name = metric.get("title") or metric.get("key")
    if metric.get("value") is not None:
        parts = [f"{name}: {format_value(metric.get('value'), metric.get('unit'), currency)}"]
    else:
        kind, categories, series = chart_data(metric)
        values = series[0][1] if series else []
        pairs = [(c, v) for c, v in zip(categories, values, strict=False) if v is not None]
        if kind == "bar" and pairs:
            label, top = max(pairs, key=lambda p: p[1])
            parts = [
                f"{name}: {len(pairs)} rows, the largest {label} at {format_value(top, metric.get('unit'), currency)}"
            ]
        elif pairs:
            last, value = pairs[-1]
            shown = format_value(value, metric.get("unit"), currency)
            parts = [f"{name}: {len(categories)} points, the latest ({last}) {shown}"]
        else:
            parts = [f"{name}: {NO_DATA}"]
    change = _change(metric)
    if change != "-":
        parts.append(f"change {change} on the previous period")
    if metric.get("state"):
        parts.append(f"state {metric['state']}")
    parts.append(f"data quality {_quality(metric)}")
    return "; ".join(parts) + "."


class _Deck:
    def __init__(self) -> None:
        from pptx import Presentation
        from pptx.util import Inches

        self.prs = Presentation()
        self.prs.slide_width = Inches(13.333)
        self.prs.slide_height = Inches(7.5)
        self.blank = self.prs.slide_layouts[6]

    def slide(self, heading: str, sub: str = "") -> Any:
        from pptx.util import Inches, Pt

        slide = self.prs.slides.add_slide(self.blank)
        box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(12.3), Inches(0.8)).text_frame
        box.word_wrap = True
        box.text = heading
        box.paragraphs[0].runs[0].font.size = Pt(26)
        box.paragraphs[0].runs[0].font.bold = True
        if sub:
            para = box.add_paragraph()
            para.text = sub
            para.runs[0].font.size = Pt(12)
        return slide

    def text(self, slide: Any, lines: list[str], top: float, height: float, size: int = 12) -> None:
        from pptx.util import Inches, Pt

        frame = slide.shapes.add_textbox(Inches(0.5), Inches(top), Inches(12.3), Inches(height)).text_frame
        frame.word_wrap = True
        for i, line in enumerate(lines):
            para = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
            para.text = line
            for run in para.runs:
                run.font.size = Pt(size)

    def table(self, slide: Any, headers: list[str], rows: list[list[str]], top: float = 1.4) -> None:
        from pptx.util import Inches, Pt

        shape = slide.shapes.add_table(
            len(rows) + 1, len(headers), Inches(0.5), Inches(top), Inches(12.3), Inches(0.4 * (len(rows) + 1))
        )
        table = shape.table
        for col, header in enumerate(headers):
            table.cell(0, col).text = header
        for r, row in enumerate(rows, start=1):
            for col, value in enumerate(row):
                table.cell(r, col).text = str(value)
        for r in range(len(rows) + 1):
            for col in range(len(headers)):
                for para in table.cell(r, col).text_frame.paragraphs:
                    for run in para.runs:
                        run.font.size = Pt(11)

    def chart(self, slide: Any, kind: str, categories: list[str], series: list[tuple[str, list[float | None]]]) -> None:
        from pptx.chart.data import CategoryChartData
        from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
        from pptx.util import Inches

        data = CategoryChartData()
        data.categories = categories
        for name, values in series:
            data.add_series(name, values)
        chart_type = XL_CHART_TYPE.LINE_MARKERS if kind == "line" else XL_CHART_TYPE.BAR_CLUSTERED
        frame = slide.shapes.add_chart(chart_type, Inches(0.5), Inches(1.3), Inches(12.3), Inches(4.6), data)
        chart = frame.chart
        chart.has_legend = len(series) > 1
        if chart.has_legend:
            chart.legend.position = XL_LEGEND_POSITION.BOTTOM
            chart.legend.include_in_layout = False

    def notes(self, slide: Any, text: str) -> None:
        slide.notes_slide.notes_text_frame.text = text

    def output(self) -> bytes:
        buffer = io.BytesIO()
        self.prs.save(buffer)
        return buffer.getvalue()


def view_pptx(
    *,
    title: str,
    organisation: str,
    period: dict[str, Any],
    filters: dict[str, Any],
    tiles: list[dict[str, Any]],
    charts: list[dict[str, Any]],
    currency: str | None,
    computed_at: datetime,
) -> bytes:
    """One Insights view as a deck: title, tiles, then a chart slide and a data slide for each chart."""
    deck = _Deck()
    shown = ", ".join(f"{k}: {v}" for k, v in filters.items() if v not in (None, "", []))
    qualities = sorted({_quality(m) for m in [*tiles, *charts]})
    first = deck.slide(title, f"{organisation} · {period.get('from')} to {period.get('to')}")
    deck.text(
        first,
        [
            f"Filters: {shown}" if shown else "Filters: none",
            f"Computed {computed_at.strftime('%d %b %Y %H:%M UTC')}",
            f"Data quality of these figures: {', '.join(qualities) if qualities else 'no data'}",
            "Estimated figures are labelled estimated and are not measured.",
        ],
        top=1.6,
        height=3.0,
        size=16,
    )
    rows = [
        [
            str(m.get("title") or m.get("key")),
            format_value(m.get("value"), m.get("unit"), currency),
            str(m.get("state") or "-"),
            _change(m),
            _quality(m),
        ]
        for m in tiles
    ]
    for start in range(0, len(rows), TILE_ROWS_PER_SLIDE) or [0]:
        slide = deck.slide("Headline figures" if start == 0 else "Headline figures (continued)")
        chunk = rows[start : start + TILE_ROWS_PER_SLIDE]
        if chunk:
            deck.table(slide, ["Metric", "Value", "State", "Change", "Data quality"], chunk)
        else:
            deck.text(slide, ["This view has no headline figures."], top=1.6, height=1.0)
    missing = [m for m in tiles if m.get("data_quality") == "no_data" and m.get("hint")]
    if missing:
        slide = deck.slide("Why some figures are empty")
        deck.text(slide, [f"{m.get('title')}: {m.get('hint')}" for m in missing][:12], top=1.4, height=5.5)
    for metric in charts:
        name = str(metric.get("title") or metric.get("key"))
        slide = deck.slide(name)
        notes = f"{metric.get('definition') or ''} Source: {metric.get('source') or '-'}.".strip()
        deck.notes(slide, notes)
        if metric.get("data_quality") == "no_data":
            deck.text(slide, [f"{NO_DATA}. {metric.get('hint') or ''}".strip()], top=1.6, height=1.5, size=16)
            continue
        kind, categories, series = chart_data(metric)
        if categories and series:
            deck.chart(slide, kind, categories, series)
        else:
            deck.text(slide, ["Nothing to chart: the figure has no rows."], top=1.6, height=1.0)
        deck.text(slide, [summary(metric, currency)], top=6.1, height=1.0, size=12)
        data = table_rows(metric, currency)
        if data:
            table_slide = deck.slide(f"{name}: data")
            deck.notes(table_slide, notes)
            deck.table(table_slide, ["Label", "Value"], data[:TABLE_ROWS_PER_SLIDE])
            if len(data) > TABLE_ROWS_PER_SLIDE:
                deck.text(
                    table_slide,
                    [f"... and {len(data) - TABLE_ROWS_PER_SLIDE} more rows in the data export."],
                    top=7.0,
                    height=0.4,
                    size=10,
                )
    return deck.output()
