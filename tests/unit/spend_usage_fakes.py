# SPDX-License-Identifier: Apache-2.0
"""An in-memory tenant session for the spend usage services.

It extends the reference-data fake (``tests/unit/spend_fakes.py``) with what
the usage code builds: multi-row inserts with ``ON CONFLICT DO NOTHING`` or an
additive ``DO UPDATE`` and ``RETURNING``; updates and deletes; selects with
``sum``/``count``/``max``/``min``/``coalesce``/``case``/``cast``, several
``group_by`` columns, ``distinct``, subqueries in ``IN``, tuple ``IN``,
keyset conditions, ``= ANY(array)``, JSONB ``@>`` and ``FOR UPDATE SKIP
LOCKED`` (skipping the rows a test marks as held by another transaction);
and the text statements of the resolver, the job claim and the statement
timeout. Expressions are evaluated against the ORM rows it
holds, one table per statement.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.sql import operators
from sqlalchemy.sql.dml import Delete, Insert, Update
from sqlalchemy.sql.elements import (
    BinaryExpression,
    BindParameter,
    BooleanClauseList,
    Case,
    Cast,
    ClauseList,
    CollectionAggregate,
    ColumnClause,
    False_,
    Grouping,
    Label,
    Null,
    TextClause,
    True_,
    Tuple,
    UnaryExpression,
)
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.sql.selectable import ScalarSelect, Select

from tests.unit.spend_fakes import FakeSession, Result

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
AGGREGATES = {"sum", "count", "max", "min"}
UNIQUE = {
    "spend_usage_records": ("tenant_id", "idempotency_key", "event_time"),
    "spend_usage_rollups": ("tenant_id", "day", "dims_hash"),
    "spend_meter_gaps": ("tenant_id", "day", "usage_type", "reason", "detail"),
}


def _model_for(table_name: str) -> Any:
    from core.models.base import BaseModel

    return next(m.class_ for m in BaseModel.registry.mappers if m.local_table.name == table_name)


def _strip(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


class Evaluator:
    """Evaluates a SQL expression against one row (or a group of rows for aggregates)."""

    def __init__(self, session: UsageSession, excluded: dict[str, Any] | None = None, outer: Any = None):
        self.session = session
        self.excluded = excluded or {}
        self.outer = outer  # the row of the enclosing statement (a correlated subquery reads it)

    def value(self, expr: Any, row: Any) -> Any:
        if hasattr(expr, "__clause_element__"):
            expr = expr.__clause_element__()
        if isinstance(expr, Null):
            return None
        if isinstance(expr, True_):
            return True
        if isinstance(expr, False_):
            return False
        if isinstance(expr, BindParameter):
            return expr.effective_value
        if isinstance(expr, Label):
            return self.value(expr.element, row)
        if isinstance(expr, Grouping):
            return self.value(expr.element, row)
        if isinstance(expr, ColumnClause):
            table = getattr(expr, "table", None)
            if table is not None and getattr(table, "name", None) == "excluded":
                return self.excluded[expr.key]
            name = getattr(table, "name", None)
            if (
                self.outer is not None
                and name is not None
                and name != getattr(row, "__tablename__", name)
                and name == getattr(self.outer, "__tablename__", None)
            ):
                return getattr(self.outer, expr.key)
            return getattr(row, expr.key)
        if isinstance(expr, BooleanClauseList):
            parts = [bool(self.value(p, row)) for p in expr.clauses]
            return any(parts) if expr.operator is operators.or_ else all(parts)
        if isinstance(expr, Tuple):
            return tuple(self.value(p, row) for p in expr.clauses)
        if isinstance(expr, ClauseList):
            return [self.value(p, row) for p in expr.clauses]
        if isinstance(expr, UnaryExpression):
            if expr.operator is operators.inv:
                return not self.value(expr.element, row)
            return self.value(expr.element, row)
        if isinstance(expr, Case):
            for condition, result in expr.whens:
                if self.value(condition, row):
                    return self.value(result, row)
            return self.value(expr.else_, row) if expr.else_ is not None else None
        if isinstance(expr, Cast):
            inner = self.value(expr.clause, row)
            if isinstance(inner, datetime) and expr.type.__class__.__name__ == "Date":
                return inner.date()
            return inner
        if isinstance(expr, FunctionElement):
            return self.function(expr, row)
        if isinstance(expr, BinaryExpression):
            return self.binary(expr, row)
        if isinstance(expr, ScalarSelect):
            expr = expr.element
        if isinstance(expr, Select):
            return [r[0] if isinstance(r, tuple) else r for r in self.session.select_rows(expr, outer=row)]
        raise NotImplementedError(f"{type(expr).__name__}: {expr}")

    def function(self, expr: FunctionElement, row: Any) -> Any:
        name = expr.name.lower()
        args = list(expr.clauses)
        if name == "coalesce":
            for arg in args:
                found = self.value(arg, row)
                if found is not None:
                    return found
            return None
        if name == "timezone":
            moment = self.value(args[1], row)
            return moment.astimezone(UTC).replace(tzinfo=None) if moment is not None else None
        if name == "now":
            return NOW
        if name in AGGREGATES:
            raise NotImplementedError("an aggregate outside a grouped select")
        raise NotImplementedError(name)

    def binary(self, expr: BinaryExpression, row: Any) -> Any:
        op = expr.operator
        for value_side, array_side in ((expr.left, expr.right), (expr.right, expr.left)):
            if isinstance(array_side, CollectionAggregate) and array_side.operator is operators.any_op:
                if op is not operators.eq:
                    raise NotImplementedError(f"{op} ANY")
                members = self.value(array_side.element, row) or []
                return _strip(self.value(value_side, row)) in [_strip(m) for m in members]
        if getattr(op, "opstring", None) == "@>":  # JSONB containment
            return _json_contains(self.value(expr.left, row), self.value(expr.right, row))
        left = self.value(expr.left, row)
        if op in (operators.in_op, operators.not_in_op):
            right = self.value(expr.right, row)
            members = right if isinstance(right, (list, tuple, set)) else [right]
            members = [tuple(m) if isinstance(m, list) else m for m in members]
            inside = _strip(left) in [_strip(m) for m in members]
            return inside if op is operators.in_op else not inside
        right = self.value(expr.right, row)
        if op is operators.is_:
            return left is right or left == right if right is not None else left is None
        if op is operators.is_not:
            return left is not None if right is None else left != right
        if op is operators.add:
            return left + right
        if op is operators.sub:
            return left - right
        if op is operators.mul:
            return left * right
        if op is operators.truediv:
            return left / right
        if left is None or right is None:
            return False if op not in (operators.eq, operators.ne) else (op is operators.ne) != (left == right)
        left, right = _strip(left), _strip(right)
        compare = {
            operators.eq: lambda a, b: a == b,
            operators.ne: lambda a, b: a != b,
            operators.ge: lambda a, b: a >= b,
            operators.gt: lambda a, b: a > b,
            operators.le: lambda a, b: a <= b,
            operators.lt: lambda a, b: a < b,
        }.get(op)
        if compare is None:
            raise NotImplementedError(str(op))
        return compare(left, right)

    def aggregate(self, expr: Any, rows: list[Any]) -> Any:
        if hasattr(expr, "__clause_element__"):
            expr = expr.__clause_element__()
        if isinstance(expr, Label):
            return self.aggregate(expr.element, rows)
        if isinstance(expr, FunctionElement) and expr.name.lower() in AGGREGATES:
            name = expr.name.lower()
            args = list(expr.clauses)
            if name == "count":
                return len(rows)
            values = [self.value(args[0], r) for r in rows]
            values = [v for v in values if v is not None]
            if not values:
                return None
            if name == "sum":
                total = values[0]
                for v in values[1:]:
                    total = total + v
                return total
            return max(values) if name == "max" else min(values)
        return self.value(expr, rows[0]) if rows else None


def _json_contains(container: Any, contained: Any) -> bool:
    """PostgreSQL ``jsonb @> jsonb``: every element or key of the right side is contained in the left."""
    if isinstance(container, list) and isinstance(contained, list):
        return all(any(_json_contains(have, want) for have in container) for want in contained)
    if isinstance(container, dict) and isinstance(contained, dict):
        return all(key in container and _json_contains(container[key], want) for key, want in contained.items())
    return container == contained


def _is_aggregate(expr: Any) -> bool:
    if hasattr(expr, "__clause_element__"):
        expr = expr.__clause_element__()
    if isinstance(expr, Label):
        return _is_aggregate(expr.element)
    return isinstance(expr, FunctionElement) and expr.name.lower() in AGGREGATES


class UsageSession(FakeSession):
    """The reference-data fake plus what the usage services build."""

    def __init__(self) -> None:
        super().__init__()
        self.agents: dict[str, tuple] = {}  # agent id -> the resolver's agent row
        self.user_departments: dict[str, tuple] = {}  # user id -> (department_id, code)
        self.fail_on: set[str] = set()  # markers of text statements that raise
        self.claims: list[dict[str, Any]] = []
        self.held_elsewhere: set[Any] = set()  # ids of rows another transaction holds (SKIP LOCKED skips them)

    # ---------------------------------------------------------------- execute

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Result:
        self.statements.append(statement)
        if isinstance(statement, TextClause):
            return self._usage_text(str(statement), params or {})
        if isinstance(statement, Insert):
            return self._usage_insert(statement)
        if isinstance(statement, Update):
            return self._update(statement)
        if isinstance(statement, Delete):
            return self._delete(statement)
        return Result(self.select_rows(statement))

    async def scalars(self, statement: Any) -> Result:
        rows = (await self.execute(statement)).all()
        return Result([r[0] if isinstance(r, tuple) else r for r in rows])

    def _usage_text(self, sql: str, params: dict[str, Any]) -> Result:
        for marker in self.fail_on:
            if marker in sql:
                raise RuntimeError(f"forced failure on {marker}")
        if "set_config('statement_timeout'" in sql or "SET LOCAL row_security" in sql:
            return Result([])
        if "FROM agents a" in sql:
            found = self.agents.get(str(params["agent"]))
            return Result([found] if found is not None else [])
        if "FROM users u" in sql:
            found = self.user_departments.get(str(params["user"]))
            return Result([found] if found is not None else [])
        if "UPDATE spend_jobs AS j SET status = 'running'" in sql:
            return self._claim(params)
        return self._text(sql, params)

    def _claim(self, params: dict[str, Any]) -> Result:
        """The job claim: queued, or running with no heartbeat for ten minutes; never beside another running job."""
        jobs = self.of("spend_jobs")
        for row in jobs:
            if row.id == params["id"] and row.tenant_id == params["tid"]:
                beat = row.heartbeat_at or row.started_at
                stale = row.status == "running" and beat is not None and (NOW - beat).total_seconds() > 600
                blocked = any(
                    other.id != row.id
                    and other.tenant_id == row.tenant_id
                    and other.kind == row.kind
                    and other.status == "running"
                    for other in jobs
                )
                if (row.status == "queued" or stale) and not blocked:
                    row.status = "running"
                    row.started_at = NOW
                    row.heartbeat_at = NOW
                    self.claims.append({"id": row.id})
                    return Result([(row.kind, row.params, row.requested_by, row.result)])
        return Result([])

    # ---------------------------------------------------------------- writes

    def _usage_insert(self, statement: Insert) -> Result:
        table = statement.table.name
        model = _model_for(table)
        if getattr(statement, "_multi_values", None):
            batches = [
                {(k if isinstance(k, str) else k.key): v for k, v in d.items()} for d in statement._multi_values[0]
            ]
        else:
            compiled = {(k if isinstance(k, str) else k.key): v for k, v in (statement._values or {}).items()}
            batches = [{k: (v.effective_value if isinstance(v, BindParameter) else v) for k, v in compiled.items()}]
        conflict = getattr(statement, "_post_values_clause", None)
        key = UNIQUE.get(table)
        returned = []
        for values in batches:
            existing = None
            if key is not None:
                existing = next(
                    (r for r in self.of(table) if all(_strip(getattr(r, k)) == _strip(values.get(k)) for k in key)),
                    None,
                )
            if existing is not None:
                if conflict is not None and type(conflict).__name__ == "OnConflictDoUpdate":
                    evaluator = Evaluator(self, excluded=values)
                    to_set = conflict.update_values_to_set  # pairs in SQLAlchemy 2.0, a dict in 2.1
                    for name, expr in to_set.items() if isinstance(to_set, dict) else to_set:
                        column = name if isinstance(name, str) else name.key
                        setattr(existing, column, evaluator.value(expr, existing))
                    continue
                if conflict is not None:
                    continue
                raise AssertionError(f"unique {table}{key} violated")
            row = model(**values)
            self.add(row)
            returned.append(row)
        if statement._returning:
            return Result([tuple(getattr(r, c.key) for c in statement._returning) for r in returned])
        return Result([(r.id,) for r in returned])

    def _matching(self, table: str, where: Any, outer: Any = None) -> list[Any]:
        evaluator = Evaluator(self, outer=outer)
        return [r for r in self.of(table) if where is None or evaluator.value(where, r)]

    def _update(self, statement: Update) -> Result:
        table = statement.table.name
        rows = self._matching(table, statement.whereclause)
        evaluator = Evaluator(self)
        for row in rows:
            for column, expr in statement._values.items():
                name = column if isinstance(column, str) else column.key
                setattr(row, name, evaluator.value(expr, row))
        if getattr(statement, "_returning", None):
            return Result([tuple(getattr(r, c.key) for c in statement._returning) for r in rows])
        return Result([])

    def _delete(self, statement: Delete) -> Result:
        table = statement.table.name
        doomed = {id(r) for r in self._matching(table, statement.whereclause)}
        self.rows = [r for r in self.rows if id(r) not in doomed]
        return Result([])

    # ---------------------------------------------------------------- selects

    def select_rows(self, statement: Select, outer: Any = None) -> list[Any]:
        froms = [f for f in statement.get_final_froms() if f.name != getattr(outer, "__tablename__", None)]
        table = froms[0].name
        rows = self._matching(table, statement.whereclause, outer)
        locking = getattr(statement, "_for_update_arg", None)
        if locking is not None and locking.skip_locked:
            rows = [r for r in rows if getattr(r, "id", None) not in self.held_elsewhere]
        evaluator = Evaluator(self, outer=outer)
        described = statement.column_descriptions
        entity = len(described) == 1 and isinstance(described[0]["expr"], type)
        columns = list(statement.selected_columns)
        grouped = bool(statement._group_by_clauses) or (not entity and any(_is_aggregate(c) for c in columns))
        if grouped:
            groups: dict[Any, list[Any]] = {}
            keys = list(statement._group_by_clauses)
            for row in rows:
                groups.setdefault(tuple(_strip(evaluator.value(k, row)) for k in keys), []).append(row)
            if not keys and not groups:
                groups[()] = []
            out = [tuple(evaluator.aggregate(c, members) for c in columns) for members in groups.values()]
            return out
        for clause in reversed(list(statement._order_by_clauses)):
            descending = isinstance(clause, UnaryExpression) and clause.modifier is operators.desc_op
            column = clause.element if isinstance(clause, UnaryExpression) else clause
            rows.sort(
                key=lambda r, c=column: (evaluator.value(c, r) is None, _sortable(evaluator.value(c, r))),
                reverse=descending,
            )
        if statement._offset:
            rows = rows[statement._offset :]
        if entity:
            out_rows: list[Any] = rows
        else:
            out_rows = [tuple(evaluator.value(c, r) for c in columns) for r in rows]
            if statement._distinct:
                seen: list[Any] = []
                for item in out_rows:
                    if item not in seen:
                        seen.append(item)
                out_rows = seen
        if statement._limit is not None:
            out_rows = out_rows[: statement._limit]
        return out_rows


def _sortable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return value.int
    if isinstance(value, (datetime, date, Decimal, int, str, float)):
        return value
    return str(value)


# ---------------------------------------------------------------- fixtures shared by the usage tests

TENANT = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER_TENANT = uuid.UUID("22222222-2222-4222-8222-222222222222")
ACTOR = "33333333-3333-4333-8333-333333333333"
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)  # 14:30 IST on 1 October; UTC billing date 1 October


def install(monkeypatch: Any) -> UsageSession:
    """Spend on, a frozen clock, empty caches, follow-up jobs kept queued, and one in-memory session."""
    import core.database
    from core.config import settings
    from core.spend import billing, clock, jobs, pricing, resolver

    store = UsageSession()
    monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: store)
    monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
    monkeypatch.setattr(settings, "spend_reporting_timezone", "Asia/Kolkata")
    monkeypatch.setattr(settings, "spend_provider_billing_timezones_json", "")
    monkeypatch.setattr(settings, "model_price_overrides_json", "")
    monkeypatch.setattr(clock, "now_utc", lambda: T0)
    monkeypatch.setattr(jobs, "_dispatch", lambda tenant_id, job_id, **options: None)
    pricing._ALIAS_CACHE.clear()
    resolver._RESOLUTION_CACHE.clear()
    billing._BILLING_CACHE.clear()
    return store
