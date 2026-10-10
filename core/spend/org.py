# SPDX-License-Identifier: Apache-2.0
"""The organisation tree spend rolls up through: group, business unit, department, team, cost centre.

A node has the organisation's own code (unique per tenant, upper-cased), a
name, a kind, a parent, an owner (a user of the tenant) and an active flag.
Kinds nest by ``vocab.PARENT_KINDS`` (a team sits under a department or a
team, only a group may be a root) and the tree is at most ``MAX_DEPTH``
levels below its root. A node is never deleted: it is deactivated, and its
children keep rolling up through it.

Every parent change and every import takes the tenant's org-tree advisory
lock and then checks the whole tenant's parent links for a cycle, so two
concurrent re-parentings can never build one together.

The import upserts by code: every row is checked first (codes, names,
kinds, owners, parents that exist or are created by the file, parent kinds
and cycles on the resulting tree, existing children of a node whose kind
changes), a row whose parent row was rejected is rejected too, and only
then are the accepted rows written, parents before children, each in its
own savepoint. A dry run reports the same and writes nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from core.spend import audit, clock, imports, locks, vocab
from core.spend.errors import SpendError, require_actor

logger = structlog.get_logger()

IMPORT_REQUIRED = ("code", "name", "kind")
IMPORT_OPTIONAL = ("parent_code", "owner_user_id", "active")
MAX_NAME = 200

_ANCESTORS_SQL = text(
    """
    WITH RECURSIVE up(id, parent_id, kind, code, active, depth) AS (
      SELECT id, parent_id, kind, code, active, 0 FROM spend_org_nodes WHERE tenant_id = :tid AND id = :node
      UNION ALL
      SELECT n.id, n.parent_id, n.kind, n.code, n.active, up.depth + 1
      FROM spend_org_nodes n JOIN up ON n.id = up.parent_id
      WHERE n.tenant_id = :tid AND up.depth < 16)
    SELECT id, parent_id, kind, code, active, depth FROM up ORDER BY depth
    """
)


@dataclass(frozen=True)
class NodeRef:
    id: uuid.UUID
    code: str
    kind: str
    active: bool
    parent_id: uuid.UUID | None


def _on_change(tenant_id: uuid.UUID) -> None:
    """Called after every write to the tree; later parts drop the attribution cache here."""
    return None


# ---------------------------------------------------------------- pure checks


def check_parent_kind(kind: str, parent_kind: str | None) -> None:
    """422 ``invalid_parent_kind`` when a node of ``kind`` may not sit under ``parent_kind`` (``None`` = a root)."""
    allowed = vocab.PARENT_KINDS.get(kind, ())
    if parent_kind not in allowed:
        where = f"under a {parent_kind}" if parent_kind else "at the root"
        raise SpendError(422, "invalid_parent_kind", f"a {kind} cannot sit {where}")


def _depth_of(parent_of: Mapping[uuid.UUID, uuid.UUID | None], node_id: uuid.UUID | None) -> int | None:
    """Levels above ``node_id`` (a root is 0); ``None`` when the walk loops or runs past the tree's size."""
    depth = 0
    current = parent_of.get(node_id) if node_id is not None else None
    limit = len(parent_of) + 1
    while current is not None:
        depth += 1
        if depth > limit:
            return None
        current = parent_of.get(current)
    return depth


def _height(parent_of: Mapping[uuid.UUID, uuid.UUID | None], node_id: uuid.UUID) -> int:
    """Levels below ``node_id`` in the current tree (a leaf is 0)."""
    children: dict[uuid.UUID, list[uuid.UUID]] = {}
    for child, parent in parent_of.items():
        if parent is not None:
            children.setdefault(parent, []).append(child)
    best = 0
    stack = [(node_id, 0)]
    seen: set[uuid.UUID] = set()
    while stack:
        current, level = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        best = max(best, level)
        stack.extend((child, level + 1) for child in children.get(current, ()))
    return best


