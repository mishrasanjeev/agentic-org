# SPDX-License-Identifier: Apache-2.0
"""Source mappings and model aliases.

A **source mapping** ties a spend source to the organisation tree and to
labels: an agent, a workflow, a legacy cost-centre or department id (each a
UUID) or an application (``vocab.APPLICATIONS``) maps to a node (by code), a
product line and a use case. It is upserted by ``(source_type, source_ref)``
and turned off with ``active=false``, never deleted.

A **model alias** maps a model name as called (a dated model name, a
deployment name) to the SKU the tenant's rate cards and invoices use, per
provider. ``canonical_model`` applies it, so a called alias prices with the
SKU's card and matches the SKU's invoice line. Aliases do not chain: an
alias is never another alias's SKU, so applying the map twice changes
nothing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.spend import audit, clock, imports, locks, pricing, vocab
from core.spend.errors import SpendError, require_actor

logger = structlog.get_logger()

IMPORT_REQUIRED = ("source_type", "source_ref")
IMPORT_OPTIONAL = ("org_node_code", "product_line", "use_case", "active")
UUID_SOURCE_TYPES = ("agent", "workflow", "cost_center", "department")


def _on_change(tenant_id: uuid.UUID) -> None:
    """Called after every mapping write; later parts drop the attribution cache here."""
    return None


def check_source_ref(source_type: str, source_ref: str) -> str:
    """An agent, workflow, cost-centre or department id as canonical UUID text; an application by name."""
    kind = vocab.choice(source_type, vocab.SOURCE_TYPES, field="source_type", code="invalid_reference")
    ref = str(source_ref or "").strip()
    if kind in UUID_SOURCE_TYPES:
        try:
            return str(uuid.UUID(ref))
        except ValueError:
            raise SpendError(422, "invalid_reference", f"a {kind} source_ref is its id") from None
    return vocab.choice(ref, vocab.APPLICATIONS, field="source_ref", code="invalid_reference")


def canonical_model(provider: str, model: str, aliases: Any) -> str:
    """The SKU a called model prices and reconciles as (``core.spend.pricing.canonical_model``)."""
    return pricing.canonical_model(provider, model, aliases)


async def alias_map(session: Any, tenant_id: uuid.UUID) -> dict[tuple[str, str], str]:
    """``(provider, alias) -> model_sku`` for the tenant, read now."""
    return await pricing.alias_map(session, tenant_id)


# ---------------------------------------------------------------- mappings


def _mapping_dict(row: Any, node_code: str | None) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "source_type": row.source_type,
        "source_ref": row.source_ref,
        "org_node_id": str(row.org_node_id) if row.org_node_id else None,
        "org_node_code": node_code,
        "product_line": row.product_line,
        "use_case": row.use_case,
        "active": bool(row.active),
        "updated_by": row.updated_by or "",
        "updated_at": row.updated_at.isoformat() if getattr(row, "updated_at", None) else None,
    }


def _mapping_audit(row: Any, node_code: str | None) -> dict[str, Any]:
    return {
        "org_node_code": node_code,
        "product_line": row.product_line,
        "use_case": row.use_case,
        "active": bool(row.active),
    }


MAPPING_FIELDS = ("org_node_code", "product_line", "use_case", "active")


def _check_mapping(body: dict[str, Any]) -> dict[str, Any]:
    """A mapping's fields, checked; ``given`` names the optional fields ``body`` carries (an absent one is kept)."""
    source_type = vocab.choice(
        body.get("source_type"), vocab.SOURCE_TYPES, field="source_type", code="invalid_reference"
    )
    fields: dict[str, Any] = {
        "source_type": source_type,
        "source_ref": check_source_ref(source_type, body.get("source_ref", "")),
        "org_node_code": vocab.norm_code(body["org_node_code"]) if body.get("org_node_code") else None,
        "product_line": vocab.label(body.get("product_line")) or None,
        "use_case": vocab.label(body.get("use_case")) or None,
        "given": {name for name in MAPPING_FIELDS if name in body},
    }
    active = body.get("active")
    fields["active"] = None if active in (None, "") else vocab.parse_bool(active, field="active")
    return fields


async def _node_ids(session: Any, tenant_id: uuid.UUID) -> tuple[dict[str, uuid.UUID], dict[uuid.UUID, str]]:
    from core.models.spend import SpendOrgNode

    rows = (
        await session.execute(select(SpendOrgNode.id, SpendOrgNode.code).where(SpendOrgNode.tenant_id == tenant_id))
    ).all()
    return {r[1]: r[0] for r in rows}, {r[0]: r[1] for r in rows}


