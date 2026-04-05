"""Dynamic workflow re-planning — sends failure context to LLM for alternative steps."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import structlog

from workflows.parser import WorkflowParser

logger = structlog.get_logger()

MAX_REPLAN_ATTEMPTS = 3


class ReplanError(Exception):
    """Raised when re-planning fails (invalid LLM output, validation error, etc.)."""


_REPLAN_PROMPT_TEMPLATE = """\
You are a workflow re-planner for an enterprise automation platform.

A workflow step has failed. Your job is to generate replacement steps for the
remaining portion of the workflow so it can still achieve its goal.

## Original Workflow Definition
{original_definition}

## Steps Completed Successfully
{completed_steps}

## Failed Step
{failed_step}

## Remaining Steps (not yet executed)
{remaining_steps}

## Instructions
- Return ONLY a JSON array of replacement steps for the remaining portion.
- Each step MUST have at minimum: "id" (string), "type" (one of: {valid_types}).
- Agent steps should include "agent" or "agent_type" specifying the agent.
- You may skip steps that are no longer needed, replace the failed step with an
  alternative approach, or add new steps.
- Step IDs must be unique and must NOT reuse IDs from completed steps.
- Do NOT include any explanation — return raw JSON only.
"""


async def replan_workflow(
    original_definition: dict,
    completed_steps: list[dict],
    failed_step: dict,
    remaining_steps: list[dict],
) -> list[dict]:
    """Ask the LLM to re-plan remaining workflow steps after a failure.

    Parameters
    ----------
    original_definition:
        The full original workflow definition dict.
    completed_steps:
        List of dicts with step id, output, and status for each completed step.
    failed_step:
        Dict describing the step that failed (id, error, config).
    remaining_steps:
        List of step definitions that have not yet been executed.

    Returns
    -------
    list[dict]
        Validated list of replacement steps.

    Raises
    ------
    ReplanError
        If the LLM call fails or the returned steps are invalid.
    """
    parser = WorkflowParser()
    valid_types = ", ".join(sorted(parser.VALID_STEP_TYPES))

    prompt = _REPLAN_PROMPT_TEMPLATE.format(
        original_definition=json.dumps(original_definition, indent=2, default=str),
        completed_steps=json.dumps(completed_steps, indent=2, default=str),
        failed_step=json.dumps(failed_step, indent=2, default=str),
        remaining_steps=json.dumps(remaining_steps, indent=2, default=str),
        valid_types=valid_types,
    )

    # ----- call LLM -----
    raw_response = await _call_llm(prompt)

    # ----- parse JSON from response -----
    try:
        new_steps = _parse_json_array(raw_response)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ReplanError(f"LLM returned invalid JSON: {exc}") from exc

    if not isinstance(new_steps, list):
        raise ReplanError(f"Expected JSON array from LLM, got {type(new_steps).__name__}")

    if len(new_steps) == 0:
        raise ReplanError("LLM returned empty step list")

    # ----- validate each step -----
    completed_ids = {s.get("id") for s in completed_steps if s.get("id")}
    seen_ids: set[str] = set()
    for step in new_steps:
        if not isinstance(step, dict):
            raise ReplanError(f"Step must be a dict, got {type(step).__name__}")
        if "id" not in step:
            raise ReplanError("Every replanned step must have an 'id'")
        step_id = step["id"]
        if step_id in completed_ids:
            raise ReplanError(f"Replanned step id '{step_id}' conflicts with an already-completed step")
        if step_id in seen_ids:
            raise ReplanError(f"Duplicate replanned step id: {step_id}")
        seen_ids.add(step_id)

        step_type = step.get("type", "agent")
        if step_type not in parser.VALID_STEP_TYPES:
            raise ReplanError(f"Invalid step type '{step_type}' in replanned step '{step_id}'")

    logger.info(
        "workflow_replanned",
        original_remaining=len(remaining_steps),
        new_steps=len(new_steps),
    )

    return new_steps


async def _call_llm(prompt: str) -> str:
    """Call the configured LLM and return the raw text response.

    Guards all LLM imports with try/except so the module loads even without
    LLM dependencies.
    """
    # Try Google Gemini first (primary LLM for this project)
    try:
        from core.config import external_keys

        api_key = external_keys.google_gemini_api_key
        if api_key:
            import google.generativeai as genai

            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt)
            return response.text
    except Exception as exc:
        logger.debug("gemini_llm_unavailable", error=str(exc))

    # Fallback: OpenAI-compatible
    try:
        from core.config import external_keys as ek

        openai_key = getattr(ek, "openai_api_key", None)
        if openai_key:
            import openai

            client = openai.AsyncOpenAI(api_key=openai_key)
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            return resp.choices[0].message.content or ""
    except Exception as exc:
        logger.debug("openai_llm_unavailable", error=str(exc))

    raise ReplanError("No LLM backend available for re-planning")


def _parse_json_array(raw: str) -> list[dict]:
    """Extract a JSON array from an LLM response that may contain markdown fences."""
    text = raw.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last fence lines
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    result = json.loads(text)
    if not isinstance(result, list):
        raise ValueError(f"Expected list, got {type(result).__name__}")
    return result


def build_replan_event(
    replan_count: int,
    failed_step_id: str,
    error: str,
    new_steps: list[dict],
) -> dict[str, Any]:
    """Build a replan history event record."""
    return {
        "replan_number": replan_count,
        "timestamp": datetime.now(UTC).isoformat(),
        "failed_step_id": failed_step_id,
        "error": error,
        "replacement_steps": [s.get("id", "unknown") for s in new_steps],
        "replacement_count": len(new_steps),
    }
