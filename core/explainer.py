"""Explainable AI panel — generate plain-English explanations of agent runs.

Summarises an agent's reasoning trace, output, and tool usage into
executive-friendly bullet points with a confidence score and
readability grade.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Flesch-Kincaid helpers (no external library needed)
# ---------------------------------------------------------------------------


def _count_syllables(word: str) -> int:
    """Estimate syllable count for an English word."""
    word = word.lower().strip()
    if not word:
        return 0
    # Remove trailing "e" (silent e)
    if word.endswith("e") and len(word) > 2:
        word = word[:-1]
    # Count vowel groups
    count = len(re.findall(r"[aeiouy]+", word))
    return max(count, 1)


def compute_flesch_kincaid_grade(text: str) -> float:
    """Compute Flesch-Kincaid Grade Level for *text*.

    FK Grade = 0.39 * (words/sentences) + 11.8 * (syllables/words) - 15.59

    Returns a float clamped to [0, 20].
    """
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    words = re.findall(r"[a-zA-Z]+", text)
    if not words or not sentences:
        return 0.0

    total_syllables = sum(_count_syllables(w) for w in words)
    grade = (
        0.39 * (len(words) / len(sentences))
        + 11.8 * (total_syllables / len(words))
        - 15.59
    )
    return round(max(0.0, min(grade, 20.0)), 1)


# ---------------------------------------------------------------------------
# Fallback: extract key phrases when LLM is unavailable
# ---------------------------------------------------------------------------


def _fallback_bullets(reasoning_trace: list[str], output: dict[str, Any], tools_used: list[str]) -> list[str]:
    """Extract key phrases from reasoning trace as bullet points (no LLM)."""
    bullets: list[str] = []

    # Pick up to 3 informative trace entries
    informative = [
        t for t in reasoning_trace
        if len(t) > 20 and not t.startswith("DEBUG")
    ]
    for entry in informative[:3]:
        # Truncate long entries
        short = entry[:200].strip()
        if not short.endswith("."):
            short += "."
        bullets.append(short)

    # Mention tools if present
    if tools_used:
        bullets.append(f"Tools used: {', '.join(tools_used[:5])}.")

    # Mention output status if available
    status = output.get("status") or output.get("result")
    if status:
        bullets.append(f"Final status: {status}.")

    return bullets[:5] or ["Agent completed the task."]


# ---------------------------------------------------------------------------
# LLM summarisation prompt
# ---------------------------------------------------------------------------

_SUMMARISE_PROMPT = (
    "Summarize this agent's decision in 3-5 bullet points for a "
    "non-technical executive. Each bullet should be one plain-English "
    "sentence. Do NOT use jargon.\n\n"
    "Reasoning trace:\n{trace}\n\n"
    "Tools called: {tools}\n\n"
    "Output summary: {output}\n\n"
    "Return ONLY a JSON object: {{\"bullets\": [\"...\", ...]}}"
)


async def _call_llm_for_summary(
    reasoning_trace: list[str],
    output: dict[str, Any],
    tools_used: list[str],
) -> list[str] | None:
    """Try to call the configured LLM for a concise summary.

    Returns a list of bullet strings, or None if LLM is unavailable.
    """
    try:
        import json as _json

        from core.langgraph.llm_factory import create_chat_model

        llm = create_chat_model(model="")  # use default model
        trace_text = "\n".join(reasoning_trace[:20])
        tools_text = ", ".join(tools_used) if tools_used else "(none)"
        output_text = _json.dumps(output, default=str)[:500]

        prompt = _SUMMARISE_PROMPT.format(
            trace=trace_text, tools=tools_text, output=output_text,
        )

        response = await llm.ainvoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)

        # Parse JSON from response
        # Try to extract JSON from fenced block or raw content
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            parsed = _json.loads(json_match.group())
            bullets = parsed.get("bullets", [])
            if isinstance(bullets, list) and bullets:
                return [str(b) for b in bullets[:5]]
    except Exception as exc:
        logger.warning("explainer_llm_failed", error=str(exc))

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_explanation(
    reasoning_trace: list[str],
    output: dict[str, Any],
    tools_used: list[str],
) -> dict[str, Any]:
    """Generate a plain-English explanation of an agent run.

    Args:
        reasoning_trace: Raw list of strings from the agent run.
        output: The agent's output dict.
        tools_used: List of tool names that were called.

    Returns:
        dict with keys: bullets, confidence, tools_cited, readability_grade
    """
    # Attempt LLM summarisation; fall back to heuristic extraction
    bullets = await _call_llm_for_summary(reasoning_trace, output, tools_used)
    if bullets is None:
        bullets = _fallback_bullets(reasoning_trace, output, tools_used)

    # Compute confidence from the output if present
    confidence = 0.0
    if isinstance(output, dict):
        confidence = float(output.get("confidence", 0.0))
    # If no confidence in output, derive from trace length (heuristic)
    if not confidence and reasoning_trace:
        confidence = round(min(0.5 + len(reasoning_trace) * 0.05, 0.99), 2)

    # Readability of the generated bullets
    full_text = " ".join(bullets)
    readability_grade = compute_flesch_kincaid_grade(full_text)

    # Tools actually cited in the bullets
    tools_cited = [t for t in tools_used if any(t in b for b in bullets)]

    return {
        "bullets": bullets,
        "confidence": confidence,
        "tools_cited": tools_cited,
        "readability_grade": readability_grade,
    }
