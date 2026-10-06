# SPDX-License-Identifier: Apache-2.0
"""An evaluation run: a version of a dataset scored with a prompt and a model, and what is kept of it.

Each case is answered once by the model under test through the prompt-comparison
path (``core/prompts/compare.run_one``: the requested model, the tenant's model
policy, pseudonymisation where the tenant requires it). The answer is scored
against the case's expectations, any labelled case has its predicted label
read, the chosen judges rate it (``core/evals/scoring.py``), and the metrics
are computed over the outcomes (``core/evals/metrics.py``).

What is stored (``eval_runs``) is what was measured and what came out: the
version and its hash, the model, the judges, the prompt's hash and an optional
label, the counts, metrics, scores and the outcome per case id. Never an
answer, an input, the prompt's text or a judge's reason; the reasons are
returned with the run once.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from typing import Any

import structlog
from sqlalchemy import select

from core.evals import metrics as eval_metrics
from core.evals import scoring
from core.evals.datasets import DatasetError
from core.models.eval_run import EvalRun
from core.prompts import compare as prompt_compare

logger = structlog.get_logger()

MAX_RUNS_LISTED = 50


def prompt_hash(system_text: str) -> str:
    return hashlib.sha256(system_text.strip().encode("utf-8")).hexdigest()


def _scores(results: list[dict[str, Any]], judges: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kind in judges:
        rated = [entry["scores"][kind] for entry in results if kind in entry.get("scores", {})]
        errors = sum(1 for entry in results if kind in entry.get("judge_errors", {}))
        out[kind] = {
            "cases": len(rated),
            "mean": round(sum(rated) / len(rated), 4) if rated else None,
            "errors": errors,
        }
    return out


async def run_version(
    tenant_id: uuid.UUID,
    *,
    dataset_id: uuid.UUID,
    version: int,
    content_hash: str,
    cases_total: int,
    cases: list[prompt_compare.Case],
    offset: int,
    system_text: str,
    model: str,
    judges: tuple[str, ...] = (),
    judge_model: str | None = None,
    max_tokens: int | None = None,
    labels: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Answer, score and judge ``cases`` (one slice of the version) and report; nothing is stored here.

    ``labels`` are the whole version's labels: a slice is scored against every
    label the dataset uses, not only those its own cases carry, so an answer
    that names another of the dataset's labels first is read as that label.

    Raises ``ValueError`` for a model or judge that cannot be used and
    ``PseudonymisationError`` when pseudonymisation is on but unavailable, in
    both cases before any model is called.
    """
    [name] = prompt_compare.validate_models([model])
    judge_name: str | None = None
    if judges:
        [judge_name] = prompt_compare.validate_models([judge_model or ""])
    limit = prompt_compare.validate_max_tokens(max_tokens)
    system = prompt_compare._input_text(system_text, "the prompt")
    session = await prompt_compare.open_pseudonymiser(tenant_id)
    gate = asyncio.Semaphore(1 if session is not None else prompt_compare.CONCURRENCY)
    answers = await asyncio.gather(
        *(prompt_compare.run_one(tenant_id, name, system, case.input, limit, gate, session) for case in cases)
    )
    labels = labels if labels is not None else prompt_compare.labels_of(cases)
    results: list[dict[str, Any]] = []
    reasons: dict[str, dict[str, str]] = {}
    latency_ms = 0
    cost_usd = 0.0
    tokens = 0
    passed = failed = errors = 0
    judge_jobs: list[tuple[int, str, Any]] = []
    for index, (case, answer) in enumerate(zip(cases, answers, strict=True)):
        latency_ms += answer.latency_ms
        cost_usd += answer.cost_usd
        tokens += int(answer.tokens or 0)
        entry: dict[str, Any] = {"id": case.id}
        if case.equals is not None:
            entry["has_equals"] = True
        if not answer.ok:
            errors += 1
            entry.update(result="error", error_type=answer.error_type)
            if answer.served_model:
                entry["served_model"] = answer.served_model
            results.append(entry)
            continue
        missed = prompt_compare.score(case, answer.output, labels)
        if case.label:
            entry["label"] = {
                "expected": case.label,
                "predicted": prompt_compare.predict_label(answer.output, labels),
            }
        if missed:
            failed += 1
            entry.update(result="failed", failed_checks=missed)
        else:
            passed += 1
            entry["result"] = "passed"
        results.append(entry)
        for kind in judges:
            if scoring.applicable(kind, case):
                judge_jobs.append((index, kind, answer.output))
    if judge_jobs and judge_name:
        verdicts = await asyncio.gather(
            *(
                scoring.judge_one(tenant_id, judge_name, kind, cases[index], system, output, gate, session)
                for index, kind, output in judge_jobs
            )
        )
        for (index, kind, _output), verdict in zip(judge_jobs, verdicts, strict=True):
            entry = results[index]
            cost_usd += verdict.cost_usd
            if verdict.score is None:
                entry.setdefault("judge_errors", {})[kind] = verdict.error_type
                continue
            entry.setdefault("scores", {})[kind] = verdict.score
            if verdict.reason:
                reasons.setdefault(entry["id"], {})[kind] = verdict.reason
    scored = passed + failed
    logger.info(
        "eval_run_completed",
        dataset_id=str(dataset_id),
        version=version,
        cases=len(cases),
        errors=errors,
        judges=len(judges),
    )
    return {
        "dataset_id": str(dataset_id),
        "version": version,
        "content_hash": content_hash,
        "cases_total": cases_total,
        "offset": offset,
        "cases_run": len(cases),
        # ``cases`` is the first release's name for the number of cases scored.
        "cases": len(cases),
        "complete": offset == 0 and len(cases) == cases_total,
        "model": name,
        "judge_model": judge_name,
        "judges": list(judges),
        "prompt_hash": prompt_hash(system),
        "max_tokens": limit,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "pass_rate": round(passed / scored, 4) if scored else None,
        "avg_latency_ms": int(latency_ms / len(cases)) if cases else 0,
        "cost_usd": round(cost_usd, 6),
        "tokens": tokens,
        "metrics": eval_metrics.summarise(results, labels),
        "scores": _scores(results, judges),
        "results": results,
        "reasons": reasons,
    }


