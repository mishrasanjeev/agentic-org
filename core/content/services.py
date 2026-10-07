# SPDX-License-Identifier: Apache-2.0
"""The content service framework: a registry of services, each with a schema, a guardrail profile and a dataset.

A service turns a validated input into a JSON output that matches its output
schema. The input text passes the tenant's input guardrails, the model answers
in JSON (one retry when the answer is not valid JSON or does not match the
schema), the service checks the answer against its sources, and the rendered
output passes the output guardrails with the sources as context, so a
grounding detector can judge it. A blocked stage is a refusal, never a
silently changed answer. Behind ``AGENTICORG_CONTENT_SERVICES_ENABLED``.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from jsonschema import Draft202012Validator
from pydantic import BaseModel

from core.config import settings
from core.content.sources import Source

logger = structlog.get_logger()

MAX_TOKENS = 3_000
RETRIES = 1
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


class ContentError(Exception):
    """A refused or failed service call, with the HTTP status it maps to."""

    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def enabled() -> bool:
    return bool(getattr(settings, "content_services_enabled", False))


@dataclass(frozen=True)
class GuardrailProfile:
    """Which guardrail stages a service passes, and whether its output is judged against its sources."""

    input: bool = True
    output: bool = True
    grounded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"input": self.input, "output": self.output, "grounded": self.grounded}


@dataclass
class Service:
    name: str
    title: str
    description: str
    input_model: type[BaseModel]
    output_schema: dict[str, Any]
    guardrails: GuardrailProfile
    dataset_name: str
    dataset_cases: list[dict[str, Any]]
    messages: Callable[[BaseModel, list[Source]], list[dict[str, str]]]
    finish: Callable[[BaseModel, list[Source], dict[str, Any]], dict[str, Any]]
    rendered: Callable[[dict[str, Any]], str]
    apply_text: Callable[[dict[str, Any], str], dict[str, Any]]
    resolve_sources: Callable[[uuid.UUID, BaseModel, list[str] | None], Awaitable[list[Source]]]
    max_tokens: int = MAX_TOKENS  # the completion budget; a service whose output scales with its input sets its own

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
            "output_schema": self.output_schema,
            "guardrail_profile": self.guardrails.to_dict(),
            "dataset": {"name": self.dataset_name, "cases": len(self.dataset_cases)},
            "endpoint": f"/content/{self.name}",
        }


REGISTRY: dict[str, Service] = {}


def register(service: Service) -> Service:
    REGISTRY[service.name] = service
    return service


def get(name: str) -> Service:
    service = REGISTRY.get(name)
    if service is None:
        raise ContentError(404, "service_unknown", f"No content service named {name!r}")
    return service


def catalogue() -> list[dict[str, Any]]:
    return [service.describe() for service in REGISTRY.values()]


# ── The model ─────────────────────────────────────────────────────────────────


async def _complete(tenant_id: uuid.UUID, model: str | None, messages: list[dict[str, str]], max_tokens: int) -> Any:
    """One model call through the direct router as the tenant (the seam the tests replace)."""
    from core.llm.router import llm_router

    return await llm_router.complete(
        messages, model_override=model or None, max_tokens=max_tokens, tenant_id=str(tenant_id)
    )


def parse_json(text: str) -> dict[str, Any] | None:
    """The JSON object in a model answer (fences and leading prose tolerated), or None."""
    if not isinstance(text, str) or not text.strip():
        return None
    candidate = text.strip()
    fenced = _FENCE_RE.match(candidate)
    if fenced:
        candidate = fenced.group(1)
    try:
        value = json.loads(candidate)
        return value if isinstance(value, dict) else None
    except ValueError:
        pass
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(candidate[start : end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def schema_errors(value: Any, schema: dict[str, Any]) -> list[str]:
    """Every way ``value`` fails ``schema``, as ``path: message`` lines, bounded."""
    errors = []
    for error in sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: list(e.absolute_path)):
        path = "/".join(str(p) for p in error.absolute_path) or "$"
        errors.append(f"{path}: {error.message}"[:200])
        if len(errors) >= 10:
            break
    return errors


async def ask_model(
    tenant_id: uuid.UUID,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    *,
    complete: Any = None,
    max_tokens: int = MAX_TOKENS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The model's JSON answer matching ``schema``, after at most one retry; the usage beside it."""
    complete = complete or _complete
    model = str(getattr(settings, "content_services_model", "") or "") or None
    tokens = 0
    used_model = model or ""
    attempt = list(messages)
    last_errors: list[str] = []
    for round_index in range(RETRIES + 1):
        try:
            response = await complete(tenant_id, model, attempt, max_tokens)
        # enterprise-gate: broad-except-ok reason=model-provider-boundary-returns-an-explicit-refusal
        except Exception as exc:  # noqa: BLE001 - the provider boundary; the reason is the type only
            logger.warning("content_model_failed", error_type=type(exc).__name__)
            raise ContentError(502, "model_failed", "The model did not answer") from None
        content = getattr(response, "content", response if isinstance(response, str) else "")
        tokens += int(getattr(response, "tokens_used", 0) or 0)
        used_model = str(getattr(response, "model", used_model) or used_model)
        parsed = parse_json(str(content))
        last_errors = ["$: not a JSON object"] if parsed is None else schema_errors(parsed, schema)
        if parsed is not None and not last_errors:
            return parsed, {"model": used_model, "tokens": tokens, "retries": round_index}
        attempt = attempt + [
            {"role": "assistant", "content": str(content)[:4000]},
            {
                "role": "user",
                "content": "That answer was not valid. Return only one JSON object matching the schema; "
                + "problems: "
                + "; ".join(last_errors[:5]),
            },
        ]
    raise ContentError(502, "model_output_invalid", "The model's answer did not match the service schema", last_errors)


