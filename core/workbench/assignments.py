# SPDX-License-Identifier: Apache-2.0
"""Per-user workbench assignments an administrator keeps (``workbench_assignments``)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from core.workbench.definitions import NAMES


class AssignmentError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def check_names(raw: Any) -> list[str]:
    if not isinstance(raw, list) or len(raw) > len(NAMES):
        raise AssignmentError(422, "workbenches_invalid", f"workbenches is a list of up to {len(NAMES)} names")
    names: list[str] = []
    for item in raw:
        if not isinstance(item, str) or item not in NAMES:
            raise AssignmentError(
                422, "workbench_unknown", f"unknown workbench; the workbenches are {', '.join(NAMES)}"
            )
        if item not in names:
            names.append(item)
    return names


async def assigned_to(tenant_id: uuid.UUID, user_id: str) -> set[str]:
    """The workbenches assigned to one user."""
    from core.database import get_tenant_session
    from core.models.workbench_assignment import WorkbenchAssignment

    async with get_tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    select(WorkbenchAssignment.workbench).where(
                        WorkbenchAssignment.tenant_id == tenant_id, WorkbenchAssignment.user_id == str(user_id)[:128]
                    )
                )
            )
            .scalars()
            .all()
        )
    return {str(r) for r in rows}


async def list_assignments(tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    from core.database import get_tenant_session
    from core.models.workbench_assignment import WorkbenchAssignment

    async with get_tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    select(WorkbenchAssignment)
                    .where(WorkbenchAssignment.tenant_id == tenant_id)
                    .order_by(WorkbenchAssignment.user_id, WorkbenchAssignment.workbench)
                )
            )
            .scalars()
            .all()
        )
    by_user: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = by_user.setdefault(
            row.user_id, {"user_id": row.user_id, "workbenches": [], "assigned_by": row.assigned_by, "updated_at": None}
        )
        entry["workbenches"].append(row.workbench)
        stamp = row.created_at.isoformat() if row.created_at else None
        entry["updated_at"] = max(entry["updated_at"] or "", stamp or "") or None
    return list(by_user.values())


async def set_assignments(
    tenant_id: uuid.UUID, user_id: str, workbenches: list[str], *, assigned_by: str
) -> dict[str, Any]:
    """Replace a user's assignments with the given workbenches (an empty list removes them all)."""
    from core.database import get_tenant_session
    from core.models.workbench_assignment import WorkbenchAssignment

    user = str(user_id)[:128]
    async with get_tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    select(WorkbenchAssignment)
                    .where(WorkbenchAssignment.tenant_id == tenant_id, WorkbenchAssignment.user_id == user)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        current = {row.workbench: row for row in rows}
        for name, row in current.items():
            if name not in workbenches:
                await session.delete(row)
        for name in workbenches:
            if name not in current:
                session.add(
                    WorkbenchAssignment(
                        tenant_id=tenant_id,
                        user_id=user,
                        workbench=name,
                        assigned_by=str(assigned_by)[:128] or None,
                        created_at=datetime.now(UTC),
                    )
                )
        await session.flush()
    return {"user_id": user, "workbenches": list(workbenches), "assigned_by": str(assigned_by)[:128] or None}
