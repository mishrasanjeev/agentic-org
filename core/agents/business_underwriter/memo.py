# SPDX-License-Identifier: Apache-2.0
"""Assemble the cited ``underwriting_memo`` for a business case.

Sections, findings, the recommendation and the missing-items list are computed from provider
data and the policy result by fixed rules. The model contributes only ``narrative_summary``
findings, and only after :func:`accept_narrative` has checked each one against the section it
summarises: the section must have content, every citation must be one of that section's own
evidence entries, and the text must be plain, bounded and free of untrusted source text.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from connectors.framework.verification_provider import (
    BusinessCandidate,
    BusinessVerification,
    CheckOutcome,
    Evidence,
    OwnershipGraph,
    RegistryStatus,
    ScreeningResult,
)
from core.agents.business_underwriter.facts import activity_mismatch, declared_activity_categories
from core.agents.business_underwriter.reconciliation import Reconciliation
from core.policy.types import Tier

SECTION_ORDER = ("identity", "registry", "ownership", "screening", "web_presence", "activity")
SECTION_ERROR_REASONS = frozenset(
    {
        "provider_timeout",
        "provider_unavailable",
        "provider_rate_limited",
        "provider_authentication_failed",
        "provider_response_invalid",
        "invalid_query",
        "not_found",
    }
)
NARRATIVE_CODE = "narrative_summary"
MAX_SUMMARY_CHARS = 600
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2, "info": 3}
_TOKEN_REMNANT = re.compile(r"\[\[|\]\]|untrusted_ref")


@dataclass(frozen=True, slots=True)
class Step:
    """How one investigation step ended: ``ok``, ``no_match``, ``not_available``, ``no_subject`` or ``error``."""

    status: str
    reason: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"status": self.status, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Step:
        return cls(status=str(data["status"]), reason=str(data.get("reason") or ""))


@dataclass
class ScreenedParty:
    party: dict[str, Any]
    step: Step
    result: ScreeningResult | None = None


@dataclass
class WebPageFacts:
    url: str
    evidence: tuple[Evidence, ...]
    content_sha256: str | None
    extraction: dict[str, Any] | None


@dataclass
class Investigation:
    case_id: str
    application: dict[str, Any]
    resolve: Step
    candidate: BusinessCandidate | None = None
    verify: Step = field(default_factory=lambda: Step("no_subject"))
    verification: BusinessVerification | None = None
    ownership: Step = field(default_factory=lambda: Step("no_subject"))
    graph: OwnershipGraph | None = None
    reconciliation: Reconciliation | None = None
    screening: Step = field(default_factory=lambda: Step("no_subject"))
    screened: list[ScreenedParty] = field(default_factory=list)
    web: Step = field(default_factory=lambda: Step("no_subject"))
    web_evidence: tuple[Evidence, ...] = ()
    web_domains: tuple[str, ...] = ()
    pages: list[WebPageFacts] = field(default_factory=list)
    excerpts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def extractions(self) -> list[dict[str, Any]]:
        return [page.extraction for page in self.pages if page.extraction is not None]


def _ev(evidence: tuple[Evidence, ...] | list[Evidence]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for item in evidence:
        key = (item.provider, item.record_id, item.field)
        if key in seen:
            continue
        seen.add(key)
        out.append(item.model_dump(mode="json"))
    return out


def _finding(
    code: str, severity: str, statement: str, evidence: tuple[Evidence, ...] | list[Evidence]
) -> dict[str, Any]:
    return {"code": code, "severity": severity, "statement": statement, "evidence": _ev(evidence)}


def _section(
    section_id: str, step: Step, findings: list[dict[str, Any]], *, capability_missing: bool
) -> dict[str, Any]:
    if step.status in ("not_available", "no_subject", "no_match") and not findings:
        reason = "capability_not_supported" if step.status == "not_available" else "no_registry_match"
        return {
            "section_id": section_id,
            "status": "not_available",
            "not_available_reason": reason,
            "findings": [],
            "evidence": [],
        }
    if step.status == "error":
        return {
            "section_id": section_id,
            "status": "error",
            "error_reason": step.reason if step.reason in SECTION_ERROR_REASONS else "provider_unavailable",
            "findings": [],
            "evidence": [],
        }
    findings = sorted(findings, key=lambda f: (_SEVERITY_RANK[f["severity"]], f["code"]))
    evidence: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for finding in findings:
        for item in finding["evidence"]:
            key = (item["provider"], item["record_id"], item["field"])
            if key not in seen:
                seen.add(key)
                evidence.append(item)
    if not evidence:
        # Nothing to cite: a section with content must cite a record, so it is reported as an error.
        return {
            "section_id": section_id,
            "status": "error",
            "error_reason": "provider_response_invalid",
            "findings": [],
            "evidence": [],
        }
    return {
        "section_id": section_id,
        "status": "partial" if capability_missing else "complete",
        "findings": findings,
        "evidence": evidence,
    }


def _identity(inv: Investigation) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    if inv.resolve.status == "ok" and inv.candidate is not None:
        score = inv.candidate.match_score
        detail = f" (match score {score:.2f})" if score is not None else ""
        findings.append(
            _finding(
                "registry_match",
                "info",
                f"The application resolved to one registry record in {inv.candidate.ref.jurisdiction}{detail}.",
                inv.candidate.evidence,
            )
        )
    return _section("identity", inv.resolve, findings, capability_missing=False)


_CHECK_CODES = {
    "name": "name_mismatch",
    "address": "address_mismatch",
    "identifiers": "identifier_mismatch",
    "registration": "registration_check_failed",
    "officers": "officers_check_failed",
}


def _registry(inv: Investigation) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    verification = inv.verification
    if inv.verify.status == "ok" and verification is not None:
        status = verification.registry_status
        if status is RegistryStatus.ACTIVE:
            findings.append(
                _finding(
                    "registry_active", "info", "The registry reports the business as active.", verification.evidence
                )
            )
        else:
            findings.append(
                _finding(
                    "registry_not_active",
                    "high",
                    f"The registry reports the business as {status.value.replace('_', ' ')}.",
                    verification.evidence,
                )
            )
        for check in verification.checks:
            if check.outcome is CheckOutcome.FAILED and check.evidence:
                code = _CHECK_CODES[check.check.value]
                findings.append(
                    _finding(
                        code,
                        "medium",
                        f"The registry {check.check.value} check did not match the application.",
                        check.evidence,
                    )
                )
            elif check.outcome is CheckOutcome.INCONCLUSIVE and check.evidence:
                findings.append(
                    _finding(
                        f"{check.check.value}_check_inconclusive",
                        "low",
                        f"The registry {check.check.value} check was inconclusive.",
                        check.evidence,
                    )
                )
        current = [officer for officer in verification.officers if not officer.resigned_on]
        if current:
            findings.append(
                _finding(
                    "current_officers",
                    "info",
                    f"The registry lists {len(current)} current officer(s).",
                    [e for officer in current for e in officer.evidence],
                )
            )
    return _section("registry", inv.verify, findings, capability_missing=False)


def _ownership(inv: Investigation) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    graph, rec = inv.graph, inv.reconciliation
    if inv.ownership.status == "ok" and graph is not None and rec is not None:
        threshold = f"{rec.threshold_pct:g}%"
        for missing in rec.missing_owners:
            pct = (
                f" declared at {missing.declared_pct:g}%"
                if missing.declared_pct is not None
                else " with no stated share"
            )
            findings.append(
                _finding(
                    "missing_owner",
                    "medium",
                    f"Declared owner {missing.declared_index + 1}{pct} is absent from the ownership graph "
                    f"(threshold {threshold}).",
                    graph.evidence,
                )
            )
        for undeclared in rec.undeclared_owners:
            pct = (
                f"up to {undeclared.effective_pct:g}%"
                if undeclared.effective_pct is not None
                else "through a control relationship with no stated percentage"
            )
            findings.append(
                _finding(
                    "undeclared_owner",
                    "medium",
                    f"The ownership graph shows a {undeclared.kind} owner (node {undeclared.node_id}) holding {pct} "
                    f"that the applicant did not declare (threshold {threshold}).",
                    undeclared.evidence,
                )
            )
        if graph.completeness != "complete":
            findings.append(
                _finding(
                    "ownership_graph_incomplete",
                    "low",
                    f"The provider reports the ownership graph as {graph.completeness}.",
                    graph.evidence,
                )
            )
        if not rec.missing_owners and not rec.undeclared_owners:
            findings.append(
                _finding(
                    "ownership_reconciled",
                    "info",
                    f"Every declared owner appears in the ownership graph and no undeclared owner reaches {threshold}.",
                    graph.evidence,
                )
            )
    return _section("ownership", inv.ownership, findings, capability_missing=False)


def _screening(inv: Investigation) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    screened = [entry for entry in inv.screened if entry.step.status == "ok" and entry.result is not None]
    unscreened = [entry for entry in inv.screened if entry.step.status != "ok"]
    for entry in screened:
        assert entry.result is not None
        for hit in entry.result.hits:
            findings.append(
                _finding(
                    "screening_hit",
                    "medium",
                    f"Screening of {entry.party['party_id']} ({entry.party['kind']}) returned a possible "
                    f"{hit.list_type.value.replace('_', ' ')} match awaiting disposition (hit {hit.hit_id}).",
                    hit.evidence,
                )
            )
    clear = [entry for entry in screened if entry.result is not None and not entry.result.hits]
    if clear:
        findings.append(
            _finding(
                "screening_clear",
                "info",
                f"{len(clear)} of {len(inv.screened)} screened parties returned no hits.",
                [e for entry in clear if entry.result is not None for e in entry.result.evidence],
            )
        )
    if unscreened and screened:
        findings.append(
            _finding(
                "screening_incomplete",
                "low",
                f"{len(unscreened)} of {len(inv.screened)} parties could not be screened.",
                [e for entry in screened if entry.result is not None for e in entry.result.evidence],
            )
        )
    step = inv.screening
    if step.status == "ok" and not screened:
        # Every party failed or was unsupported: report the first reason for the whole section.
        first = unscreened[0].step if unscreened else Step("not_available")
        step = first if first.status in ("error", "not_available") else Step("not_available")
    return _section("screening", step, findings, capability_missing=bool(unscreened and screened))


def _web_presence(inv: Investigation) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    if inv.web.status == "ok":
        for page in inv.pages:
            extraction = page.extraction
            if extraction is None or not extraction.get("ok"):
                failure = (extraction or {}).get("failure") or "not_extracted"
                findings.append(
                    _finding(
                        "web_page_not_extracted", "low", f"A web page could not be analysed ({failure}).", page.evidence
                    )
                )
            else:
                fields = extraction["fields"]
                categories = ", ".join(fields.get("activity_categories") or ()) or "none recognised"
                findings.append(
                    _finding(
                        "web_page_analysed",
                        "info",
                        f"A web page was analysed in the sandbox; activity categories: {categories}.",
                        page.evidence,
                    )
                )
        if inv.web_domains:
            findings.append(
                _finding(
                    "web_domain_observed",
                    "info",
                    f"The provider observed {len(inv.web_domains)} domain(s) for the business.",
                    inv.web_evidence,
                )
            )
        if not inv.pages and not inv.web_domains:
            evidence = inv.web_evidence or (inv.candidate.evidence if inv.candidate else ())
            if evidence:
                findings.append(
                    _finding(
                        "no_web_presence",
                        "low",
                        "The provider returned no website or domain for the business.",
                        evidence,
                    )
                )
    return _section("web_presence", inv.web, findings, capability_missing=False)


def _activity(inv: Investigation) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    declared = inv.application.get("declared_activity")
    page_evidence = [e for page in inv.pages if page.extraction and page.extraction.get("ok") for e in page.evidence]
    if inv.web.status == "ok" and page_evidence:
        mismatch = activity_mismatch(declared, inv.extractions)
        if mismatch is True:
            findings.append(
                _finding(
                    "activity_mismatch",
                    "medium",
                    "Activity observed on the website differs from the declared activity.",
                    page_evidence,
                )
            )
        elif mismatch is False:
            findings.append(
                _finding(
                    "activity_consistent",
                    "info",
                    "Activity observed on the website is consistent with the declared activity.",
                    page_evidence,
                )
            )
        else:
            reason = (
                "the declared activity has no recognised category"
                if not declared_activity_categories(declared)
                else "no activity category was recognised on the website"
            )
            findings.append(
                _finding(
                    "activity_not_comparable",
                    "low",
                    f"Declared and observed activity could not be compared: {reason}.",
                    page_evidence,
                )
            )
        return _section("activity", inv.web, findings, capability_missing=mismatch is None)
    if inv.web.status == "ok":
        evidence = inv.web_evidence or (inv.candidate.evidence if inv.candidate else ())
        findings.append(
            _finding(
                "activity_not_comparable",
                "low",
                "Declared and observed activity could not be compared: no website content was analysed.",
                evidence,
            )
        )
        return _section("activity", inv.web, findings, capability_missing=True)
    step = inv.web
    return _section("activity", step, findings, capability_missing=False)


def build_sections(inv: Investigation) -> list[dict[str, Any]]:
    return [_identity(inv), _registry(inv), _ownership(inv), _screening(inv), _web_presence(inv), _activity(inv)]


def missing_items(inv: Investigation, sections: list[dict[str, Any]]) -> list[dict[str, str]]:
    """What a human would need before deciding, derived from section status and the application."""
    items: dict[str, str] = {}
    by_id = {section["section_id"]: section for section in sections}
    if inv.resolve.status in ("no_match",):
        items["registry_record"] = "No registry record matched the application; a registration document is needed."
    for section_id, item in (
        ("registry", "registry_verification"),
        ("ownership", "ownership_information"),
        ("screening", "screening_results"),
        ("web_presence", "web_presence_information"),
    ):
        section = by_id[section_id]
        if section["status"] == "error":
            items[item] = (
                f"The {section_id.replace('_', ' ')} check failed ({section['error_reason']}) and must be repeated."
            )
        elif section["status"] == "not_available" and section.get("not_available_reason") == "capability_not_supported":
            items[item] = f"The configured provider does not supply {section_id.replace('_', ' ')} data."
    if by_id["screening"]["status"] == "partial":
        items["screening_results"] = "Some parties could not be screened."
    for index, owner in enumerate(inv.application.get("declared_owners") or ()):
        if owner.get("kind", "person") == "person" and not owner.get("date_of_birth"):
            items["owner_date_of_birth"] = (
                f"Declared owner {index + 1} has no date of birth, which screening needs to tell people apart."
            )
            break
    jurisdiction = str(inv.application.get("jurisdiction") or "")
    if jurisdiction.startswith("US") and not any(
        i.get("scheme") == "us_ein" for i in inv.application.get("identifiers") or ()
    ):
        items["tax_identifier"] = "The application does not declare a federal employer identification number."
    return [{"item": item, "reason": reason} for item, reason in sorted(items.items())]


def recommend(tier: Tier, items: list[dict[str, str]]) -> str:
    """Map the policy tier and missing items to a proposed recommendation. Model output never feeds this."""
    if tier is Tier.BLOCKED:
        return "decline"
    if tier is Tier.HIGH:
        return "refer"
    if items:
        return "request_information"
    if tier is Tier.MEDIUM:
        return "refer"
    return "approve"


# --- narrative -----------------------------------------------------------------------------------


def narrative_context(
    sections: list[dict[str, Any]], policy: Mapping[str, Any], recommendation: str, items: list[dict[str, str]]
) -> dict[str, Any]:
    """The only case facts the model sees: statuses, codes, counts and policy tokens."""
    return {
        "sections": [
            {
                "section_id": section["section_id"],
                "status": section["status"],
                "finding_codes": [finding["code"] for finding in section["findings"]],
                "evidence_count": len(section["evidence"]),
            }
            for section in sections
        ],
        "policy": {
            "tier": policy["tier"],
            "score": policy["score"],
            "fired_rules": [reason["rule_id"] for reason in policy["reasons"]],
        },
        "recommendation": recommendation,
        "missing_items": [item["item"] for item in items],
    }


@dataclass(frozen=True, slots=True)
class NarrativeReport:
    accepted: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...]
    model_confidence: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": list(self.accepted),
            "rejected": [{"section_id": s, "reason": r} for s, r in self.rejected],
            "model_confidence": self.model_confidence,
        }


def accept_narrative(
    sections: list[dict[str, Any]],
    output: Mapping[str, Any],
    *,
    restore: Callable[[str], str] = lambda text: text,
    contains_untrusted: Callable[[str], bool] = lambda text: False,
) -> NarrativeReport:
    """Add valid model summaries to ``sections`` as ``narrative_summary`` findings; report the rest.

    A summary is refused (never repaired) when its section is unknown, has no content or already
    has a summary; when a citation is not an index into that section's evidence; when the text is
    empty, too long, or still contains a placeholder or reference marker after pseudonyms are
    restored; or when it contains untrusted source text.
    """
    by_id = {section["section_id"]: section for section in sections}
    accepted: list[str] = []
    rejected: list[tuple[str, str]] = []
    summaries = output.get("summaries") if isinstance(output, Mapping) else None
    if not isinstance(summaries, list):
        summaries = []
        rejected.append(("", "summaries_missing"))
    for entry in summaries:
        if not isinstance(entry, Mapping):
            rejected.append(("", "summary_not_an_object"))
            continue
        section_id = entry.get("section_id")
        section = by_id.get(section_id) if isinstance(section_id, str) else None
        label = section_id if isinstance(section_id, str) and section_id in by_id else ""
        if section is None:
            rejected.append((label, "section_unknown"))
            continue
        if section["status"] not in ("complete", "partial"):
            rejected.append((label, "section_has_no_content"))
            continue
        if label in accepted:
            rejected.append((label, "duplicate_summary"))
            continue
        citations = entry.get("citations")
        count = len(section["evidence"])
        if (
            not isinstance(citations, list)
            or not citations
            or any(isinstance(c, bool) or not isinstance(c, int) or not 0 <= c < count for c in citations)
            or len(set(citations)) != len(citations)
        ):
            rejected.append((label, "citation_invalid"))
            continue
        summary = entry.get("summary")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > MAX_SUMMARY_CHARS:
            rejected.append((label, "summary_invalid"))
            continue
        text = " ".join(restore(summary).split())
        if _TOKEN_REMNANT.search(text) or len(text) > 2000:
            rejected.append((label, "summary_invalid"))
            continue
        if contains_untrusted(text):
            rejected.append((label, "summary_contains_untrusted_text"))
            continue
        section["findings"].append(
            {
                "code": NARRATIVE_CODE,
                "severity": "info",
                "statement": text,
                "evidence": [section["evidence"][index] for index in sorted(citations)],
            }
        )
        accepted.append(label)
    raw_confidence = output.get("confidence") if isinstance(output, Mapping) else None
    confidence = (
        float(raw_confidence)
        if isinstance(raw_confidence, int | float) and not isinstance(raw_confidence, bool) and 0 <= raw_confidence <= 1
        else None
    )
    return NarrativeReport(tuple(accepted), tuple(rejected), confidence)


def build_memo(
    *,
    inv: Investigation,
    sections: list[dict[str, Any]],
    policy_document: Mapping[str, Any],
    recommendation: str,
    items: list[dict[str, str]],
    created_at: str,
    memo_id: str,
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "memo_id": memo_id,
        "case_id": inv.case_id,
        "created_at": created_at,
        "subject": inv.candidate.ref.model_dump(mode="json") if inv.candidate else None,
        "sections": sections,
        "policy_result": dict(policy_document),
        "recommendation": {"proposed": recommendation, "basis": "policy_result", "requires_human_decision": True},
        "missing_items": items,
        "excerpts": inv.excerpts,
        "provenance": dict(provenance),
    }


def iter_memo_evidence(memo: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every evidence entry in a memo with where it sits, for tracing assertions back to records."""
    out: list[tuple[str, dict[str, Any]]] = []
    for section in memo["sections"]:
        for item in section["evidence"]:
            out.append((f"{section['section_id']}.evidence", item))
        for index, finding in enumerate(section["findings"]):
            for item in finding["evidence"]:
                out.append((f"{section['section_id']}.findings[{index}]", item))
    return out
