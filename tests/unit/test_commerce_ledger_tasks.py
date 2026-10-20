# SPDX-License-Identifier: Apache-2.0
"""The daily OACP retention task: off by default, and one tenant's failure never stops the sweep."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from core.config import settings
from core.tasks import commerce_ledger_tasks


@pytest.fixture()
def tenants(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    from core.tasks import audit_chain_tasks

    ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]

    async def _ids() -> list[uuid.UUID]:
        return ids

    monkeypatch.setattr(audit_chain_tasks, "_tenant_ids", _ids)
    return ids


def test_the_task_is_a_no_op_while_the_flag_is_off(monkeypatch: pytest.MonkeyPatch, tenants) -> None:
    from core.commerce import oacp_retention

    monkeypatch.setattr(settings, "oacp_retention_apply_enabled", False)
    called: list[Any] = []

    async def _apply(tenant_id: Any) -> dict[str, int]:
        called.append(tenant_id)
        return {"deleted": 1, "errors": 0}

    monkeypatch.setattr(oacp_retention, "apply_due_dispositions", _apply)
    result = asyncio.run(commerce_ledger_tasks._apply_oacp_retention_async())
    assert result == {"enabled": False, "tenants": 0, "deleted": 0, "errors": 0}
    assert called == []


def test_a_failing_tenant_is_counted_and_the_sweep_continues(monkeypatch: pytest.MonkeyPatch, tenants) -> None:
    from core.commerce import oacp_retention

    monkeypatch.setattr(settings, "oacp_retention_apply_enabled", True)
    seen: list[Any] = []

    async def _apply(tenant_id: Any) -> dict[str, int]:
        seen.append(tenant_id)
        if tenant_id == tenants[1]:
            raise RuntimeError("database unavailable")
        return {"deleted": 2, "errors": 1 if tenant_id == tenants[2] else 0}

    monkeypatch.setattr(oacp_retention, "apply_due_dispositions", _apply)
    result = asyncio.run(commerce_ledger_tasks._apply_oacp_retention_async())
    assert seen == tenants
    assert result == {"enabled": True, "tenants": 3, "deleted": 4, "errors": 2}


def test_the_celery_task_runs_the_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _sweep() -> dict[str, Any]:
        return {"enabled": False, "tenants": 0, "deleted": 0, "errors": 0}

    monkeypatch.setattr(commerce_ledger_tasks, "_apply_oacp_retention_async", _sweep)
    monkeypatch.setattr(commerce_ledger_tasks, "run_async", asyncio.run)
    assert commerce_ledger_tasks.apply_oacp_retention() == {"enabled": False, "tenants": 0, "deleted": 0, "errors": 0}
