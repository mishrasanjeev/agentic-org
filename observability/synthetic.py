# SPDX-License-Identifier: Apache-2.0
"""Synthetic checks: scheduled probes of a tenant's own paths, with a stored result each run.

A check names a probe kind, its configuration and an interval. Four kinds:

``model``
    Sends a fixed prompt through the direct router (the model gateway's
    policies, limits and records apply as for any call) and expects an
    answer, optionally one containing a given text.
``knowledge``
    Runs a fixed query against the tenant's knowledge search and expects a
    minimum number of results.
``guardrail``
    Dry-runs the tenant's guardrail rules for a stage over a fixed text and
    expects it blocked, detected or clean: the rules are still in place and
    still catch what they are there to catch.
``audit_chain``
    Verifies the newest links of the tenant's audit chain.
``adversarial``
    Dry-runs the tenant's guardrail rules over the adversarial evaluation set
    (``core.governance.guardrails.adversarial``) and expects a minimum recall
    and at most a given number of benign controls wrongly caught.
``eval_dataset``
    Scores a prompt with a model against a version of one of the tenant's
    evaluation datasets (``core.evals.runs``), keeps the run in the
    evaluation history, and expects a minimum pass rate. One billed model
    call per case each run, plus one per case and judge.

Every kind takes ``max_latency_ms``. A run ends ``ok``, ``failed`` (the probe
answered but an expectation did not hold; ``reasons`` says which) or
``error`` (the probe itself could not run). A result keeps counts, the
reasons and the error type; never an answer, retrieved text or the probe's
input. Prompts and texts are the tenant administrator's own synthetic
inputs.

Everything that runs a probe is behind ``AGENTICORG_SYNTHETIC_CHECKS_ENABLED``
(off by default): the scheduled sweep (``core.tasks.synthetic_tasks``), a run
by hand and adding a check. Off, the stored checks and results can still be
read, changed and removed.

A run claims its check first: one conditional UPDATE moves ``last_run_at``
from the value the runner read to now, so of two runners that read the same
due check (an overlapping sweep, a run by hand during a sweep) exactly one
probes and the other skips.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from core.config import settings
from observability import tracing

logger = structlog.get_logger()

KINDS: tuple[str, ...] = ("model", "knowledge", "guardrail", "audit_chain", "adversarial", "eval_dataset")
STATUSES: tuple[str, ...] = ("ok", "failed", "error")
TRIGGERS: tuple[str, ...] = ("schedule", "manual")
GUARDRAIL_EXPECTATIONS: tuple[str, ...] = ("blocked", "detected", "clean")
MAX_CHECKS = 20
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 1440
MAX_LATENCY_MS = 600_000
PROBE_TIMEOUT_SECONDS = 60.0
MODEL_MAX_TOKENS = 64


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _text(config: dict[str, Any], key: str, *, limit: int, required: bool = True) -> str | None:
    value = config.get(key)
    if value is None or value == "":
        if required:
            raise ValueError(f"{key} is required")
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be text")
    if len(value) > limit:
        raise ValueError(f"{key} must be at most {limit} characters")
    return value.strip()


def _number(config: dict[str, Any], key: str, *, low: int, high: int, default: int | None) -> int | None:
    value = config.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be a whole number")
    if not low <= value <= high:
        raise ValueError(f"{key} must be between {low} and {high}")
    return value


_KEYS: dict[str, tuple[str, ...]] = {
    "model": ("prompt", "model", "contains", "max_latency_ms"),
    "knowledge": ("query", "top_k", "min_results", "max_latency_ms"),
    "guardrail": ("stage", "text", "expect", "max_latency_ms"),
    "audit_chain": ("recent", "max_latency_ms"),
    "adversarial": ("min_recall", "max_false_positives", "max_latency_ms"),
    "eval_dataset": (
        "dataset_id",
        "version",
        "system",
        "model",
        "judges",
        "judge_model",
        "limit",
        "min_pass_rate",
        "max_latency_ms",
    ),
}


def _percent(config: dict[str, Any], key: str, *, default: int) -> int:
    """A whole-number percentage, 0 to 100."""
    value = _number(config, key, low=0, high=100, default=default)
    return int(value if value is not None else default)


def validate_config(kind: str, config: Any) -> dict[str, Any]:
    """The configuration of a ``kind`` check with defaults filled; ValueError names what is wrong."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    unknown = sorted(set(config) - set(_KEYS[kind]))
    if unknown:
        raise ValueError(f"unknown config keys for a {kind} check: {', '.join(unknown)}")
    clean: dict[str, Any] = {}
    if kind == "model":
        clean["prompt"] = _text(config, "prompt", limit=2000)
        clean["model"] = _text(config, "model", limit=128, required=False)
        clean["contains"] = _text(config, "contains", limit=200, required=False)
    elif kind == "knowledge":
        clean["query"] = _text(config, "query", limit=500)
        clean["top_k"] = _number(config, "top_k", low=1, high=20, default=5)
        clean["min_results"] = _number(config, "min_results", low=0, high=20, default=1)
        if clean["min_results"] > clean["top_k"]:
            raise ValueError("min_results cannot exceed top_k")
    elif kind == "guardrail":
        from core.governance.guardrails import STAGES

        stage = str(config.get("stage") or "").strip().lower()
        if stage not in STAGES:
            raise ValueError(f"stage must be one of {', '.join(STAGES)}")
        expect = str(config.get("expect") or "detected").strip().lower()
        if expect not in GUARDRAIL_EXPECTATIONS:
            raise ValueError(f"expect must be one of {', '.join(GUARDRAIL_EXPECTATIONS)}")
        clean["stage"] = stage
        clean["text"] = _text(config, "text", limit=2000)
        clean["expect"] = expect
    elif kind == "audit_chain":
        clean["recent"] = _number(config, "recent", low=1, high=10_000, default=1000)
    elif kind == "adversarial":
        clean["min_recall"] = _percent(config, "min_recall", default=50)
        clean["max_false_positives"] = _number(config, "max_false_positives", low=0, high=1000, default=0)
    else:
        from core.evals import scoring

        try:
            clean["dataset_id"] = str(uuid.UUID(str(config.get("dataset_id") or "")))
        except ValueError:
            raise ValueError("dataset_id must be an evaluation dataset id") from None
        clean["version"] = _number(config, "version", low=1, high=1_000_000, default=None)
        clean["system"] = _text(config, "system", limit=20_000)
        clean["model"] = _text(config, "model", limit=128)
        try:
            judges = list(scoring.validate_judges(config.get("judges")))
        except scoring.JudgeError as exc:
            raise ValueError(str(exc)) from None
        clean["judges"] = judges or None
        clean["judge_model"] = _text(config, "judge_model", limit=128, required=False)
        if judges and not clean["judge_model"]:
            raise ValueError("judge_model is required with judges")
        clean["limit"] = _number(config, "limit", low=1, high=25, default=25)
        clean["min_pass_rate"] = _percent(config, "min_pass_rate", default=100)
    clean["max_latency_ms"] = _number(config, "max_latency_ms", low=1, high=MAX_LATENCY_MS, default=None)
    return {key: value for key, value in clean.items() if value is not None}


