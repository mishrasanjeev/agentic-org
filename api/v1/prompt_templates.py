"""Prompt template CRUD endpoints — admin + domain head access."""

from __future__ import annotations

import re
import uuid as _uuid
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.deps import get_current_tenant, get_user_domains
from core.database import get_tenant_session
from core.models.prompt_template import PromptTemplate
from core.schemas.api import PromptTemplateCreate, PromptTemplateUpdate

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
        if user_domains is not None:
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
    return _template_to_dict(template)


# ── POST /prompt-templates ─────────────────────────────────────────────────
@router.post("/prompt-templates", status_code=201)
async def create_prompt_template(
    body: PromptTemplateCreate,
    tenant_id: str = Depends(get_current_tenant),
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
        # TC-005: Check for existing template with same (tenant_id, name, agent_type)
        existing = await session.execute(
            select(PromptTemplate).where(
                PromptTemplate.tenant_id == tid,
                PromptTemplate.name == body.name,
                PromptTemplate.agent_type == body.agent_type,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                409, "A template with this name and agent_type already exists"
            )

        template = PromptTemplate(
            tenant_id=tid,
            name=body.name,
            agent_type=body.agent_type,
            domain=body.domain,
            template_text=body.template_text,
            variables=body.variables,
            description=body.description,
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
@router.put("/prompt-templates/{template_id}")
async def update_prompt_template(
    template_id: UUID,
    body: PromptTemplateUpdate,
    tenant_id: str = Depends(get_current_tenant),
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

        if "name" in update_data:
            template.name = update_data["name"]
        if "template_text" in update_data:
            template.template_text = update_data["template_text"]
        if "variables" in update_data:
            template.variables = update_data["variables"]
        if "description" in update_data:
            template.description = update_data["description"]

    return {"id": str(template_id), "updated": True}


# ── DELETE /prompt-templates/{id} ──────────────────────────────────────────
@router.delete("/prompt-templates/{template_id}")
async def delete_prompt_template(
    template_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
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
        if template.is_builtin:
            raise HTTPException(409, "Cannot delete built-in templates")

        template.is_active = False  # Soft delete

    return {"id": str(template_id), "deleted": True}
