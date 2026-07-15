"""Hermetic tenant/company ownership helpers for scoped execution tests."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

TEST_TENANT_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
TEST_COMPANY_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


def scoped_state(*, domain: str | None = None) -> dict[str, str]:
    """Return the minimum valid execution scope used by workflow fixtures."""
    state = {
        "tenant_id": str(TEST_TENANT_ID),
        "company_id": str(TEST_COMPANY_ID),
    }
    if domain is not None:
        state["domain"] = domain
    return state


def owned_company_validator(
    expected_tenant_id: str | uuid.UUID = TEST_TENANT_ID,
    expected_company_id: str | uuid.UUID = TEST_COMPANY_ID,
) -> Callable[[str | uuid.UUID, str | uuid.UUID | None], Awaitable[uuid.UUID]]:
    """Build an async ownership seam that rejects every unexpected scope."""
    expected_tenant = uuid.UUID(str(expected_tenant_id))
    expected_company = uuid.UUID(str(expected_company_id))

    async def _validate(
        tenant_id: str | uuid.UUID,
        company_id: str | uuid.UUID | None,
    ) -> uuid.UUID:
        assert uuid.UUID(str(tenant_id)) == expected_tenant
        assert company_id is not None
        assert uuid.UUID(str(company_id)) == expected_company
        return expected_company

    return _validate


async def scoped_test_chat_agent(
    domain: str,
    tenant_id: str | uuid.UUID,
    company_id: str | uuid.UUID,
) -> tuple[str, None, None, list[str]]:
    """Return a deterministic company-scoped chat agent without a DB read."""
    uuid.UUID(str(tenant_id))
    uuid.UUID(str(company_id))
    names = {
        "finance": "CFO Agent (test)",
        "hr": "CHRO Agent (test)",
        "marketing": "CMO Agent (test)",
        "operations": "COO Agent (test)",
    }
    return names.get(domain, "General Assistant (test)"), None, None, []


async def no_scoped_connector_bindings(
    tenant_id: str | uuid.UUID,
    agent_type: str,
    company_id: str | uuid.UUID | None = None,
) -> list[str]:
    """Model a scoped agent with no connector bindings for API unit tests."""
    uuid.UUID(str(tenant_id))
    assert agent_type
    assert company_id is not None
    uuid.UUID(str(company_id))
    return []
