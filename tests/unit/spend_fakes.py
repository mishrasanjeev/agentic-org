# SPDX-License-Identifier: Apache-2.0
"""An in-memory stand-in for the tenant session the spend services use.

It evaluates the SQLAlchemy statements those services build against a list
of ORM rows: selects of entities, columns, ``count`` and ``max`` with
``where`` (``= <> >= > <= < IN IS IS NOT AND OR``), ``order_by``, ``limit``,
``offset`` and ``group_by``; inserts with ``ON CONFLICT DO NOTHING`` and
``RETURNING``; savepoints that restore the rows on rollback; and the few
text statements (advisory locks, the ancestors query, the users checks).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import operators
from sqlalchemy.sql.dml import Insert
from sqlalchemy.sql.elements import (
    BinaryExpression,
    BindParameter,
    BooleanClauseList,
    False_,
    Grouping,
    Null,
    TextClause,
    True_,
    UnaryExpression,
)
from sqlalchemy.sql.functions import FunctionElement

_OPS = {
    operators.eq: lambda a, b: a == b,
    operators.ne: lambda a, b: a != b,
    operators.ge: lambda a, b: a is not None and a >= b,
    operators.gt: lambda a, b: a is not None and a > b,
    operators.le: lambda a, b: a is not None and a <= b,
    operators.lt: lambda a, b: a is not None and a < b,
    operators.in_op: lambda a, b: a in list(b),
    operators.not_in_op: lambda a, b: a not in list(b),
    operators.is_: lambda a, b: a is b,
    operators.is_not: lambda a, b: a is not b,
}

UNIQUE_KEYS = {
    "spend_org_nodes": [("tenant_id", "code")],
    "spend_source_mappings": [("tenant_id", "source_type", "source_ref")],
    "spend_model_aliases": [("tenant_id", "provider", "alias")],
    "spend_fx_rates": [("tenant_id", "currency", "rate_date")],
}


def _value(element: Any) -> Any:
    if isinstance(element, Null):
        return None
    if isinstance(element, True_):
        return True
    if isinstance(element, False_):
        return False
    if isinstance(element, BindParameter):
        return element.effective_value
    if isinstance(element, Grouping):
        return _value(element.element)
    if hasattr(element, "value"):
        return element.value
    raise NotImplementedError(repr(element))


def matches(clause: Any, row: Any) -> bool:
    """Evaluate a where clause against one row."""
    if clause is None or isinstance(clause, True_):
        return True
    if isinstance(clause, BooleanClauseList):
        parts = [matches(part, row) for part in clause.clauses]
        return any(parts) if clause.operator is operators.or_ else all(parts)
    if isinstance(clause, Grouping):
        return matches(clause.element, row)
    if isinstance(clause, BinaryExpression):
        attr = getattr(row, clause.left.key)
        op = _OPS.get(clause.operator)
        if op is None:
            raise NotImplementedError(str(clause.operator))
        return bool(op(attr, _value(clause.right)))
    raise NotImplementedError(str(clause))


class Result:
    def __init__(self, rows: list[Any]):
        self.rows = rows

    def scalars(self) -> Result:
        return self

    def all(self) -> list[Any]:
        return list(self.rows)

    def first(self) -> Any:
        return self.rows[0] if self.rows else None

    def scalar(self) -> Any:
        if not self.rows:
            return None
        first = self.rows[0]
        return first[0] if isinstance(first, tuple) else first

    def scalar_one_or_none(self) -> Any:
        return self.scalar()


class Nested:
    """A savepoint: awaitable (``await session.begin_nested()``) and an async context manager."""

    def __init__(self, session: FakeSession):
        self.session = session
        self.snapshot = session.snapshot()

    def __await__(self):
        async def _self() -> Nested:
            return self

        return _self().__await__()

    async def rollback(self) -> None:
        self.session.restore(self.snapshot)

    async def commit(self) -> None:
        return None

    async def __aenter__(self) -> Nested:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self.session.restore(self.snapshot)
        return False


class FakeSession:
    def __init__(self) -> None:
        self.rows: list[Any] = []
        self.locks: list[str] = []
        self.users: set[tuple[str, str]] = set()  # (tenant_id, user_id)
        self.statements: list[Any] = []
        self.fail_flush_for: set[str] = set()  # codes of org nodes whose write raises

    # ---------------------------------------------------------------- helpers

    def of(self, table: str) -> list[Any]:
        return [row for row in self.rows if row.__tablename__ == table]

    def snapshot(self) -> tuple[list[Any], list[dict[str, Any]]]:
        return list(self.rows), [self._state(row) for row in self.rows]

    def restore(self, snapshot: tuple[list[Any], list[dict[str, Any]]]) -> None:
        rows, states = snapshot
        self.rows = list(rows)
        for row, state in zip(rows, states, strict=True):
            for key, value in state.items():
                setattr(row, key, value)

    @staticmethod
    def _state(row: Any) -> dict[str, Any]:
        return {column.key: getattr(row, column.key) for column in row.__table__.columns}

    @staticmethod
    def _defaults(row: Any) -> None:
        for column in row.__table__.columns:
            if getattr(row, column.key) is None and column.default is not None and column.default.is_scalar:
                setattr(row, column.key, column.default.arg)
            if getattr(row, column.key) is None and column.default is not None and column.default.is_callable:
                if column.key != "id":
                    setattr(row, column.key, column.default.arg(None))

    def add(self, row: Any) -> None:
        if getattr(row, "id", None) is None:
            row.id = uuid.uuid4()
        self._defaults(row)
        self.rows.append(row)

    async def flush(self) -> None:
        for row in self.of("spend_org_nodes"):
            if row.code in self.fail_flush_for:
                from sqlalchemy.exc import IntegrityError

                raise IntegrityError("INSERT", {}, Exception("forced"))
        self._check_unique()

    def _check_unique(self) -> None:
        for table, keys in UNIQUE_KEYS.items():
            for key in keys:
                seen = set()
                for row in self.of(table):
                    value = tuple(getattr(row, k) for k in key)
                    assert value not in seen, f"unique {table}{key} violated by {value}"
                    seen.add(value)
        seen_cards = set()
        for row in self.of("spend_rate_cards"):
            if row.status != "active":
                continue
            value = (
                row.tenant_id,
                row.provider,
                row.usage_type,
                row.model_sku,
                row.unit,
                row.source,
                row.effective_from,
            )
            assert value not in seen_cards, f"unique active card violated by {value}"
            seen_cards.add(value)

    def begin_nested(self) -> Nested:
        return Nested(self)

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    # ---------------------------------------------------------------- execute

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Result:
        self.statements.append(statement)
        if isinstance(statement, TextClause):
            return self._text(str(statement), params or {})
        if isinstance(statement, Insert):
            return self._insert(statement)
        return self._select(statement)

    def _text(self, sql: str, params: dict[str, Any]) -> Result:
        if "advisory_xact_lock" in sql:
            self.locks.append(params["k"])
            return Result([(True,)])
        if "set_config('lock_timeout'" in sql:
            return Result([(params["v"],)])
        if "WITH RECURSIVE up" in sql:
            by_id = {row.id: row for row in self.of("spend_org_nodes") if row.tenant_id == params["tid"]}
            out = []
            current = by_id.get(params["node"])
            depth = 0
            while current is not None and depth <= 16:
                out.append((current.id, current.parent_id, current.kind, current.code, current.active, depth))
                current = by_id.get(current.parent_id)
                depth += 1
            return Result(out)
        if "FROM users WHERE id = :u" in sql:
            return Result([(1,)] if (str(params["t"]), str(params["u"])) in self.users else [])
        if "FROM users WHERE tenant_id = :t AND id = ANY(:ids)" in sql:
            return Result([(u,) for u in params["ids"] if (str(params["t"]), str(u)) in self.users])
        raise NotImplementedError(sql)

    def _insert(self, statement: Insert) -> Result:
        from core.models.base import BaseModel

        table = statement.table.name
        model = next(m.class_ for m in BaseModel.registry.mappers if m.local_table.name == table)
        values = statement.compile(dialect=postgresql.dialect()).params
        values = {k: v for k, v in values.items() if k in statement.table.c}
        for key in UNIQUE_KEYS.get(table, []):
            if any(all(getattr(r, k) == values[k] for k in key) for r in self.of(table)):
                return Result([])
        row = model(**values)
        self.add(row)
        return Result([(row.id,)])

    def _select(self, statement: Any) -> Result:
        table = statement.get_final_froms()[0]
        rows = [row for row in self.rows if row.__tablename__ == table.name and matches(statement.whereclause, row)]
        for clause in reversed(list(statement._order_by_clauses)):
            descending = isinstance(clause, UnaryExpression) and clause.modifier is operators.desc_op
            column = clause.element if isinstance(clause, UnaryExpression) else clause
            rows.sort(key=lambda r, c=column: (getattr(r, c.key) is None, getattr(r, c.key)), reverse=descending)
        offset = statement._offset or 0
        if offset:
            rows = rows[offset:]
        if statement._limit is not None:
            rows = rows[: statement._limit]
        described = statement.column_descriptions
        if len(described) == 1 and isinstance(described[0]["expr"], type):
            return Result(rows)
        columns = list(statement.selected_columns)
        if len(columns) == 1 and isinstance(columns[0], FunctionElement) and columns[0].name == "count":
            return Result([(len(rows),)])
        if (
            not statement._group_by_clauses
            and columns
            and all(isinstance(c, FunctionElement) and c.name in ("max", "min") for c in columns)
        ):
            values = []
            for column in columns:
                inner = list(column.clauses)[0]
                found = [getattr(r, inner.key) for r in rows if getattr(r, inner.key) is not None]
                values.append((max(found) if column.name == "max" else min(found)) if found else None)
            return Result([tuple(values)])
        if statement._group_by_clauses:
            group = list(statement._group_by_clauses)[0]
            buckets: dict[Any, list[Any]] = {}
            for row in rows:
                buckets.setdefault(getattr(row, group.key), []).append(row)
            out = []
            for key, members in buckets.items():
                values = []
                for column in columns:
                    if isinstance(column, FunctionElement) and column.name == "max":
                        inner = list(column.clauses)[0]
                        values.append(max(getattr(m, inner.key) for m in members))
                    else:
                        values.append(key)
                out.append(tuple(values))
            return Result(out)
        return Result([tuple(getattr(row, column.key) for column in columns) for row in rows])
