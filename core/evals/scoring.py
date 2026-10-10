# SPDX-License-Identifier: Apache-2.0
"""Model-graded scorers: a judge model rates an answer on a fixed rubric.

Four judges, each a question with a five-point scale that the judge answers as
JSON. Each needs something the case or the run supplies, and a case without it
is not scored by that judge:

* ``faithfulness``: whether every claim in the answer is supported by the
  case's ``context`` (needs ``context``);
* ``relevance``: whether the answer addresses the case's ``input``;
* ``instruction_adherence``: whether the answer follows the prompt under test;
* ``context_recall``: whether the answer covers what the case's ``reference``
  answer says (needs ``reference``).

A score is the judge's 1 to 5 mapped to 0 to 1. A judge call that fails, or a
reply the judge does not give as a rating, is an error for that judge and
case, kept apart from the scores. Judge calls go through the same router path
as the answers, as the tenant, with the tenant's pseudonymisation; the judge's
reason is bounded and returned with the run, never logged or stored.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

import structlog

from core.prompts import compare as prompt_compare
from core.spend import context as spend_context

logger = structlog.get_logger()

JUDGES: tuple[str, ...] = ("faithfulness", "relevance", "instruction_adherence", "context_recall")
MAX_JUDGES = 4
JUDGE_MAX_TOKENS = 200
MAX_REASON_CHARS = 300
MAX_JUDGED_CHARS = 6000

_SCALE = (
    "Reply with one JSON object and nothing else: "
    '{"score": <1 to 5>, "reason": "<one sentence>"}. '
    "5 means fully, 1 means not at all."
)

_RUBRICS: dict[str, tuple[str, str]] = {
    "faithfulness": (
        "You check whether an answer is faithful to the material it was given.",
        "Rate how fully every claim and figure in the ANSWER is supported by the CONTEXT. "
        "A claim the context does not support lowers the score even if it is true.",
    ),
    "relevance": (
        "You check whether an answer addresses the question that was asked.",
        "Rate how fully the ANSWER addresses the QUESTION, without going off topic.",
    ),
    "instruction_adherence": (
        "You check whether an answer follows the instructions its author was given.",
        "Rate how fully the ANSWER follows the INSTRUCTIONS it was produced under: format, scope, tone and limits.",
    ),
    "context_recall": (
        "You check whether an answer covers what a reference answer says.",
        "Rate how fully the ANSWER covers the facts in the REFERENCE answer. Extra material does not lower the "
        "score; a missing fact does.",
    ),
}


class JudgeError(ValueError):
    """The judges asked for cannot be used."""


def validate_judges(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > MAX_JUDGES:
        raise JudgeError(f"judges is a list of at most {MAX_JUDGES} names")
    chosen: list[str] = []
    for item in raw:
        if not isinstance(item, str) or item not in JUDGES:
            raise JudgeError(f"unknown judge; the judges are {', '.join(JUDGES)}")
        if item not in chosen:
            chosen.append(item)
    return tuple(chosen)


def applicable(kind: str, case: prompt_compare.Case) -> bool:
    if kind == "faithfulness":
        return bool(case.context)
    if kind == "context_recall":
        return bool(case.reference)
    return True


def _clip(text: str) -> str:
    return text if len(text) <= MAX_JUDGED_CHARS else text[:MAX_JUDGED_CHARS] + " [cut]"


def messages_for(kind: str, case: prompt_compare.Case, system_text: str, output: str) -> list[dict[str, str]]:
    """What the judge is sent: its role, the rubric, the material and the answer."""
    role, question = _RUBRICS[kind]
    parts = [question, ""]
    if kind == "faithfulness":
        parts += ["CONTEXT:", _clip(case.context or ""), ""]
    if kind == "instruction_adherence":
        parts += ["INSTRUCTIONS:", _clip(system_text), ""]
    if kind == "context_recall":
        parts += ["REFERENCE:", _clip(case.reference or ""), ""]
    parts += ["QUESTION:", _clip(case.input), "", "ANSWER:", _clip(output), "", _SCALE]
    return [{"role": "system", "content": role}, {"role": "user", "content": "\n".join(parts)}]


_OBJECT = re.compile(r"\{[^{}]*\}")


def parse_verdict(text: str) -> tuple[float, str] | None:
    """The score (0 to 1) and reason from the judge's reply, or None when it gave no usable rating."""
    for match in _OBJECT.finditer(text or ""):
        try:
            data = json.loads(match.group(0))
        except ValueError:
            continue
        if not isinstance(data, dict) or "score" not in data:
            continue
        score = data["score"]
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 1 <= score <= 5:
            return None
        reason = str(data.get("reason") or "").strip()[:MAX_REASON_CHARS]
        return round((float(score) - 1) / 4, 4), reason
    return None


@dataclass
class Verdict:
    kind: str
    score: float | None = None
    reason: str = ""
    error_type: str | None = None
    cost_usd: float = 0.0


async def judge_one(
    tenant_id: uuid.UUID,
    judge_model: str,
    kind: str,
    case: prompt_compare.Case,
    system_text: str,
    output: str,
    gate: asyncio.Semaphore,
    pseudonymiser: Any = None,
) -> Verdict:
    """One judge's verdict on one answer; a failure is an error for this judge and case."""
    messages = messages_for(kind, case, system_text, output)
    async with gate:
        started = time.monotonic()
        try:
            with spend_context.scope(application="console", default_use_case="evals.judge"):
                if pseudonymiser is not None:
                    response = await prompt_compare._complete(
                        tenant_id, judge_model, messages, JUDGE_MAX_TOKENS, pseudonymiser=pseudonymiser
                    )
                else:
                    response = await prompt_compare._complete(tenant_id, judge_model, messages, JUDGE_MAX_TOKENS)
        # enterprise-gate: broad-except-ok reason=one-judge-failure-is-its-own-error-and-is-logged
        except Exception as exc:
            logger.warning(
                "eval_judge_failed",
                judge=kind,
                model=judge_model,
                error_type=type(exc).__name__,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return Verdict(kind=kind, error_type=type(exc).__name__)
    served = str(getattr(response, "model", "") or "") or None
    cost = float(getattr(response, "cost_usd", 0.0) or 0.0)
    if served is not None and served != judge_model:
        logger.warning("eval_judge_served_by_other_model", judge=kind, model=judge_model, served_model=served)
        return Verdict(kind=kind, error_type="served_by_other_model", cost_usd=cost)
    text = str(response.content or "")
    if pseudonymiser is not None:
        text = pseudonymiser.restore_text(text)
    parsed = parse_verdict(text)
    if parsed is None:
        return Verdict(kind=kind, error_type="unrated", cost_usd=cost)
    score, reason = parsed
    return Verdict(kind=kind, score=score, reason=reason, cost_usd=cost)
