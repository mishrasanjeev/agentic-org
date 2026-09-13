"""CDC trigger evaluation — matches CDC events against a tenant's workflow triggers.

Trigger rules live in the tenant-scoped ``cdc_triggers`` table (RLS-enforced,
see migration ``v6z18``): ``tenant_id, connector, event_type, resource_type,
workflow_id, active``. ``"*"`` in ``connector``/``event_type``/
``resource_type`` matches anything. The former process-global registry
(one list shared by every tenant) is gone: a rule registered by tenant A
can never fire tenant B's workflow.

Store selection mirrors ``core.cdc.receiver``: relaxed environments use an
in-memory store; strict runtimes use PostgreSQL through
``core.database.get_tenant_session`` so RLS applies.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

import structlog
from sqlalchemy import text

from core.config import is_relaxed_env, settings

logger = structlog.get_logger()

WILDCARD = "*"


def _matches(trigger: dict[str, str], event: dict[str, Any]) -> bool:
    for field in ("connector", "event_type", "resource_type"):
        wanted = trigger.get(field, WILDCARD)
        if wanted != WILDCARD and wanted != event.get(field):
            return False
    return True


class CDCTriggerStore(Protocol):
    async def list_active(self, tenant_id: str) -> list[dict[str, str]]: ...

    async def register(
        self,
        tenant_id: str,
        connector: str,
        event_type: str,
        resource_type: str,
        workflow_id: str,
    ) -> dict[str, str]: ...


class InMemoryCDCTriggerStore:
    """Relaxed-env store; keyed by tenant so tests exercise isolation too."""

    def __init__(self) -> None:
        self._by_tenant: dict[str, list[dict[str, str]]] = {}

    async def list_active(self, tenant_id: str) -> list[dict[str, str]]:
        return [t for t in self._by_tenant.get(str(tenant_id), []) if t.get("active", "1") == "1"]

    async def register(
        self,
        tenant_id: str,
        connector: str,
        event_type: str,
        resource_type: str,
        workflow_id: str,
    ) -> dict[str, str]:
        trigger = {
            "id": uuid.uuid4().hex,
            "tenant_id": str(tenant_id),
            "connector": connector,
            "event_type": event_type,
            "resource_type": resource_type,
            "workflow_id": str(workflow_id),
            "active": "1",
        }
        self._by_tenant.setdefault(str(tenant_id), []).append(trigger)
        return trigger

    def clear(self) -> None:
        self._by_tenant.clear()


class SqlCDCTriggerStore:
    """PostgreSQL store; every query runs inside the tenant's RLS session."""

    _LIST_SQL = text(
        "SELECT id, connector, event_type, resource_type, workflow_id "
        "FROM cdc_triggers "
        "WHERE tenant_id = CAST(:tid AS uuid) AND active = TRUE"
    )
    _INSERT_SQL = text(
        "INSERT INTO cdc_triggers "
        "(tenant_id, connector, event_type, resource_type, workflow_id, active) "
        "VALUES (CAST(:tid AS uuid), :connector, :event_type, :resource_type, "
        "CAST(:workflow_id AS uuid), TRUE) RETURNING id"
    )

    @staticmethod
    def _tenant_uuid(tenant_id: str) -> uuid.UUID:
        return uuid.UUID(str(tenant_id))

    async def list_active(self, tenant_id: str) -> list[dict[str, str]]:
        from core.database import get_tenant_session

        try:
            tid = self._tenant_uuid(tenant_id)
        except ValueError:
            # Fail closed: a non-UUID tenant cannot own DB-backed triggers.
            logger.warning("cdc_trigger_tenant_not_uuid")
            return []
        async with get_tenant_session(tid) as session:
            rows = (await session.execute(self._LIST_SQL, {"tid": str(tid)})).all()
        return [
            {
                "id": str(row.id),
                "connector": row.connector,
                "event_type": row.event_type,
                "resource_type": row.resource_type,
                "workflow_id": str(row.workflow_id),
                "active": "1",
            }
            for row in rows
        ]

    async def register(
        self,
        tenant_id: str,
        connector: str,
        event_type: str,
        resource_type: str,
        workflow_id: str,
    ) -> dict[str, str]:
        from core.database import get_tenant_session

        tid = self._tenant_uuid(tenant_id)
        wf = uuid.UUID(str(workflow_id))
        async with get_tenant_session(tid) as session:
            row = (
                await session.execute(
                    self._INSERT_SQL,
                    {
                        "tid": str(tid),
                        "connector": connector,
                        "event_type": event_type,
                        "resource_type": resource_type,
                        "workflow_id": str(wf),
                    },
                )
            ).first()
        return {
            "id": str(row.id) if row else "",
            "tenant_id": str(tid),
            "connector": connector,
            "event_type": event_type,
            "resource_type": resource_type,
            "workflow_id": str(wf),
            "active": "1",
        }


_default_store: CDCTriggerStore | None = None


def get_cdc_trigger_store() -> CDCTriggerStore:
    global _default_store
    if _default_store is not None:
        return _default_store
    if is_relaxed_env(settings.env):
        _default_store = InMemoryCDCTriggerStore()
    else:
        _default_store = SqlCDCTriggerStore()
    return _default_store


def set_cdc_trigger_store_for_tests(store: CDCTriggerStore | None) -> None:
    global _default_store
    _default_store = store


async def register_trigger(
    tenant_id: str,
    connector: str,
    event_type: str,
    resource_type: str,
    workflow_id: str,
    *,
    store: CDCTriggerStore | None = None,
) -> dict[str, str]:
    """Register a tenant-scoped trigger rule mapping a CDC event pattern to a workflow."""
    if not tenant_id:
        raise ValueError("cdc trigger requires a tenant_id")
    return await (store or get_cdc_trigger_store()).register(
        str(tenant_id), connector, event_type, resource_type, str(workflow_id)
    )


async def evaluate_triggers(
    event: dict[str, Any],
    tenant_id: str,
    *,
    store: CDCTriggerStore | None = None,
) -> list[str]:
    """Return workflow IDs whose trigger rules (for ``tenant_id`` only) match ``event``."""
    if not tenant_id:
        return []
    triggers = await (store or get_cdc_trigger_store()).list_active(str(tenant_id))
    return [t["workflow_id"] for t in triggers if _matches(t, event)]


def clear_triggers() -> None:
    """Clear the in-memory trigger store (for testing)."""
    store = get_cdc_trigger_store()
    if isinstance(store, InMemoryCDCTriggerStore):
        store.clear()
