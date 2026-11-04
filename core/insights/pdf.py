# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights PDFs (fpdf2, light theme): a view, and the summary page of an evidence pack.

Every page carries the title, the organisation, the period, the filters and
when the figures were computed. Tiles are a table (value, state, change and
data quality); each chart is its data table with a simple bar for each row, so
the PDF says what the screen says without a picture to interpret. ``estimated``
and ``no_data`` are written out, never left blank. The core PDF fonts are
Latin-1: text in other scripts prints with ``?`` in place of what they cannot
draw.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

PAGE_ROWS = 12
BAR_WIDTH = 60.0
NO_DATA = "No data"


def latin(text: Any) -> str:
    """Text the core PDF fonts can draw: Latin-1, with the rupee sign spelt out and other characters as '?'."""
    raw = "" if text is None else str(text)
    raw = raw.replace("₹", "INR ").replace("—", "-").replace("–", "-").replace("·", "-").replace("’", "'")
    return raw.encode("latin-1", "replace").decode("latin-1")


def _indian(n: int) -> str:
    s = str(abs(n))
    if len(s) <= 3:
        out = s
    else:
        head, tail = s[:-3], s[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        out = ",".join(groups) + "," + tail
    return ("-" if n < 0 else "") + out


def format_value(value: Any, unit: str | None, currency: str | None = None) -> str:
    """A metric value as the screen writes it: INR with Indian grouping, durations humanised, percent to 0.1."""
    if value is None:
        return NO_DATA
    try:
        number = float(value)
    except (TypeError, ValueError):
        return latin(value)
    if unit == "percent":
        return f"{number:.1f}%"
    if unit == "duration_ms":
        seconds = number / 1000.0
        if seconds < 1:
            return f"{number:.0f} ms"
        if seconds < 60:
            return f"{seconds:.1f} s"
        minutes, rest = divmod(int(round(seconds)), 60)
        if minutes < 60:
            return f"{minutes} m {rest} s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours} h {minutes} m"
    if unit in ("inr", "usd"):
        code = unit.upper()
        whole = int(round(number))
        if code == "INR":
            if abs(number) < 1000 and number != whole:
                return f"INR {number:,.2f}"
            return f"INR {_indian(whole)}"
        return f"USD {number:,.2f}"
    if unit == "hours":
        return f"{number:,.1f} h"
    if currency == "INR" and abs(number) >= 1000 and number == int(number):
        return _indian(int(number))
    if number == int(number):
        return f"{int(number):,}"
    return f"{number:,.2f}"


def _change(metric: dict[str, Any]) -> str:
    value, before = metric.get("value"), metric.get("compare_value")
    try:
        v, b = float(value), float(before)
    except (TypeError, ValueError):
        return "-"
    if b == 0:
        return "-"
    return f"{(v - b) / abs(b) * 100:+.1f}%"


def _rows(metric: dict[str, Any]) -> list[tuple[str, float | None, str]]:
    """(label, number, text) for a chart's table: its rows, else its series points (latest last)."""
    out: list[tuple[str, float | None, str]] = []
    unit = metric.get("unit")
    for row in metric.get("rows") or []:
        if not isinstance(row, dict):
            continue
        label = row.get("label") or row.get("name") or row.get("key") or row.get("t") or ""
        value = row.get("value", row.get("count"))
        try:
            number = float(value) if value is not None else None
        except (TypeError, ValueError):
            number = None
        out.append((latin(label), number, format_value(value, unit) if value is not None else latin(value or "")))
    if not out:
        for point in metric.get("series") or []:
            if not isinstance(point, dict):
                continue
            label = point.get("t", "") + (f" {point['group']}" if point.get("group") else "")
            value = point.get("value")
            try:
                number = float(value) if value is not None else None
            except (TypeError, ValueError):
                number = None
            out.append((latin(label), number, format_value(value, unit)))
    return out


class _Doc:
    """A thin wrapper over FPDF with the page frame every Insights PDF shares."""

    def __init__(self, title: str, organisation: str, period: str, filters: str, computed: str) -> None:
        from fpdf import FPDF

        frame = {"title": latin(title), "sub": latin(f"{organisation} - {period}"), "filters": latin(filters)}
        footer_text = latin(f"Computed {computed}. AgenticOrg Insights.")

        class _PDF(FPDF):
            def header(self) -> None:  # noqa: D401 - fpdf hook
                self.set_font("Helvetica", "B", 13)
                self.cell(0, 7, frame["title"], new_x="LMARGIN", new_y="NEXT")
                self.set_font("Helvetica", "", 9)
                self.cell(0, 5, frame["sub"], new_x="LMARGIN", new_y="NEXT")
                if frame["filters"]:
                    self.multi_cell(0, 4, frame["filters"], new_x="LMARGIN", new_y="NEXT")
                self.ln(2)

            def footer(self) -> None:  # noqa: D401 - fpdf hook
                self.set_y(-12)
                self.set_font("Helvetica", "", 8)
                self.cell(0, 5, f"{footer_text}  Page {self.page_no()}", align="C")

        self.pdf = _PDF(orientation="P", unit="mm", format="A4")
        self.pdf.set_auto_page_break(auto=True, margin=15)
        self.pdf.add_page()

    def heading(self, text: str) -> None:
        self.pdf.ln(2)
        self.pdf.set_font("Helvetica", "B", 11)
        self.pdf.cell(0, 6, latin(text), new_x="LMARGIN", new_y="NEXT")

    def para(self, text: str, size: int = 9) -> None:
        self.pdf.set_font("Helvetica", "", size)
        self.pdf.multi_cell(0, 4.5, latin(text), new_x="LMARGIN", new_y="NEXT")

    def table(self, headers: list[str], rows: list[list[str]], widths: list[float]) -> None:
        pdf = self.pdf
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(240, 240, 240)
        for header, width in zip(headers, widths, strict=True):
            pdf.cell(width, 5, latin(header), border=1, fill=True)
        pdf.ln()
        pdf.set_font("Helvetica", "", 8)
        for row in rows:
            for value, width in zip(row, widths, strict=True):
                text = latin(value)
                while text and pdf.get_string_width(text) > width - 2:
                    text = text[:-2] + "~"
                pdf.cell(width, 5, text, border=1)
            pdf.ln()

    def bars(self, rows: list[tuple[str, float | None, str]]) -> None:
        pdf = self.pdf
        numbers = [abs(n) for _, n, _ in rows if n is not None]
        top = max(numbers) if numbers else 0.0
        pdf.set_font("Helvetica", "", 8)
        for label, number, text in rows[:PAGE_ROWS]:
            y = pdf.get_y()
            short = latin(label)
            while short and pdf.get_string_width(short) > 58:
                short = short[:-2] + "~"
            pdf.cell(60, 5, short)
            width = (abs(number) / top * BAR_WIDTH) if (number is not None and top) else 0.0
            if width:
                pdf.set_fill_color(79, 70, 229)
                pdf.rect(pdf.get_x(), y + 1, max(width, 0.5), 3, style="F")
            pdf.set_x(pdf.get_x() + BAR_WIDTH + 4)
            pdf.cell(40, 5, latin(text), new_x="LMARGIN", new_y="NEXT")
        if len(rows) > PAGE_ROWS:
            self.para(f"... and {len(rows) - PAGE_ROWS} more rows in the data export.", 8)

    def output(self) -> bytes:
        return bytes(self.pdf.output())


def view_pdf(
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
    """One Insights view as a PDF: the tiles as a table, then each chart's data with a bar per row."""
    shown = ", ".join(f"{k}: {v}" for k, v in filters.items() if v not in (None, "", []))
    doc = _Doc(
        title,
        organisation,
        f"{period.get('from')} to {period.get('to')}",
        f"Filters: {shown}" if shown else "",
        computed_at.strftime("%d %b %Y %H:%M UTC"),
    )
    doc.heading("Headline figures")
    doc.table(
        ["Metric", "Value", "State", "Change", "Data quality"],
        [
            [
                str(m.get("title") or m.get("key")),
                format_value(m.get("value"), m.get("unit"), currency),
                str(m.get("state") or "-"),
                _change(m),
                str(m.get("data_quality") or NO_DATA).replace("_", " "),
            ]
            for m in tiles
        ],
        [70, 35, 20, 22, 33],
    )
    missing = [m for m in tiles if m.get("data_quality") == "no_data" and m.get("hint")]
    if missing:
        doc.heading("Why some figures are empty")
        for m in missing:
            doc.para(f"{m.get('title')}: {m.get('hint')}")
    for metric in charts:
        doc.heading(str(metric.get("title") or metric.get("key")))
        quality = metric.get("data_quality") or NO_DATA
        if quality == "no_data":
            doc.para(f"No data. {metric.get('hint') or ''}")
            continue
        label = "estimated, not measured" if quality == "estimated" else str(quality).replace("_", " ")
        doc.para(f"{metric.get('definition') or ''} Data quality: {label}.", 8)
        doc.bars(_rows(metric))
    return doc.output()


def summary_pdf(
    *, title: str, organisation: str, period: str, sections: list[tuple[str, list[str]]], computed_at: datetime
) -> bytes:
    """A text summary (the evidence pack's first page): headed sections of plain lines."""
    doc = _Doc(title, organisation, period, "", computed_at.strftime("%d %b %Y %H:%M UTC"))
    for heading, lines in sections:
        doc.heading(heading)
        for line in lines:
            doc.para(line)
    return doc.output()


def client_pdf(
    *,
    client: str,
    organisation: str,
    period: str,
    facts: list[tuple[str, str]],
    deadlines: list[list[str]],
    invoices: list[list[str]],
    computed_at: datetime,
) -> bytes:
    """One client company's page in the partner client pack: its facts, open deadlines and the period's invoices."""
    doc = _Doc(f"Client report - {client}", organisation, period, "", computed_at.strftime("%d %b %Y %H:%M UTC"))
    doc.heading("Summary")
    doc.table(["", "Value"], [[label, value] for label, value in facts], [90, 90])
    doc.heading("Deadlines not yet filed (overdue and next 30 days)")
    if deadlines:
        doc.table(["Type", "Period", "Due", "State"], deadlines, [55, 45, 40, 40])
    else:
        doc.para("No deadline is open for this client.")
    doc.heading("Invoices issued in the period")
    if invoices:
        doc.table(["Invoice", "Issued", "Total", "Balance due", "Status"], invoices, [40, 30, 40, 40, 30])
    else:
        doc.para("No invoice was issued to this client in the period.")
    return doc.output()