def would_cycle(
    parent_of: Mapping[uuid.UUID, uuid.UUID | None], node_id: uuid.UUID, new_parent_id: uuid.UUID | None
) -> bool:
    """True when ``node_id`` under ``new_parent_id`` makes a cycle or puts any node deeper than ``MAX_DEPTH``."""
    if new_parent_id is None:
        return _height(parent_of, node_id) > vocab.MAX_DEPTH
    if new_parent_id == node_id:
        return True
    current: uuid.UUID | None = new_parent_id
    steps = 0
    limit = len(parent_of) + 2
    while current is not None:
        if current == node_id:
            return True
        steps += 1
        if steps > limit:
            return True
        current = parent_of.get(current)
    parent_depth = _depth_of(parent_of, new_parent_id)
    if parent_depth is None:
        return True
    return parent_depth + 1 + _height(parent_of, node_id) > vocab.MAX_DEPTH


# ---------------------------------------------------------------- reads


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _node_dict(row: Any, parent_code: str | None) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "code": row.code,
        "name": row.name,
        "kind": row.kind,
        "parent_id": str(row.parent_id) if row.parent_id else None,
        "parent_code": parent_code,
        "owner_user_id": str(row.owner_user_id) if row.owner_user_id else None,
        "active": bool(row.active),
        "deactivated_at": _iso(row.deactivated_at),
        "updated_at": _iso(getattr(row, "updated_at", None)),
    }


def _audit_fields(row: Any, parent_code: str | None) -> dict[str, Any]:
    return {
        "name": row.name,
        "kind": row.kind,
        "parent_code": parent_code,
        "owner_user_id": row.owner_user_id,
        "active": bool(row.active),
    }


async def _all_nodes(session: Any, tenant_id: uuid.UUID) -> list[Any]:
    from core.models.spend import SpendOrgNode

    return list(
        (await session.execute(select(SpendOrgNode).where(SpendOrgNode.tenant_id == tenant_id))).scalars().all()
    )


