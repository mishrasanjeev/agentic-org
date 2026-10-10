# SPDX-License-Identifier: Apache-2.0
"""An in-memory session for the non-token metering services (storage samples, GPU allocation).

It extends the usage fake (``tests/unit/spend_usage_fakes.py``) with what
those services add: ``UPDATE ... RETURNING`` (the pool-hour claim), a guarded
upsert (``ON CONFLICT DO UPDATE ... WHERE ... RETURNING``, the operator
command), commits on a plain session, the uniqueness of the GPU tables, and
the text queries of the storage sample (``to_regclass`` and the bytes per
store), answered from per-tenant figures a test sets. ``spend.metering_paused``
reads as no flag row unless a test sets one.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.sql.dml import Insert, Update
from sqlalchemy.sql.elements import BindParameter, ClauseElement

from tests.unit import spend_usage_fakes
from tests.unit.spend_fakes import Result
from tests.unit.spend_usage_fakes import Evaluator, UsageSession, _model_for, install

GPU_UNIQUE = {
    "spend_gpu_pool_hours": ("provider", "node_pool", "hour_start"),
    "spend_gpu_allocations": ("tenant_id", "pool_hour_id"),
}
STORE_TABLES = {
    "knowledge_documents": "knowledge",
    "documents": "documents",
    "idp_documents": "idp",
    "speech_recordings": "speech",
}


class MeteringSession(UsageSession):
    """The usage fake plus the pool-hour claim, commits and the storage queries."""

    def __init__(self) -> None:
        super().__init__()
        self.commits = 0
        self.bytes: dict[tuple[str, str], int] = {}  # (tenant id, store) -> bytes kept
        self.missing_tables: set[str] = set()
        self.measured: list[str] = []  # tenant ids measured

    async def commit(self) -> None:
        self.commits += 1

    def _usage_insert(self, statement: Insert) -> Result:
        """A guarded single-row upsert as PostgreSQL runs it; any other insert is the usage fake's.

        On a conflict the update applies only where its ``WHERE`` holds, and
        ``RETURNING`` answers the row inserted or updated, or nothing.
        """
        conflict = getattr(statement, "_post_values_clause", None)
        if type(conflict).__name__ != "OnConflictDoUpdate" or conflict.update_whereclause is None:
            return super()._usage_insert(statement)
        table = statement.table.name
        values = {
            (k if isinstance(k, str) else k.key): (v.effective_value if isinstance(v, BindParameter) else v)
            for k, v in (statement._values or {}).items()
        }
        key = spend_usage_fakes.UNIQUE[table]
        row = next((r for r in self.of(table) if all(getattr(r, k) == values.get(k) for k in key)), None)
        if row is None:
            row = _model_for(table)(**values)
            self.add(row)
        else:
            evaluator = Evaluator(self, excluded=values)
            if not evaluator.value(conflict.update_whereclause, row):
                return Result([])
            to_set = conflict.update_values_to_set  # pairs in SQLAlchemy 2.0, a dict in 2.1
            for name, expr in to_set.items() if isinstance(to_set, dict) else to_set:
                value = evaluator.value(expr, row) if isinstance(expr, ClauseElement) else expr  # a plain value
                setattr(row, name if isinstance(name, str) else name.key, value)
        returning = statement._returning or ()
        return Result([tuple(getattr(row, c.key) for c in returning)] if returning else [(row.id,)])

    def _update(self, statement: Update) -> Result:
        rows = self._matching(statement.table.name, statement.whereclause)
        super()._update(statement)
        if statement._returning:
            return Result([tuple(getattr(r, c.key) for c in statement._returning) for r in rows])
        return Result([])

    def _usage_text(self, sql: str, params: dict[str, Any]) -> Result:
        if "to_regclass" in sql:
            table = str(params["name"]).removeprefix("public.")
            return Result([(None if table in self.missing_tables else table,)])
        for table, store in STORE_TABLES.items():
            if f"FROM {table} WHERE tenant_id = :t" in sql:
                if store == "knowledge":
                    self.measured.append(str(params["t"]))
                return Result([(self.bytes.get((str(params["t"]), store), 0),)])
        return super()._usage_text(sql, params)


async def _no_flag_row(tenant_id: Any, flag_key: str) -> None:
    return None


def install_metering(monkeypatch: Any) -> MeteringSession:
    """``install`` with a metering session for tenant and plain sessions, the GPU tables' unique keys, and
    no ``spend.metering_paused`` row (a test pauses a tenant by answering one)."""
    import core.database
    from core import feature_flags
    from core.spend import metering

    install(monkeypatch)
    monkeypatch.setattr(feature_flags, "_query_flag", _no_flag_row)
    store = MeteringSession()
    monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: store)
    monkeypatch.setattr(core.database, "async_session_factory", lambda: store)
    for table, key in GPU_UNIQUE.items():
        monkeypatch.setitem(spend_usage_fakes.UNIQUE, table, key)
    metering._PRICED_TOOLS_CACHE.clear()
    return store


def tenant_ids(*ids: uuid.UUID) -> Any:
    """A stand-in for ``tenants.active_tenant_ids`` answering ``ids``."""

    async def active() -> list[uuid.UUID]:
        return list(ids)

    return active


def tenants_since(*ids: uuid.UUID, deleted: tuple[uuid.UUID, ...] = ()) -> Any:
    """A stand-in for ``tenants.tenants_since`` answering ``(id, active)`` for ``ids``; ``deleted`` are not active."""

    async def since(moment: Any) -> list[tuple[uuid.UUID, bool]]:
        return [(tenant, tenant not in deleted) for tenant in ids]

    return since
