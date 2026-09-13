"""Feedback analyzer — detect patterns and suggest prompt amendments.

When an agent accumulates >= 10 feedback entries, the analyzer calls
the LLM to identify recurring issues and propose a prompt amendment
that can be prepended to the agent's system prompt.

Safety properties of anything that can become a learned rule:

* Only feedback that has not already been consumed (``applied_at IS NULL``)
  is analysed, so the same complaint is not re-learned on every run.
* Raw feedback text is never copied into an amendment. The heuristic
  fallback only summarises; it cannot produce an auto-applicable rule.
* LLM-produced amendments are length-capped, single-line, and only
  auto-applied in shadow mode above a confidence threshold.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

logger = structlog.get_logger()

MAX_AMENDMENT_CHARS = 500
AUTO_APPLY_MIN_CONFIDENCE = 0.7
NEGATIVE_FEEDBACK_TYPES = ("thumbs_down", "correction", "hitl_reject", "hitl_override")

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

    entries = await list_feedback(agent_id, tenant_id=tenant_id, limit=50, unapplied_only=True)

    # Filter to negative / actionable feedback only
    negative = [e for e in entries if e.get("feedback_type") in NEGATIVE_FEEDBACK_TYPES]

    if len(entries) < MIN_FEEDBACK_FOR_ANALYSIS:
        return {
            "amendment": "",
            "reason": f"Need at least {MIN_FEEDBACK_FOR_ANALYSIS} unapplied feedback entries, have {len(entries)}.",
            "confidence": 0.0,
            "source": "none",
        }

    if not negative:
        return {
            "amendment": "",
            "reason": "No negative feedback found — no amendment needed.",
            "confidence": 1.0,
            "source": "none",
        }

    # Build feedback text for the LLM
    feedback_lines: list[str] = []
    for e in negative[:20]:
        line = f"- [{e['feedback_type']}] {_clean_text(e.get('text') or '(no text)', 300)}"
        diff = correction_diff(e.get("original_output"), e.get("corrected_output"))
        if diff:
            line += " | Corrected fields: " + "; ".join(diff)
        elif e.get("corrected_output"):
            line += f" | Corrected: {_clean_text(str(e['corrected_output']), 200)}"
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
            amendment = _clean_text(str(parsed.get("amendment") or ""), MAX_AMENDMENT_CHARS)
            reason = _clean_text(str(parsed.get("reason") or ""), 500)
            confidence = _clamp_confidence(parsed.get("confidence"))

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
                    "confidence": confidence,
                    "source": "llm",
                }
    # enterprise-gate: broad-except-ok reason=feedback-llm-analysis-failure-degrades-to-explicit-heuristic
    except Exception as exc:
        logger.warning("feedback_analysis_llm_failed", error=str(exc))

    # Fallback: simple heuristic analysis
    return _fallback_analysis(negative)


def _clean_text(value: str, limit: int) -> str:
    """Collapse whitespace/newlines and cap length (prompt-safe, single line)."""
    return " ".join(str(value).split())[:limit]


def _clamp_confidence(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:  # NaN
        return default
    return round(max(0.0, min(1.0, parsed)), 2)


def correction_diff(original: Any, corrected: Any, limit: int = 6) -> list[str]:
    """Field-level diff of a correction: ``key: original -> corrected``.

    Corrections carry the strongest learning signal (a human said exactly
    what the right answer was). Surfacing the changed fields lets the
    analyser learn *what* was wrong instead of only that something was.
    """
    if not isinstance(original, dict) or not isinstance(corrected, dict):
        return []
    lines: list[str] = []
    for key in corrected:
        if key in original and original[key] == corrected[key]:
            continue
        before = _clean_text(repr(original.get(key, "<missing>")), 60)
        after = _clean_text(repr(corrected[key]), 60)
        lines.append(f"{key}: {before} -> {after}")
        if len(lines) >= limit:
            break
    return lines


def _fallback_analysis(negative_entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Heuristic summary when the LLM is unavailable.

    Deliberately produces NO amendment: copying a user's raw feedback text
    into the agent's system prompt would let anyone who can submit feedback
    inject instructions into the agent. The summary is informational only.
    """
    type_counts: dict[str, int] = {}
    corrected_fields: dict[str, int] = {}
    for e in negative_entries:
        ft = e.get("feedback_type", "unknown")
        type_counts[ft] = type_counts.get(ft, 0) + 1
        for line in correction_diff(e.get("original_output"), e.get("corrected_output")):
            field = line.split(":", 1)[0]
            corrected_fields[field] = corrected_fields.get(field, 0) + 1

    total = len(negative_entries)
    most_common_type = max(type_counts, key=type_counts.get)  # type: ignore[arg-type]
    count = type_counts[most_common_type]

    reason = f"{count}/{total} negative feedback entries were '{most_common_type}'."
    if corrected_fields:
        top = sorted(corrected_fields.items(), key=lambda kv: -kv[1])[:3]
        reason += " Most-corrected fields: " + ", ".join(f"{k} ({n})" for k, n in top) + "."
    reason += " LLM analysis unavailable; no rule proposed."

    return {
        "amendment": "",
        "reason": reason,
        "confidence": round(min(count / total, 0.95), 2) if total > 0 else 0.0,
        "source": "heuristic",
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


async def analyze_and_apply_feedback(
    agent_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    """Analyze accumulated feedback and apply a learned rule in shadow mode.

    Active agents never self-modify. Shadow agents may receive a deduplicated,
    bounded learned-rule set, so the next sample evaluates the improvement.
    """
    analysis = await analyze_feedback(agent_id, tenant_id)
    amendment = str(analysis.get("amendment") or "").strip()
    if not amendment:
        return {**analysis, "applied": False}
    if analysis.get("source") != "llm":
        return {**analysis, "applied": False, "reason": "Only LLM-analysed rules may be auto-applied."}
    if float(analysis.get("confidence") or 0.0) < AUTO_APPLY_MIN_CONFIDENCE:
        return {
            **analysis,
            "applied": False,
            "reason": f"Confidence below auto-apply threshold {AUTO_APPLY_MIN_CONFIDENCE}.",
        }

    import uuid
    from datetime import UTC, datetime

    from sqlalchemy import select
    from sqlalchemy import text as sql_text

    from core.database import get_tenant_session
    from core.models.agent import Agent

    tid = uuid.UUID(tenant_id)
    aid = uuid.UUID(agent_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Agent)
            .where(Agent.id == aid, Agent.tenant_id == tid)
            .with_for_update()
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            return {**analysis, "applied": False, "reason": "Agent not found."}
        if agent.status != "shadow":
            return {
                **analysis,
                "applied": False,
                "reason": "Learned rules auto-apply only while the agent is in shadow mode.",
            }

        amendments = [str(value) for value in (agent.prompt_amendments or [])]
        if amendment not in amendments:
            agent.prompt_amendments = [*amendments[-9:], amendment]
        await session.execute(
            sql_text(
                "UPDATE agent_feedback SET applied_at = :applied_at "
                "WHERE tenant_id = :tenant_id AND agent_id = :agent_id "
                "AND applied_at IS NULL"
            ),
            {
                "applied_at": datetime.now(UTC),
                "tenant_id": tenant_id,
                "agent_id": agent_id,
            },
        )

    logger.info(
        "feedback_amendment_applied",
        agent_id=agent_id,
        tenant_id=tenant_id,
        confidence=analysis.get("confidence"),
    )
    return {**analysis, "applied": True}
