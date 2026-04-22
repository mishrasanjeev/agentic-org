"""Prompt template CRUD endpoints — admin + domain head access."""

from __future__ import annotations

import re
import uuid as _uuid
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.deps import (
    get_current_tenant,
    get_current_user,
    get_user_domains,
    require_tenant_admin,
)
from core.database import get_tenant_session
from core.models.prompt_template import PromptTemplate, PromptTemplateEditHistory
from core.schemas.api import PromptTemplateCreate, PromptTemplateUpdate


def _user_uuid_from_claims(user: dict | None) -> _uuid.UUID | None:
    """Extract a user UUID from JWT claims for created_by / edited_by.

    Codex 2026-04-22 audit gap #9 — the existing prompt audit trail
    didn't record who made the change. Claims carry ``user_id``
    (canonical) or ``sub`` (email). Return a UUID when the claim is
    UUID-shaped, else None so malformed tokens don't blow up writes.

    Tolerant of non-dict inputs (e.g., the Depends() sentinel in
    direct-call tests).
    """
    if not isinstance(user, dict) or not user:
        return None
    for key in ("user_id", "sub"):
        raw = user.get(key)
        if not raw:
            continue
        try:
            return _uuid.UUID(str(raw))
        except (TypeError, ValueError):
            continue
    return None

logger = structlog.get_logger()

router = APIRouter()

# Regex to find tool references in prompt templates.
# Matches patterns like: {{tool:send_email}}, {{tools.send_email}},
# {tool:send_email}, use_tool(send_email), @tool(send_email)
_TOOL_REF_PATTERNS = [
    re.compile(r"\{\{?tools?[.:]\s*(\w+)\s*\}?\}"),  # {{tool:name}} or {{tools.name}}
    re.compile(r"@tool\(\s*(\w+)\s*\)"),               # @tool(name)
    re.compile(r"use_tool\(\s*['\"]?(\w+)['\"]?\s*\)"), # use_tool(name) or use_tool('name')
]


def _extract_tool_references(template_text: str) -> list[str]:
    """Extract all connector tool references from a prompt template."""
    refs: set[str] = set()
    for pattern in _TOOL_REF_PATTERNS:
        for match in pattern.finditer(template_text):
            refs.add(match.group(1))
    return sorted(refs)


def _validate_tool_references(template_text: str) -> list[str]:
    """Validate that tool references in a prompt template exist in the registry.

    Returns a list of invalid tool names (empty if all are valid).
    """
    refs = _extract_tool_references(template_text)
    if not refs:
        return []

    from core.langgraph.tool_adapter import _build_tool_index

    index = _build_tool_index()
    return [ref for ref in refs if ref not in index]


