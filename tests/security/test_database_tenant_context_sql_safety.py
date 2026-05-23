"""Regression coverage for tenant RLS context SQL construction."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TENANT_ID = "11111111-2222-3333-4444-555555555555"


class _MaliciousTenantId:
    def __str__(self) -> str:
        return "11111111-2222-3333-4444-555555555555'; DROP TABLE tenants; --"


def _session_factory_for(mock_session: AsyncMock) -> MagicMock:
    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_factory


@pytest.mark.asyncio
async def test_tenant_context_uses_bound_parameter_not_interpolated_sql() -> None:
    from core.database import get_tenant_session

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()

    with patch("core.database.async_session_factory", _session_factory_for(mock_session)):
        async with get_tenant_session(uuid.UUID(TENANT_ID)):
            pass

    statement, params = mock_session.execute.call_args.args
    sql_text = getattr(statement, "text", str(statement))

    assert "set_config('agenticorg.tenant_id', :tenant_id, true)" in sql_text
    assert TENANT_ID not in sql_text
    assert params == {"tenant_id": TENANT_ID}


@pytest.mark.asyncio
async def test_tenant_context_rejects_malicious_tenant_string_before_execute() -> None:
    from core.database import get_tenant_session

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()

    with patch("core.database.async_session_factory", _session_factory_for(mock_session)):
        with pytest.raises(ValueError):
            async with get_tenant_session(_MaliciousTenantId()):  # type: ignore[arg-type]
                pass

    mock_session.execute.assert_not_called()
