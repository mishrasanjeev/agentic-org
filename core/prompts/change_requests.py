# SPDX-License-Identifier: Apache-2.0
"""Maker-checker for prompt templates: a change waits for a second person before it takes effect.

While maker-checker is on for a tenant, creating, changing, rolling back or
deleting a prompt template does not happen when it is asked for. The request
is stored as a change request holding the template as it would be afterwards;
a different person approves or rejects it; only an approval applies it, and
the template's history records who proposed it, who approved it and the
request it came from.

Rules:

* The person who proposed a change cannot decide it. Identity is the caller's
  local user id and nothing else: an API key or any other credential without
  one cannot propose or decide, so one person cannot be both maker and
  checker by switching credentials.
* One pending change per template: a second proposal is refused until the
  first is decided or withdrawn (a partial unique index holds this under
  concurrent proposals).
* A change is applied only to the template it was proposed against. If the
  template has changed since, the request becomes ``stale`` and nothing is
  applied.
* A rejection needs a note. The proposer can withdraw a pending request.

On: ``AGENTICORG_PROMPTS_MAKER_CHECKER`` for the whole deployment, or the
authority flag ``prompts.maker_checker`` for a tenant. The flag is read
strictly: if it cannot be read, the change is refused rather than applied
unchecked.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.exc import IntegrityError

from core.config import settings

logger = structlog.get_logger()

FLAG_KEY = "prompts.maker_checker"
KINDS: tuple[str, ...] = ("create", "update", "rollback", "delete")
STATUSES: tuple[str, ...] = ("pending", "approved", "rejected", "withdrawn", "stale")
FIELDS: tuple[str, ...] = ("name", "agent_type", "domain", "template_text", "variables", "description")


class ChangeRequestError(Exception):
    """A refusal with the HTTP status it maps to."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


async def enabled(tenant_id: uuid.UUID) -> bool:
    """Whether prompt changes wait for approval for this tenant; an unreadable flag raises."""
    if settings.prompts_maker_checker:
        return True
    from core.feature_flags import load_flag_rows_strict, row_enabled

    rows = await load_flag_rows_strict(FLAG_KEY, tenant_id=tenant_id)
    subject = str(tenant_id)
    return row_enabled(FLAG_KEY, rows.global_row, subject_id=subject) or row_enabled(
        FLAG_KEY, rows.tenant_row, subject_id=subject
    )


def user_uuid(user: Any) -> uuid.UUID | None:
    if not isinstance(user, dict):
        return None
    for key in ("agenticorg:user_id", "user_id"):
        raw = user.get(key)
        if raw:
            try:
                return uuid.UUID(str(raw))
            except (TypeError, ValueError):
                continue
    return None


def actor_of(user: Any) -> str:
    """Who is acting: the local user, the one identity that is the same whatever credential is used.

    A token subject is not accepted. An administrator's API key has a subject
    of its own, so accepting it would let one person propose as themselves
    and approve with their key.
    """
    local = user_uuid(user)
    if local is not None:
        return f"user:{local}"
    raise ChangeRequestError(
        403, "Under maker-checker a prompt change is proposed and decided by a signed-in user, not an API key"
    )


def state_of(template: Any) -> dict[str, Any]:
    """The fields of a template a change request proposes."""
    return {name: getattr(template, name) for name in FIELDS}


def to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "template_id": str(row.template_id) if row.template_id else None,
        "kind": row.kind,
        "domain": row.domain,
        "proposed": dict(row.proposed or {}),
        "reason": row.reason,
        "status": row.status,
        "requested_by": row.requested_by,
        "requested_at": row.requested_at.isoformat() if row.requested_at else None,
        "decided_by": row.decided_by,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "decision_note": row.decision_note,
    }