def _template_to_dict(t: PromptTemplate) -> dict:
    return {
        "id": str(t.id),
        "name": t.name,
        "agent_type": t.agent_type,
        "domain": t.domain,
        "template_text": t.template_text,
        "variables": t.variables,
        "description": t.description,
        "is_builtin": t.is_builtin,
        "is_active": t.is_active,
        "created_by": str(t.created_by) if t.created_by else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


# ── GET /prompt-templates ──────────────────────────────────────────────────
@router.get("/prompt-templates")
async def list_prompt_templates(
    agent_type: str | None = None,
    domain: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        query = select(PromptTemplate).where(
            PromptTemplate.tenant_id == tid,
            PromptTemplate.is_active == True,  # noqa: E712
        )

        # RBAC: domain heads see only their domain
        if isinstance(user_domains, list):
            query = query.where(PromptTemplate.domain.in_(user_domains))

        if agent_type:
            query = query.where(PromptTemplate.agent_type == agent_type)
        if domain:
            query = query.where(PromptTemplate.domain == domain)

        query = query.order_by(PromptTemplate.agent_type, PromptTemplate.name)
        result = await session.execute(query)
        templates = result.scalars().all()

    return [_template_to_dict(t) for t in templates]


# ── GET /prompt-templates/{id} ─────────────────────────────────────────────
@router.get("/prompt-templates/{template_id}")
async def get_prompt_template(
    template_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(PromptTemplate).where(
                PromptTemplate.id == template_id, PromptTemplate.tenant_id == tid
            )
        )
        template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(404, "Prompt template not found")
    # Codex 2026-04-22 audit gap — domain RBAC bypassable by ID. The list
    # endpoint filters by user_domains but this object route did not.
    # Enforce here with 404 so template existence isn't leaked to a
    # caller that shouldn't see it.
    if (
        isinstance(user_domains, list)
        and template.domain
        and template.domain not in user_domains
    ):
        raise HTTPException(404, "Prompt template not found")
    return _template_to_dict(template)


# ── POST /prompt-templates ─────────────────────────────────────────────────
@router.post(
    "/prompt-templates",
    status_code=201,
    dependencies=[require_tenant_admin],
)
async def create_prompt_template(
    body: PromptTemplateCreate,
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
):
    # TC-004: Validate input format
    name_pattern = re.compile(r"^[a-zA-Z][a-zA-Z0-9 _\-]{1,99}$")
    type_pattern = re.compile(r"^[a-z][a-z0-9_]{1,49}$")

    if not name_pattern.match(body.name or ""):
        raise HTTPException(
            422,
            "Invalid name: must start with a letter and contain only letters, numbers, spaces, hyphens, underscores",
        )
    if body.agent_type and not type_pattern.match(body.agent_type):
        raise HTTPException(
            422,
            "Invalid agent_type: must be lowercase snake_case (e.g., ap_processor)",
        )
    if body.description:
        desc_letter_count = sum(1 for c in body.description if c.isalpha())
        if desc_letter_count < 3:
            raise HTTPException(
                422, "Description must contain meaningful text (at least 3 letters)"
            )
    template_letter_count = sum(1 for c in (body.template_text or "") if c.isalpha())
    if template_letter_count < 10:
        raise HTTPException(
            422, "Template text must contain meaningful content (at least 10 letters)"
        )

    # Validate that any connector tool references in the template actually exist
    invalid_refs = _validate_tool_references(body.template_text)
    if invalid_refs:
        raise HTTPException(
            422,
            detail={
                "error": "invalid_tool_references",
                "invalid_tools": invalid_refs,
                "message": (
                    f"Prompt template references tools that do not exist in the "
                    f"connector registry: {', '.join(invalid_refs)}. "
                    f"Register the required connectors or fix the template."
                ),
            },
        )

    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        # TC-005: Check for existing ACTIVE template with same name + agent_type.
        # Soft-deleted templates (is_active=False) should not block reuse.
        existing = await session.execute(
            select(PromptTemplate).where(
                PromptTemplate.tenant_id == tid,
                PromptTemplate.name == body.name,
                PromptTemplate.agent_type == body.agent_type,
                PromptTemplate.is_active.is_(True),
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                409, "A template with this name and agent_type already exists"
            )

        # Codex 2026-04-22 audit gap #9 — created_by was never populated,
        # so the "who authored this template" claim in marketing copy had
        # no backing data.
        template = PromptTemplate(
            tenant_id=tid,
            name=body.name,
            agent_type=body.agent_type,
            domain=body.domain,
            template_text=body.template_text,
            variables=body.variables,
            description=body.description,
            created_by=_user_uuid_from_claims(user),
        )
        session.add(template)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                409, "A template with this name and agent_type already exists"
            ) from exc

    return {"id": str(template.id), "created": True}


# ── PUT /prompt-templates/{id} ─────────────────────────────────────────────
@router.put(
    "/prompt-templates/{template_id}",
    dependencies=[require_tenant_admin],
)
async def update_prompt_template(
    template_id: UUID,
    body: PromptTemplateUpdate,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
    user: dict = Depends(get_current_user),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(PromptTemplate).where(
                PromptTemplate.id == template_id, PromptTemplate.tenant_id == tid
            )
        )
        template = result.scalar_one_or_none()
        if not template:
            raise HTTPException(404, "Prompt template not found")
        # Codex 2026-04-22 audit gap — domain RBAC check on object route.
        if (
            isinstance(user_domains, list)
            and template.domain
            and template.domain not in user_domains
        ):
            raise HTTPException(404, "Prompt template not found")
        if template.is_builtin:
            raise HTTPException(
                409, "Cannot edit built-in templates. Clone it to create a custom version."
            )

        update_data = body.model_dump(exclude_unset=True)

        # Validate tool references in updated template_text
        if "template_text" in update_data and update_data["template_text"]:
            invalid_refs = _validate_tool_references(update_data["template_text"])
            if invalid_refs:
                raise HTTPException(
                    422,
                    detail={
                        "error": "invalid_tool_references",
                        "invalid_tools": invalid_refs,
                        "message": (
                            f"Updated template references tools that do not exist in the "
                            f"connector registry: {', '.join(invalid_refs)}. "
                            f"Register the required connectors or fix the template."
                        ),
                    },
                )

        # Codex 2026-04-22 audit gap #8 — template history was marketed
        # but never recorded. Snapshot the "before" state so rollback
        # has something to restore, and capture who did the edit.
        before_snapshot = {
            "name_before": template.name,
            "template_text_before": template.template_text,
            "variables_before": template.variables,
            "description_before": template.description,
        }

        if "name" in update_data:
            template.name = update_data["name"]
        if "template_text" in update_data:
            template.template_text = update_data["template_text"]
        if "variables" in update_data:
            template.variables = update_data["variables"]
        if "description" in update_data:
            template.description = update_data["description"]

        change_reason = update_data.get("change_reason") if isinstance(update_data.get("change_reason"), str) else None

        history = PromptTemplateEditHistory(
            tenant_id=tid,
            template_id=template.id,
            edited_by=_user_uuid_from_claims(user),
            name_after=template.name,
            template_text_after=template.template_text,
            variables_after=template.variables,
            description_after=template.description,
            change_reason=change_reason,
            **before_snapshot,
        )
        session.add(history)

        # Codex 2026-04-22 audit gap #10 — update did not catch the unique
        # rename constraint. The model has a partial unique index on
        # active (tenant_id, name, agent_type) so renaming into an
        # existing row raises IntegrityError; catch it as a clean 409 so
        # callers don't see a raw DB stack trace.
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                409, "A template with this name and agent_type already exists"
            ) from exc

    return {"id": str(template_id), "updated": True}


# ── GET /prompt-templates/{id}/history ─────────────────────────────────────
@router.get("/prompt-templates/{template_id}/history")
async def get_prompt_template_history(
    template_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
    limit: int = 50,
):
    """Return recent edit history for a template.

    Codex 2026-04-22 audit gap #8 — marketing copy promised this,
    backend never implemented it. Ordered newest-first, capped at
    ``limit`` entries. Guarded by the same domain RBAC as the get
    route so a domain-limited user doesn't leak history for another
    domain's templates.
    """
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        tpl_result = await session.execute(
            select(PromptTemplate).where(
                PromptTemplate.id == template_id, PromptTemplate.tenant_id == tid
            )
        )
        template = tpl_result.scalar_one_or_none()
        if not template:
            raise HTTPException(404, "Prompt template not found")
        if (
            isinstance(user_domains, list)
            and template.domain
            and template.domain not in user_domains
        ):
            raise HTTPException(404, "Prompt template not found")

        rows = await session.execute(
            select(PromptTemplateEditHistory)
            .where(
                PromptTemplateEditHistory.tenant_id == tid,
                PromptTemplateEditHistory.template_id == template_id,
            )
            .order_by(PromptTemplateEditHistory.created_at.desc())
            .limit(max(1, min(limit, 200)))
        )
        history = rows.scalars().all()

    return {
        "template_id": str(template_id),
        "history": [
            {
                "id": str(h.id),
                "edited_by": str(h.edited_by) if h.edited_by else None,
                "name_before": h.name_before,
                "name_after": h.name_after,
                "template_text_before": h.template_text_before,
                "template_text_after": h.template_text_after,
                "variables_before": h.variables_before,
                "variables_after": h.variables_after,
                "description_before": h.description_before,
                "description_after": h.description_after,
                "change_reason": h.change_reason,
                "created_at": h.created_at.isoformat() if h.created_at else None,
            }
            for h in history
        ],
    }


# ── POST /prompt-templates/{id}/rollback ───────────────────────────────────
@router.post(
    "/prompt-templates/{template_id}/rollback",
    dependencies=[require_tenant_admin],
)
async def rollback_prompt_template(
    template_id: UUID,
    history_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
    user: dict = Depends(get_current_user),
):
    """Restore the template to the state captured in ``history_id``.

    Codex 2026-04-22 audit gap #8 — marketing promised rollback, this
    is the backend for it. Writes a new history row recording the
    rollback itself so the "who reverted to what" chain stays
    auditable. Guards:

    - Must be tenant admin (route dependency).
    - Domain RBAC applies — a domain-limited user can't rollback a
      template outside their scope.
    - The history row must belong to the same template + tenant, or
      the request is treated as not-found.
    - Built-in templates cannot be rolled back (matches the edit gate).
    """
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        tpl_result = await session.execute(
            select(PromptTemplate).where(
                PromptTemplate.id == template_id, PromptTemplate.tenant_id == tid
            )
        )
        template = tpl_result.scalar_one_or_none()
        if not template:
            raise HTTPException(404, "Prompt template not found")
        if (
            isinstance(user_domains, list)
            and template.domain
            and template.domain not in user_domains
        ):
            raise HTTPException(404, "Prompt template not found")
        if template.is_builtin:
            raise HTTPException(
                409, "Built-in templates cannot be rolled back"
            )

        hist_result = await session.execute(
            select(PromptTemplateEditHistory).where(
                PromptTemplateEditHistory.id == history_id,
                PromptTemplateEditHistory.tenant_id == tid,
                PromptTemplateEditHistory.template_id == template_id,
            )
        )
        hist = hist_result.scalar_one_or_none()
        if not hist:
            raise HTTPException(404, "History entry not found for this template")

        # Snapshot current state before rolling back so the audit chain
        # stays intact ("you reverted from X to Y on DATE").
        before_snapshot = {
            "name_before": template.name,
            "template_text_before": template.template_text,
            "variables_before": template.variables,
            "description_before": template.description,
        }

        if hist.name_before is not None:
            template.name = hist.name_before
        if hist.template_text_before is not None:
            template.template_text = hist.template_text_before
        if hist.variables_before is not None:
            template.variables = hist.variables_before
        if hist.description_before is not None:
            template.description = hist.description_before

        rollback_row = PromptTemplateEditHistory(
            tenant_id=tid,
            template_id=template.id,
            edited_by=_user_uuid_from_claims(user),
            name_after=template.name,
            template_text_after=template.template_text,
            variables_after=template.variables,
            description_after=template.description,
            change_reason=f"Rollback to history {history_id}",
            **before_snapshot,
        )
        session.add(rollback_row)

        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                409,
                "Rollback would conflict with another active template "
                "of the same name and agent_type.",
            ) from exc

    return {"id": str(template_id), "rolled_back_to": str(history_id)}


# ── DELETE /prompt-templates/{id} ──────────────────────────────────────────
@router.delete(
    "/prompt-templates/{template_id}",
    dependencies=[require_tenant_admin],
)
async def delete_prompt_template(
    template_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(PromptTemplate).where(
                PromptTemplate.id == template_id, PromptTemplate.tenant_id == tid
            )
        )
        template = result.scalar_one_or_none()
        if not template:
            raise HTTPException(404, "Prompt template not found")
        if (
            isinstance(user_domains, list)
            and template.domain
            and template.domain not in user_domains
        ):
            raise HTTPException(404, "Prompt template not found")
        if template.is_builtin:
            raise HTTPException(409, "Cannot delete built-in templates")

        template.is_active = False  # Soft delete

    return {"id": str(template_id), "deleted": True}
