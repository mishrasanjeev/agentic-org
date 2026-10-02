# SPDX-License-Identifier: Apache-2.0
"""The daily prune of routing records: per tenant, past the retention period, one tenant's failure isolated."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from core.tasks import gateway_tasks

T1, T2 = uuid.uuid4(), uuid.uuid4()


def _catalogue(tenant_ids):
    statements: list[str] = []

    class _Session:
        async def execute(self, statement, *_a, **_k):
            statements.append(str(statement))

        async def scalars(self, _query):
            return SimpleNamespace(all=lambda: list(tenant_ids))

    @contextlib.asynccontextmanager
    async def factory():
        yield _Session()

    return factory, statements


def test_prunes_each_tenant_past_the_cutoff_and_isolates_a_failing_tenant(monkeypatch):
    monkeypatch.setattr("core.config.settings.model_gateway_records_retention_days", 30)
    factory, statements = _catalogue([T1, T2])
    deletes: list[tuple[uuid.UUID, str]] = []

    class _TenantSession:
        def __init__(self, tenant_id):
            self.tenant_id = tenant_id

        async def execute(self, statement):
            if self.tenant_id == T2:
                raise RuntimeError("tenant database unavailable")
            deletes.append((self.tenant_id, str(statement)))
            return SimpleNamespace(rowcount=3)

    @contextlib.asynccontextmanager
    async def tenant_session(tenant_id):
        yield _TenantSession(tenant_id)

    with (
        patch("core.database.async_session_factory", factory),
        patch("core.database.get_tenant_session", tenant_session),
    ):
        result = asyncio.run(gateway_tasks._prune_model_gateway_records_async())
    assert "row_security = off" in statements[0]
    assert [tenant for tenant, _ in deletes] == [T1] and "model_gateway_records" in deletes[0][1]
    assert result["tenants"] == 2 and result["deleted"] == 3 and result["errors"] == 1
    cutoff = datetime.fromisoformat(result["cutoff"])
    assert abs((datetime.now(UTC) - cutoff) - timedelta(days=30)) < timedelta(minutes=1)


def test_an_explicit_retention_overrides_the_setting_and_never_drops_below_a_day():
    factory, _statements = _catalogue([])
    with patch("core.database.async_session_factory", factory):
        result = asyncio.run(gateway_tasks._prune_model_gateway_records_async(days=0))
    cutoff = datetime.fromisoformat(result["cutoff"])
    assert abs((datetime.now(UTC) - cutoff) - timedelta(days=1)) < timedelta(minutes=1)
    assert result == {"cutoff": result["cutoff"], "tenants": 0, "deleted": 0, "errors": 0}


def test_the_task_is_scheduled_daily_on_the_maintenance_queue():
    from core.tasks.celery_app import app

    entry = app.conf.beat_schedule["prune-model-gateway-records"]
    assert entry["task"] == "core.tasks.gateway_tasks.prune_model_gateway_records"
    assert entry["options"] == {"queue": "maintenance"}
    assert "core.tasks.gateway_tasks" in app.conf.include
    assert gateway_tasks.prune_model_gateway_records.name == "core.tasks.gateway_tasks.prune_model_gateway_records"
