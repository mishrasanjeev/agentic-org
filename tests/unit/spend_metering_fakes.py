# SPDX-License-Identifier: Apache-2.0
"""An in-memory session for the non-token metering services (storage samples, GPU allocation).

It extends the usage fake (``tests/unit/spend_usage_fakes.py``) with what
those services add: ``UPDATE ... RETURNING`` (the pool-hour claim), commits
on a plain session, the uniqueness of the GPU tables, and the text queries of
the storage sample (``to_regclass`` and the bytes per store), answered from
per-tenant figures a test sets.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.sql.dml import Update

from tests.unit import spend_usage_fakes
from tests.unit.spend_fakes import Result
from tests.unit.spend_usage_fakes import UsageSession, install

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


def install_metering(monkeypatch: Any) -> MeteringSession:
    """``install`` with a metering session for tenant and plain sessions, and the GPU tables' unique keys."""
    import core.database
    from core.spend import metering

    install(monkeypatch)
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
