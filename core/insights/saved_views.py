# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights saved views: a person's named filter sets on a landing view or an explorer, optionally shared.

``view_key`` is a landing view (``finance``) or ``explore:<explorer>``; the
caller must be able to open it. ``filters`` is the URL query the page already
uses (``range``, ``from``, ``to``, ``compare``, ``filter[col]``, ``sort``, ...):
string keys and string (or list of string) values, bounded in size. A saved
view is listed to its owner, and to everyone who may open the view when it is
shared. Only the owner changes it; the owner or an administrator deletes it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from core.insights import explore_registry, registry

EXPLORE_PREFIX = "explore:"
MAX_FILTERS = 40
MAX_KEY = 64
MAX_VALUE = 500
MAX_NAME = 120
MAX_PER_OWNER = 200
# Query keys a saved view never stores: the company is the person's own choice in the header.
DROPPED_KEYS = frozenset({"company_id"})


class SavedViewError(ValueError):
    def __init__(self, error: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.error = error
        self.message = message
        self.status = status


def may_open_view(view_key: str, role: str, *, is_admin: bool) -> bool:
    if view_key.startswith(EXPLORE_PREFIX):
        explorer = explore_registry.EXPLORERS.get(view_key[len(EXPLORE_PREFIX) :])
        return explorer is not None and explorer.readable_by(role, is_admin=is_admin)
    return view_key in registry.allowed_views(role, is_admin=is_admin)


def clean_filters(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SavedViewError("invalid_filters", "filters is an object of query parameter to value")
    out: dict[str, Any] = {}
    for key, value in raw.items():
        key = str(key)
        if key in DROPPED_KEYS:
            continue
        if not key or len(key) > MAX_KEY:
            raise SavedViewError("invalid_filters", f"filter names are 1 to {MAX_KEY} characters")
        values = value if isinstance(value, list) else [value]
        if not all(isinstance(v, str | int | float | bool) for v in values):
            raise SavedViewError("invalid_filters", f"{key} takes text values")
        texts = [str(v) for v in values]
        if any(len(v) > MAX_VALUE for v in texts):
            raise SavedViewError("invalid_filters", f"{key} values are at most {MAX_VALUE} characters")
        out[key] = texts if isinstance(value, list) else texts[0]
    if len(out) > MAX_FILTERS:
        raise SavedViewError("invalid_filters", f"at most {MAX_FILTERS} filters")
    return out


def clean_name(raw: Any) -> str:
    name = str(raw or "").strip()
    if not name or len(name) > MAX_NAME:
        raise SavedViewError("invalid_name", f"the name is 1 to {MAX_NAME} characters")
    return name


def serialize(row: Any, caller_id: uuid.UUID | None) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "view_key": row.view_key,
        "name": row.name,
        "filters": row.filters or {},
        "shared": bool(row.shared),
        "mine": caller_id is not None and row.user_id == caller_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def list_views(session: Any, tenant_id: uuid.UUID, caller: Any, view_key: str | None) -> list[dict[str, Any]]:
    """The caller's own views and the shared ones on views the caller may open (one view key, or all)."""
    from sqlalchemy import or_, select

    from core.models.insights import SavedView as V

    clauses = [V.tenant_id == tenant_id, or_(V.user_id == caller.user_id, V.shared.is_(True))]
    if view_key:
        clauses.append(V.view_key == view_key)
    rows = (await session.execute(select(V).where(*clauses).order_by(V.view_key, V.name))).scalars()
    return [
        serialize(row, caller.user_id)
        for row in rows
        if may_open_view(row.view_key, caller.role, is_admin=caller.is_admin)
    ]


async def create_view(session: Any, tenant_id: uuid.UUID, caller: Any, body: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy import func, select

    from core.models.insights import SavedView as V

    if caller.user_id is None:
        raise SavedViewError("saved_views_need_a_person", "only a signed-in person can save a view", 403)
    view_key = str(body.get("view_key") or "")
    if not may_open_view(view_key, caller.role, is_admin=caller.is_admin):
        raise SavedViewError("view_forbidden", "you may not open this view", 403)
    name = clean_name(body.get("name"))
    filters = clean_filters(body.get("filters"))
    owned = (
        await session.execute(select(func.count()).where(V.tenant_id == tenant_id, V.user_id == caller.user_id))
    ).scalar_one()
    if owned >= MAX_PER_OWNER:
        raise SavedViewError("too_many_saved_views", f"at most {MAX_PER_OWNER} saved views per person")
    clash = (
        await session.execute(
            select(V.id).where(
                V.tenant_id == tenant_id, V.user_id == caller.user_id, V.view_key == view_key, V.name == name
            )
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise SavedViewError("name_taken", "you already have a saved view with this name here", 409)
    row = V(
        tenant_id=tenant_id,
        user_id=caller.user_id,
        view_key=view_key,
        name=name,
        filters=filters,
        shared=bool(body.get("shared")),
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return serialize(row, caller.user_id)


async def _owned(session: Any, tenant_id: uuid.UUID, view_id: uuid.UUID, caller: Any) -> Any:
    from sqlalchemy import select

    from core.models.insights import SavedView as V

    row = (await session.execute(select(V).where(V.tenant_id == tenant_id, V.id == view_id))).scalar_one_or_none()
    visible = row is not None and (row.user_id == caller.user_id or row.shared)
    if not visible or not may_open_view(row.view_key, caller.role, is_admin=caller.is_admin):
        raise SavedViewError("saved_view_not_found", "no such saved view", 404)
    return row


async def update_view(
    session: Any, tenant_id: uuid.UUID, view_id: uuid.UUID, caller: Any, body: dict[str, Any]
) -> dict[str, Any]:
    row = await _owned(session, tenant_id, view_id, caller)
    if row.user_id != caller.user_id:
        raise SavedViewError("not_the_owner", "only the person who saved it can change it", 403)
    if "name" in body:
        row.name = clean_name(body["name"])
    if "filters" in body:
        row.filters = clean_filters(body["filters"])
    if "shared" in body:
        row.shared = bool(body["shared"])
    row.updated_at = datetime.now(UTC)
    await session.flush()
    return serialize(row, caller.user_id)


async def delete_view(session: Any, tenant_id: uuid.UUID, view_id: uuid.UUID, caller: Any) -> None:
    row = await _owned(session, tenant_id, view_id, caller)
    if row.user_id != caller.user_id and not caller.is_admin:
        raise SavedViewError("not_the_owner", "only the person who saved it, or an administrator, can delete it", 403)
    await session.delete(row)
