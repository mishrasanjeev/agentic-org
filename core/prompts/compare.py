# SPDX-License-Identifier: Apache-2.0
"""Side-by-side comparison and dataset evaluation for prompts.

**Compare** runs one prompt with one input against several models at once and
returns each model's answer with its latency, token counts and cost, so the
choice of model for a prompt is made on what the models actually return.

**Evaluate** scores prompt variants against a small reference dataset before
release: each case is an input with deterministic expectations (text the
answer must or must not contain, an exact answer, a bounded pattern), every
variant answers every case with one model, and the result is each variant's
pass rate with the cases it failed.

Both make real, billed model calls through the direct router as the tenant,
so the model gateway's policies, limits and records apply to every call and a
model the tenant may not use is refused there. Both are bounded (models,
cases, variants, answer tokens, concurrent calls) and behind
``AGENTICORG_PROMPT_COMPARE_ENABLED`` (off by default).

Answers are returned to the caller, the administrator trying their own
prompt; nothing is stored and the logs carry counts and outcomes only. One
model's failure is that model's result, never the whole comparison's.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

from core.config import settings

logger = structlog.get_logger()

MAX_MODELS = 4
MAX_VARIANTS = 3
MAX_CASES = 25
MAX_INPUT_CHARS = 20_000
DEFAULT_MAX_TOKENS = 512
MAX_MAX_TOKENS = 2048
CONCURRENCY = 4
ROUTER_PROVIDERS: tuple[str, ...] = ("gemini", "openai", "anthropic")
CHECKS: tuple[str, ...] = ("contains", "not_contains", "equals", "matches")
MAX_CHECK_ITEMS = 10
MAX_CHECK_TEXT = 500


def enabled() -> bool:
    return bool(settings.prompt_compare_enabled)


def comparable_models() -> list[dict[str, Any]]:
    """The catalogue models the direct router can call, which is what a comparison can use."""
    from core.ai_providers.catalog import LLM_CATALOG
    from core.llm.router import _model_provider

    return [
        {"provider": entry.provider, "model": entry.model, "context_window": entry.context_window}
        for entry in LLM_CATALOG
        # The router dispatches by model family to these providers' own APIs; a
        # deployment-named or locally served entry is not one it can call.
        if entry.provider in ROUTER_PROVIDERS and _model_provider(entry.model) is not None
    ]


def validate_models(models: Any) -> list[str]:
    """One to four distinct models from the comparable catalogue; ValueError otherwise."""
    if not isinstance(models, list) or not models:
        raise ValueError("models must be a non-empty list")
    names = list(dict.fromkeys(str(model).strip() for model in models))
    if len(names) > MAX_MODELS:
        raise ValueError(f"a comparison takes at most {MAX_MODELS} models")
    known = {entry["model"] for entry in comparable_models()}
    unknown = [name for name in names if name not in known]
    if unknown:
        raise ValueError(f"not a model a comparison can call: {', '.join(unknown)}")
    return names


def validate_max_tokens(value: Any) -> int:
    if value is None:
        return DEFAULT_MAX_TOKENS
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_MAX_TOKENS:
        raise ValueError(f"max_tokens is a whole number between 1 and {MAX_MAX_TOKENS}")
    return value


def _input_text(value: Any, label: str = "input") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    if len(value) > MAX_INPUT_CHARS:
        raise ValueError(f"{label} must be at most {MAX_INPUT_CHARS} characters")
    return value


@dataclass
class ModelResult:
    model: str
    ok: bool
    output: str = ""
    served_model: str | None = None
    latency_ms: int = 0
    tokens: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float = 0.0
    error_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "ok": self.ok,
            "output": self.output,
            "served_model": self.served_model,
            "latency_ms": self.latency_ms,
            "tokens": self.tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "error_type": self.error_type,
        }


async def _complete(tenant_id: uuid.UUID, model: str, messages: list[dict[str, str]], max_tokens: int) -> Any:
    """One model call through the direct router as the tenant (the seam the tests replace)."""
    from core.llm.router import llm_router

    return await llm_router.complete(messages, model_override=model, max_tokens=max_tokens, tenant_id=str(tenant_id))


async def run_one(
    tenant_id: uuid.UUID,
    model: str,
    system_text: str,
    user_input: str,
    max_tokens: int,
    gate: asyncio.Semaphore,
) -> ModelResult:
    """One model's answer; a failure is recorded as this model's result with the error type only."""
    messages = [{"role": "system", "content": system_text}, {"role": "user", "content": user_input}]
    async with gate:
        started = time.monotonic()
        try:
            response = await _complete(tenant_id, model, messages, max_tokens)
        # enterprise-gate: broad-except-ok reason=one-models-failure-is-its-own-result-and-is-logged
        except Exception as exc:
            logger.warning("prompt_compare_model_failed", model=model, error_type=type(exc).__name__)
            return ModelResult(
                model=model,
                ok=False,
                latency_ms=int((time.monotonic() - started) * 1000),
                error_type=type(exc).__name__,
            )
    return ModelResult(
        model=model,
        ok=True,
        output=str(response.content or ""),
        served_model=str(getattr(response, "model", "") or "") or None,
        latency_ms=int(getattr(response, "latency_ms", 0) or (time.monotonic() - started) * 1000),
        tokens=int(getattr(response, "tokens_used", 0) or 0),
        input_tokens=getattr(response, "input_tokens", None),
        output_tokens=getattr(response, "output_tokens", None),
        cost_usd=float(getattr(response, "cost_usd", 0.0) or 0.0),
    )


async def compare(
    tenant_id: uuid.UUID, *, system_text: str, user_input: str, models: list[str], max_tokens: int | None = None
) -> dict[str, Any]:
    """Run one prompt and one input against each model, a few at a time; every model gets its own result."""
    names = validate_models(models)
    limit = validate_max_tokens(max_tokens)
    system = _input_text(system_text, "the prompt")
    question = _input_text(user_input)
    gate = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(*(run_one(tenant_id, name, system, question, limit, gate) for name in names))
    logger.info("prompt_compared", models=len(names), failed=sum(1 for r in results if not r.ok))
    return {
        "max_tokens": limit,
        "results": [result.to_dict() for result in results],
        "total_cost_usd": round(sum(result.cost_usd for result in results), 6),
    }


# ---------------------------------------------------------------------------
# Evaluation against a reference dataset
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    id: str
    input: str
    contains: tuple[str, ...] = ()
    not_contains: tuple[str, ...] = ()
    equals: str | None = None
    matches: str | None = None


def _texts(raw: Any, label: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > MAX_CHECK_ITEMS:
        raise ValueError(f"{label} is a list of at most {MAX_CHECK_ITEMS} texts")
    out = []
    for item in raw:
        if not isinstance(item, str) or not item.strip() or len(item) > MAX_CHECK_TEXT:
            raise ValueError(f"each entry of {label} is non-empty text of at most {MAX_CHECK_TEXT} characters")
        out.append(item.strip())
    return tuple(out)


def parse_cases(raw: Any) -> list[Case]:
    """The reference dataset: 1 to 25 cases, each an input with at least one expectation."""
    from core.prompts.parameters import bounded_pattern

    if not isinstance(raw, list) or not raw:
        raise ValueError("cases must be a non-empty list")
    if len(raw) > MAX_CASES:
        raise ValueError(f"an evaluation takes at most {MAX_CASES} cases")
    cases: list[Case] = []
    for index, item in enumerate(raw):
        label = f"case {index + 1}"
        if not isinstance(item, dict):
            raise ValueError(f"{label} must be an object")
        unknown = sorted(set(item) - {"id", "input", *CHECKS})
        if unknown:
            raise ValueError(f"{label} has unknown keys: {', '.join(unknown)}")
        equals = item.get("equals")
        if equals is not None and (not isinstance(equals, str) or len(equals) > MAX_INPUT_CHARS):
            raise ValueError(f"{label}: equals must be text")
        pattern = item.get("matches")
        if pattern is not None:
            if not isinstance(pattern, str) or not pattern:
                raise ValueError(f"{label}: matches must be a regular expression")
            pattern = bounded_pattern(pattern)
        case = Case(
            id=str(item.get("id") or index + 1),
            input=_input_text(item.get("input"), f"{label}: input"),
            contains=_texts(item.get("contains"), f"{label}: contains"),
            not_contains=_texts(item.get("not_contains"), f"{label}: not_contains"),
            equals=equals,
            matches=pattern,
        )
        if not (case.contains or case.not_contains or case.equals is not None or case.matches):
            raise ValueError(f"{label} needs at least one of {', '.join(CHECKS)}")
        cases.append(case)
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("case ids must be distinct")
    return cases


# A pattern is searched in at most this much of an answer: the matcher has no timeout.
_MATCH_WINDOW = 2000


def score(case: Case, output: str) -> list[str]:
    """The expectations of ``case`` that ``output`` does not meet (empty when it passes)."""
    failed: list[str] = []
    lowered = output.lower()
    failed += [f"contains:{text}" for text in case.contains if text.lower() not in lowered]
    failed += [f"not_contains:{text}" for text in case.not_contains if text.lower() in lowered]
    if case.equals is not None and output.strip() != case.equals.strip():
        failed.append("equals")
    if case.matches and not re.search(case.matches, output[:_MATCH_WINDOW]):
        failed.append("matches")
    return failed


@dataclass
class VariantReport:
    name: str
    passed: int = 0
    failed: int = 0
    errors: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    cases: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        total = self.passed + self.failed + self.errors
        return {
            "name": self.name,
            "cases": total,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "pass_rate": round(self.passed / total, 4) if total else None,
            "avg_latency_ms": int(self.latency_ms / total) if total else 0,
            "cost_usd": round(self.cost_usd, 6),
            "results": list(self.cases),
        }


async def evaluate(
    tenant_id: uuid.UUID,
    *,
    variants: list[tuple[str, str]],
    cases: list[Case],
    model: str,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Every variant answers every case with one model; each variant gets a pass rate and its failed cases.

    A case whose model call fails is an error, not a pass and not a failure
    of the prompt; the report keeps the three apart.
    """
    if not variants or len(variants) > MAX_VARIANTS:
        raise ValueError(f"an evaluation takes 1 to {MAX_VARIANTS} variants")
    if len({name for name, _text in variants}) != len(variants):
        raise ValueError("variant names must be distinct")
    [name] = validate_models([model])
    limit = validate_max_tokens(max_tokens)
    gate = asyncio.Semaphore(CONCURRENCY)
    reports: list[VariantReport] = []
    for variant_name, system_text in variants:
        system = _input_text(system_text, f"variant {variant_name}")
        answers = await asyncio.gather(*(run_one(tenant_id, name, system, case.input, limit, gate) for case in cases))
        report = VariantReport(name=variant_name)
        for case, answer in zip(cases, answers, strict=True):
            report.latency_ms += answer.latency_ms
            report.cost_usd += answer.cost_usd
            if not answer.ok:
                report.errors += 1
                report.cases.append({"id": case.id, "result": "error", "error_type": answer.error_type})
                continue
            failed = score(case, answer.output)
            if failed:
                report.failed += 1
                report.cases.append({"id": case.id, "result": "failed", "failed_checks": failed})
            else:
                report.passed += 1
                report.cases.append({"id": case.id, "result": "passed"})
        reports.append(report)
    logger.info("prompt_evaluated", variants=len(reports), cases=len(cases), model=name)
    return {"model": name, "max_tokens": limit, "variants": [report.to_dict() for report in reports]}
