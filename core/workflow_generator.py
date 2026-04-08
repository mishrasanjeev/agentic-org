"""NL-to-Workflow generator — converts plain English descriptions to WorkflowDefinition JSON.

Takes a natural language description like "Automate invoice approval when amount > 5L"
and produces a validated WorkflowDefinition that the workflow engine can execute.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from workflows.parser import WorkflowParser

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Available agent types (from the 35 built-in agents)
# ---------------------------------------------------------------------------
KNOWN_AGENT_TYPES: list[str] = [
    "ap_processor",
    "ar_collector",
    "bank_reconciler",
    "budget_analyst",
    "expense_auditor",
    "financial_reporter",
    "gst_filer",
    "invoice_processor",
    "payroll_processor",
    "tax_analyst",
    "treasury_manager",
    "leave_manager",
    "onboarding_specialist",
    "recruiter",
    "timesheet_processor",
    "campaign_manager",
    "content_creator",
    "lead_scorer",
    "seo_optimizer",
    "social_media_manager",
    "compliance_monitor",
    "data_migrator",
    "fleet_ops_manager",
    "incident_responder",
    "it_helpdesk",
    "procurement_manager",
    "quality_inspector",
    "scheduler",
    "vendor_manager",
    "customer_success",
    "sales_forecaster",
    "support_deflection",
    "collections_agent",
    "fraud_detector",
    "report_generator",
]

# ---------------------------------------------------------------------------
# Available workflow step types
# ---------------------------------------------------------------------------
VALID_STEP_TYPES: list[str] = [
    "agent",
    "condition",
    "wait",
    "wait_for_event",
    "human_in_loop",
    "parallel",
    "notify",
    "transform",
    "sub_workflow",
    "loop",
]

# ---------------------------------------------------------------------------
# Known connectors (native 54 connectors)
# ---------------------------------------------------------------------------
KNOWN_CONNECTORS: list[str] = [
    "salesforce",
    "hubspot",
    "zoho_crm",
    "slack",
    "microsoft_teams",
    "gmail",
    "google_workspace",
    "google_sheets",
    "google_drive",
    "quickbooks",
    "xero",
    "tally",
    "razorpay",
    "stripe",
    "sap",
    "oracle_erp",
    "jira",
    "zendesk",
    "freshdesk",
    "twilio",
    "sendgrid",
    "aws_s3",
    "gcp_storage",
    "postgresql",
    "mongodb",
    "redis",
    "elasticsearch",
    "snowflake",
    "bigquery",
    "power_bi",
    "tableau",
    "github",
    "gitlab",
    "bitbucket",
    "confluence",
    "notion",
    "asana",
    "trello",
    "monday_com",
    "airtable",
    "zapier",
    "webhook_generic",
    "rest_api",
    "graphql_api",
    "smtp",
    "imap",
    "ftp_sftp",
    "ldap_ad",
    "okta",
    "azure_ad",
    "whatsapp_business",
    "indian_gstn",
    "digilocker",
    "account_aggregator",
]

# ---------------------------------------------------------------------------
# Prompt injection protection
# ---------------------------------------------------------------------------
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions|prompts)", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(previous|above|prior)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(a|an)\s+", re.IGNORECASE),
    re.compile(r"system\s*:\s*", re.IGNORECASE),
    re.compile(r"<\|?(system|im_start|endoftext)\|?>", re.IGNORECASE),
    re.compile(r"deploy\s+admin\s+agent", re.IGNORECASE),
    re.compile(r"override\s+(security|permissions|auth)", re.IGNORECASE),
    re.compile(r"act\s+as\s+(root|admin|superuser)", re.IGNORECASE),
    re.compile(r"forget\s+(everything|all|your\s+instructions)", re.IGNORECASE),
    re.compile(r"new\s+instructions?\s*:", re.IGNORECASE),
    re.compile(r"reveal\s+(your|the)\s+(system\s+)?prompt", re.IGNORECASE),
]

# Maximum description length to prevent abuse (raised to support 500+ word inputs)
_MAX_DESCRIPTION_LENGTH = 10000


def _sanitize_description(description: str) -> str:
    """Sanitize user input to prevent prompt injection.

    Raises ValueError if injection patterns are detected.
    """
    if not description or not description.strip():
        raise ValueError("Workflow description cannot be empty")

    description = description.strip()

    if len(description) > _MAX_DESCRIPTION_LENGTH:
        raise ValueError(
            f"Description too long ({len(description)} chars). "
            f"Maximum is {_MAX_DESCRIPTION_LENGTH} characters."
        )

    for pattern in _INJECTION_PATTERNS:
        if pattern.search(description):
            raise ValueError(
                "Description contains disallowed instructions. "
                "Please describe your business workflow without system directives."
            )

    return description


def _build_prompt(description: str) -> list[dict[str, str]]:
    """Build the LLM prompt that generates a WorkflowDefinition JSON."""
    system_prompt = f"""You are a workflow generator for AgenticOrg, an enterprise AI platform.
