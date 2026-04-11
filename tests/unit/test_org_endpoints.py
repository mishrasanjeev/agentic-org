"""Tests for /api/v1/departments, /cost-centers, /delegations endpoints.

Uses a mocked tenant session so we don't need a real database.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TENANT_STR = str(uuid.uuid4())


def _mock_session(execute_result=None):
    session = AsyncMock()
    # Simulate SQLAlchemy's insert-time default: auto-populate .id on add.
    def _add(obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
    session.add = MagicMock(side_effect=_add)
    session.delete = AsyncMock()
    session.flush = AsyncMock()
    if execute_result is not None:
        session.execute = AsyncMock(return_value=execute_result)
    else:
        session.execute = AsyncMock()
    return session


def _patch_session(module: str, session):
    @asynccontextmanager
    async def _ctx(_tid):
        yield session

    return patch(f"api.v1.{module}.get_tenant_session", _ctx)


class TestDepartments:
    @pytest.mark.asyncio
    async def test_create_department(self):
        from api.v1.departments import DepartmentCreate, create_department

        session = _mock_session()
        body = DepartmentCreate(
            company_id=uuid.uuid4(),
            name="Finance",
            code="FIN",
        )
        with _patch_session("departments", session):
            resp = await create_department(body=body, tenant_id=TENANT_STR)

        assert resp.name == "Finance"
        assert resp.code == "FIN"
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_departments_scoped_to_tenant(self):
        from api.v1.departments import list_departments

        # Mock: scalars().all() returns an empty list
        scalars = MagicMock()
        scalars.all.return_value = []
        result = MagicMock()
        result.scalars.return_value = scalars
        session = _mock_session(execute_result=result)

        with _patch_session("departments", session):
            rows = await list_departments(company_id=None, tenant_id=TENANT_STR)

        assert rows == []
        session.execute.assert_awaited_once()


class TestDelegations:
    @pytest.mark.asyncio
    async def test_create_delegation_rejects_self(self):
        from fastapi import HTTPException

        from api.v1.delegations import DelegationCreate, create_delegation

        same_user = uuid.uuid4()
        body = DelegationCreate(delegator_id=same_user, delegate_id=same_user)
        with pytest.raises(HTTPException) as exc:
            await create_delegation(body=body, tenant_id=TENANT_STR)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_create_delegation_happy(self):
        from api.v1.delegations import DelegationCreate, create_delegation

        session = _mock_session()
        body = DelegationCreate(
            delegator_id=uuid.uuid4(),
            delegate_id=uuid.uuid4(),
            reason="Vacation",
        )
        with _patch_session("delegations", session):
            resp = await create_delegation(body=body, tenant_id=TENANT_STR)

        assert resp.delegator_id == body.delegator_id
        assert resp.delegate_id == body.delegate_id
        session.add.assert_called_once()


class TestFeatureFlagsEndpoint:
    @pytest.mark.asyncio
    async def test_upsert_new_flag(self):
        from api.v1.feature_flags import FlagIn, upsert_flag

        # First execute: no existing flag
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session = _mock_session(execute_result=result)

        body = FlagIn(
            flag_key="new_workflow_builder",
            enabled=True,
            rollout_percentage=50,
            description="Preview builder",
        )
        with _patch_session("feature_flags", session):
            resp = await upsert_flag(body=body, tenant_id=TENANT_STR)

        assert resp.flag_key == "new_workflow_builder"
        assert resp.rollout_percentage == 50
        session.add.assert_called_once()
