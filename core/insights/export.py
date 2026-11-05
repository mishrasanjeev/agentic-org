# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights exports: one metric's data as CSV, XLSX, JSON or Parquet, with its filters and definition.

The exported rows are the metric's ``series`` points (``t``, ``group``,
``value``) or, for a metric without a series, its ``rows``. At most
``MAX_EXPORT_ROWS`` rows are exported; a larger result is refused (413) with a
hint to narrow the range. Every text cell of a CSV or XLSX file that a
spreadsheet would read as a formula (it starts with ``=``, ``+``, ``-``,
``@``, a tab, a carriage return or a line feed) is prefixed with ``'``. An
XLSX file has three sheets: ``Data``, ``Filters`` and ``Definitions``. Each
export writes one signed ``audit_log`` row (``insights.export``) with the
metric key, format, filters and row count, never the figures.

A Parquet file keeps each column's type: a column whose values are all whole
numbers is ``int64``, all numbers ``float64``, all booleans ``bool``, all
``YYYY-MM-DD`` dates ``date32``; anything else (and an object or list, as
JSON) is text. Its key-value metadata carries the definitions (``insights.*``)
and the filters (``insights.filters``, JSON). Parquet is read by tools, not
evaluated as a spreadsheet, so its cells are not formula-guarded.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date
from typing import Any

MAX_EXPORT_ROWS = 50_000
FORMATS: tuple[str, ...] = ("csv", "xlsx", "json", "parquet")
MEDIA_TYPES: dict[str, str] = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
    "parquet": "application/vnd.apache.parquet",
}
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")
EXPORT_TOO_LARGE_HINT = (
    f"An export is at most {MAX_EXPORT_ROWS:,} rows. Narrow the date range, choose a coarser "
    "granularity or remove the grouping."
)


class ExportTooLargeError(ValueError):
    """More rows than an export may carry."""

    def __init__(self, rows: int) -> None:
        super().__init__(EXPORT_TOO_LARGE_HINT)
        self.rows = rows


def guard_cell(value: Any) -> Any:
    """A text cell a spreadsheet would evaluate as a formula, made literal with a leading ``'``."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)


def export_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    """The rows an export of ``response`` carries: its series points, else its rows."""
    series = response.get("series") or []
    if series:
        rows = []
        for point in series:
            row = {"t": point.get("t")}
            if "group" in point:
                row["group"] = point.get("group")
            row["value"] = point.get("value")
            rows.append(row)
        return rows
    return [dict(row) for row in response.get("rows") or []]


def check_size(rows: list[dict[str, Any]]) -> None:
    if len(rows) > MAX_EXPORT_ROWS:
        raise ExportTooLargeError(len(rows))


def columns(rows: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for row in rows:
        for key in row:
            if key not in seen:
                seen.append(key)
    return seen


def definition_rows(response: dict[str, Any]) -> list[tuple[str, Any]]:
    """The metric's description, one (field, value) pair per line."""
    period = response.get("period") or {}
    pairs: list[tuple[str, Any]] = [
        ("key", response.get("key")),
        ("title", response.get("title")),
        ("definition", response.get("definition")),
        ("unit", response.get("unit")),
        ("currency", response.get("currency")),
        ("source", response.get("source")),
        ("data_quality", response.get("data_quality")),
        ("computed_at", response.get("computed_at")),
        ("period_from", period.get("from")),
        ("period_to", period.get("to")),
        ("granularity", period.get("granularity")),
        ("value", response.get("value")),
        ("state", response.get("state")),
        ("hint", response.get("hint")),
    ]
    conversion = response.get("conversion")
    if conversion:
        pairs.append(("conversion", json.dumps(conversion, sort_keys=True)))
    # An explorer export describes each column it carries.
    for key, title, description in response.get("columns") or []:
        pairs.append((f"column:{key}", f"{title}. {description}".strip() if description else title))
    return pairs


def to_csv(rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO()
    header = columns(rows)
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([guard_cell(name) for name in header])
    for row in rows:
        writer.writerow([guard_cell(_cell(row.get(name))) for name in header])
    return buffer.getvalue().encode("utf-8")


def to_xlsx(rows: list[dict[str, Any]], filters: dict[str, Any], response: dict[str, Any]) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook(write_only=True)
    data = workbook.create_sheet("Data")
    header = columns(rows)
    data.append([guard_cell(name) for name in header])
    for row in rows:
        data.append([guard_cell(_cell(row.get(name))) for name in header])
    sheet = workbook.create_sheet("Filters")
    sheet.append(["filter", "value"])
    for name, value in filters.items():
        sheet.append([guard_cell(str(name)), guard_cell(_cell(value))])
    sheet = workbook.create_sheet("Definitions")
    sheet.append(["field", "value"])
    for name, value in definition_rows(response):
        sheet.append([name, guard_cell(_cell(value))])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def to_json(rows: list[dict[str, Any]], filters: dict[str, Any], response: dict[str, Any]) -> bytes:
    body = {
        "metric": dict(definition_rows(response)),
        "filters": filters,
        "rows": rows,
    }
    return json.dumps(body, default=str, indent=2).encode("utf-8")


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parquet_type(values: list[Any]) -> str:
    """The Parquet type of a column: ``int64``, ``float64``, ``bool``, ``date32`` or ``string``."""
    present = [v for v in values if v is not None]
    if not present:
        return "string"
    if all(isinstance(v, bool) for v in present):
        return "bool"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in present):
        return "int64"
    if all(isinstance(v, int | float) and not isinstance(v, bool) for v in present):
        return "float64"
    if all(isinstance(v, str) and _DATE.match(v) for v in present):
        try:
            for v in present:
                date.fromisoformat(v)
        except ValueError:
            return "string"
        return "date32"
    return "string"


def to_parquet(rows: list[dict[str, Any]], filters: dict[str, Any], response: dict[str, Any]) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    header = columns(rows)
    arrays = []
    for name in header:
        values = [row.get(name) for row in rows]
        kind = parquet_type(values)
        if kind == "date32":
            arrays.append(pa.array([date.fromisoformat(v) if v is not None else None for v in values], pa.date32()))
        elif kind == "float64":
            arrays.append(pa.array([float(v) if v is not None else None for v in values], pa.float64()))
        elif kind == "string":
            arrays.append(pa.array([None if v is None else str(_cell(v)) for v in values], pa.string()))
        elif kind == "bool":
            arrays.append(pa.array(values, pa.bool_()))
        else:
            arrays.append(pa.array(values, pa.int64()))
    metadata = {f"insights.{field}": "" if value is None else str(value) for field, value in definition_rows(response)}
    metadata["insights.filters"] = json.dumps(filters, sort_keys=True, default=str)
    table = pa.Table.from_arrays(arrays, names=header) if header else pa.table({})
    table = table.replace_schema_metadata({k.encode(): v.encode() for k, v in metadata.items()})
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd")
    return buffer.getvalue()


def render(fmt: str, rows: list[dict[str, Any]], filters: dict[str, Any], response: dict[str, Any]) -> bytes:
    if fmt == "csv":
        return to_csv(rows)
    if fmt == "xlsx":
        return to_xlsx(rows, filters, response)
    if fmt == "json":
        return to_json(rows, filters, response)
    if fmt == "parquet":
        return to_parquet(rows, filters, response)
    raise ValueError(f"format is one of {', '.join(FORMATS)}")


def filename(key: str, period: dict[str, Any], fmt: str) -> str:
    return f"insights-{key}-{period.get('from')}-{period.get('to')}.{fmt}"