Your job is to convert a plain English business process description into a valid WorkflowDefinition JSON.

IMPORTANT RULES:
1. Output ONLY valid JSON. No markdown, no explanation, no code fences.
2. Every step must have a unique "id" field (use descriptive slugs like "check_amount", "approve_invoice").
3. Every step must have a "type" field from: {json.dumps(VALID_STEP_TYPES)}
4. For "agent" steps, the "agent_type" must be from: {json.dumps(KNOWN_AGENT_TYPES)}
5. For "condition" steps, include "condition" (a simple expression), "true_path" and "false_path" (step ids).
6. For "wait" steps, include "duration_minutes".
7. For "wait_for_event" steps, include "event_type".
8. For "human_in_loop" steps (approval), include "assignee_role" and "timeout_hours".
9. For "parallel" steps, include "parallel_steps" (list of step id lists to run concurrently).
10. Use "depends_on" to express step ordering (list of step ids that must complete first).
11. Choose a trigger_type from: manual, schedule, webhook, api_event, email_received.
12. Connectors available: {json.dumps(KNOWN_CONNECTORS[:20])} and more.

OUTPUT FORMAT (strict JSON):
{{
  "name": "descriptive workflow name",
  "description": "what this workflow does",
  "domain": "finance|hr|marketing|ops|backoffice",
  "trigger_type": "manual|schedule|webhook|api_event|email_received",
  "trigger_config": {{}},
  "timeout_hours": 24,
  "steps": [
    {{
      "id": "step_slug",
      "type": "agent|condition|wait|wait_for_event|human_in_loop|parallel|notify|transform",
      "title": "Human readable title",
      "agent_type": "one_of_the_known_types",
      "depends_on": [],
      ...type-specific fields...
    }}
  ]
}}