async def open_request(
    session: Any,
    tenant_id: uuid.UUID,
    *,
    kind: str,
    template: Any,
    proposed: dict[str, Any],
    domain: str,
    reason: str | None,
    user: Any,
) -> Any:
    """Store a proposed change; refused while the template already has one pending."""
    from sqlalchemy import select

    from core.models.prompt_template import PromptChangeRequest

    if kind not in KINDS:
        raise ChangeRequestError(422, f"kind must be one of {', '.join(KINDS)}")
    maker = actor_of(user)
    if template is not None:
        pending = await session.scalar(
            select(PromptChangeRequest.id).where(
                PromptChangeRequest.tenant_id == tenant_id,
                PromptChangeRequest.template_id == template.id,
                PromptChangeRequest.status == "pending",
            )
        )
        if pending is not None:
            raise ChangeRequestError(409, "This template already has a change waiting for approval")
    row = PromptChangeRequest(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        template_id=template.id if template is not None else None,
        kind=kind,
        domain=domain,
        proposed={name: proposed.get(name) for name in FIELDS if name in proposed},
        base_updated_at=getattr(template, "updated_at", None) if template is not None else None,
        reason=(reason or "").strip()[:500] or None,
        status="pending",
        requested_by=maker,
        requested_by_user=user_uuid(user),
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        # A concurrent proposal for the same template won the partial unique index.
        raise ChangeRequestError(409, "This template already has a change waiting for approval") from None
    logger.info("prompt_change_requested", change_request_id=str(row.id), kind=kind, template_id=str(row.template_id))
    return row


async def list_requests(
    session: Any, tenant_id: uuid.UUID, *, status: str | None, domains: list[str] | None, limit: int = 100
) -> list[Any]:
    from sqlalchemy import select

    from core.models.prompt_template import PromptChangeRequest

    query = select(PromptChangeRequest).where(PromptChangeRequest.tenant_id == tenant_id)
    if status:
        query = query.where(PromptChangeRequest.status == status)
    if isinstance(domains, list):
        query = query.where(PromptChangeRequest.domain.in_(domains))
    rows = await session.scalars(query.order_by(PromptChangeRequest.requested_at.desc()).limit(limit))
    return list(rows.all())


async def _load(session: Any, tenant_id: uuid.UUID, request_id: uuid.UUID, domains: list[str] | None) -> Any:
    from sqlalchemy import select

    from core.models.prompt_template import PromptChangeRequest

    row = await session.scalar(
        select(PromptChangeRequest)
        .where(PromptChangeRequest.tenant_id == tenant_id, PromptChangeRequest.id == request_id)
        .with_for_update()
    )
    if row is None or (isinstance(domains, list) and row.domain not in domains):
        raise ChangeRequestError(404, "Change request not found")
    return row


async def get_request(session: Any, tenant_id: uuid.UUID, request_id: uuid.UUID, domains: list[str] | None) -> Any:
    return await _load(session, tenant_id, request_id, domains)


def _close(row: Any, status: str, by: str, note: str | None) -> None:
    row.status = status
    row.decided_by = by
    row.decided_at = datetime.now(UTC)
    row.decision_note = (note or "").strip()[:500] or None


async def withdraw(
    session: Any, tenant_id: uuid.UUID, request_id: uuid.UUID, *, user: Any, domains: list[str] | None
) -> Any:
    """The proposer takes a pending request back."""
    row = await _load(session, tenant_id, request_id, domains)
    if row.status != "pending":
        raise ChangeRequestError(409, f"The change request is already {row.status}")
    if actor_of(user) != row.requested_by:
        raise ChangeRequestError(403, "Only the person who proposed a change can withdraw it")
    _close(row, "withdrawn", row.requested_by, None)
    return row


async def reject(
    session: Any, tenant_id: uuid.UUID, request_id: uuid.UUID, *, user: Any, note: str, domains: list[str] | None
) -> Any:
    row = await _load(session, tenant_id, request_id, domains)
    checker = _checker(row, user)
    if not (note or "").strip():
        raise ChangeRequestError(422, "A rejection needs a note")
    _close(row, "rejected", checker, note)
    logger.info("prompt_change_rejected", change_request_id=str(row.id))
    return row


def _checker(row: Any, user: Any) -> str:
    if row.status != "pending":
        raise ChangeRequestError(409, f"The change request is already {row.status}")
    checker = actor_of(user)
    if checker == row.requested_by:
        raise ChangeRequestError(403, "A change cannot be decided by the person who proposed it")
    return checker


async def approve(
    session: Any,
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    *,
    user: Any,
    note: str | None,
    domains: list[str] | None,
) -> Any:
    """Apply a pending change on a second person's approval.

    Returns the request: ``approved`` with the change applied, or ``stale``
    with nothing applied when the template is no longer the one the change
    was proposed against.
    """
    from sqlalchemy import select

    from core.models.prompt_template import PromptTemplate, PromptTemplateEditHistory

    row = await _load(session, tenant_id, request_id, domains)
    checker = _checker(row, user)
    proposed = dict(row.proposed or {})
    if row.kind == "create":
        template = PromptTemplate(
            tenant_id=tenant_id,
            name=proposed["name"],
            agent_type=proposed["agent_type"],
            domain=proposed["domain"],
            template_text=proposed["template_text"],
            variables=proposed.get("variables") or [],
            description=proposed.get("description"),
            created_by=row.requested_by_user,
        )
        session.add(template)
        await session.flush()
        row.template_id = template.id
        _close(row, "approved", checker, note)
        return row
    template = await session.scalar(
        select(PromptTemplate)
        .where(PromptTemplate.tenant_id == tenant_id, PromptTemplate.id == row.template_id)
        .with_for_update()
    )
    if template is None or not template.is_active or template.is_builtin or template.updated_at != row.base_updated_at:
        _close(row, "stale", checker, "The template changed after this change was proposed; nothing was applied.")
        logger.info("prompt_change_stale", change_request_id=str(row.id))
        return row
    if row.kind == "delete":
        template.is_active = False
    else:
        before = {
            "name_before": template.name,
            "template_text_before": template.template_text,
            "variables_before": template.variables,
            "description_before": template.description,
        }
        for name in ("name", "template_text", "variables", "description"):
            if name in proposed:
                setattr(template, name, proposed[name])
        session.add(
            PromptTemplateEditHistory(
                tenant_id=tenant_id,
                template_id=template.id,
                edited_by=row.requested_by_user,
                name_after=template.name,
                template_text_after=template.template_text,
                variables_after=template.variables,
                description_after=template.description,
                change_reason=row.reason,
                approved_by=checker,
                change_request_id=row.id,
                **before,
            )
        )
    await session.flush()
    _close(row, "approved", checker, note)
    logger.info("prompt_change_approved", change_request_id=str(row.id), kind=row.kind)
    return row