def run_dict(run: EvalRun, *, with_results: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(run.id),
        "dataset_id": str(run.dataset_id),
        "version": run.version,
        "content_hash": run.content_hash,
        "model": run.model,
        "judge_model": run.judge_model,
        "judges": list(run.judges or []),
        "prompt_hash": run.prompt_hash,
        "prompt_label": run.prompt_label,
        "max_tokens": run.max_tokens,
        "cases_total": run.cases_total,
        "offset": run.offset,
        "cases_run": run.cases_run,
        "cases": run.cases_run,
        "complete": run.offset == 0 and run.cases_run == run.cases_total,
        "passed": run.passed,
        "failed": run.failed,
        "errors": run.errors,
        "pass_rate": run.pass_rate,
        "avg_latency_ms": run.avg_latency_ms,
        "cost_usd": run.cost_usd,
        "tokens": int(run.tokens or 0),
        "metrics": dict(run.metrics or {}),
        "scores": dict(run.scores or {}),
        "created_by_user": str(run.created_by_user) if run.created_by_user else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }
    if with_results:
        out["results"] = list(run.results or [])
    return out


def store(
    session: Any,
    tenant_id: uuid.UUID,
    report: dict[str, Any],
    *,
    prompt_label: str | None,
    actor: uuid.UUID | None,
) -> EvalRun:
    """Keep what a run measured and what came out; the report's reasons are not written."""
    run = EvalRun(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        dataset_id=uuid.UUID(report["dataset_id"]),
        version=report["version"],
        content_hash=report["content_hash"],
        model=report["model"],
        judge_model=report["judge_model"],
        judges=list(report["judges"]),
        prompt_hash=report["prompt_hash"],
        prompt_label=prompt_label,
        max_tokens=report["max_tokens"],
        cases_total=report["cases_total"],
        offset=report["offset"],
        cases_run=report["cases_run"],
        passed=report["passed"],
        failed=report["failed"],
        errors=report["errors"],
        pass_rate=report["pass_rate"],
        metrics=report["metrics"],
        scores=report["scores"],
        results=report["results"],
        avg_latency_ms=report["avg_latency_ms"],
        cost_usd=report["cost_usd"],
        tokens=int(report.get("tokens") or 0),
        created_by_user=actor,
    )
    session.add(run)
    return run