Generate the most appropriate workflow for the user's description.
Include conditions, approvals, and parallel steps where the business logic demands them."""

    user_prompt = f"Generate a workflow for: {description}"

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _validate_generated_workflow(definition: dict[str, Any]) -> dict[str, Any]:
    """Validate the LLM-generated workflow definition against our schema.

    Checks:
    1. Has required top-level fields
    2. Steps have valid types
    3. Agent types reference real agents
    4. Dependencies reference existing step ids
    5. Condition steps have required fields
    6. Passes WorkflowParser validation (circular deps, etc.)
    """
    errors: list[str] = []

    # Top-level checks
    if "steps" not in definition:
        raise ValueError("Generated workflow is missing 'steps' array")

    steps = definition["steps"]
    if not isinstance(steps, list) or len(steps) == 0:
        raise ValueError("Generated workflow must have at least one step")

    step_ids = {s.get("id") for s in steps if s.get("id")}

    for i, step in enumerate(steps):
        step_id = step.get("id")
        if not step_id:
            errors.append(f"Step {i} is missing 'id' field")
            continue

        step_type = step.get("type", "agent")
        if step_type not in VALID_STEP_TYPES:
            errors.append(f"Step '{step_id}' has invalid type '{step_type}'")

        # Agent steps must reference known agent types
        if step_type == "agent":
            agent_type = step.get("agent_type", "")
            if agent_type and agent_type not in KNOWN_AGENT_TYPES:
                errors.append(
                    f"Step '{step_id}' references unknown agent_type '{agent_type}'. "
                    f"Available: {', '.join(KNOWN_AGENT_TYPES[:10])}..."
                )

        # Condition steps must have condition expression
        if step_type == "condition" and not step.get("condition"):
            errors.append(f"Condition step '{step_id}' is missing 'condition' field")

        # Dependencies must reference existing steps
        for dep in step.get("depends_on", []):
            if dep not in step_ids:
                errors.append(
                    f"Step '{step_id}' depends on '{dep}' which does not exist"
                )

    if errors:
        raise ValueError(
            "Generated workflow has validation errors:\n- " + "\n- ".join(errors)
        )

    # Run through the WorkflowParser for structural validation (circular deps, etc.)
    parser = WorkflowParser()
    parser.parse(definition)

    # Ensure top-level defaults
    definition.setdefault("name", "Generated Workflow")
    definition.setdefault("description", "")
    definition.setdefault("domain", "ops")
    definition.setdefault("trigger_type", "manual")
    definition.setdefault("trigger_config", {})
    definition.setdefault("version", "1.0")

    return definition


def _extract_json_from_response(text: str) -> dict[str, Any]:
    """Extract JSON from LLM response, handling markdown code fences and extra text."""
    text = text.strip()

    # Try to extract from markdown code fences
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # Try to find the first { ... } block
    brace_start = text.find("{")
    if brace_start == -1:
        raise ValueError("No JSON object found in LLM response")

    # Find matching closing brace
    depth = 0
    for i, ch in enumerate(text[brace_start:], start=brace_start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                text = text[brace_start : i + 1]
                break
    else:
        text = text[brace_start:]

    return json.loads(text)


async def generate_workflow(
    description: str,
    tenant_id: str,
    *,
    _llm_override: Any | None = None,
) -> dict[str, Any]:
    """Generate a WorkflowDefinition JSON from a plain English description.

    Args:
        description: Natural language description of the workflow.
        tenant_id: Tenant ID for context.
        _llm_override: Optional callable for testing (replaces LLM call).

    Returns:
        Validated WorkflowDefinition dict ready for creation.

    Raises:
        ValueError: If description is invalid, injection detected, or LLM
                     output cannot be parsed after retries.
    """
    # Step 1: Sanitize input
    sanitized = _sanitize_description(description)

    # Step 2: Build prompt
    messages = _build_prompt(sanitized)

    # Step 3: Call LLM with retries
    max_retries = 2
    last_error: str = ""

    for attempt in range(max_retries + 1):
        try:
            if _llm_override is not None:
                # Test/mock path
                if callable(_llm_override):
                    raw_response = await _llm_override(messages)
                else:
                    raw_response = str(_llm_override)
            else:
                # Production path — use the existing LLM router
                from core.llm.router import llm_router

                llm_response = await llm_router.complete(messages)
                raw_response = llm_response.content

            logger.info(
                "workflow_generator_llm_response",
                attempt=attempt + 1,
                response_length=len(raw_response),
                tenant_id=tenant_id,
            )

            # Step 4: Parse JSON from response
            definition = _extract_json_from_response(raw_response)

            # Step 5: Validate
            validated = _validate_generated_workflow(definition)

            logger.info(
                "workflow_generated",
                name=validated.get("name"),
                steps=len(validated.get("steps", [])),
                tenant_id=tenant_id,
            )

            return validated

        except (json.JSONDecodeError, ValueError) as exc:
            last_error = str(exc)
            logger.warning(
                "workflow_generation_attempt_failed",
                attempt=attempt + 1,
                error=last_error,
                tenant_id=tenant_id,
            )
            if attempt < max_retries:
                # Add a hint to the messages for the retry
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Your previous output was invalid: {last_error}. "
                            "Please output ONLY valid JSON matching the schema."
                        ),
                    }
                )
                continue

    # All retries exhausted
    raise ValueError(
        f"Could not generate a valid workflow after {max_retries + 1} attempts. "
        f"Last error: {last_error}. "
        "Please try the template wizard instead for a guided workflow creation experience."
    )