def validate_interval(minutes: Any) -> int:
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        raise ValueError("interval_minutes must be a whole number")
    if not MIN_INTERVAL_MINUTES <= minutes <= MAX_INTERVAL_MINUTES:
        raise ValueError(f"interval_minutes must be between {MIN_INTERVAL_MINUTES} and {MAX_INTERVAL_MINUTES}")
    return minutes


def validate_name(name: Any) -> str:
    text = str(name or "").strip()
    if not text or len(text) > 120:
        raise ValueError("name must be 1 to 120 characters")
    return text


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    kind: str
    config: dict[str, Any]
    interval_minutes: int = 60
    enabled: bool = True
    last_run_at: datetime | None = None
    last_status: str | None = None
    created_by: str = ""
    created_at: datetime | None = None
    updated_by: str | None = None
    updated_at: datetime | None = None

    def due(self, now: datetime) -> bool:
        if not self.enabled:
            return False
        return self.last_run_at is None or self.last_run_at + timedelta(minutes=self.interval_minutes) <= now

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "name": self.name,
            "kind": self.kind,
            "config": dict(self.config),
            "interval_minutes": self.interval_minutes,
            "enabled": self.enabled,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_status": self.last_status,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class Result:
    check_id: uuid.UUID
    status: str
    latency_ms: int
    started_at: datetime
    reasons: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    trigger: str = "schedule"
    id: uuid.UUID = field(default_factory=uuid.uuid4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "check_id": str(self.check_id),
            "status": self.status,
            "latency_ms": self.latency_ms,
            "reasons": list(self.reasons),
            "detail": dict(self.detail),
            "trigger": self.trigger,
            "started_at": self.started_at.isoformat(),
        }