async def _node_row(session: Any, tenant_id: uuid.UUID, node_id: uuid.UUID) -> Any:
    from core.models.spend import SpendOrgNode

    rows = (
        (
            await session.execute(
                select(SpendOrgNode).where(SpendOrgNode.tenant_id == tenant_id, SpendOrgNode.id == node_id)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise SpendError(404, "not_found", "no such organisation node")
    return rows[0]


async def _code_of(session: Any, tenant_id: uuid.UUID, node_id: uuid.UUID | None) -> str | None:
    if node_id is None:
        return None
    from core.models.spend import SpendOrgNode

    rows = (
        await session.execute(
            select(SpendOrgNode.code).where(SpendOrgNode.tenant_id == tenant_id, SpendOrgNode.id == node_id)
        )
    ).all()
    return rows[0][0] if rows else None


async def ancestors(session: Any, tenant_id: uuid.UUID, node_id: uuid.UUID) -> list[NodeRef]:
    """The node and its ancestors, nearest first, through a tenant-bounded recursive query."""
    rows = (await session.execute(_ANCESTORS_SQL, {"tid": tenant_id, "node": node_id})).all()
    return [NodeRef(id=r[0], code=r[3], kind=r[2], active=bool(r[4]), parent_id=r[1]) for r in rows]


async def business_unit_of(session: Any, tenant_id: uuid.UUID, node_id: uuid.UUID) -> uuid.UUID | None:
    """The nearest business unit at or above the node."""
    for ref in await ancestors(session, tenant_id, node_id):
        if ref.kind == "business_unit":
            return ref.id
    return None


async def list_nodes(
    tenant_id: uuid.UUID,
    *,
    active: bool | None = None,
    kind: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    """Nodes by code, with their parent's code."""
    from core.database import get_tenant_session
    from core.models.spend import SpendOrgNode

    conditions = [SpendOrgNode.tenant_id == tenant_id]
    if active is not None:
        conditions.append(SpendOrgNode.active == active)
    if kind:
        conditions.append(SpendOrgNode.kind == vocab.choice(kind, vocab.NODE_KINDS, field="kind", code="invalid_kind"))
    async with get_tenant_session(tenant_id) as session:
        total = (await session.execute(select(func.count()).select_from(SpendOrgNode).where(*conditions))).scalar()
        rows = (
            (
                await session.execute(
                    select(SpendOrgNode)
                    .where(*conditions)
                    .order_by(SpendOrgNode.code)
                    .limit(max(1, min(limit, 500)))
                    .offset(max(0, offset))
                )
            )
            .scalars()
            .all()
        )
        parent_ids = sorted({row.parent_id for row in rows if row.parent_id}, key=str)
        codes: dict[Any, str] = {}
        if parent_ids:
            found = await session.execute(
                select(SpendOrgNode.id, SpendOrgNode.code).where(
                    SpendOrgNode.tenant_id == tenant_id, SpendOrgNode.id.in_(parent_ids)
                )
            )
            codes = {r[0]: r[1] for r in found.all()}
    items = [_node_dict(row, codes.get(row.parent_id)) for row in rows]
    return {"items": items, "total": int(total or 0)}


async def get_node(tenant_id: uuid.UUID, node_id: uuid.UUID) -> dict[str, Any]:
    """A node with its ancestors (nearest first) and its business unit."""
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        row = await _node_row(session, tenant_id, node_id)
        chain = await ancestors(session, tenant_id, node_id)
    above = [ref for ref in chain if ref.id != row.id]
    business_unit = next((ref.id for ref in chain if ref.kind == "business_unit"), None)
    parent_code = above[0].code if above else None
    return {
        **_node_dict(row, parent_code),
        "ancestors": [{"id": str(ref.id), "code": ref.code, "kind": ref.kind} for ref in above],
        "business_unit_node_id": str(business_unit) if business_unit else None,
    }


# ---------------------------------------------------------------- writes


async def _check_owner(session: Any, tenant_id: uuid.UUID, owner: Any) -> uuid.UUID | None:
    """The owner as a user id of this tenant; 422 ``invalid_reference`` otherwise."""
    if owner in (None, ""):
        return None
    try:
        owner_id = owner if isinstance(owner, uuid.UUID) else uuid.UUID(str(owner).strip())
    except ValueError:
        raise SpendError(422, "invalid_reference", "owner_user_id is a user id") from None
    found = (
        await session.execute(
            text("SELECT 1 FROM users WHERE id = :u AND tenant_id = :t"), {"u": owner_id, "t": tenant_id}
        )
    ).all()
    if not found:
        raise SpendError(422, "invalid_reference", "owner_user_id is not a user of this tenant")
    return owner_id


async def create_node(
    tenant_id: uuid.UUID, body: dict[str, Any], *, actor: str, now: datetime | None = None
) -> dict[str, Any]:
    """A new node under its parent (by ``parent_code``); refused when the code is taken or the kinds do not nest."""
    from core.database import get_tenant_session
    from core.models.spend import SpendOrgNode

    who = require_actor(actor)
    code = vocab.norm_code(body.get("code"))
    name = vocab.free_text(body.get("name"), field="name", max_len=MAX_NAME, required=True)
    kind = vocab.choice(body.get("kind"), vocab.NODE_KINDS, field="kind", code="invalid_kind")
    parent_code = vocab.norm_code(body["parent_code"]) if body.get("parent_code") else None
    stamp = now or clock.now_utc()
    async with get_tenant_session(tenant_id) as session:
        await locks.xact_lock(session, locks.org_tree(tenant_id))
        nodes = await _all_nodes(session, tenant_id)
        by_code = {n.code: n for n in nodes}
        if code in by_code:
            raise SpendError(409, "code_taken", f"a node with code {code} exists")
        parent = None
        if parent_code is not None:
            parent = by_code.get(parent_code)
            if parent is None:
                raise SpendError(422, "invalid_reference", f"no node has code {parent_code}")
        check_parent_kind(kind, parent.kind if parent else None)
        node_id = uuid.uuid4()
        parent_of = {n.id: n.parent_id for n in nodes}
        if parent is not None and would_cycle(parent_of, node_id, parent.id):
            raise SpendError(409, "cycle", f"the tree is at most {vocab.MAX_DEPTH} levels deep")
        owner = await _check_owner(session, tenant_id, body.get("owner_user_id"))
        row = SpendOrgNode(
            id=node_id,
            tenant_id=tenant_id,
            code=code,
            name=name,
            kind=kind,
            parent_id=parent.id if parent else None,
            owner_user_id=owner,
            active=True,
            created_by=who,
            updated_by=who,
            created_at=stamp,
            updated_at=stamp,
        )
        session.add(row)
        await session.flush()
        session.add(
            audit.audit_change(
                tenant_id,
                actor_id=who,
                action="org_node.create",
                resource_type="spend_org_node",
                resource_id=str(row.id),
                changes=[audit.Change(code, None, _audit_fields(row, parent_code))],
                now=stamp,
            )
        )
        out = _node_dict(row, parent_code)
    _on_change(tenant_id)
    logger.info("spend_org_node_created", kind=kind)
    return out


async def update_node(
    tenant_id: uuid.UUID, node_id: uuid.UUID, body: dict[str, Any], *, actor: str, now: datetime | None = None
) -> dict[str, Any]:
    """Change a node's name, parent (``parent_code`` or ``clear_parent``), owner or active flag.

    Only the keys present in ``body`` change. ``active=false`` stamps
    ``deactivated_at``; there is no delete.
    """
    from core.database import get_tenant_session

    who = require_actor(actor)
    stamp = now or clock.now_utc()
    async with get_tenant_session(tenant_id) as session:
        row = await _node_row(session, tenant_id, node_id)
        old_parent_code = await _code_of(session, tenant_id, row.parent_id)
        before = _audit_fields(row, old_parent_code)
        parent_code = old_parent_code
        if "name" in body and body["name"] is not None:
            row.name = vocab.free_text(body["name"], field="name", max_len=MAX_NAME, required=True)
        moving = bool(body.get("clear_parent")) or (
            body.get("parent_code") not in (None, "") and vocab.norm_code(body["parent_code"]) != old_parent_code
        )
        if moving:
            await locks.xact_lock(session, locks.org_tree(tenant_id))
            nodes = await _all_nodes(session, tenant_id)
            parent = None
            if not body.get("clear_parent"):
                wanted = vocab.norm_code(body["parent_code"])
                parent = next((n for n in nodes if n.code == wanted), None)
                if parent is None:
                    raise SpendError(422, "invalid_reference", f"no node has code {wanted}")
            check_parent_kind(row.kind, parent.kind if parent else None)
            parent_of = {n.id: n.parent_id for n in nodes}
            if would_cycle(parent_of, row.id, parent.id if parent else None):
                raise SpendError(409, "cycle", f"the move makes a cycle or a tree deeper than {vocab.MAX_DEPTH} levels")
            row.parent_id = parent.id if parent else None
            parent_code = parent.code if parent else None
        if "owner_user_id" in body:
            row.owner_user_id = await _check_owner(session, tenant_id, body.get("owner_user_id"))
        if "active" in body and body["active"] is not None and bool(body["active"]) != bool(row.active):
            row.active = bool(body["active"])
            row.deactivated_at = None if row.active else stamp
        after = _audit_fields(row, parent_code)
        if after != before:
            row.updated_by = who
            row.updated_at = stamp
            await session.flush()
            session.add(
                audit.audit_change(
                    tenant_id,
                    actor_id=who,
                    action="org_node.update",
                    resource_type="spend_org_node",
                    resource_id=str(row.id),
                    changes=[audit.Change(row.code, before, after)],
                    now=stamp,
                )
            )
        out = _node_dict(row, parent_code)
    _on_change(tenant_id)
    return out


# ---------------------------------------------------------------- import


@dataclass
class _Planned:
    row: int
    code: str
    name: str
    kind: str
    parent_code: str | None
    keep_parent: bool
    owner: uuid.UUID | None
    keep_owner: bool
    active: bool | None


def _plan_row(raw: dict[str, str], index: int) -> _Planned:
    code = vocab.norm_code(raw.get("code"))
    name = vocab.free_text(raw.get("name"), field="name", max_len=MAX_NAME, required=True)
    kind = vocab.choice(raw.get("kind"), vocab.NODE_KINDS, field="kind", code="invalid_kind")
    keep_parent = "parent_code" not in raw
    parent_code = None
    if not keep_parent and raw.get("parent_code"):
        try:
            parent_code = vocab.norm_code(raw["parent_code"])
        except SpendError:
            raise SpendError(422, "unknown_parent", "parent_code is not a code") from None
    keep_owner = "owner_user_id" not in raw
    owner = None
    if not keep_owner and raw.get("owner_user_id"):
        try:
            owner = uuid.UUID(raw["owner_user_id"].strip())
        except ValueError:
            raise SpendError(422, "invalid_owner", "owner_user_id is a user id") from None
    try:
        active = vocab.parse_bool(raw.get("active"), field="active") if "active" in raw else None
    except SpendError:
        raise SpendError(422, "invalid_text", "active is true or false") from None
    return _Planned(index, code, name, kind, parent_code, keep_parent, owner, keep_owner, active)


async def _tenant_users(session: Any, tenant_id: uuid.UUID, ids: set[uuid.UUID]) -> set[uuid.UUID]:
    if not ids:
        return set()
    rows = (
        await session.execute(
            text("SELECT id FROM users WHERE tenant_id = :t AND id = ANY(:ids)"),
            {"t": tenant_id, "ids": sorted(ids, key=str)},
        )
    ).all()
    return {uuid.UUID(str(r[0])) for r in rows}


def _final_parents(planned: Mapping[str, _Planned], existing: Mapping[str, Any]) -> dict[str, str | None]:
    """Code -> parent code of the tree once ``planned`` rows are applied (a new row without parent is a root)."""
    parent_code_of: dict[str, str | None] = {code: node.parent_code for code, node in existing.items()}
    for code, plan in planned.items():
        if not plan.keep_parent or code not in existing:
            parent_code_of[code] = plan.parent_code
    return parent_code_of


def _path_up(parent_code_of: Mapping[str, str | None], code: str) -> list[str] | None:
    """``code`` and its ancestors, nearest first; ``None`` when the walk meets a cycle."""
    path: list[str] = []
    seen: set[str] = set()
    current: str | None = code
    while current is not None:
        if current in seen:
            return None
        seen.add(current)
        path.append(current)
        current = parent_code_of.get(current)
    return path


def _tree_problems(accepted: Mapping[str, _Planned], parent_code_of: Mapping[str, str | None]) -> dict[str, str]:
    """Accepted rows that make a cycle, or that put a node deeper than ``MAX_DEPTH`` (the deepest such row)."""
    found: dict[str, str] = {}
    for code in accepted:
        if _path_up(parent_code_of, code) is None:
            found[code] = "cycle"
    if found:
        return found
    for code in parent_code_of:
        path = _path_up(parent_code_of, code)
        if path is None or len(path) - 1 <= vocab.MAX_DEPTH:
            continue
        culprit = next((step for step in path if step in accepted), None)
        if culprit is not None:
            found[culprit] = "cycle"
    return found


def _resolve_plan(planned: dict[str, _Planned], existing: dict[str, Any], rejected: dict[str, str]) -> dict[str, str]:
    """Reject planned rows until the resulting tree is consistent; returns code -> reason for the new rejections."""
    reasons: dict[str, str] = {}
    while True:
        accepted = {c: p for c, p in planned.items() if c not in rejected and c not in reasons}
        kind_of = {code: node.kind for code, node in existing.items()}
        kind_of.update({c: p.kind for c, p in accepted.items()})
        parent_code_of = _final_parents(accepted, existing)
        found: dict[str, str] = {}
        staying_children: dict[str, list[str]] = {}
        for child_code, parent in parent_code_of.items():
            if parent is not None and child_code not in accepted and child_code in existing:
                staying_children.setdefault(parent, []).append(child_code)
        for code, plan in accepted.items():
            target = parent_code_of.get(code)
            if target is not None and (target in rejected or target in reasons) and target in planned:
                found[code] = "parent_rejected"
                continue
            if target is not None and target not in kind_of:
                found[code] = "unknown_parent"
                continue
            if plan.kind not in vocab.PARENT_KINDS or kind_of.get(target) not in vocab.PARENT_KINDS[plan.kind]:
                found[code] = "invalid_parent_kind"
                continue
            # existing children that stay where they are must still nest under the new kind
            for child_code in staying_children.get(code, ()):
                if plan.kind not in vocab.PARENT_KINDS.get(existing[child_code].kind, ()):
                    found[code] = "invalid_parent_kind"
                    break
        if not found:
            found = _tree_problems(accepted, parent_code_of)
        if not found:
            return reasons
        reasons.update(found)


@dataclass
class _Existing:
    kind: str
    parent_code: str | None
    row: Any


def _apply_order(accepted: dict[str, _Planned], parent_code_of: Mapping[str, str | None]) -> list[str]:
    """Accepted codes with every planned parent before its children."""
    order: list[str] = []
    placed: set[str] = set()

    def place(code: str, depth: int = 0) -> None:
        if code in placed or depth > len(accepted) + 1:
            return
        parent = parent_code_of.get(code)
        if parent in accepted:
            place(parent, depth + 1)
        placed.add(code)
        order.append(code)

    for code in accepted:
        place(code)
    return order


async def import_nodes(
    tenant_id: uuid.UUID,
    rows: list[dict[str, str]],
    *,
    actor: str,
    dry_run: bool,
    file_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Upsert nodes by code under the org-tree lock; reports created, updated, unchanged and rejected rows."""
    from core.database import get_tenant_session
    from core.models.spend import SpendOrgNode

    who = require_actor(actor)
    stamp = now or clock.now_utc()
    report = imports.new_report(dry_run=dry_run, received=len(rows))
    planned: dict[str, _Planned] = {}
    rejected: dict[str, str] = {}
    for index, raw in enumerate(rows, start=2):
        try:
            plan = _plan_row(raw, index)
        except SpendError as exc:
            imports.reject(report, row=index, key=str(raw.get("code", "")), reason=exc.code)
            continue
        if plan.code in planned:
            imports.reject(report, row=index, key=plan.code, reason="duplicate_in_file")
            continue
        planned[plan.code] = plan
    changes: list[audit.Change] = []
    async with get_tenant_session(tenant_id) as session:
        await locks.xact_lock(session, locks.org_tree(tenant_id))
        nodes = await _all_nodes(session, tenant_id)
        code_by_id = {n.id: n.code for n in nodes}
        existing = {n.code: _Existing(n.kind, code_by_id.get(n.parent_id), n) for n in nodes}
        owners = await _tenant_users(session, tenant_id, {p.owner for p in planned.values() if p.owner})
        for code, plan in planned.items():
            if plan.owner is not None and plan.owner not in owners:
                rejected[code] = "invalid_owner"
        rejected.update(_resolve_plan(planned, existing, rejected))
        accepted = {c: p for c, p in planned.items() if c not in rejected}
        parent_code_of = _final_parents(accepted, existing)
        outer = await session.begin_nested() if dry_run else None
        written: dict[str, Any] = {code: entry.row for code, entry in existing.items()}
        for code in _apply_order(accepted, parent_code_of):
            plan = accepted[code]
            target = parent_code_of.get(code)
            if target is not None and target in rejected and target in planned:
                rejected[code] = "parent_rejected"
                continue
            current = existing.get(code)
            before = _audit_fields(current.row, current.parent_code) if current else None
            try:
                async with session.begin_nested():
                    row = current.row if current else None
                    if row is None:
                        row = SpendOrgNode(
                            id=uuid.uuid4(),
                            tenant_id=tenant_id,
                            code=code,
                            active=True,
                            created_by=who,
                            created_at=stamp,
                        )
                        session.add(row)
                    row.name = plan.name
                    row.kind = plan.kind
                    parent_row = written.get(target) if target else None
                    row.parent_id = parent_row.id if parent_row is not None else None
                    if not plan.keep_owner:
                        row.owner_user_id = plan.owner
                    if plan.active is not None and bool(row.active) != plan.active:
                        row.active = plan.active
                        row.deactivated_at = None if plan.active else stamp
                    after = _audit_fields(row, target)
                    if before is None or after != before:
                        row.updated_by = who
                        row.updated_at = stamp
                    await session.flush()
            except SQLAlchemyError as exc:
                logger.warning("spend_org_import_row_failed", error=type(exc).__name__)
                rejected[code] = "write_failed"
                continue
            written[code] = row
            if before is None:
                report["created"] += 1
                changes.append(audit.Change(code, None, after))
            elif after != before:
                report["updated"] += 1
                changes.append(audit.Change(code, before, after))
            else:
                report["unchanged"] += 1
        for code, reason in rejected.items():
            if code in planned:
                imports.reject(report, row=planned[code].row, key=code, reason=reason)
        report["rejected"].sort(key=lambda item: item["row"])
        if outer is not None:
            await outer.rollback()
        elif changes:
            for entry in audit.audit_changes(
                tenant_id,
                actor_id=who,
                action="org_node.import",
                resource_type="spend_org_node",
                changes=changes,
                summary=_summary(report),
                file_sha256=file_sha256,
                now=stamp,
            ):
                session.add(entry)
    if changes and not dry_run:
        _on_change(tenant_id)
    return report


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    return {key: report[key] for key in ("received", "created", "updated", "unchanged")} | {
        "rejected": len(report["rejected"])
    }
