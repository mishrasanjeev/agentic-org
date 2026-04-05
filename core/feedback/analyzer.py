"""Feedback analyzer — detect patterns and suggest prompt amendments.

When an agent accumulates >= 10 feedback entries, the analyzer calls
the LLM to identify recurring issues and propose a prompt amendment
that can be prepended to the agent's system prompt.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

logger = structlog.get_logger()

_ANALYSIS_PROMPT = (
    "You are analysing user feedback on an AI agent. Below are the most "
    "recent feedback entries (thumbs_down, corrections, hitl_reject).\n\n"
    "{feedback_text}\n\n"
    "Identify the most common pattern or recurring complaint. "
    "Suggest ONE concise amendment rule the agent should follow to avoid "
    "these issues in the future.\n\n"
    "Return ONLY a JSON object:\n"
    '{{"amendment": "...", "reason": "...", "confidence": 0.85}}'
)

MIN_FEEDBACK_FOR_ANALYSIS = 10


async def analyze_feedback(
    agent_id: str,
    tenant_id: str = "",
) -> dict[str, Any]:
    """Analyse recent feedback for an agent and suggest a prompt amendment.

    Requires at least MIN_FEEDBACK_FOR_ANALYSIS entries. Uses the LLM to
    detect patterns in negative feedback and propose an amendment.

    Returns:
        dict with keys: amendment, reason, confidence.
        If not enough data, returns amendment="" with a reason.
    """
    from core.feedback.collector import list_feedback

    entries = await list_feedback(agent_id, tenant_id=tenant_id, limit=50)

    # Filter to negative / actionable feedback only
    negative = [
        e for e in entries
        if e.get("feedback_type") in ("thumbs_down", "correction", "hitl_reject")
    ]

    if len(entries) < MIN_FEEDBACK_FOR_ANALYSIS:
        return {
            "amendment": "",
            "reason": f"Need at least {MIN_FEEDBACK_FOR_ANALYSIS} feedback entries, have {len(entries)}.",
            "confidence": 0.0,
        }

    if not negative:
        return {
            "amendment": "",
            "reason": "No negative feedback found — no amendment needed.",
            "confidence": 1.0,
        }

    # Build feedback text for the LLM
    feedback_lines: list[str] = []
    for e in negative[:20]:
        line = f"- [{e['feedback_type']}] {e.get('text', '(no text)')}"
        if e.get("corrected_output"):
            line += f" | Corrected: {str(e['corrected_output'])[:200]}"
        feedback_lines.append(line)
    feedback_text = "\n".join(feedback_lines)

    # Try LLM analysis
    try:
        import json as _json

        from core.langgraph.llm_factory import create_chat_model

        llm = create_chat_model(model="")
        prompt = _ANALYSIS_PROMPT.format(feedback_text=feedback_text)
        response = await llm.ainvoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)

        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            parsed = _json.loads(json_match.group())
            amendment = parsed.get("amendment", "")
            reason = parsed.get("reason", "")
            confidence = float(parsed.get("confidence", 0.85))

            if amendment:
                logger.info(
                    "feedback_analysis_complete",
                    agent_id=agent_id,
                    amendment=amendment[:100],
                    confidence=confidence,
                )
                return {
                    "amendment": amendment,
                    "reason": reason,
                    "confidence": round(confidence, 2),
                }
    except Exception as exc:
        logger.warning("feedback_analysis_llm_failed", error=str(exc))

    # Fallback: simple heuristic analysis
    return _fallback_analysis(negative)


def _fallback_analysis(negative_entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Simple heuristic analysis when LLM is unavailable."""
    # Count feedback types
    type_counts: dict[str, int] = {}
    texts: list[str] = []
    for e in negative_entries:
        ft = e.get("feedback_type", "unknown")
        type_counts[ft] = type_counts.get(ft, 0) + 1
        if e.get("text"):
            texts.append(e["text"])

    total = len(negative_entries)
    most_common_type = max(type_counts, key=type_counts.get)  # type: ignore[arg-type]
    count = type_counts[most_common_type]

    # Build a simple amendment from the most common feedback text
    amendment = ""
    if texts:
        # Use the most recent feedback text as a hint
        amendment = f"Based on user feedback: {texts[0][:200]}"

    reason = f"{count}/{total} negative feedback entries were '{most_common_type}'."
    confidence = round(min(count / total, 0.95), 2) if total > 0 else 0.0

    return {
        "amendment": amendment,
        "reason": reason,
        "confidence": confidence,
    }


def format_amendments_for_prompt(amendments: list[str]) -> str:
    """Format a list of amendment strings for prepending to a system prompt.

    Returns:
        Formatted block like:
        IMPORTANT LEARNED RULES:
        - amendment1
        - amendment2

    """
    if not amendments:
        return ""
    lines = "\n".join(f"- {a}" for a in amendments)
    return f"IMPORTANT LEARNED RULES:\n{lines}\n\n"
