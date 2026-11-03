# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""PII masking for Insights exports, by the tenant's masking setting.

When the tenant's governance setting ``pii_masking`` is on (the default), every
Insights export passes its rows through ``mask_pii`` (emails, phone numbers,
Aadhaar, PAN, bank account and IFSC patterns). An administrator or an auditor
may ask for raw values (``include_pii``) after a confirmation; the choice is
recorded on the audit row of the export.
"""

from __future__ import annotations

import uuid
from typing import Any

# Roles that may ask for unmasked values (with the admin scope for administrators).
RAW_PII_ROLES = frozenset({"auditor"})


class IncludePiiForbiddenError(PermissionError):
    """Raw values were asked for by someone who may not have them."""


def may_include_pii(role: str, *, is_admin: bool) -> bool:
    return is_admin or (role or "").lower() in RAW_PII_ROLES


async def masking_on(session: Any, tenant_id: uuid.UUID) -> bool:
    """The tenant's ``pii_masking`` governance setting; on when the tenant has not saved one."""
    from core.models.governance_config import GovernanceConfig

    config = await session.get(GovernanceConfig, tenant_id)
    return True if config is None else bool(config.pii_masking)


def mask_rows(rows: list[Any]) -> list[Any]:
    from core.tool_gateway.pii_masker import mask_pii

    return [mask_pii(row) for row in rows]


async def apply(
    session: Any, tenant_id: uuid.UUID, rows: list[Any], *, include_pii: bool, role: str, is_admin: bool
) -> tuple[list[Any], bool]:
    """The rows as the export carries them, and whether they were masked; raises when raw values are not allowed."""
    if include_pii and not may_include_pii(role, is_admin=is_admin):
        raise IncludePiiForbiddenError("include_pii_forbidden")
    if include_pii or not await masking_on(session, tenant_id):
        return rows, False
    return mask_rows(rows), True
