# SPDX-License-Identifier: Apache-2.0
"""Promotion gates: an agent is not put into production while its prompt fails its evaluation.

An agent may declare a gate in its configuration (``config["eval_gate"]``): an
evaluation dataset, optionally a version (the latest when not given), the
minimum pass rate its prompt must reach there, and how far the pass rate may
fall below an explicitly selected baseline run. At promotion or resume to
``active``, the newest stored run of that dataset version, prompt hash and
configured model is read. Global primary/fallback and agent fallback models
also require independent full, error-free passing runs:

* no such run: blocked, the prompt has not been evaluated;
* its pass rate below ``min_pass_rate``: blocked;
* an incomplete run or an answer/judge error: blocked;
* its pass rate more than ``max_regression`` points below the agent's explicit
  ``baseline_run_id``: blocked as a regression. No implicit baseline is chosen.

Behind ``AGENTICORG_EVAL_PROMOTION_GATE_ENABLED`` (off by default) and the
evaluation switch. Off, a declared gate is kept and reported, and promotion
is not held to it. An agent with no gate is not affected either way.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select

from core.config import settings
from core.evals import datasets, runs
from core.models.eval_run import EvalRun

logger = structlog.get_logger()

GATE_KEY = "eval_gate"
TRIGGER = "evaluation_gate"


class GateError(Exception):
    """The gate is not passed; ``code`` says why and ``detail`` carries the numbers."""

    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


def enabled() -> bool:
    return bool(settings.eval_promotion_gate_enabled) and datasets.enabled()


def _percent(raw: Any, key: str, default: int) -> int:
    value = default if raw is None else raw
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise datasets.DatasetError(422, "invalid", f"{key} is a whole number between 0 and 100")
    return value


def parse_gate(raw: Any) -> dict[str, Any]:
    """A gate as it is stored; ``DatasetError`` names what is wrong."""
    if not isinstance(raw, dict):
        raise datasets.DatasetError(422, "invalid", "the gate must be an object")
    unknown = sorted(set(raw) - {"dataset_id", "version", "min_pass_rate", "max_regression", "baseline_run_id"})
    if unknown:
        raise datasets.DatasetError(422, "invalid", f"unknown gate keys: {', '.join(unknown)}")
    try:
        dataset_id = str(uuid.UUID(str(raw.get("dataset_id") or "")))
    except ValueError:
        raise datasets.DatasetError(422, "invalid", "dataset_id must be an evaluation dataset id") from None
    version = raw.get("version")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int) or version < 1):
        raise datasets.DatasetError(422, "invalid", "version is a whole number from 1, or null for the latest")
    gate = {
        "dataset_id": dataset_id,
        "version": version,
        "min_pass_rate": _percent(raw.get("min_pass_rate"), "min_pass_rate", 100),
        "max_regression": _percent(raw.get("max_regression"), "max_regression", 0),
    }
    if raw.get("baseline_run_id") is not None:
        try:
            gate["baseline_run_id"] = str(uuid.UUID(str(raw["baseline_run_id"])))
        except ValueError:
            raise datasets.DatasetError(422, "invalid", "baseline_run_id must be an evaluation run id") from None
    return gate


def declared(agent: Any) -> dict[str, Any] | None:
    gate = (getattr(agent, "config", None) or {}).get(GATE_KEY)
    # Malformed declarations must be rejected, not mistaken for no gate.
    return dict(gate) if isinstance(gate, dict) else gate


@dataclass
class Verdict:
    declared: bool
    enforced: bool
    ok: bool
    code: str = "passed"
    message: str = ""
    detail: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "declared": self.declared,
            "enforced": self.enforced,
            "ok": self.ok,
            "code": self.code,
            "message": self.message,
            "detail": dict(self.detail or {}),
        }


async def _newest_run(
    session: Any, tenant_id: uuid.UUID, dataset_id: uuid.UUID, version: int, *, prompt_hash: str, model: str
) -> EvalRun | None:
    statement = (
        select(EvalRun)
        .where(EvalRun.dataset_id == dataset_id, EvalRun.tenant_id == tenant_id, EvalRun.version == version)
        .where(EvalRun.prompt_hash == prompt_hash, EvalRun.model == model)
        .order_by(EvalRun.created_at.desc())
        .limit(1)
    )
    return (await session.execute(statement)).scalar_one_or_none()


def _run_problem(run: EvalRun, version: Any) -> str | None:
    if run.content_hash != version.content_hash or run.cases_total != version.case_count:
        return "dataset_mismatch"
    if run.offset != 0 or run.cases_run != run.cases_total or run.cases_run < 1:
        return "incomplete_run"
    if run.errors or any(score.get("errors", 0) for score in (run.scores or {}).values()):
        return "evaluation_errors"
    if run.pass_rate is None:
        return "not_scored"
    return None


async def evaluate(session: Any, tenant_id: uuid.UUID, agent: Any) -> Verdict:
    """Whether the agent's current prompt passes its declared gate; never raises for a gate that fails."""
    gate = declared(agent)
    if gate is None:
        return Verdict(declared=False, enforced=enabled(), ok=True, code="no_gate", message="No evaluation gate")
    try:
        gate = parse_gate(gate)
        dataset_id = uuid.UUID(gate["dataset_id"])
        version = await datasets.get_version(session, tenant_id, dataset_id, gate["version"])
    except datasets.DatasetError as exc:
        # A gate that cannot be read is not passed: an agent that says it is gated is not promoted on a broken gate.
        return Verdict(declared=True, enforced=enabled(), ok=False, code="gate_unusable", message=exc.message)
    text = str(getattr(agent, "system_prompt_text", None) or "")
    if not text.strip():
        return Verdict(
            declared=True,
            enforced=enabled(),
            ok=False,
            code="no_prompt_text",
            message="The agent has no prompt text to evaluate",
        )
    prompt_hash = runs.prompt_hash(text)
    model = str(getattr(agent, "llm_model", None) or "").strip()
    if not model:
        return Verdict(True, enabled(), False, "no_model", "Pin the agent's model before evaluating promotion")
    # Runtime can use an agent fallback or the router's global primary/fallback.
    # Require independent evidence for each; a fallback answer is not evidence
    # for the requested model (run_one already rejects that substitution).
    models = list(
        dict.fromkeys(
            filter(
                None,
                (
                    model,
                    getattr(agent, "llm_fallback", None),
                    settings.llm_primary,
                    settings.llm_fallback,
                ),
            )
        )
    )
    detail: dict[str, Any] = {
        "dataset_id": str(dataset_id),
        "version": version.version,
        "content_hash": version.content_hash,
        "prompt_hash": prompt_hash,
        "min_pass_rate": gate["min_pass_rate"],
        "max_regression": gate["max_regression"],
        "required_models": models,
    }
    current = await _newest_run(session, tenant_id, dataset_id, version.version, prompt_hash=prompt_hash, model=model)
    if current is None:
        return Verdict(
            declared=True,
            enforced=enabled(),
            ok=False,
            code="not_evaluated",
            message="No evaluation run of this dataset version was made with the agent's current prompt",
            detail=detail,
        )
    detail.update(run_id=str(current.id), pass_rate=current.pass_rate, cases_run=current.cases_run)
    problem = _run_problem(current, version)
    if problem:
        return Verdict(
            declared=True,
            enforced=enabled(),
            ok=False,
            code=problem,
            message="The newest run must cover the entire dataset version without answer or judge errors",
            detail=detail,
        )
    assert current.pass_rate is not None
    if current.pass_rate * 100 < gate["min_pass_rate"]:
        return Verdict(
            declared=True,
            enforced=enabled(),
            ok=False,
            code="below_minimum",
            message=f"Pass rate {current.pass_rate:.0%} is below the gate's {gate['min_pass_rate']}%",
            detail=detail,
        )
    for fallback in models[1:]:
        evidence = await _newest_run(
            session, tenant_id, dataset_id, version.version, prompt_hash=prompt_hash, model=fallback
        )
        if (
            evidence is None
            or _run_problem(evidence, version)
            or evidence.pass_rate is None
            or evidence.pass_rate * 100 < gate["min_pass_rate"]
        ):
            detail["unevaluated_model"] = fallback
            return Verdict(
                True,
                enabled(),
                False,
                "fallback_not_evaluated",
                "A runtime fallback lacks passing full-dataset evidence",
                detail,
            )
    previous = None
    if gate.get("baseline_run_id"):
        try:
            previous = await runs.get_run(session, tenant_id, uuid.UUID(gate["baseline_run_id"]))
        except datasets.DatasetError:
            return Verdict(
                True, enabled(), False, "baseline_unusable", "The selected baseline is not accessible", detail
            )
        if (
            previous.dataset_id != dataset_id
            or previous.version != version.version
            or previous.model != model
            or _run_problem(previous, version)
        ):
            return Verdict(
                True,
                enabled(),
                False,
                "baseline_unusable",
                "The selected baseline must match the dataset and model and be complete and error-free",
                detail,
            )
    if previous is not None:
        assert previous.pass_rate is not None
        detail.update(previous_run_id=str(previous.id), previous_pass_rate=previous.pass_rate)
        if (previous.pass_rate - current.pass_rate) * 100 > gate["max_regression"]:
            return Verdict(
                declared=True,
                enforced=enabled(),
                ok=False,
                code="regressed",
                message=(
                    f"Pass rate {current.pass_rate:.0%} is more than {gate['max_regression']} points below "
                    f"the prompt it replaces ({previous.pass_rate:.0%})"
                ),
                detail=detail,
            )
    return Verdict(declared=True, enforced=enabled(), ok=True, message="The gate is passed", detail=detail)


async def check_promotion(session: Any, tenant_id: uuid.UUID, agent: Any) -> Verdict:
    """The gate as promotion applies it: raises ``GateError`` when the gate is enforced and not passed."""
    verdict = await evaluate(session, tenant_id, agent)
    if verdict.declared and not verdict.ok:
        logger.warning(
            "eval_promotion_gate_not_passed",
            agent_id=str(getattr(agent, "id", "")),
            code=verdict.code,
            enforced=verdict.enforced,
        )
        if verdict.enforced:
            raise GateError(verdict.code, verdict.message, verdict.detail)
    return verdict
