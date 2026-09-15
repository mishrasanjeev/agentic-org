# SPDX-License-Identifier: Apache-2.0
"""The Business Onboarding Underwriter.

One run investigates one business application and hands off a cited memo for a human decision:

1. **resolve** the application to a registry record (a unique, strong match only);
2. **verify** it - start, then poll while the provider returns ``Pending``, within a deadline;
3. **reconcile ownership** against the declared owners (``missing_owner`` / ``undeclared_owner``);
4. **screen every party** - the business, its graph owners, current officers and declared owners;
5. **web presence** - pages are parsed only by the sandboxed untrusted-content extractor;
6. **evaluate** the deterministic policy over evidence fields computed from provider data;
7. **narrate** - the model writes section summaries from statuses, codes and counts only, behind
   the untrusted-content guard (and pseudonymisation when ``pseudonymisation.pre_model`` is on);
8. **assemble** the ``underwriting_memo`` and check it against its schema and against every
   record the provider actually returned, then hand off.

Tool calls are made by this code from application and provider data, never chosen by the model:
the model has no tools. The agent's tool set holds read tools only, so it cannot approve,
decline, close or file anything, and every call passes the run's grant check in the tool gateway.
A capability the provider does not offer yields a ``not_available`` section, not a failure.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from prometheus_client import Counter
from pydantic import ValidationError

from connectors.framework.verification_provider import (
    Address,
    BusinessCandidate,
    BusinessQuery,
    BusinessVerification,
    Deadline,
    DeclaredBusiness,
    Identifier,
    NotAvailable,
    OwnershipGraph,
    Pending,
    ProviderError,
    ScreeningResult,
    ScreenOptions,
    VerificationProvider,
    VerifyOptions,
)
from core.agents.business_underwriter import facts
from core.agents.business_underwriter.memo import (
    Investigation,
    NarrativeReport,
    ScreenedParty,
    Step,
    WebPageFacts,
    accept_narrative,
    build_memo,
    build_sections,
    iter_memo_evidence,
    missing_items,
    narrative_context,
    recommend,
)
from core.agents.business_underwriter.prompts import NARRATIVE, PromptIntegrityError, PromptRecord, load_prompt
from core.agents.business_underwriter.reconciliation import DEFAULT_THRESHOLD_PCT, reconcile
from core.agents.case_model_call import call_case_model
from core.domain_schemas import DomainSchemaError, validate
from core.extraction import (
    CONTENT_TYPES,
    ExcerptStore,
    InMemoryExcerptStore,
    SourceKind,
    UntrustedContentLeakError,
    UntrustedTextRegistry,
    build_model_context,
    extract,
)
from core.policy import Policy, PolicyResult, evaluate
from core.policy.document import policy_result_document
from core.tool_gateway.provider_gateway import (
    READ_TOOLS,
    ProviderToolGateway,
    ToolAuthorizer,
    ToolRefusedError,
)

logger = structlog.get_logger()

AGENT_NAME = "business_underwriter"
AGENT_VERSION = "1.0.0"
#: Everything this agent may call. Read tools only; there is no decision, filing or closing tool.
TOOL_SET: frozenset[str] = frozenset(READ_TOOLS)
MIN_MATCH_SCORE = 0.9

_runs_total = Counter(
    "agenticorg_case_agent_runs_total",
    "Governed case agent runs, by agent and outcome",
    ["agent", "outcome"],
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class UnderwriterConfig:
    policy: Policy
    ownership_threshold_pct: float = DEFAULT_THRESHOLD_PCT
    llm_model: str = ""
    llm_provider: str | None = None
    call_timeout_s: float = 30.0
    verification_timeout_s: float = 120.0
    max_poll_interval_s: float = 5.0
    extraction_timeout_s: float = 10.0
    #: ``None`` uses the extractor's platform default (seccomp required on Linux).
    require_os_isolation: bool | None = None
    prompt: tuple[str, str] = NARRATIVE


@dataclass
class UnderwriterDependencies:
    provider: VerificationProvider
    #: The run's grant check (PRD F-1). ``None`` means calls are not grant-checked.
    authorizer: ToolAuthorizer | None = None
    excerpts: ExcerptStore = field(default_factory=InMemoryExcerptStore)
    clock: Callable[[], datetime] = _utc_now
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    #: Pseudonym map store; ``None`` uses the database store when the flag is on.
    pseudonym_store: Any = None


@dataclass
class UnderwritingOutcome:
    """The hand-off. ``status`` is ``completed`` (memo ready for a human decision) or ``failed``."""

    status: str
    case_id: str
    run_id: str
    failure_reason: str = ""
    memo: dict[str, Any] | None = None
    policy_result: dict[str, Any] | None = None
    policy_evidence: dict[str, Any] | None = None
    ownership_graph: dict[str, Any] | None = None
    screening_results: list[dict[str, Any]] = field(default_factory=list)
    parties: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    prompt: dict[str, str] | None = None
    model_id: str = ""
    narrative: dict[str, Any] | None = None
    pseudonymised: bool = False

    @property
    def recommendation(self) -> str | None:
        return self.memo["recommendation"]["proposed"] if self.memo else None

    def case_record(self) -> dict[str, Any]:
        """What the case store keeps for the evidence package: versions, hashes and every call."""
        return {
            "agent": AGENT_NAME,
            "agent_version": AGENT_VERSION,
            "run_id": self.run_id,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "prompt": self.prompt,
            "model_id": self.model_id,
            "policy_result": self.policy_result,
            "policy_evidence": self.policy_evidence,
            "narrative": self.narrative,
            "pseudonymised": self.pseudonymised,
            "tool_calls": self.tool_calls,
        }


class _RunFailedError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _step_for(exc: ProviderError) -> Step:
    return Step("error", exc.reason)


# --- investigation ------------------------------------------------------------------------------


def _select(candidates: list[BusinessCandidate], provider: str) -> BusinessCandidate | None:
    ours = [c for c in candidates if c.ref.provider == provider]
    if not ours:
        return None
    ranked = sorted(ours, key=lambda c: -(c.match_score or 0.0))
    top = ranked[0]
    if top.match_score is None or top.match_score < MIN_MATCH_SCORE:
        return None
    if len(ranked) > 1 and (ranked[1].match_score or 0.0) >= top.match_score:
        return None
    return top


def _query(application: Mapping[str, Any]) -> BusinessQuery:
    address = application.get("registered_address")
    return BusinessQuery(
        name=application["legal_name"],
        jurisdiction=application.get("jurisdiction"),
        identifiers=tuple(Identifier.model_validate(i) for i in application.get("identifiers") or ()),
        address=Address.model_validate(address) if address else None,
    )


class _Investigator:
    def __init__(
        self, case_id: str, application: dict[str, Any], config: UnderwriterConfig, deps: UnderwriterDependencies,
        gateway: ProviderToolGateway, untrusted: UntrustedTextRegistry,
    ) -> None:  # fmt: skip
        self.case_id = case_id
        self.application = application
        self.config = config
        self.deps = deps
        self.gateway = gateway
        self.untrusted = untrusted
        self.inv = Investigation(case_id=case_id, application=application, resolve=Step("no_match"))

    def _deadline(self) -> Deadline:
        return Deadline.after(self.config.call_timeout_s)

    async def resolve(self) -> None:
        try:
            query = _query(self.application)
        except ValidationError:
            self.inv.resolve = Step("error", "invalid_query")
            return
        try:
            result = await self.gateway.resolve_business(query, deadline=self._deadline())
        except ProviderError as exc:
            self.inv.resolve = _step_for(exc)
            return
        if isinstance(result, NotAvailable):
            self.inv.resolve = Step("not_available")
            return
        candidate = _select(result, self.gateway.provider.name)
        if candidate is None:
            self.inv.resolve = Step("no_match", "ambiguous" if result else "no_candidates")
            return
        self.inv.resolve = Step("ok")
        self.inv.candidate = candidate

    async def verify(self) -> None:
        candidate = self.inv.candidate
        if candidate is None:
            return
        address = self.application.get("registered_address")
        declared = DeclaredBusiness(
            legal_name=self.application["legal_name"],
            registered_address=Address.model_validate(address) if address else None,
            identifiers=tuple(Identifier.model_validate(i) for i in self.application.get("identifiers") or ()),
        )
        opts = VerifyOptions(idempotency_key=f"{self.case_id}.verify", declared=declared)
        overall = Deadline.after(self.config.verification_timeout_s)
        try:
            handle = await self.gateway.verify_business(candidate.ref, opts, deadline=self._deadline())
            if isinstance(handle, NotAvailable):
                self.inv.verify = Step("not_available")
                return
            while True:
                if overall.expired:
                    self.inv.verify = Step("error", "provider_timeout")
                    return
                call_deadline = Deadline(min(overall.expires_at, self._deadline().expires_at))
                result = await self.gateway.verification_result(handle, deadline=call_deadline)
                if isinstance(result, NotAvailable):
                    self.inv.verify = Step("not_available")
                    return
                if isinstance(result, Pending):
                    wait = min(result.retry_after_seconds, self.config.max_poll_interval_s, overall.remaining())
                    await self.deps.sleep(wait)
                    continue
                self.inv.verification = result
                self.inv.verify = Step("ok")
                return
        except ProviderError as exc:
            self.inv.verify = _step_for(exc)

    async def ownership(self) -> None:
        candidate = self.inv.candidate
        if candidate is None:
            return
        try:
            graph = await self.gateway.ownership(candidate.ref, deadline=self._deadline())
        except ProviderError as exc:
            self.inv.ownership = _step_for(exc)
            return
        if isinstance(graph, NotAvailable):
            self.inv.ownership = Step("not_available")
            return
        self.inv.graph = graph
        self.inv.reconciliation = reconcile(
            list(self.application.get("declared_owners") or ()),
            graph,
            threshold_pct=self.config.ownership_threshold_pct,
        )
        self.inv.ownership = Step("ok")

    async def screen(self) -> None:
        candidate, verification = self.inv.candidate, self.inv.verification
        parties = facts.screening_parties(
            self.application,
            verification=verification,
            graph=self.inv.graph,
            subject_legal_name=(
                verification.legal_name if verification else candidate.legal_name if candidate else None
            ),
            subject_jurisdiction=candidate.ref.jurisdiction if candidate else None,
            subject_identifiers=candidate.ref.identifiers if candidate else (),
        )
        any_attempted = False
        for party in parties:
            options = ScreenOptions(idempotency_key=f"{self.case_id}.screen.{party.key()}")
            entry = ScreenedParty(party=party.to_dict(), step=Step("ok"))
            try:
                if party.kind == "person":
                    result: ScreeningResult | NotAvailable = await self.gateway.screen_person(
                        party.person_subject(), options, deadline=self._deadline()
                    )
                else:
                    result = await self.gateway.screen_business(
                        party.business_subject(), options, deadline=self._deadline()
                    )
            except ValidationError:
                entry.step = Step("error", "invalid_query")
            except ProviderError as exc:
                entry.step = _step_for(exc)
            else:
                if isinstance(result, NotAvailable):
                    entry.step = Step("not_available")
                else:
                    any_attempted = True
                    entry.result = result
            self.inv.screened.append(entry)
        if any_attempted or any(e.step.status == "error" for e in self.inv.screened):
            self.inv.screening = Step("ok")
        else:
            self.inv.screening = Step("not_available")

    async def web_presence(self) -> None:
        candidate = self.inv.candidate
        if candidate is None:
            return
        try:
            presence = await self.gateway.web_presence(candidate.ref, deadline=self._deadline())
        except ProviderError as exc:
            self.inv.web = _step_for(exc)
            return
        if isinstance(presence, NotAvailable):
            self.inv.web = Step("not_available")
            return
        self.inv.web_evidence = tuple(presence.evidence) + tuple(e for d in presence.domains for e in d.evidence)
        self.inv.web_domains = tuple(d.domain for d in presence.domains)
        self.untrusted.register_all(self.inv.web_domains)
        for page in presence.pages:
            self.untrusted.register(page.url)
            extraction: dict[str, Any] | None = None
            if (
                page.content is not None
                and page.http_status < 400
                and page.media_type in CONTENT_TYPES[SourceKind.WEBSITE]
            ):
                # The only place page content is read: it goes straight to the sandboxed extractor.
                result = await extract(
                    page.content.unsafe_value().encode("utf-8"),
                    kind=SourceKind.WEBSITE,
                    content_type=page.media_type,
                    excerpts=self.deps.excerpts,
                    untrusted=self.untrusted,
                    timeout_s=self.config.extraction_timeout_s,
                    require_os_isolation=self.config.require_os_isolation,
                )
                extraction = result.to_dict()
                if result.ok:
                    record_id = page.evidence[0].record_id
                    for refs in result.excerpt_refs.values():
                        for ref in refs:
                            excerpt = self.deps.excerpts.get(ref)
                            if excerpt is None:
                                continue
                            self.inv.excerpts.append(
                                {
                                    "excerpt_ref": ref,
                                    "provider": self.gateway.provider.name,
                                    "record_id": record_id,
                                    "media_type": "text/plain",
                                    "sha256": _sha256(excerpt.text),
                                }
                            )
            self.inv.pages.append(
                WebPageFacts(
                    url=page.url, evidence=page.evidence, content_sha256=page.content_sha256, extraction=extraction
                )
            )
        unique: dict[str, dict[str, Any]] = {e["excerpt_ref"]: e for e in self.inv.excerpts}
        self.inv.excerpts = [unique[ref] for ref in sorted(unique)]
        self.inv.web = Step("ok")


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def register_untrusted(untrusted: UntrustedTextRegistry, inv: Investigation) -> None:
    """Register every free-text value from the applicant or the provider, so none can reach a model."""
    app = inv.application
    texts: list[str] = [str(app.get("legal_name") or ""), str(app.get("website") or "")]
    texts += list(app.get("trading_names") or ())
    texts += [str(owner.get("name") or "") for owner in app.get("declared_owners") or ()]
    texts += _address_texts(app.get("registered_address"))
    if inv.candidate is not None:
        texts.append(inv.candidate.legal_name)
    verification: BusinessVerification | None = inv.verification
    if verification is not None:
        texts.append(verification.legal_name)
        texts += [officer.name for officer in verification.officers]
        texts += _address_texts(
            verification.registered_address.model_dump() if verification.registered_address else None
        )
    graph: OwnershipGraph | None = inv.graph
    if graph is not None:
        for node in graph.nodes:
            texts.append(node.name)
            texts += _address_texts(node.address.model_dump() if node.address else None)
    for entry in inv.screened:
        texts.append(str(entry.party.get("name") or ""))
        if entry.result is None:
            continue
        for hit in entry.result.hits:
            texts += [
                hit.matched_name,
                *hit.aliases,
                *hit.associated_entities,
                hit.source.name,
                hit.source.authority or "",
            ]
            for address in hit.addresses:
                texts += _address_texts(address.model_dump())
    untrusted.register_all(text for text in texts if text)


def _address_texts(address: Mapping[str, Any] | None) -> list[str]:
    if not address:
        return []
    return [*address.get("lines", ()), str(address.get("locality") or ""), str(address.get("region") or "")]


# --- narrative ----------------------------------------------------------------------------------


async def _narrate(
    *, tenant_id: str, run_id: str, prompt: PromptRecord, context: str, config: UnderwriterConfig,
    deps: UnderwriterDependencies, untrusted: UntrustedTextRegistry, sections: list[dict[str, Any]],
) -> tuple[NarrativeReport, bool]:  # fmt: skip
    """Ask the model for section summaries. Returns the report and whether pseudonymisation was on.

    A leak of untrusted text fails the run. Any other failure to get a narrative leaves the memo
    without summaries - the deterministic memo is complete without them - and says why.
    """
    result = await call_case_model(
        agent=AGENT_NAME,
        tenant_id=tenant_id,
        run_id=run_id,
        system_prompt=prompt.text,
        context=context,
        untrusted=untrusted,
        llm_model=config.llm_model,
        llm_provider=config.llm_provider,
        pseudonym_store=deps.pseudonym_store,
    )
    if result.output is None:
        return NarrativeReport((), (("", result.failure),), None), result.pseudonymised
    report = accept_narrative(
        sections,
        result.output,
        restore=result.restore,
        contains_untrusted=lambda text: bool(untrusted.find(text)),
    )
    return report, result.pseudonymised


# --- run ----------------------------------------------------------------------------------------


async def run_underwriter(
    *,
    tenant_id: str,
    case_id: str,
    application: dict[str, Any],
    config: UnderwriterConfig,
    deps: UnderwriterDependencies,
    run_id: str | None = None,
    screening_reviews: Mapping[str, str] | None = None,
) -> UnderwritingOutcome:
    """Investigate ``application`` and return the hand-off. Never raises for a provider or grant failure.

    ``run_id`` must be server-generated (it keys the pseudonym map); one is generated when omitted.
    ``screening_reviews`` maps hit ids to outcomes a human analyst recorded, for re-evaluation.
    """
    run_id = run_id or f"uw_{uuid.uuid4().hex}"
    outcome = UnderwritingOutcome(
        status="failed", case_id=case_id, run_id=run_id, model_id=config.llm_model or "default"
    )
    gateway = ProviderToolGateway(
        provider=deps.provider, agent=AGENT_NAME, tool_set=TOOL_SET, authorizer=deps.authorizer, clock=deps.clock
    )
    untrusted = UntrustedTextRegistry()
    try:
        prompt = load_prompt(*config.prompt)
        outcome.prompt = prompt.to_dict()
        investigator = _Investigator(case_id, application, config, deps, gateway, untrusted)
        await investigator.resolve()
        await investigator.verify()
        await investigator.ownership()
        await investigator.screen()
        await investigator.web_presence()
        inv = investigator.inv
        register_untrusted(untrusted, inv)

        screening_results = [e.result for e in inv.screened if e.result is not None]
        registry_identifiers = (
            inv.verification.identifiers if inv.verification else inv.candidate.ref.identifiers if inv.candidate else ()
        )
        registry_match = {"ok": True, "no_match": False}.get(inv.resolve.status)
        evidence = facts.policy_evidence(
            application=application,
            registry_match=registry_match,
            verification=inv.verification,
            registry_identifiers=registry_identifiers,
            reconciliation=inv.reconciliation,
            screening_results=screening_results,
            screening_complete=bool(inv.screened) and all(e.step.status == "ok" for e in inv.screened),
            screening_available=inv.screening.status == "ok",
            extractions=inv.extractions,
            reviews=screening_reviews,
        )
        result: PolicyResult = evaluate(config.policy, evidence)
        policy_document = policy_result_document(config.policy, result)
        outcome.policy_result = result.to_dict()
        outcome.policy_evidence = evidence

        sections = build_sections(inv)
        items = missing_items(inv, sections)
        recommendation = recommend(result.tier, items)
        context = build_model_context(
            narrative_context(sections, policy_document, recommendation, items), untrusted=untrusted
        )
        report, pseudonymised = await _narrate(
            tenant_id=tenant_id, run_id=run_id, prompt=prompt, context=context, config=config, deps=deps,
            untrusted=untrusted, sections=sections,
        )  # fmt: skip
        outcome.narrative = report.to_dict()
        outcome.pseudonymised = pseudonymised

        memo = build_memo(
            inv=inv,
            sections=sections,
            policy_document=policy_document,
            recommendation=recommendation,
            items=items,
            created_at=deps.clock().isoformat(),
            memo_id=f"memo-{case_id}"[:128],
            provenance={
                "agent": AGENT_NAME,
                "agent_version": AGENT_VERSION,
                "prompt_version": prompt.version,
                "model_id": outcome.model_id,
                "model_confidence": report.model_confidence,
            },
        )
        try:
            validate("underwriting_memo", memo)
        except DomainSchemaError as exc:
            logger.error("underwriter_memo_invalid", case_id=case_id, errors=exc.errors[:5])
            raise _RunFailedError("memo_schema_invalid") from exc
        retrieved = gateway.retrieved_evidence
        untraced = [
            where
            for where, item in iter_memo_evidence(memo)
            if (item["provider"], item["record_id"], item["field"]) not in retrieved
        ]
        if untraced:
            logger.error("underwriter_memo_untraced_evidence", case_id=case_id, locations=untraced[:5])
            raise _RunFailedError("memo_evidence_untraced")

        outcome.memo = memo
        outcome.ownership_graph = inv.graph.model_dump(mode="json") if inv.graph else None
        outcome.screening_results = [r.model_dump(mode="json") for r in screening_results]
        outcome.parties = [e.party for e in inv.screened]
        outcome.status = "completed"
    except ToolRefusedError as exc:
        outcome.failure_reason = f"tool_refused:{exc.reason}"
    except UntrustedContentLeakError as exc:
        outcome.failure_reason = exc.reason
    except _RunFailedError as exc:
        outcome.failure_reason = exc.reason
    except PromptIntegrityError as exc:
        logger.error("underwriter_prompt_refused", detail=str(exc))
        outcome.failure_reason = "prompt_integrity_failed"
    finally:
        outcome.tool_calls = [record.to_dict() for record in gateway.records]
    _runs_total.labels(agent=AGENT_NAME, outcome=outcome.status if outcome.status == "completed" else "failed").inc()
    logger.info(
        "underwriter_run_finished",
        case_id=case_id,
        run_id=run_id,
        status=outcome.status,
        failure_reason=outcome.failure_reason,
        tool_calls=len(outcome.tool_calls),
    )
    return outcome
