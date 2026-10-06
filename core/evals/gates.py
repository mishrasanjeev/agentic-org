# SPDX-License-Identifier: Apache-2.0
"""Promotion gates: an agent is not put into production while its prompt fails its evaluation.

An agent may declare a gate in its configuration (``config["eval_gate"]``): an
evaluation dataset, optionally a version (the latest when not given), the
minimum pass rate its prompt must reach there, and how far the pass rate may
fall below the prompt it replaces. When the agent is promoted or resumed to
``active``, the newest stored run of that dataset version made with the
agent's current prompt text (matched by the prompt's hash, so a run made
through ``POST /eval-datasets/{id}/run`` with ``agent_id`` always matches) is
read:

* no such run: blocked, the prompt has not been evaluated;
* its pass rate below ``min_pass_rate``: blocked;
* its pass rate more than ``max_regression`` points below the newest run of
  the same dataset version made with a different prompt (the prompt it
  replaces): blocked as a regression.

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
    unknown = sorted(set(raw) - {"dataset_id", "version", "min_pass_rate", "max_regression"})
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
    return gate


def declared(agent: Any) -> dict[str, Any] | None:
    gate = (getattr(agent, "config", None) or {}).get(GATE_KEY)
    return dict(gate) if isinstance(gate, dict) and gate else None


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
    session: Any, tenant_id: uuid.UUID, dataset_id: uuid.UUID, version: int, *, prompt_hash: str, same: bool
) -> EvalRun | None:
    statement = (
        select(EvalRun)
        .where(EvalRun.dataset_id == dataset_id, EvalRun.tenant_id == tenant_id, EvalRun.version == version)
        .where(EvalRun.prompt_hash == prompt_hash if same else EvalRun.prompt_hash != prompt_hash)
        .order_by(EvalRun.created_at.desc())
        .limit(1)
    )
    return (await session.execute(statement)).scalar_one_or_none()


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
    detail: dict[str, Any] = {
        "dataset_id": str(dataset_id),
        "version": version.version,
        "content_hash": version.content_hash,
        "prompt_hash": prompt_hash,
        "min_pass_rate": gate["min_pass_rate"],
        "max_regression": gate["max_regression"],
    }
    current = await _newest_run(session, tenant_id, dataset_id, version.version, prompt_hash=prompt_hash, same=True)
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
    if current.pass_rate is None:
        return Verdict(
            declared=True,
            enforced=enabled(),
            ok=False,
            code="not_scored",
            message="The newest run of this prompt scored no case",
            detail=detail,
        )
    if current.pass_rate * 100 < gate["min_pass_rate"]:
        return Verdict(
            declared=True,
            enforced=enabled(),
            ok=False,
            code="below_minimum",
            message=f"Pass rate {current.pass_rate:.0%} is below the gate's {gate['min_pass_rate']}%",
            detail=detail,
        )
    previous = await _newest_run(session, tenant_id, dataset_id, version.version, prompt_hash=prompt_hash, same=False)
    if previous is not None and previous.pass_rate is not None:
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
