# SPDX-License-Identifier: Apache-2.0
"""Per-identifier comparison of a screened subject with a screening hit, and the proposed outcome.

Deterministic: the same subject and hit always give the same comparisons, outcome and confidence
band. The band is metadata for the reviewer and never gates anything.

Outcome rules, in order:

1. the names do not match, or the dates of birth disagree → ``false_positive``;
2. the names match (fully or partly) and the dates of birth agree → ``true_match``;
3. the names match, no date of birth can be compared and the associated entities overlap →
   ``true_match``;
4. otherwise → ``insufficient_information``.

Nationality and address are reported and weigh on the confidence band, but never decide alone:
dual nationality and stale addresses are common in list data.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from connectors.framework.verification_provider import Evidence, ScreeningHit
from core.agents.business_underwriter.reconciliation import normalise_name

IDENTIFIERS = ("name", "date_of_birth", "nationality", "address", "associated_entities")
NAME_PARTIAL_THRESHOLD = 0.85
MAX_VALUE_CHARS = 1024


@dataclass(frozen=True, slots=True)
class Comparison:
    identifier: str
    subject_value: str | None
    hit_value: str | None
    result: str
    note: str | None
    evidence: tuple[Evidence, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "subject_value": _clip(self.subject_value),
            "hit_value": _clip(self.hit_value),
            "result": self.result,
            "note": self.note,
            "evidence": [e.model_dump(mode="json") for e in self.evidence],
        }


def _clip(value: str | None) -> str | None:
    if value is None:
        return None
    return value if len(value) <= MAX_VALUE_CHARS else value[: MAX_VALUE_CHARS - 1] + "…"


def _hit_evidence(hit: ScreeningHit, field: str) -> tuple[Evidence, ...]:
    """Evidence for one field of the hit record: the record the hit cites, at that field."""
    source = hit.evidence[0]
    return (
        Evidence(
            provider=source.provider,
            record_id=source.record_id,
            field=field,
            retrieved_at=source.retrieved_at,
            excerpt_ref=source.excerpt_ref if field == source.field else None,
        ),
    )


def compare_name(subject_name: str, hit: ScreeningHit, *, business: bool) -> Comparison:
    wanted = " ".join(normalise_name(subject_name, business=business))
    candidates = [("names[0]", hit.matched_name)] + [(f"aliases[{i}]", alias) for i, alias in enumerate(hit.aliases)]
    scored: list[tuple[float, int, str, str]] = []
    for field, value in candidates:
        other = " ".join(normalise_name(value, business=business))
        ratio = 1.0 if other == wanted else difflib.SequenceMatcher(None, wanted, other).ratio()
        scored.append((ratio, -len(scored), field, value))
    ratio, _, field, value = max(scored)
    if ratio == 1.0:
        result, note = "match", None
    elif ratio >= NAME_PARTIAL_THRESHOLD:
        result, note = "partial_match", f"Normalised names are {ratio:.2f} similar."
    else:
        result, note = "mismatch", f"Normalised names are {ratio:.2f} similar."
    return Comparison("name", subject_name, value, result, note, _hit_evidence(hit, field))


def compare_date_of_birth(subject_dob: str | None, hit: ScreeningHit) -> Comparison:
    if not subject_dob or not hit.dates_of_birth:
        side = "subject" if not subject_dob else "hit"
        return Comparison(
            "date_of_birth",
            subject_dob,
            ", ".join(hit.dates_of_birth) or None,
            "not_comparable",
            f"No date of birth on the {side}.",
            (),
        )
    best: tuple[int, str, int] | None = None
    for index, candidate in enumerate(hit.dates_of_birth):
        shared = min(len(subject_dob), len(candidate))
        if subject_dob[:shared] == candidate[:shared]:
            rank = 2 if shared >= 10 else 1
            if best is None or rank > best[0]:
                best = (rank, candidate, index)
    if best is None:
        return Comparison(
            "date_of_birth",
            subject_dob,
            ", ".join(hit.dates_of_birth),
            "mismatch",
            None,
            _hit_evidence(hit, "dates_of_birth[0]"),
        )
    rank, value, index = best
    result = "match" if rank == 2 else "partial_match"
    note = None if rank == 2 else "Dates agree to the precision both records give."
    return Comparison("date_of_birth", subject_dob, value, result, note, _hit_evidence(hit, f"dates_of_birth[{index}]"))


def compare_nationality(subject: Sequence[str], hit: ScreeningHit) -> Comparison:
    subject_value = ", ".join(sorted(subject)) or None
    hit_value = ", ".join(sorted(hit.nationalities)) or None
    if not subject or not hit.nationalities:
        return Comparison("nationality", subject_value, hit_value, "not_comparable", "No nationality on one side.", ())
    shared = sorted(set(subject) & set(hit.nationalities))
    if shared:
        index = list(hit.nationalities).index(shared[0])
        return Comparison(
            "nationality", subject_value, hit_value, "match", None, _hit_evidence(hit, f"nationalities[{index}]")
        )
    return Comparison("nationality", subject_value, hit_value, "mismatch", None, _hit_evidence(hit, "nationalities[0]"))


def _address_text(address: Mapping[str, Any]) -> str:
    parts = [*address.get("lines", ()), address.get("locality"), address.get("postal_code"), address.get("country")]
    return ", ".join(str(p) for p in parts if p)


def _postcode(value: Any) -> str:
    return str(value or "").replace(" ", "").upper()


def compare_address(subject: Mapping[str, Any] | None, hit: ScreeningHit) -> Comparison:
    if not subject or not hit.addresses:
        return Comparison(
            "address",
            _address_text(subject) if subject else None,
            "; ".join(_address_text(a.model_dump()) for a in hit.addresses) or None,
            "not_comparable",
            "No address on one side.",
            (),
        )
    best = ("mismatch", 0)
    for index, candidate in enumerate(hit.addresses):
        if candidate.country != subject.get("country"):
            continue
        same_postcode = _postcode(candidate.postal_code) and _postcode(candidate.postal_code) == _postcode(
            subject.get("postal_code")
        )
        result = "match" if same_postcode else "partial_match"
        if best[0] != "match":
            best = (result, index)
    result, index = best
    note = "Same country, different or missing postal code." if result == "partial_match" else None
    return Comparison(
        "address",
        _address_text(subject),
        _address_text(hit.addresses[index].model_dump()),
        result,
        note,
        _hit_evidence(hit, f"addresses[{index}]"),
    )


def compare_associated_entities(associated: Sequence[str], hit: ScreeningHit) -> Comparison:
    subject_value = "; ".join(associated) or None
    hit_value = "; ".join(hit.associated_entities) or None
    if not associated or not hit.associated_entities:
        return Comparison(
            "associated_entities", subject_value, hit_value, "not_comparable", "No associated entities on one side.", ()
        )
    ours = {" ".join(normalise_name(name, business=True)) for name in associated}
    for index, name in enumerate(hit.associated_entities):
        if " ".join(normalise_name(name, business=True)) in ours:
            return Comparison(
                "associated_entities",
                subject_value,
                name,
                "match",
                None,
                _hit_evidence(hit, f"associated_entities[{index}]"),
            )
    return Comparison(
        "associated_entities", subject_value, hit_value, "mismatch", None, _hit_evidence(hit, "associated_entities[0]")
    )


def compare(subject: Mapping[str, Any], hit: ScreeningHit, *, associated: Sequence[str]) -> tuple[Comparison, ...]:
    """The five comparisons, in schema order. ``subject`` is a screened party (name, kind, date_of_birth, ...)."""
    business = subject.get("kind") == "business"
    return (
        compare_name(str(subject["name"]), hit, business=business),
        compare_date_of_birth(subject.get("date_of_birth"), hit),
        compare_nationality(tuple(subject.get("nationalities") or ()), hit),
        compare_address(subject.get("address"), hit),
        compare_associated_entities(associated, hit),
    )


def propose(comparisons: Sequence[Comparison]) -> tuple[str, str]:
    """``(proposed_outcome, confidence_band)`` from the comparisons."""
    by_id = {c.identifier: c.result for c in comparisons}
    name, dob, associated = by_id["name"], by_id["date_of_birth"], by_id["associated_entities"]
    agree = ("match", "partial_match")
    if name == "mismatch" or dob == "mismatch":
        outcome = "false_positive"
    elif name in agree and dob in agree:
        outcome = "true_match"
    elif name in agree and dob == "not_comparable" and associated == "match":
        outcome = "true_match"
    else:
        outcome = "insufficient_information"

    others = [by_id[i] for i in IDENTIFIERS if i != "name"]
    if outcome == "insufficient_information":
        return outcome, "low"
    if outcome == "true_match":
        support = sum(r in agree for r in others)
        against = sum(r == "mismatch" for r in others)
    else:
        support = sum(r == "mismatch" for r in [name, *others])
        against = sum(r == "match" for r in others)
    if support >= 2 and against == 0:
        return outcome, "high"
    if support >= 1:
        return outcome, "medium" if against <= support else "low"
    return outcome, "low"


def template_rationale(comparisons: Sequence[Comparison], outcome: str) -> str:
    """A plain rationale from the comparison results, used when no valid model rationale is available."""
    words = {
        "match": "matches",
        "partial_match": "partly matches",
        "mismatch": "does not match",
        "not_comparable": "cannot be compared",
    }
    labels = {
        "name": "The name",
        "date_of_birth": "The date of birth",
        "nationality": "Nationality",
        "address": "The address",
        "associated_entities": "Associated entities",
    }
    sentences = [f"{labels[c.identifier]} {words[c.result]}." for c in comparisons]
    sentences.append(f"Proposed outcome: {outcome.replace('_', ' ')}, for analyst review.")
    return " ".join(sentences)
