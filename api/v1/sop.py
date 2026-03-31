"""SOP upload and agent generation endpoints."""

from __future__ import annotations

import os
import tempfile

import structlog
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from api.deps import get_current_tenant

router = APIRouter()
_log = structlog.get_logger()

ALLOWED_EXTENSIONS = {".pdf", ".md", ".markdown", ".txt", ".docx"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


class SOPParseRequest(BaseModel):
    text: str
    domain_hint: str = ""
    llm_model: str = ""


# ── POST /sop/upload — Upload a document and parse it ──────────────────────
@router.post("/sop/upload")
async def upload_and_parse_sop(
    file: UploadFile,
    domain_hint: str = "",
    llm_model: str = "",
    tenant_id: str = Depends(get_current_tenant),
):
    """Upload a PDF/markdown/text SOP document and parse it into agent config.

    Returns a draft agent configuration for human review.
    """
    # Validate file
    if not file.filename:
        raise HTTPException(400, "No file uploaded")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            400, f"Unsupported file type '{ext}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        )

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, f"File too large (max {MAX_FILE_SIZE // 1024 // 1024}MB)")

    # Save to temp file for parsing
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        from core.langgraph.sop_parser import extract_text_from_document, parse_sop_document

        document_text = extract_text_from_document(tmp_path, file.content_type or "")
        if not document_text.strip():
            raise HTTPException(400, "Could not extract text from document")

        parsed = await parse_sop_document(
            document_text=document_text,
            llm_model=llm_model,
            domain_hint=domain_hint,
        )

        _log.info(
            "sop_parsed",
            tenant_id=tenant_id,
            filename=file.filename,
            agent_type=parsed.get("agent_type"),
            steps_count=len(parsed.get("steps", [])),
            tools_count=len(parsed.get("required_tools", [])),
        )

        return {
            "status": "draft",
            "filename": file.filename,
            "document_length": len(document_text),
            "config": parsed,
        }
    finally:
        os.unlink(tmp_path)


# ── POST /sop/parse-text — Parse plain text SOP ────────────────────────────
@router.post("/sop/parse-text")
async def parse_text_sop(
    body: SOPParseRequest,
    tenant_id: str = Depends(get_current_tenant),
):
    """Parse a plain-text SOP (pasted or typed) into agent config.

    Returns a draft agent configuration for human review.
    """
    if not body.text.strip():
        raise HTTPException(400, "SOP text is required")

    if len(body.text) > 50000:
        raise HTTPException(400, "Text too long (max 50,000 characters)")

    from core.langgraph.sop_parser import parse_sop_document

    parsed = await parse_sop_document(
        document_text=body.text,
        llm_model=body.llm_model,
        domain_hint=body.domain_hint,
    )

    return {
        "status": "draft",
        "document_length": len(body.text),
        "config": parsed,
    }


# ── POST /sop/deploy — Deploy a reviewed SOP config as an agent ────────────
@router.post("/sop/deploy", status_code=201)
async def deploy_sop_agent(
    body: dict,
    tenant_id: str = Depends(get_current_tenant),
):
    """Deploy a reviewed/edited SOP config as a new agent.

    Expects the config dict from parse response (possibly edited by human).
    Creates the agent via the standard agent creation flow.
    """
    config = body.get("config", body)

    agent_name = config.get("agent_name", "SOP Agent")
    agent_type = config.get("agent_type", "custom_agent")
    domain = config.get("domain", "ops")
    tools = config.get("required_tools", [])
    confidence = config.get("confidence_floor", 0.88)
    hitl_conditions = config.get("hitl_conditions", [])
    prompt = config.get("suggested_prompt", "")
    escalation = config.get("escalation_chain", [])

    # Build HITL condition expression
    hitl_expr = " OR ".join(hitl_conditions) if hitl_conditions else f"confidence < {confidence}"

    # Create agent via existing API logic
    from api.v1.agents import create_agent
    from core.schemas.api import AgentCreate, HITLPolicyConfig, LLMConfig

    agent_body = AgentCreate(
        name=agent_name,
        agent_type=agent_type,
        domain=domain,
        authorized_tools=tools,
        system_prompt_text=prompt,
        confidence_floor=confidence,
        hitl_policy=HITLPolicyConfig(
            condition=hitl_expr,
            assignee_role=escalation[0] if escalation else "admin",
        ),
        llm=LLMConfig(model=config.get("llm_model", "gemini-2.5-flash")),
        employee_name=agent_name,
        initial_status="shadow",
    )

    result = await create_agent(body=agent_body, tenant_id=tenant_id)

    return {
        "status": "deployed",
        "agent_id": result["agent_id"],
        "agent_name": agent_name,
        "agent_type": agent_type,
        "domain": domain,
        "tools_count": len(tools),
        "initial_status": "shadow",
        "grantex_registered": result.get("grantex_registered", False),
    }
