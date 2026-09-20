# SPDX-License-Identifier: Apache-2.0
"""The Screening Disposition agent.

For one screening hit it proposes a disposition for an analyst to accept or override:

1. **gather evidence** - re-screen the subject through the tool gateway (read tools
   ``screen_person`` / ``screen_business`` only) to confirm the provider still returns the hit
   and to cite fresh records; a provider without the capability, or an error, falls back to the
   screening result the case already holds;
2. **compare** the subject with the hit on name, date of birth, nationality, address and
   associated entities (``comparison.py``);
3. **propose** an outcome and a confidence band by fixed rules - never from the model;
4. **explain** - the model writes the rationale from comparison results and tokens only, behind
   the untrusted-content guard and pseudonymisation; an unusable rationale is replaced by a
   template rationale built from the comparisons;
5. **assemble** a ``screening_disposition`` with ``review: null``, validated against its schema
   and against the records it cites.

There is no automatic closure in any configuration: the agent's tool set holds read tools only,
nothing in this package closes, clears or dismisses a hit, and every disposition leaves the agent
unreviewed. A human records the review with :func:`core.agents.screening_disposition.review.apply_review`.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from prometheus_client import Counter
from pydantic import ValidationError

from connectors.framework.verification_provider import (
    Address,
    BusinessSubject,
    Deadline,
    Identifier,
    NotAvailable,
    PersonSubject,
    ProviderError,
    ScreeningHit,
    ScreeningResult,
    ScreenOptions,
    VerificationProvider,
)
from core.agents.business_underwriter.prompts import PromptIntegrityError, PromptRecord, load_versioned_prompt
from core.agents.case_model_call import call_case_model
from core.agents.screening_disposition.comparison import IDENTIFIERS, compare, propose, template_rationale
from core.domain_schemas import DomainSchemaError, validate
from core.extraction import UntrustedContentLeakError, UntrustedTextRegistry, build_model_context
from core.tool_gateway.provider_gateway import ProviderToolGateway, ToolAuthorizer, ToolRefusedError

logger = structlog.get_logger()

AGENT_NAME = "screening_disposition"
AGENT_VERSION = "1.0.0"
#: Everything this agent may call: read-only screening. No tool closes, clears or dismisses a hit.
TOOL_SET: frozenset[str] = frozenset({"screen_person", "screen_business"})
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PINNED: dict[tuple[str, str], str] = {
    (
        "screening_disposition.rationale",
        "1.0.0",
    ): "sha256:2cacd15a4ce096809e5c65666efec8743c2b222847c51fb7e7b29efe62cd7ab2",
}
RATIONALE = ("screening_disposition.rationale", "1.0.0")
MAX_RATIONALE_CHARS = 1200
_OUTCOME_PHRASES = {
    "true_match": "true match",
    "false_positive": "false positive",
    "insufficient_information": "insufficient information",
}
_FORBIDDEN = re.compile(
    r"\b(closed|closing|closure|cleared|dismiss(ed|al)?|auto[- ]?clos\w*|close\s+(this|the|it|out))\b"
    r"|\[\[|\]\]|untrusted_ref",
    re.IGNORECASE,
)

_dispositions_total = Counter(
    "agenticorg_screening_dispositions_proposed_total",
    "Screening dispositions proposed, by proposed outcome and confidence band",
    ["outcome", "band"],
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class DispositionConfig:
    llm_model: str = ""
    llm_provider: str | None = None
    call_timeout_s: float = 30.0
    #: Re-screen the subject to confirm the hit and cite fresh records.
    refresh: bool = True
    prompt: tuple[str, str] = RATIONALE


@dataclass
class DispositionDependencies:
    provider: VerificationProvider
    authorizer: ToolAuthorizer | None = None
    clock: Callable[[], datetime] = _utc_now
    pseudonym_store: Any = None


@dataclass
class DispositionOutcome:
    status: str
    case_id: str
    hit_id: str
    run_id: str
    failure_reason: str = ""
    disposition: dict[str, Any] | None = None
    rationale_source: str = ""
    rationale_rejected: str = ""
    model_confidence: float | None = None
    hit_confirmed: bool | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    prompt: dict[str, str] | None = None
    pseudonymised: bool = False

    def case_record(self) -> dict[str, Any]:
        return {
            "agent": AGENT_NAME,
            "agent_version": AGENT_VERSION,
            "run_id": self.run_id,
            "hit_id": self.hit_id,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "prompt": self.prompt,
            "rationale_source": self.rationale_source,
            "rationale_rejected": self.rationale_rejected,
            "model_confidence": self.model_confidence,
            "hit_confirmed": self.hit_confirmed,
            "pseudonymised": self.pseudonymised,
            "tool_calls": self.tool_calls,
        }


class _RunFailedError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def load_prompt(prompt_id: str, version: str) -> PromptRecord:
    return load_versioned_prompt(PROMPTS_DIR, PINNED, prompt_id, version)


def _same_entry(left: ScreeningHit, right: ScreeningHit) -> bool:
    return {e.record_id for e in left.evidence} & {e.record_id for e in right.evidence} != set() and (
        left.list_type is right.list_type
    )


def accept_rationale(
    output: Mapping[str, Any] | None,
    outcome: str,
    *,
    restore: Callable[[str], str],
    contains_untrusted: Callable[[str], bool],
) -> tuple[str | None, str]:
    """``(rationale, "")`` for a usable model rationale, else ``(None, reason)``."""
    raw = output.get("rationale") if isinstance(output, Mapping) else None
    if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_RATIONALE_CHARS:
        return None, "rationale_invalid"
    text = " ".join(restore(raw).split())
    if _FORBIDDEN.search(text):
        return None, "rationale_invalid"
    lowered = text.lower()
    if any(phrase in lowered for key, phrase in _OUTCOME_PHRASES.items() if key != outcome):
        return None, "rationale_contradicts_outcome"
    if contains_untrusted(text):
        return None, "rationale_contains_untrusted_text"
    return text, ""


async def run_screening_disposition(
    *,
    tenant_id: str,
    case_id: str,
    screening_result: ScreeningResult | Mapping[str, Any],
    hit_id: str,
    subject: Mapping[str, Any],
    associated_entities: Sequence[str],
    config: DispositionConfig,
    deps: DispositionDependencies,
    run_id: str | None = None,
) -> DispositionOutcome:
    """Propose a disposition for ``hit_id`` in ``screening_result``. Never raises for provider or grant failures.

    ``subject`` is the screened party (``kind``, ``name``, ``date_of_birth``, ``nationalities``,
    ``address``, ``identifiers``); ``associated_entities`` are names linked to it in the case (the
    business and its other parties).
    """
    run_id = run_id or f"sd_{uuid.uuid4().hex}"
    outcome = DispositionOutcome(status="failed", case_id=case_id, hit_id=hit_id, run_id=run_id)
    gateway = ProviderToolGateway(
        provider=deps.provider, agent=AGENT_NAME, tool_set=TOOL_SET, authorizer=deps.authorizer, clock=deps.clock
    )
    untrusted = UntrustedTextRegistry()
    try:
        prompt = load_prompt(*config.prompt)
        outcome.prompt = prompt.to_dict()
        result = (
            screening_result
            if isinstance(screening_result, ScreeningResult)
            else ScreeningResult.model_validate(screening_result)
        )
        hit = next((h for h in result.hits if h.hit_id == hit_id), None)
        if hit is None:
            raise _RunFailedError("hit_not_in_screening_result")
        known_records = {e.record_id for e in result.evidence} | {e.record_id for h in result.hits for e in h.evidence}

        if config.refresh:
            hit, outcome.hit_confirmed = await _refresh(gateway, case_id, hit, subject, config)
        comparisons = compare(subject, hit, associated=associated_entities)
        proposed, band = propose(comparisons)
        if outcome.hit_confirmed is False:
            proposed, band = "insufficient_information", "low"

        texts = [str(subject.get("name") or ""), *associated_entities, hit.matched_name, *hit.aliases]
        texts += [*hit.associated_entities, hit.source.name, hit.source.authority or ""]
        for comparison in comparisons:
            texts += [comparison.subject_value or "", comparison.hit_value or ""]
        untrusted.register_all(t for t in texts if t)

        facts = {
            "hit": {"list_type": hit.list_type.value, "entry_kind": hit.entry_kind.value if hit.entry_kind else None},
            "comparisons": [{"identifier": c.identifier, "result": c.result} for c in comparisons],
            "proposed_outcome": proposed,
            "confidence_band": band,
            "hit_confirmed_on_rescreen": outcome.hit_confirmed,
        }
        model = await call_case_model(
            agent=AGENT_NAME,
            tenant_id=tenant_id,
            run_id=run_id,
            system_prompt=prompt.text,
            context=build_model_context(facts, untrusted=untrusted),
            untrusted=untrusted,
            llm_model=config.llm_model,
            llm_provider=config.llm_provider,
            pseudonym_store=deps.pseudonym_store,
        )
        outcome.pseudonymised = model.pseudonymised
        rationale, rejected = (None, model.failure)
        if model.output is not None:
            rationale, rejected = accept_rationale(
                model.output, proposed, restore=model.restore, contains_untrusted=lambda t: bool(untrusted.find(t))
            )
            raw_confidence = model.output.get("confidence")
            if (
                isinstance(raw_confidence, int | float)
                and not isinstance(raw_confidence, bool)
                and 0 <= raw_confidence <= 1
            ):
                outcome.model_confidence = float(raw_confidence)
        outcome.rationale_source = "model" if rationale else "template"
        outcome.rationale_rejected = rejected
        if rationale is None:
            rationale = template_rationale(comparisons, proposed)
            if outcome.hit_confirmed is False:
                rationale += " The provider no longer returned this hit when the subject was screened again."

        disposition: dict[str, Any] = {
            "schema_version": "1.0.0",
            "disposition_id": "dsp-" + hashlib.sha256(f"{case_id}\x1f{hit_id}".encode()).hexdigest()[:24],
            "case_id": case_id,
            "screening_id": result.screening_id,
            "hit_id": hit_id,
            "proposed_by": {"agent": AGENT_NAME, "agent_version": AGENT_VERSION, "prompt_version": prompt.version},
            "proposed_at": deps.clock().isoformat(),
            "comparisons": [c.to_dict() for c in comparisons],
            "proposed_outcome": proposed,
            "confidence_band": band,
            "rationale": rationale,
            "evidence": [e.model_dump(mode="json") for e in hit.evidence],
            "review": None,
        }
        try:
            validate("screening_disposition", disposition)
        except DomainSchemaError as exc:
            logger.error("screening_disposition_invalid", case_id=case_id, errors=exc.errors[:5])
            raise _RunFailedError("disposition_schema_invalid") from exc
        cited = {e["record_id"] for e in disposition["evidence"]} | {
            e["record_id"] for c in disposition["comparisons"] for e in c["evidence"]
        }
        retrieved = known_records | {record_id for _, record_id, _ in gateway.retrieved_evidence}
        if not cited <= retrieved:
            raise _RunFailedError("disposition_evidence_untraced")
        if [c["identifier"] for c in disposition["comparisons"]] != list(IDENTIFIERS):
            raise _RunFailedError("disposition_comparisons_incomplete")

        outcome.disposition = disposition
        outcome.status = "completed"
        _dispositions_total.labels(outcome=proposed, band=band).inc()
    except ToolRefusedError as exc:
        outcome.failure_reason = f"tool_refused:{exc.reason}"
    except UntrustedContentLeakError as exc:
        outcome.failure_reason = exc.reason
    except _RunFailedError as exc:
        outcome.failure_reason = exc.reason
    except PromptIntegrityError as exc:
        logger.error("screening_disposition_prompt_refused", detail=str(exc))
        outcome.failure_reason = "prompt_integrity_failed"
    finally:
        outcome.tool_calls = [record.to_dict() for record in gateway.records]
    logger.info(
        "screening_disposition_run_finished",
        case_id=case_id,
        hit_id=hit_id,
        status=outcome.status,
        failure_reason=outcome.failure_reason,
    )
    return outcome


async def _refresh(
    gateway: ProviderToolGateway, case_id: str, hit: ScreeningHit, subject: Mapping[str, Any], config: DispositionConfig
) -> tuple[ScreeningHit, bool | None]:
    """Re-screen the subject. Returns the refreshed hit and whether it was confirmed (``None`` if not re-screened)."""
    options = ScreenOptions(
        idempotency_key=f"{case_id}.disposition.{hit.hit_id}"[:128], list_types=frozenset({hit.list_type})
    )
    deadline = Deadline.after(config.call_timeout_s)
    address = subject.get("address")
    identifiers = tuple(Identifier.model_validate(i) for i in subject.get("identifiers") or ())
    try:
        if subject.get("kind") == "business":
            refreshed = await gateway.screen_business(
                BusinessSubject(
                    legal_name=str(subject["name"]),
                    jurisdiction=subject.get("jurisdiction"),
                    identifiers=identifiers,
                    address=Address.model_validate(address) if address else None,
                ),
                options,
                deadline=deadline,
            )
        else:
            refreshed = await gateway.screen_person(
                PersonSubject(
                    full_name=str(subject["name"]),
                    date_of_birth=subject.get("date_of_birth"),
                    nationalities=tuple(subject.get("nationalities") or ()),
                    address=Address.model_validate(address) if address else None,
                    identifiers=identifiers,
                ),
                options,
                deadline=deadline,
            )
    except ProviderError as exc:
        logger.warning("screening_disposition_refresh_failed", case_id=case_id, reason=exc.reason)
        return hit, None
    except ValidationError:
        logger.warning("screening_disposition_refresh_failed", case_id=case_id, reason="invalid_query")
        return hit, None
    if isinstance(refreshed, NotAvailable):
        return hit, None
    for candidate in refreshed.hits:
        if _same_entry(hit, candidate):
            return hit.model_copy(update={"evidence": candidate.evidence}), True
    return hit, False