async def _mapping_row(session: Any, tenant_id: uuid.UUID, source_type: str, source_ref: str) -> Any:
    from core.models.spend import SpendSourceMapping

    rows = (
        (
            await session.execute(
                select(SpendSourceMapping)
                .where(
                    SpendSourceMapping.tenant_id == tenant_id,
                    SpendSourceMapping.source_type == source_type,
                    SpendSourceMapping.source_ref == source_ref,
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    return rows[0] if rows else None


async def _upsert_mapping(
    session: Any,
    tenant_id: uuid.UUID,
    fields: dict[str, Any],
    node_ids: dict[str, uuid.UUID],
    codes: dict[uuid.UUID, str],
    *,
    who: str,
    now: datetime,
) -> tuple[str, Any, audit.Change | None]:
    from core.models.spend import SpendSourceMapping

    node_id = None
    if fields["org_node_code"] is not None:
        node_id = node_ids.get(fields["org_node_code"])
        if node_id is None:
            raise SpendError(422, "invalid_reference", f"no node has code {fields['org_node_code']}")
    row = await _mapping_row(session, tenant_id, fields["source_type"], fields["source_ref"])
    given = fields["given"]
    values = {
        "org_node_id": node_id if "org_node_code" in given or row is None else row.org_node_id,
        "product_line": fields["product_line"] if "product_line" in given or row is None else row.product_line,
        "use_case": fields["use_case"] if "use_case" in given or row is None else row.use_case,
        "active": fields["active"] if fields["active"] is not None else (True if row is None else bool(row.active)),
    }
    if values["org_node_id"] is None and values["product_line"] is None and values["use_case"] is None:
        raise SpendError(422, "invalid_reference", "a mapping names an org node, a product line or a use case")
    node_code = codes.get(values["org_node_id"]) if values["org_node_id"] else None
    key = f"{fields['source_type']}:{fields['source_ref']}"
    if row is None:
        inserted = (
            await session.execute(
                pg_insert(SpendSourceMapping)
                .values(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    source_type=fields["source_type"],
                    source_ref=fields["source_ref"],
                    updated_by=who,
                    created_at=now,
                    updated_at=now,
                    **values,
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "source_type", "source_ref"])
                .returning(SpendSourceMapping.id)
            )
        ).scalar_one_or_none()
        row = await _mapping_row(session, tenant_id, fields["source_type"], fields["source_ref"])
        if inserted is not None:
            return "created", row, audit.Change(key, None, _mapping_audit(row, node_code))
    before = _mapping_audit(row, codes.get(row.org_node_id) if row.org_node_id else None)
    if all(getattr(row, name) == value for name, value in values.items()):
        return "unchanged", row, None
    for name, value in values.items():
        setattr(row, name, value)
    row.updated_by = who
    row.updated_at = now
    await session.flush()
    return "updated", row, audit.Change(key, before, _mapping_audit(row, node_code))


async def put_mapping(
    tenant_id: uuid.UUID, body: dict[str, Any], *, actor: str, now: datetime | None = None
) -> dict[str, Any]:
    """Upsert the mapping of ``(source_type, source_ref)``."""
    from core.database import get_tenant_session

    who = require_actor(actor)
    fields = _check_mapping(body)
    stamp = now or clock.now_utc()
    async with get_tenant_session(tenant_id) as session:
        node_ids, codes = await _node_ids(session, tenant_id)
        outcome, row, change = await _upsert_mapping(session, tenant_id, fields, node_ids, codes, who=who, now=stamp)
        if change is not None:
            session.add(
                audit.audit_change(
                    tenant_id,
                    actor_id=who,
                    action=f"mappings.{'create' if outcome == 'created' else 'update'}",
                    resource_type="spend_source_mapping",
                    resource_id=str(row.id),
                    changes=[change],
                    now=stamp,
                )
            )
        out = _mapping_dict(row, codes.get(row.org_node_id) if row.org_node_id else None)
    _on_change(tenant_id)
    return {**out, "outcome": outcome}


async def import_mappings(
    tenant_id: uuid.UUID,
    rows: list[dict[str, str]],
    *,
    actor: str,
    dry_run: bool,
    file_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Upsert every valid row by ``(source_type, source_ref)``; a dry run reports the same and writes nothing."""
    from core.database import get_tenant_session

    who = require_actor(actor)
    stamp = now or clock.now_utc()
    report = imports.new_report(dry_run=dry_run, received=len(rows))
    checked: list[tuple[int, dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(rows, start=2):
        key = f"{raw.get('source_type', '')}:{raw.get('source_ref', '')}"
        try:
            fields = _check_mapping(raw)
        except SpendError as exc:
            imports.reject(report, row=index, key=key, reason=exc.code)
            continue
        if (fields["source_type"], fields["source_ref"]) in seen:
            imports.reject(report, row=index, key=key, reason="duplicate_in_file")
            continue
        seen.add((fields["source_type"], fields["source_ref"]))
        checked.append((index, fields))
    changes: list[audit.Change] = []
    async with get_tenant_session(tenant_id) as session:
        node_ids, codes = await _node_ids(session, tenant_id)
        outer = await session.begin_nested() if dry_run else None
        for index, fields in checked:
            try:
                outcome, _row, change = await _upsert_mapping(
                    session, tenant_id, fields, node_ids, codes, who=who, now=stamp
                )
            except SpendError as exc:
                imports.reject(
                    report, row=index, key=f"{fields['source_type']}:{fields['source_ref']}", reason=exc.code
                )
                continue
            report[outcome] += 1
            if change is not None:
                changes.append(change)
        report["rejected"].sort(key=lambda item: item["row"])
        if outer is not None:
            await outer.rollback()
        elif changes:
            for entry in audit.audit_changes(
                tenant_id,
                actor_id=who,
                action="mappings.import",
                resource_type="spend_source_mapping",
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


async def list_mappings(
    tenant_id: uuid.UUID,
    *,
    source_type: str | None = None,
    active: bool | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    """Mappings by source, with each node's code."""
    from core.database import get_tenant_session
    from core.models.spend import SpendSourceMapping

    conditions = [SpendSourceMapping.tenant_id == tenant_id]
    if source_type:
        conditions.append(
            SpendSourceMapping.source_type
            == vocab.choice(source_type, vocab.SOURCE_TYPES, field="source_type", code="invalid_reference")
        )
    if active is not None:
        conditions.append(SpendSourceMapping.active == active)
    async with get_tenant_session(tenant_id) as session:
        total = (
            await session.execute(select(func.count()).select_from(SpendSourceMapping).where(*conditions))
        ).scalar()
        rows = (
            (
                await session.execute(
                    select(SpendSourceMapping)
                    .where(*conditions)
                    .order_by(SpendSourceMapping.source_type, SpendSourceMapping.source_ref)
                    .limit(max(1, min(limit, 500)))
                    .offset(max(0, offset))
                )
            )
            .scalars()
            .all()
        )
        _ids, codes = await _node_ids(session, tenant_id)
    items = [_mapping_dict(row, codes.get(row.org_node_id) if row.org_node_id else None) for row in rows]
    return {"items": items, "total": int(total or 0)}


# ---------------------------------------------------------------- aliases


def _alias_dict(row: Any) -> dict[str, Any]:
    return {
        "provider": row.provider,
        "alias": row.alias,
        "model_sku": row.model_sku,
        "updated_by": row.updated_by or "",
        "updated_at": row.updated_at.isoformat() if getattr(row, "updated_at", None) else None,
    }


async def list_aliases(tenant_id: uuid.UUID, *, provider: str | None = None) -> dict[str, Any]:
    """Every alias of the tenant, by provider and alias."""
    from core.database import get_tenant_session
    from core.models.spend import SpendModelAlias

    conditions = [SpendModelAlias.tenant_id == tenant_id]
    if provider:
        conditions.append(SpendModelAlias.provider == vocab.norm_provider(provider))
    async with get_tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    select(SpendModelAlias).where(*conditions).order_by(SpendModelAlias.provider, SpendModelAlias.alias)
                )
            )
            .scalars()
            .all()
        )
    return {"items": [_alias_dict(row) for row in rows], "total": len(rows)}


async def put_alias(
    tenant_id: uuid.UUID, body: dict[str, Any], *, actor: str, now: datetime | None = None
) -> dict[str, Any]:
    """Upsert the SKU of ``(provider, alias)``; refuses an alias of itself and alias chains."""
    from core.database import get_tenant_session
    from core.models.spend import SpendModelAlias

    who = require_actor(actor)
    provider = vocab.norm_provider(body.get("provider"))
    alias = vocab.norm_sku(body.get("alias"))
    sku = vocab.norm_sku(body.get("model_sku"))
    if alias == sku:
        raise SpendError(422, "invalid_sku", "an alias names a different SKU")
    stamp = now or clock.now_utc()
    async with get_tenant_session(tenant_id) as session:
        # one writer per provider at a time, so the chain checks below see every alias
        await locks.xact_lock(session, locks.model_alias(tenant_id, provider))
        rows = (
            (
                await session.execute(
                    select(SpendModelAlias).where(
                        SpendModelAlias.tenant_id == tenant_id, SpendModelAlias.provider == provider
                    )
                )
            )
            .scalars()
            .all()
        )
        if any(r.alias == sku for r in rows):
            raise SpendError(422, "invalid_sku", f"{sku} is itself an alias; name the SKU it maps to")
        if any(r.model_sku == alias for r in rows):
            raise SpendError(422, "invalid_sku", f"{alias} is the SKU of another alias; aliases do not chain")
        row = next((r for r in rows if r.alias == alias), None)
        if row is None:
            row = SpendModelAlias(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                provider=provider,
                alias=alias,
                model_sku=sku,
                updated_by=who,
                created_at=stamp,
                updated_at=stamp,
            )
            session.add(row)
            change: audit.Change | None = audit.Change(f"{provider}:{alias}", None, {"model_sku": sku})
        elif row.model_sku != sku:
            change = audit.Change(f"{provider}:{alias}", {"model_sku": row.model_sku}, {"model_sku": sku})
            row.model_sku = sku
            row.updated_by = who
            row.updated_at = stamp
        else:
            change = None
        await session.flush()
        if change is not None:
            session.add(
                audit.audit_change(
                    tenant_id,
                    actor_id=who,
                    action="model_aliases.put",
                    resource_type="spend_model_alias",
                    resource_id=str(row.id),
                    changes=[change],
                    now=stamp,
                )
            )
        out = _alias_dict(row)
    pricing.invalidate_aliases(tenant_id)
    _on_change(tenant_id)
    logger.info("spend_model_alias_kept", provider=provider)
    return out