# ── Guardrails ────────────────────────────────────────────────────────────────


async def guard(stage: str, text: str, *, tenant_id: uuid.UUID, service: str, context: list[str] | None = None) -> dict:
    """Run a guardrail stage over ``text``: the text that travels on, and what the rules did."""
    from core.governance.guardrails.hooks import guard_text
    from core.governance.guardrails.schema import GuardrailBlocked

    try:
        result = await guard_text(stage, text, tenant_id=str(tenant_id), use_case=f"content.{service}", context=context)
    except GuardrailBlocked as exc:
        raise ContentError(422, "guardrail_blocked", exc.reason, exc.to_error()) from None
    if result is None:
        return {"text": text, "findings": 0, "flagged": False, "applied": False}
    return {
        "text": result.text if result.text else text,
        "findings": int(result.findings),
        "flagged": bool(result.flagged),
        "applied": True,
        "correlation_id": result.correlation_id,
    }


# ── Running a service ─────────────────────────────────────────────────────────


@dataclass
class Run:
    service: str
    output: dict[str, Any]
    sources: list[dict[str, Any]] = field(default_factory=dict)
    guardrails: dict[str, Any] = field(default_factory=dict)
    model: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "output": self.output,
            "sources": self.sources,
            "guardrails": self.guardrails,
            "model": self.model,
        }


def input_text(payload: BaseModel) -> str:
    """What the input guardrails screen: every text field of the input, joined."""
    parts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list | tuple):
            for item in value:
                walk(item)

    walk(payload.model_dump())
    return "\n".join(part for part in parts if part)


async def run(
    service: Service,
    tenant_id: uuid.UUID,
    payload: BaseModel,
    *,
    domains: list[str] | None = None,
    complete: Any = None,
) -> Run:
    """Input guardrails, the model, the service's own checks, then output guardrails with the sources as context."""
    if not enabled():
        raise ContentError(404, "content_services_disabled", "Content services are off for this deployment")
    sources = await service.resolve_sources(tenant_id, payload, domains)
    guardrails: dict[str, Any] = {}
    if service.guardrails.input:
        guardrails["input"] = {
            k: v
            for k, v in (await guard("input", input_text(payload), tenant_id=tenant_id, service=service.name)).items()
            if k != "text"
        }
    messages = service.messages(payload, sources)
    answer, usage = await ask_model(
        tenant_id, messages, service.output_schema, complete=complete, max_tokens=service.max_tokens
    )
    output = service.finish(payload, sources, answer)
    if service.guardrails.output:
        context = [source.text for source in sources] if service.guardrails.grounded and sources else None
        screened = await guard(
            "output", service.rendered(output), tenant_id=tenant_id, service=service.name, context=context
        )
        if screened.get("applied") and screened["text"] != service.rendered(output):
            output = service.apply_text(output, screened["text"])
        guardrails["output"] = {k: v for k, v in screened.items() if k != "text"}
    return Run(
        service=service.name,
        output=output,
        sources=[source.describe() for source in sources],
        guardrails=guardrails,
        model=usage,
    )


def source_block(sources: list[Source], *, limit: int = 12_000) -> str:
    """The sources as the model sees them, each under its id, bounded."""
    lines: list[str] = []
    budget = limit
    for source in sources:
        text = source.text[: max(0, min(len(source.text), budget))]
        budget -= len(text)
        lines.append(f"[{source.id}] {source.title}\n{text}")
        if budget <= 0:
            break
    return "\n\n".join(lines)


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def quote_in(quote: str, text: str) -> bool:
    """Whether ``quote`` appears in ``text``, ignoring case and whitespace differences."""
    needle = normalise(quote)
    return bool(needle) and needle in normalise(text)