async def list_runs(session: Any, tenant_id: uuid.UUID, dataset_id: uuid.UUID) -> list[EvalRun]:
    statement = (
        select(EvalRun)
        .where(EvalRun.dataset_id == dataset_id, EvalRun.tenant_id == tenant_id)
        .order_by(EvalRun.created_at.desc())
        .limit(MAX_RUNS_LISTED)
    )
    return list((await session.execute(statement)).scalars().all())


async def get_run(session: Any, tenant_id: uuid.UUID, run_id: uuid.UUID) -> EvalRun:
    found = (
        await session.execute(select(EvalRun).where(EvalRun.id == run_id, EvalRun.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if found is None:
        raise DatasetError(404, "run_not_found", "Evaluation run not found")
    return found


def _per_case(total: float, cases: int, digits: int) -> float | None:
    return round(total / cases, digits) if cases else None


def rank_models(stored: list[EvalRun]) -> list[dict[str, Any]]:
    """One row per model from its newest run, ranked by pass rate, then latency, then cost per case.

    Throughput is the answers a minute one sequential caller would get at the
    run's average latency: an estimate from the measured latency, not a load
    test.
    """
    newest: dict[str, EvalRun] = {}
    for run in sorted(stored, key=lambda item: item.created_at or 0, reverse=True):
        newest.setdefault(run.model, run)
    rows: list[dict[str, Any]] = []
    for run in newest.values():
        classification = (run.metrics or {}).get("classification") or {}
        latency = int(run.avg_latency_ms or 0)
        rows.append(
            {
                "model": run.model,
                "run_id": str(run.id),
                "prompt_label": run.prompt_label,
                "prompt_hash": run.prompt_hash,
                "cases_run": run.cases_run,
                "complete": run.offset == 0 and run.cases_run == run.cases_total,
                "pass_rate": run.pass_rate,
                "accuracy": classification.get("accuracy"),
                "avg_latency_ms": latency,
                "answers_per_minute": round(60_000 / latency, 1) if latency else None,
                "tokens_per_case": _per_case(int(run.tokens or 0), run.cases_run, 1),
                "cost_per_case_usd": _per_case(float(run.cost_usd or 0.0), run.cases_run, 6),
                "scores": {kind: score.get("mean") for kind, score in (run.scores or {}).items()},
                "created_at": run.created_at.isoformat() if run.created_at else None,
            }
        )
    def _order(row: dict[str, Any]) -> tuple[float, int, float]:
        pass_rate = float(row["pass_rate"]) if row["pass_rate"] is not None else -1.0
        cost = float(row["cost_per_case_usd"]) if row["cost_per_case_usd"] is not None else 0.0
        return (-pass_rate, int(row["avg_latency_ms"]), cost)

    rows.sort(key=_order)
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows


async def compare_models(
    session: Any, tenant_id: uuid.UUID, dataset_id: uuid.UUID, version: int
) -> list[dict[str, Any]]:
    """The models that ran one dataset version, ranked from their newest stored runs."""
    statement = (
        select(EvalRun)
        .where(EvalRun.dataset_id == dataset_id, EvalRun.tenant_id == tenant_id, EvalRun.version == version)
        .order_by(EvalRun.created_at.desc())
        .limit(MAX_RUNS_LISTED * 4)
    )
    return rank_models(list((await session.execute(statement)).scalars().all()))
