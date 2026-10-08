# SPDX-License-Identifier: Apache-2.0
"""The content service framework: a registry of services, each with a schema, a guardrail profile and a dataset.

A service turns a validated input into a JSON output that matches its output
schema. The input text passes the tenant's input guardrails, the model answers
in JSON (one retry when the answer is not valid JSON or does not match the
schema), the service checks the answer against its sources, and the whole
structured output passes the output guardrails with the sources as context, so
a grounding detector can judge it. Knowledge-base sources pass the retrieval
stage before they enter the prompt, and with pre-model pseudonymisation on the
prompt is pseudonymised before it leaves. A transformed input or output
travels transformed; a blocked stage, or a transform that cannot be mapped
back onto the fields, is a refusal. Behind ``AGENTICORG_CONTENT_SERVICES_ENABLED``.
"""

from __future__ import annotations

import dataclasses
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ValidationError

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
    # A human-readable rendering and its inverse, for callers that show one text. The runner does not use
    # them for guardrails: it screens and transforms every field of the output (guard_structure).
    rendered: Callable[[dict[str, Any]], str]
    apply_text: Callable[[dict[str, Any], str], dict[str, Any]]
    resolve_sources: Callable[[uuid.UUID, BaseModel, list[str] | None], Awaitable[list[Source]]]
    # Called with the output when the output guardrails changed it, so a service whose output carries
    # derived parts (a validation result, a rendering) recomputes them from what will be returned.
    after_output_guard: Callable[[dict[str, Any]], dict[str, Any]] | None = None

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


async def _complete(
    tenant_id: uuid.UUID,
    model: str | None,
    messages: list[dict[str, str]],
    max_tokens: int,
    pseudonymiser: Any = None,
) -> Any:
    """One model call through the direct router as the tenant (the seam the tests replace)."""
    from core.llm.router import llm_router

    extra = {"pseudonymiser": pseudonymiser} if pseudonymiser is not None else {}
    return await llm_router.complete(
        messages, model_override=model or None, max_tokens=max_tokens, tenant_id=str(tenant_id), **extra
    )


async def open_pseudonymiser(tenant_id: uuid.UUID, service: str) -> Any:
    """The tenant's pseudonym session for this call, or None when pre-model pseudonymisation is off.

    A setting or map that cannot be read is a refusal, never a raw prompt.
    """
    from core.pii import pseudonymiser as pseudonymisation

    try:
        if not await pseudonymisation.pseudonymisation_enabled(tenant_id):
            return None
        # A server-generated case for this one call; nothing a client names.
        return await pseudonymisation.open_session(
            str(tenant_id), pseudonymisation.case_key(f"content-{service}-{uuid.uuid4()}")
        )
    except pseudonymisation.PseudonymisationError as exc:
        logger.warning("content_pseudonymisation_unavailable", service=service, reason=exc.reason)
        raise ContentError(
            503, "pseudonymisation_unavailable", "Pseudonymisation is on and could not be applied"
        ) from None