def _check_of(row: Any) -> Check:
    return Check(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        kind=row.kind,
        config=dict(row.config or {}),
        interval_minutes=int(row.interval_minutes),
        enabled=bool(row.enabled),
        last_run_at=row.last_run_at,
        last_status=row.last_status,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


def _result_of(row: Any) -> Result:
    return Result(
        id=row.id,
        check_id=row.check_id,
        status=row.status,
        latency_ms=int(row.latency_ms),
        started_at=row.started_at,
        reasons=list(row.reasons or []),
        detail=dict(row.detail or {}),
        trigger=row.trigger,
    )


# ---------------------------------------------------------------------------
# Probes (seams the tests replace): each returns the reasons an expectation
# failed and a detail of counts, never content.
# ---------------------------------------------------------------------------


async def _probe_model(tenant_id: uuid.UUID, config: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    from core.llm.router import llm_router

    response = await llm_router.complete(
        [{"role": "user", "content": config["prompt"]}],
        model_override=config.get("model"),
        max_tokens=MODEL_MAX_TOKENS,
        tenant_id=str(tenant_id),
    )
    answer = str(response.content or "")
    reasons: list[str] = []
    if not answer.strip():
        reasons.append("empty_answer")
    expected = config.get("contains")
    if expected and expected.lower() not in answer.lower():
        reasons.append("answer_missing_expected_text")
    return reasons, {"model": response.model, "tokens": int(response.tokens_used or 0)}


async def _probe_knowledge(tenant_id: uuid.UUID, config: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    from api.v1.knowledge import SearchRequest, _search_knowledge

    response = await _search_knowledge(SearchRequest(query=config["query"], top_k=config["top_k"]), str(tenant_id))
    found = len(response.results)
    reasons = ["too_few_results"] if found < config["min_results"] else []
    return reasons, {"results": found}


async def _probe_guardrail(tenant_id: uuid.UUID, config: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    from core.governance import guardrails

    result = await guardrails.evaluate(config["stage"], config["text"], tenant_id=tenant_id, dry_run=True)
    expect = config["expect"]
    reasons: list[str] = []
    if expect == "blocked" and result.allowed:
        reasons.append("guardrail_not_blocked")
    elif expect == "detected" and result.findings == 0:
        reasons.append("guardrail_not_detected")
    elif expect == "clean" and result.outcomes:
        reasons.append("guardrail_unexpected_finding")
    detail = {
        "findings": result.findings,
        "rules_matched": len(result.outcomes),
        "allowed": result.allowed,
        "enforced": result.enforced,
    }
    return reasons, detail


async def _probe_audit_chain(tenant_id: uuid.UUID, config: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    from core.governance import audit_chain

    current = await audit_chain.status(tenant_id)
    head_seq = int((current.get("head") or {}).get("seq") or 0)
    recent = int(config["recent"])
    checked = await audit_chain.verify(tenant_id, from_seq=max(1, head_seq - recent + 1), limit=recent)
    detail: dict[str, Any] = {"status": checked.status, "head_seq": checked.head.seq, "verified": checked.verified}
    if checked.first_break is not None:
        detail["break_seq"] = checked.first_break.seq
        detail["break_reason"] = checked.first_break.reason
        return ["audit_chain_broken"], detail
    return [], detail


async def _probe_adversarial(tenant_id: uuid.UUID, config: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """The adversarial set over the tenant's rules; the detail is the report without case texts."""
    from core.governance.guardrails import adversarial

    report = (await adversarial.run_suite(tenant_id=tenant_id)).to_dict()
    reasons: list[str] = []
    recall = report["recall"]
    if recall is None or recall * 100 < int(config["min_recall"]):
        reasons.append("adversarial_recall_below_minimum")
    if int(report["false_positives"]) > int(config["max_false_positives"]):
        reasons.append("adversarial_controls_wrongly_caught")
    if report["errors"]:
        reasons.append("adversarial_cases_not_evaluated")
    detail = {
        "rule_count": report["rule_count"],
        "attacks": report["attacks"],
        "detected": report["detected"],
        "recall": recall,
        "controls": report["controls"],
        "false_positives": report["false_positives"],
        "errors": len(report["errors"]),
        "categories": [
            {
                "category": category["category"],
                "recall": category["recall"],
                "false_positives": category["false_positives"],
                "missed": list(category["missed"]),
            }
            for category in report["categories"]
        ],
    }
    return reasons, detail


async def _probe_eval_dataset(tenant_id: uuid.UUID, config: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """A slice of a dataset version scored with the configured prompt and model; the run is kept."""
    from core.database import get_tenant_session
    from core.evals import datasets, runs
    from core.prompts import compare as prompt_compare

    if not datasets.enabled() or not prompt_compare.enabled():
        raise RuntimeError("evaluation datasets or prompt evaluation are off")
    dataset_id = uuid.UUID(config["dataset_id"])
    async with get_tenant_session(tenant_id) as session:
        version = await datasets.get_version(session, tenant_id, dataset_id, config.get("version"))
        described = datasets.version_dict(version, with_cases=True)
    selected = described["cases"][: int(config["limit"])]
    report = await runs.run_version(
        tenant_id,
        dataset_id=dataset_id,
        version=described["version"],
        content_hash=described["content_hash"],
        cases_total=described["case_count"],
        cases=prompt_compare.parse_cases(selected),
        offset=0,
        system_text=config["system"],
        model=config["model"],
        judges=tuple(config.get("judges") or ()),
        judge_model=config.get("judge_model"),
    )
    async with get_tenant_session(tenant_id) as session:
        stored = runs.store(session, tenant_id, report, prompt_label=f"scheduled:{config['model']}", actor=None)
        await session.flush()
        run_id = str(stored.id)
    reasons: list[str] = []
    if report["errors"]:
        reasons.append("eval_cases_not_answered")
    if report["pass_rate"] is None or report["pass_rate"] * 100 < int(config["min_pass_rate"]):
        reasons.append("eval_pass_rate_below_minimum")
    detail = {
        "run_id": run_id,
        "version": report["version"],
        "cases_run": report["cases_run"],
        "complete": report["complete"],
        "passed": report["passed"],
        "failed": report["failed"],
        "errors": report["errors"],
        "pass_rate": report["pass_rate"],
        "scores": {kind: score["mean"] for kind, score in report["scores"].items()},
        "cost_usd": report["cost_usd"],
    }
    return reasons, detail


_PROBES = {
    "model": "_probe_model",
    "knowledge": "_probe_knowledge",
    "guardrail": "_probe_guardrail",
    "audit_chain": "_probe_audit_chain",
    "adversarial": "_probe_adversarial",
    "eval_dataset": "_probe_eval_dataset",
}


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


async def probe(check: Check, *, trigger: str = "schedule") -> Result:
    """Run the check's probe once, under the probe time limit; never raises."""
    started_at = datetime.now(UTC)
    began = time.monotonic()
    reasons: list[str] = []
    detail: dict[str, Any] = {}
    status = "ok"
    with tracing.span(
        "agenticorg.synthetic.check", tenant=check.tenant_id, **{"check.kind": check.kind, "check.trigger": trigger}
    ) as current:
        try:
            config = validate_config(check.kind, check.config)
            run = globals()[_PROBES[check.kind]]
            async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
                reasons, detail = await run(check.tenant_id, config)
        # enterprise-gate: broad-except-ok reason=a-failing-probe-is-the-result-recorded-as-error-and-logged
        except Exception as exc:
            status = "error"
            detail = {"error_type": type(exc).__name__}
            config = {}
        latency_ms = int((time.monotonic() - began) * 1000)
        limit = config.get("max_latency_ms")
        if status == "ok" and limit and latency_ms > int(limit):
            reasons = [*reasons, "too_slow"]
        if status == "ok" and reasons:
            status = "failed"
        current.set_attribute("check.status", status)
    return Result(
        check_id=check.id,
        status=status,
        latency_ms=latency_ms,
        started_at=started_at,
        reasons=reasons,
        detail=detail,
        trigger=trigger,
    )


async def claim(check: Check, *, now: datetime | None = None) -> bool:
    """Take the check for one run: True for exactly one of the runners that read the same ``last_run_at``."""
    from sqlalchemy import update

    from core.database import get_tenant_session
    from core.models.synthetic_check import SyntheticCheck

    seen = (
        SyntheticCheck.last_run_at.is_(None)
        if check.last_run_at is None
        else SyntheticCheck.last_run_at == check.last_run_at
    )
    async with get_tenant_session(check.tenant_id) as session:
        taken = await session.execute(
            update(SyntheticCheck)
            .where(SyntheticCheck.tenant_id == check.tenant_id, SyntheticCheck.id == check.id, seen)
            .values(last_run_at=now or datetime.now(UTC))
        )
        return int(getattr(taken, "rowcount", 0) or 0) == 1


async def run_check(check: Check, *, trigger: str = "schedule") -> Result | None:
    """Claim the check, probe, store the result and meter it; None when another runner holds the check."""
    if not await claim(check):
        logger.info("synthetic_check_already_claimed", check_id=str(check.id), kind=check.kind, trigger=trigger)
        return None
    result = await probe(check, trigger=trigger)
    await _store_result(check, result)
    from observability.metrics import synthetic_checks_total

    synthetic_checks_total.labels(kind=check.kind, result=result.status).inc()
    if result.status != "ok":
        logger.warning(
            "synthetic_check_not_ok",
            tenant_id=str(check.tenant_id),
            check_id=str(check.id),
            kind=check.kind,
            status=result.status,
            reasons=result.reasons,
            error_type=result.detail.get("error_type"),
        )
    return result


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


async def _store_result(check: Check, result: Result) -> None:
    from sqlalchemy import update

    from core.database import get_tenant_session
    from core.models.synthetic_check import SyntheticCheck, SyntheticCheckResult

    async with get_tenant_session(check.tenant_id) as session:
        session.add(
            SyntheticCheckResult(
                id=result.id,
                tenant_id=check.tenant_id,
                check_id=check.id,
                status=result.status,
                latency_ms=result.latency_ms,
                reasons=result.reasons,
                detail=result.detail,
                trigger=result.trigger,
                started_at=result.started_at,
            )
        )
        await session.execute(
            update(SyntheticCheck)
            .where(SyntheticCheck.tenant_id == check.tenant_id, SyntheticCheck.id == check.id)
            .values(last_run_at=result.started_at, last_status=result.status)
        )


async def _lock_tenant(session: Any, tenant_id: uuid.UUID) -> None:
    """Serialise the tenant's check creations: held until the transaction ends."""
    from sqlalchemy import text

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": f"synthetic_checks:{tenant_id}"}
    )


async def list_checks(tenant_id: uuid.UUID) -> list[Check]:
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.synthetic_check import SyntheticCheck

    async with get_tenant_session(tenant_id) as session:
        rows = await session.scalars(
            select(SyntheticCheck).where(SyntheticCheck.tenant_id == tenant_id).order_by(SyntheticCheck.name.asc())
        )
        return [_check_of(row) for row in rows.all()]


async def get_check(tenant_id: uuid.UUID, check_id: uuid.UUID) -> Check | None:
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.synthetic_check import SyntheticCheck

    async with get_tenant_session(tenant_id) as session:
        row = await session.scalar(
            select(SyntheticCheck).where(SyntheticCheck.tenant_id == tenant_id, SyntheticCheck.id == check_id)
        )
        return _check_of(row) if row is not None else None


async def due_checks(tenant_id: uuid.UUID, *, now: datetime | None = None, limit: int = MAX_CHECKS) -> list[Check]:
    """The tenant's enabled checks whose interval has passed, the longest-waiting first."""
    moment = now or datetime.now(UTC)
    waiting = [check for check in await list_checks(tenant_id) if check.due(moment)]
    waiting.sort(key=lambda check: check.last_run_at or datetime.min.replace(tzinfo=UTC))
    return waiting[: max(1, limit)]


async def create_check(
    tenant_id: uuid.UUID,
    *,
    actor_id: str,
    name: str,
    kind: str,
    config: dict[str, Any],
    interval_minutes: int = 60,
    enabled: bool = True,
) -> Check:
    """Add a check; ValueError for an invalid one, a taken name or a tenant at its limit."""
    from sqlalchemy import func, select

    from core.database import get_tenant_session
    from core.models.synthetic_check import SyntheticCheck

    clean_name = validate_name(name)
    clean_config = validate_config(kind, config)
    interval = validate_interval(interval_minutes)
    async with get_tenant_session(tenant_id) as session:
        # The count and the insert are one step per tenant: concurrent creates queue on this lock.
        await _lock_tenant(session, tenant_id)
        existing = (
            await session.execute(
                select(func.count(), func.count().filter(SyntheticCheck.name == clean_name)).where(
                    SyntheticCheck.tenant_id == tenant_id
                )
            )
        ).one()
        if int(existing[1] or 0):
            raise ValueError("a check with this name already exists")
        if int(existing[0] or 0) >= MAX_CHECKS:
            raise ValueError(f"a tenant has at most {MAX_CHECKS} synthetic checks")
        row = SyntheticCheck(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name=clean_name,
            kind=kind,
            config=clean_config,
            interval_minutes=interval,
            enabled=bool(enabled),
            created_by=actor_id,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        return _check_of(row)


async def update_check(
    tenant_id: uuid.UUID, check_id: uuid.UUID, *, actor_id: str, changes: dict[str, Any]
) -> Check | None:
    """Change a check's name, configuration, interval or enabled state; None when it does not exist."""
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.synthetic_check import SyntheticCheck

    allowed = {"name", "config", "interval_minutes", "enabled"}
    unknown = sorted(set(changes) - allowed)
    if unknown:
        raise ValueError(f"cannot change: {', '.join(unknown)}")
    async with get_tenant_session(tenant_id) as session:
        row = await session.scalar(
            select(SyntheticCheck).where(SyntheticCheck.tenant_id == tenant_id, SyntheticCheck.id == check_id)
        )
        if row is None:
            return None
        if "name" in changes:
            clean_name = validate_name(changes["name"])
            if clean_name != row.name:
                taken = await session.scalar(
                    select(SyntheticCheck.id).where(
                        SyntheticCheck.tenant_id == tenant_id, SyntheticCheck.name == clean_name
                    )
                )
                if taken is not None:
                    raise ValueError("a check with this name already exists")
            row.name = clean_name
        if "config" in changes:
            row.config = validate_config(row.kind, changes["config"])
        if "interval_minutes" in changes:
            row.interval_minutes = validate_interval(changes["interval_minutes"])
        if "enabled" in changes:
            row.enabled = bool(changes["enabled"])
        row.updated_by = actor_id
        await session.flush()
        await session.refresh(row)
        return _check_of(row)


async def delete_check(tenant_id: uuid.UUID, check_id: uuid.UUID) -> bool:
    """Remove a check and its results; False when it does not exist."""
    from sqlalchemy import delete

    from core.database import get_tenant_session
    from core.models.synthetic_check import SyntheticCheck, SyntheticCheckResult

    async with get_tenant_session(tenant_id) as session:
        await session.execute(
            delete(SyntheticCheckResult).where(
                SyntheticCheckResult.tenant_id == tenant_id, SyntheticCheckResult.check_id == check_id
            )
        )
        removed = await session.execute(
            delete(SyntheticCheck).where(SyntheticCheck.tenant_id == tenant_id, SyntheticCheck.id == check_id)
        )
        return bool(getattr(removed, "rowcount", 0))


async def results(tenant_id: uuid.UUID, check_id: uuid.UUID, *, limit: int = 50) -> list[Result]:
    """The check's newest results."""
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.synthetic_check import SyntheticCheckResult

    async with get_tenant_session(tenant_id) as session:
        rows = await session.scalars(
            select(SyntheticCheckResult)
            .where(SyntheticCheckResult.tenant_id == tenant_id, SyntheticCheckResult.check_id == check_id)
            .order_by(SyntheticCheckResult.started_at.desc())
            .limit(max(1, min(int(limit), 500)))
        )
        return [_result_of(row) for row in rows.all()]


def enabled() -> bool:
    return bool(settings.synthetic_checks_enabled)
