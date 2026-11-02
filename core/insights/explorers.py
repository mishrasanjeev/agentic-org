# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights explorers: the Level 2 tables behind every tile, with their columns, filters, sorting and readers.

An :class:`Explorer` declares its columns once: what each holds (``type``),
whether it sorts, and how it filters (``enum`` values, a ``text`` substring, a
``min`` number, a ``bool``). The API accepts only what is declared: a filter
or sort on an undeclared column is a 422, never free-form SQL. Resolvers live
in ``core/insights/explore_sources.py``; each returns the explorer's rows
(:class:`ExploreData`) for an :class:`ExploreContext`, either already filtered,
sorted and paged in SQL (``paged=True``) or as the full row set, which
:func:`apply_query` filters, sorts and pages here.

Every response has the same shape::

    {key, title, definition, source, data_quality, computed_at, period,
     columns[], rows[], total, page, page_size, sort, order, filters, hint?, summary?}

Rows may carry ``links`` (where a row drills to: an agent, a run, an
approval). Readers are roles; administrators read every explorer. Run, agent,
approval and quality explorers are limited to the caller's domains; cost
reads are tenant-wide for administrators and the CFO and limited to the
caller's domains' agents for other roles.
"""

from __future__ import annotations

import math
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from core.insights.metrics import MetricContext

ColumnType = Literal[
    "text", "id", "count", "number", "percent", "duration_ms", "money", "tokens", "datetime", "date", "bool", "status"
]
FilterKind = Literal["enum", "text", "min", "bool"]
Order = Literal["asc", "desc"]

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50
# A full-row explorer never holds more rows than this in memory (exports included).
MAX_SCAN_ROWS = 50_001
MAX_ENUM_VALUES = 20
MAX_FILTER_TEXT = 100
_FILTER_PARAM = re.compile(r"^filter\[([a-z_]{1,40})\]$")

# The Phase 2 roles. Administrators read everything (by the admin scope, never a role claim alone).
DOMAIN_HEADS: frozenset[str] = frozenset({"cfo", "chro", "cmo", "coo"})
# Roles whose cost reads are tenant-wide, as for the v1 cost metrics.
TENANT_COST_ROLES: frozenset[str] = frozenset({"admin", "cfo"})


class ExploreQueryError(ValueError):
    """A filter, sort or page the explorer does not accept (422)."""

    def __init__(self, error: str, message: str) -> None:
        super().__init__(message)
        self.error = error
        self.message = message


@dataclass(frozen=True)
class Column:
    key: str
    title: str
    type: ColumnType
    description: str = ""
    sortable: bool = True
    filter: FilterKind | None = None
    options: tuple[str, ...] = ()

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "key": self.key,
            "title": self.title,
            "type": self.type,
            "sortable": self.sortable,
            "filter": self.filter,
        }
        if self.description:
            out["description"] = self.description
        if self.options:
            out["options"] = list(self.options)
        return out


@dataclass(frozen=True)
class Filter:
    column: str
    kind: FilterKind
    values: tuple[str, ...] = ()
    text: str | None = None
    minimum: float | None = None
    flag: bool | None = None

    def as_param(self) -> str:
        if self.kind == "enum":
            return ",".join(self.values)
        if self.kind == "text":
            return self.text or ""
        if self.kind == "min":
            return _number_text(self.minimum)
        return "true" if self.flag else "false"

    def matches(self, value: Any) -> bool:
        if self.kind == "enum":
            return value is not None and str(value) in self.values
        if self.kind == "text":
            return value is not None and (self.text or "").casefold() in str(value).casefold()
        if self.kind == "min":
            return value is not None and _as_float(value) >= float(self.minimum or 0)
        return bool(value) is bool(self.flag)


@dataclass(frozen=True)
class ExploreQuery:
    filters: dict[str, Filter] = field(default_factory=dict)
    sort: str | None = None
    order: Order = "desc"
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


@dataclass
class ExploreContext:
    """What an explorer resolver reads: the metric context (session, period, company, domains) and the query."""

    metric: MetricContext
    query: ExploreQuery
    role: str
    is_admin: bool

    @property
    def tenant_cost(self) -> bool:
        """Cost reads are tenant-wide for administrators and the CFO."""
        return self.is_admin or self.role in TENANT_COST_ROLES


@dataclass
class ExploreData:
    rows: list[dict[str, Any]]
    source: str
    data_quality: str
    # Rows already filtered, sorted and paged by the resolver; ``total`` is then the filtered count.
    paged: bool = False
    total: int | None = None
    hint: str | None = None
    summary: dict[str, Any] | None = None


Resolver = Callable[[ExploreContext], Awaitable[ExploreData]]


@dataclass(frozen=True)
class Explorer:
    key: str
    title: str
    definition: str
    source: tuple[str, ...]
    readers: frozenset[str]
    columns: tuple[Column, ...]
    default_sort: str
    resolver: Resolver
    default_order: Order = "desc"
    # The date range applies (False: the table is a snapshot of now).
    uses_period: bool = True

    def column(self, key: str) -> Column | None:
        for column in self.columns:
            if column.key == key:
                return column
        return None

    def readable_by(self, role: str, *, is_admin: bool) -> bool:
        if is_admin:
            return True
        effective = (role or "").lower()
        return effective != "admin" and effective in self.readers

    def describe(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "definition": self.definition,
            "source": list(self.source),
            "columns": [column.describe() for column in self.columns],
            "default_sort": self.default_sort,
            "default_order": self.default_order,
            "uses_period": self.uses_period,
        }


# ------------------------------------------------------------------ query parsing


def _number_text(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


def _as_float(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return math.nan


def _parse_filter(column: Column, raw: str) -> Filter:
    raw = raw.strip()
    if column.filter == "enum":
        values = tuple(dict.fromkeys(v.strip() for v in raw.split(",") if v.strip()))
        if not values:
            raise ExploreQueryError("invalid_filter", f"filter[{column.key}] needs at least one value")
        if len(values) > MAX_ENUM_VALUES:
            raise ExploreQueryError("invalid_filter", f"filter[{column.key}] takes at most {MAX_ENUM_VALUES} values")
        if any(len(v) > 64 for v in values):
            raise ExploreQueryError("invalid_filter", f"filter[{column.key}] values are at most 64 characters")
        if column.options:
            unknown = [v for v in values if v not in column.options]
            if unknown:
                allowed = ", ".join(column.options)
                raise ExploreQueryError("invalid_filter", f"filter[{column.key}] is one of: {allowed}")
        return Filter(column.key, "enum", values=values)
    if column.filter == "text":
        if not raw or len(raw) > MAX_FILTER_TEXT:
            raise ExploreQueryError(
                "invalid_filter", f"filter[{column.key}] is 1 to {MAX_FILTER_TEXT} characters of text"
            )
        return Filter(column.key, "text", text=raw)
    if column.filter == "min":
        number = _as_float(raw)
        if math.isnan(number) or math.isinf(number) or number < 0:
            raise ExploreQueryError("invalid_filter", f"filter[{column.key}] is a number of at least 0")
        return Filter(column.key, "min", minimum=number)
    if column.filter == "bool":
        lowered = raw.lower()
        if lowered not in ("true", "false"):
            raise ExploreQueryError("invalid_filter", f"filter[{column.key}] is true or false")
        return Filter(column.key, "bool", flag=lowered == "true")
    raise ExploreQueryError("filter_not_allowed", f"{column.key} cannot be filtered")


def parse_query(explorer: Explorer, params: Mapping[str, Any]) -> ExploreQuery:
    """The explorer query from request parameters: ``filter[col]``, ``sort``, ``order``, ``page``, ``page_size``."""
    filters: dict[str, Filter] = {}
    for name, raw in params.items():
        match = _FILTER_PARAM.match(str(name))
        if match is None:
            if str(name).startswith("filter"):
                raise ExploreQueryError("invalid_filter", f"{name} is not a filter; use filter[column]=value")
            continue
        column = explorer.column(match.group(1))
        if column is None or column.filter is None:
            allowed = ", ".join(c.key for c in explorer.columns if c.filter) or "none"
            raise ExploreQueryError("filter_not_allowed", f"{explorer.key} may be filtered by: {allowed}")
        filters[column.key] = _parse_filter(column, str(raw))
    sort = str(params.get("sort") or explorer.default_sort)
    column = explorer.column(sort)
    if column is None or not column.sortable:
        allowed = ", ".join(c.key for c in explorer.columns if c.sortable)
        raise ExploreQueryError("sort_not_allowed", f"{explorer.key} may be sorted by: {allowed}")
    order = str(params.get("order") or explorer.default_order)
    if order not in ("asc", "desc"):
        raise ExploreQueryError("invalid_order", "order is asc or desc")
    page = _int_param(params.get("page"), "page", default=1, low=1, high=100_000)
    page_size = _int_param(params.get("page_size"), "page_size", default=DEFAULT_PAGE_SIZE, low=1, high=MAX_PAGE_SIZE)
    return ExploreQuery(filters=filters, sort=sort, order=order, page=page, page_size=page_size)  # type: ignore[arg-type]


def _int_param(raw: Any, name: str, *, default: int, low: int, high: int) -> int:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw))
    except ValueError:
        raise ExploreQueryError("invalid_page", f"{name} is a whole number") from None
    if value < low or value > high:
        raise ExploreQueryError("invalid_page", f"{name} is from {low} to {high}")
    return value


# ------------------------------------------------------------------ in-memory paging


def _sort_key(value: Any) -> tuple[int, Any]:
    """Nulls last whatever the order; numbers, dates and text each compare among themselves."""
    if value is None:
        return (2, 0)
    if isinstance(value, bool):
        return (0, int(value))
    if isinstance(value, (int, float, Decimal)):
        return (0, float(value))
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return (0, moment.timestamp())
    if isinstance(value, date):
        return (0, value.toordinal())
    return (1, str(value).casefold())


def apply_query(rows: list[dict[str, Any]], query: ExploreQuery) -> tuple[list[dict[str, Any]], int]:
    """``rows`` filtered and sorted by ``query``, the requested page of them, and the filtered count."""
    kept = [row for row in rows if all(f.matches(row.get(f.column)) for f in query.filters.values())]
    if query.sort:
        sort = query.sort
        present = [row for row in kept if row.get(sort) is not None]
        missing = [row for row in kept if row.get(sort) is None]
        present.sort(key=lambda row: _sort_key(row.get(sort)), reverse=query.order == "desc")
        kept = present + missing
    return kept[query.offset : query.offset + query.page_size], len(kept)


# ------------------------------------------------------------------ SQL paging


def sql_filters(query: ExploreQuery, expressions: Mapping[str, Any]) -> list[Any]:
    """WHERE clauses for the query's filters over allow-listed column expressions."""
    clauses: list[Any] = []
    for name, flt in query.filters.items():
        expr = expressions.get(name)
        if expr is None:
            raise ExploreQueryError("filter_not_allowed", f"{name} cannot be filtered")
        if flt.kind == "enum":
            clauses.append(expr.in_(list(flt.values)))
        elif flt.kind == "text":
            escaped = (flt.text or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append(expr.ilike(f"%{escaped}%", escape="\\"))
        elif flt.kind == "min":
            clauses.append(expr >= flt.minimum)
        else:
            clauses.append(expr.is_(True) if flt.flag else expr.is_not(True))
    return clauses


def sql_order(query: ExploreQuery, expressions: Mapping[str, Any], tiebreak: Any) -> list[Any]:
    expr = expressions.get(query.sort or "")
    if expr is None:
        raise ExploreQueryError("sort_not_allowed", f"{query.sort} cannot be sorted")
    primary = expr.desc().nulls_last() if query.order == "desc" else expr.asc().nulls_last()
    return [primary, tiebreak.desc() if query.order == "desc" else tiebreak.asc()]


async def sql_page(
    session: Any, statement: Any, query: ExploreQuery, expressions: Mapping[str, Any], tiebreak: Any
) -> tuple[list[Any], int]:
    """The requested page of ``statement`` (filtered, sorted) and the filtered count."""
    from sqlalchemy import func, select

    filtered = statement.where(*sql_filters(query, expressions))
    total = int((await session.execute(select(func.count()).select_from(filtered.subquery()))).scalar_one() or 0)
    rows = (
        await session.execute(
            filtered.order_by(*sql_order(query, expressions, tiebreak)).offset(query.offset).limit(query.page_size)
        )
    ).all()
    return list(rows), total


# ------------------------------------------------------------------ response


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return moment.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


async def resolve(explorer: Explorer, ctx: ExploreContext) -> dict[str, Any]:
    """The explorer's response for ``ctx``: its columns and the requested page of rows."""
    data = await explorer.resolver(ctx)
    if data.paged:
        rows, total = data.rows, int(data.total or 0)
    else:
        rows, total = apply_query(data.rows, ctx.query)
    response: dict[str, Any] = {
        "key": explorer.key,
        "title": explorer.title,
        "definition": explorer.definition,
        "source": data.source,
        "data_quality": data.data_quality,
        "computed_at": datetime.now(UTC).isoformat(),
        "period": ctx.metric.period.as_dict() if explorer.uses_period else None,
        "columns": [column.describe() for column in explorer.columns],
        "rows": [jsonable(row) for row in rows],
        "total": total,
        "page": ctx.query.page,
        "page_size": ctx.query.page_size,
        "sort": ctx.query.sort,
        "order": ctx.query.order,
        "filters": {name: f.as_param() for name, f in ctx.query.filters.items()},
    }
    if data.hint:
        response["hint"] = data.hint
    if data.summary is not None:
        response["summary"] = jsonable(data.summary)
    return response


def export_columns(explorer: Explorer) -> list[str]:
    return [column.key for column in explorer.columns]


def export_rows(explorer: Explorer, response: dict[str, Any]) -> list[dict[str, Any]]:
    """An explorer page as export rows: the declared columns in order, without links."""
    keys = export_columns(explorer)
    return [{key: row.get(key) for key in keys} for row in response.get("rows") or []]