def with_pseudonym_guidance(messages: list[dict[str, str]], pseudonymiser: Any) -> list[dict[str, str]]:
    """The messages with the pseudonym guidance added to the system prompt when a session is open."""
    if pseudonymiser is None:
        return messages
    from core.pii.pseudonymiser import with_model_guidance

    out = [dict(message) for message in messages]
    for message in out:
        if message.get("role") == "system":
            message["content"] = with_model_guidance(str(message.get("content") or ""))
            return out
    return [{"role": "system", "content": with_model_guidance("")}, *out]


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
    pseudonymiser: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The model's JSON answer matching ``schema``, after at most one retry; the usage beside it.

    With ``pseudonymiser`` the router pseudonymises every message before it
    leaves and the answer is restored before it is parsed.
    """
    from core.pii.pseudonymiser import PseudonymisationError

    complete = complete or _complete
    extra = {"pseudonymiser": pseudonymiser} if pseudonymiser is not None else {}
    model = str(getattr(settings, "content_services_model", "") or "") or None
    tokens = 0
    used_model = model or ""
    attempt = list(messages)
    last_errors: list[str] = []
    for round_index in range(RETRIES + 1):
        try:
            response = await complete(tenant_id, model, attempt, max_tokens, **extra)
        except PseudonymisationError as exc:
            logger.warning("content_pseudonymisation_unavailable", reason=exc.reason)
            raise ContentError(
                503, "pseudonymisation_unavailable", "Pseudonymisation is on and could not be applied"
            ) from None
        # enterprise-gate: broad-except-ok reason=model-provider-boundary-returns-an-explicit-refusal
        except Exception as exc:  # noqa: BLE001 - the provider boundary; the reason is the type only
            logger.warning("content_model_failed", error_type=type(exc).__name__)
            raise ContentError(502, "model_failed", "The model did not answer") from None
        content = getattr(response, "content", response if isinstance(response, str) else "")
        if pseudonymiser is not None:
            content = pseudonymiser.restore_text(str(content or ""))
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
        # A rule may redact a text to nothing: only a missing text means unchanged.
        "text": result.text if isinstance(result.text, str) else text,
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
    # The input as it reached the model (after the input guardrails); kept out of to_dict.
    input: BaseModel | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "output": self.output,
            "sources": self.sources,
            "guardrails": self.guardrails,
            "model": self.model,
        }


def _texts(value: Any) -> list[str]:
    """Every non-empty string in a JSON-like value, in order (keys are not text)."""
    parts: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, str):
            if item:
                parts.append(item)
        elif isinstance(item, dict):
            for child in item.values():
                walk(child)
        elif isinstance(item, list | tuple):
            for child in item:
                walk(child)

    walk(value)
    return parts


def _replace_texts(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, dict):
        return {key: _replace_texts(child, mapping) for key, child in value.items()}
    if isinstance(value, list):
        return [_replace_texts(child, mapping) for child in value]
    if isinstance(value, tuple):
        return tuple(_replace_texts(child, mapping) for child in value)
    return value


def input_text(payload: BaseModel) -> str:
    """What the input guardrails screen: every text field of the input, joined."""
    return structured_text(payload.model_dump())


def structured_text(value: Any) -> str:
    """What a guardrail stage screens for a structured value: every string in it, joined."""
    return "\n".join(_texts(value))


async def guard_structure(
    stage: str, value: Any, *, tenant_id: uuid.UUID, service: str, context: list[str] | None = None
) -> tuple[Any, dict[str, Any]]:
    """Screen every string of ``value`` as one text; a transform is applied field by field.

    The whole text is screened once, so a rule sees every field together. When
    the stage changes the text, each distinct string is screened on its own and
    replaced by what the stage made of it, and the rebuilt value is screened
    again: if that still changes, the transform could not be mapped back onto
    the fields and the call is refused rather than sending or returning the
    original text.
    """
    text = structured_text(value)
    screened = await guard(stage, text, tenant_id=tenant_id, service=service, context=context)
    if not screened.get("applied") or screened["text"] == text:
        return value, screened
    mapping: dict[str, str] = {}
    for part in dict.fromkeys(_texts(value)):
        mapping[part] = (await guard(stage, part, tenant_id=tenant_id, service=service, context=context))["text"]
    updated = _replace_texts(value, mapping)
    again = structured_text(updated)
    recheck = await guard(stage, again, tenant_id=tenant_id, service=service, context=context)
    if recheck.get("applied") and recheck["text"] != again:
        logger.warning("content_guardrail_transform_unmappable", stage=stage, service=service)
        raise ContentError(
            422,
            "guardrail_transform_unmappable",
            f"The {stage} guardrail changed text in a way that cannot be mapped back onto its fields",
        )
    return updated, screened


async def guard_input(service: Service, tenant_id: uuid.UUID, payload: BaseModel) -> tuple[BaseModel, dict[str, Any]]:
    """The input after the input guardrails, rebuilt from the transformed text when a rule changed it."""
    value = payload.model_dump()
    updated, screened = await guard_structure("input", value, tenant_id=tenant_id, service=service.name)
    if updated is value:
        return payload, screened
    try:
        return service.input_model.model_validate(updated), screened
    except ValidationError:
        logger.warning("content_guardrail_transform_unmappable", stage="input", service=service.name)
        raise ContentError(
            422,
            "guardrail_transform_unmappable",
            "The input guardrail changed the input so that it no longer fits the service's input schema",
        ) from None


async def guard_sources(
    tenant_id: uuid.UUID, service: str, sources: list[Source]
) -> tuple[list[Source], dict[str, Any] | None]:
    """Knowledge-base sources pass the retrieval stage: a withheld one is dropped, a transformed one replaced.

    Inline sources are part of the request and pass the input stage instead.
    Returns the sources that may reach the model and what the stage did (None
    when there was no knowledge source). Refused when every source is withheld.
    """
    from core.governance.guardrails.hooks import guard_retrieval_texts

    knowledge = [index for index, source in enumerate(sources) if source.origin == "knowledge"]
    if not knowledge:
        return sources, None
    texts = await guard_retrieval_texts(
        [sources[index].text for index in knowledge], tenant_id=str(tenant_id), use_case=f"content.{service}"
    )
    guarded: dict[int, str | None] = dict(zip(knowledge, texts, strict=True))
    out: list[Source] = []
    withheld = transformed = 0
    for index, source in enumerate(sources):
        if index not in guarded:
            out.append(source)
            continue
        text = guarded[index]
        if text is None:
            withheld += 1
            continue
        if text != source.text:
            transformed += 1
            source = dataclasses.replace(source, text=text)
        out.append(source)
    if not out:
        raise ContentError(422, "sources_withheld", "The retrieval guardrails withheld every source")
    return out, {"sources": len(knowledge), "withheld": withheld, "transformed": transformed}


async def run(
    service: Service,
    tenant_id: uuid.UUID,
    payload: BaseModel,
    *,
    domains: list[str] | None = None,
    complete: Any = None,
) -> Run:
    """Input guardrails, the sources (knowledge ones through the retrieval stage), the model, the service's own
    checks, then output guardrails over the whole output with the sources as context."""
    if not enabled():
        raise ContentError(404, "content_services_disabled", "Content services are off for this deployment")
    guardrails: dict[str, Any] = {}
    if service.guardrails.input:
        payload, screened_input = await guard_input(service, tenant_id, payload)
        guardrails["input"] = {k: v for k, v in screened_input.items() if k != "text"}
    sources = await service.resolve_sources(tenant_id, payload, domains)
    sources, retrieval = await guard_sources(tenant_id, service.name, sources)
    if retrieval is not None:
        guardrails["retrieval"] = retrieval
    pseudonymiser = await open_pseudonymiser(tenant_id, service.name)
    messages = with_pseudonym_guidance(service.messages(payload, sources), pseudonymiser)
    answer, usage = await ask_model(
        tenant_id, messages, service.output_schema, complete=complete, pseudonymiser=pseudonymiser
    )
    output = service.finish(payload, sources, answer)
    if service.guardrails.output:
        context = [source.text for source in sources] if service.guardrails.grounded and sources else None
        guarded, screened = await guard_structure(
            "output", output, tenant_id=tenant_id, service=service.name, context=context
        )
        if guarded is not output and service.after_output_guard is not None:
            guarded = service.after_output_guard(guarded)
        output = guarded
        guardrails["output"] = {k: v for k, v in screened.items() if k != "text"}
    return Run(
        service=service.name,
        output=output,
        sources=[source.describe() for source in sources],
        guardrails=guardrails,
        model=usage,
        input=payload,
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
