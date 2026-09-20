# SPDX-License-Identifier: Apache-2.0
"""Per-identifier comparison rules and the analyst override capture model."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from connectors.framework.verification_provider import Address, Evidence, ListSource, ListType, ScreeningHit
from core.agents.screening_disposition.comparison import (
    compare,
    compare_address,
    compare_associated_entities,
    compare_date_of_birth,
    compare_name,
    compare_nationality,
    propose,
    template_rationale,
)
from core.agents.screening_disposition.review import DispositionReviewError, DispositionReviewRequest, apply_review

AT = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
EXAMPLE = (
    Path(__file__).resolve().parents[3]
    / "schemas"
    / "examples"
    / "screening_disposition"
    / "false_positive_overridden.json"
)


def hit(**overrides: Any) -> ScreeningHit:
    values: dict[str, Any] = {
        "hit_id": "hit-1",
        "list_type": ListType.SANCTIONS,
        "source": ListSource(name="Example List"),
        "matched_name": "Radomir Vexley",
        "aliases": ("Radomir Vexli",),
        "dates_of_birth": ("1966-09-12",),
        "nationalities": ("ZZ",),
        "addresses": (),
        "associated_entities": ("Corvane Maritime Logistics Ltd",),
        "evidence": (Evidence(provider="mock", record_id="mock:watchlist:wl-0003", field="names[0]", retrieved_at=AT),),
    }
    values.update(overrides)
    return ScreeningHit(**values)


# --- comparisons ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "result", "field"),
    [
        ("Radomir Vexley", "match", "names[0]"),
        ("VEXLEY, Radomir", "match", "names[0]"),
        ("Radomir Vexli", "match", "aliases[0]"),
        ("Radomir Vexlee", "partial_match", "names[0]"),
        ("Orla Venncastle", "mismatch", "names[0]"),
    ],
)
def test_name_comparison(subject: str, result: str, field: str) -> None:
    comparison = compare_name(subject, hit(), business=False)
    assert comparison.result == result
    assert comparison.evidence[0].field == field and comparison.evidence[0].record_id == "mock:watchlist:wl-0003"


@pytest.mark.parametrize(
    ("subject", "result"),
    [
        ("1966-09-12", "match"),
        ("1966-09", "partial_match"),
        ("1966", "partial_match"),
        ("1958-02-14", "mismatch"),
        (None, "not_comparable"),
    ],
)
def test_date_of_birth_comparison_respects_partial_dates(subject: str | None, result: str) -> None:
    assert compare_date_of_birth(subject, hit()).result == result


def test_date_of_birth_is_not_comparable_when_the_hit_has_none() -> None:
    comparison = compare_date_of_birth("1966-09-12", hit(dates_of_birth=()))
    assert comparison.result == "not_comparable" and comparison.evidence == ()


@pytest.mark.parametrize(
    ("subject", "result"), [(("ZZ", "GB"), "match"), (("GB",), "mismatch"), ((), "not_comparable")]
)
def test_nationality_comparison(subject: tuple[str, ...], result: str) -> None:
    assert compare_nationality(subject, hit()).result == result


def test_address_comparison() -> None:
    listed = hit(addresses=(Address(lines=("1 Example Quay",), postal_code="ZZ99 9ZZ", country="GB"),))
    assert compare_address({"lines": ["2 Other"], "postal_code": "zz999zz", "country": "GB"}, listed).result == "match"
    assert (
        compare_address({"lines": ["2 Other"], "postal_code": "ZZ1 1ZZ", "country": "GB"}, listed).result
        == "partial_match"
    )
    assert compare_address({"lines": ["2 Other"], "country": "US"}, listed).result == "mismatch"
    assert compare_address(None, listed).result == "not_comparable"


def test_associated_entities_comparison_normalises_business_names() -> None:
    assert compare_associated_entities(["Corvane Maritime Logistics Limited"], hit()).result == "match"
    assert compare_associated_entities(["Brightwater Lantern Works Ltd"], hit()).result == "mismatch"
    assert compare_associated_entities([], hit()).result == "not_comparable"


@pytest.mark.parametrize(
    ("subject", "associated", "outcome", "band"),
    [
        (
            {"name": "Radomir Vexley", "date_of_birth": "1966-09", "nationalities": ["ZZ"]},
            ["Corvane Maritime Logistics Ltd"],
            "true_match",
            "high",
        ),
        ({"name": "Radomir Vexley", "date_of_birth": "1958-02"}, [], "false_positive", "medium"),
        ({"name": "Orla Venncastle", "date_of_birth": "1966-09"}, [], "false_positive", "medium"),
        ({"name": "Radomir Vexley"}, ["Corvane Maritime Logistics Ltd"], "true_match", "medium"),
        ({"name": "Radomir Vexley"}, [], "insufficient_information", "low"),
        (
            {"name": "Radomir Vexley", "date_of_birth": "1966", "nationalities": ["GB"]},
            ["Other Example Ltd"],
            "true_match",
            "low",
        ),
    ],
)
def test_outcome_and_band_rules(subject: dict[str, Any], associated: list[str], outcome: str, band: str) -> None:
    comparisons = compare({"kind": "person", **subject}, hit(), associated=associated)
    assert propose(comparisons) == (outcome, band)


def test_template_rationale_states_every_comparison_and_the_outcome() -> None:
    comparisons = compare({"kind": "person", "name": "Radomir Vexley"}, hit(), associated=[])
    text = template_rationale(comparisons, "insufficient_information")
    assert text.startswith("The name matches. The date of birth cannot be compared.")
    assert text.endswith("Proposed outcome: insufficient information, for analyst review.")


def test_comparison_values_are_clipped_to_the_schema_limit() -> None:
    long = "Radomir " + "x" * 2000
    assert len(compare_name(long, hit(), business=False).to_dict()["subject_value"]) == 1024


# --- review / override capture --------------------------------------------------------------------


def unreviewed() -> dict[str, Any]:
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    document["review"] = None
    return document


def test_accepting_records_the_analyst_and_keeps_the_proposed_outcome() -> None:
    reviewed = apply_review(
        unreviewed(),
        {"action": "accepted", "final_outcome": "false_positive"},
        analyst_id="user:analyst-a",
        reviewed_at=AT,
    )
    assert reviewed["review"] == {
        "action": "accepted", "final_outcome": "false_positive", "analyst_id": "user:analyst-a",
        "reviewed_at": AT.isoformat(), "reason": None,
    }  # fmt: skip


def test_overriding_records_the_reason_and_analyst() -> None:
    request = DispositionReviewRequest(
        action="overridden", final_outcome="insufficient_information", reason="  Requesting a passport copy.  "
    )
    reviewed = apply_review(unreviewed(), request, analyst_id="user:analyst-b", reviewed_at=AT)
    assert reviewed["review"]["reason"] == "Requesting a passport copy."
    assert reviewed["review"]["analyst_id"] == "user:analyst-b"
    assert reviewed["proposed_outcome"] == "false_positive"


@pytest.mark.parametrize(
    ("document", "submitted", "analyst", "reason"),
    [
        (unreviewed(), {"action": "overridden", "final_outcome": "true_match"}, "user:a", "override_reason_required"),
        (
            unreviewed(),
            {"action": "overridden", "final_outcome": "true_match", "reason": "   "},
            "user:a",
            "override_reason_required",
        ),
        (
            unreviewed(),
            {"action": "overridden", "final_outcome": "false_positive", "reason": "same"},
            "user:a",
            "override_outcome_unchanged",
        ),
        (unreviewed(), {"action": "accepted", "final_outcome": "true_match"}, "user:a", "accepted_outcome_differs"),
        (
            unreviewed(),
            {"action": "accepted", "final_outcome": "false_positive"},
            "agent:screening_disposition",
            "analyst_invalid",
        ),
        (unreviewed(), {"action": "accepted", "final_outcome": "false_positive"}, " ", "analyst_invalid"),
        # Every identity that is not a human session's is refused, not only ``agent:``.
        (
            unreviewed(),
            {"action": "accepted", "final_outcome": "false_positive"},
            "api_key:ao_key_prefix",
            "analyst_invalid",
        ),
        (
            unreviewed(),
            {"action": "accepted", "final_outcome": "false_positive"},
            "workflow:business_onboarding",
            "analyst_invalid",
        ),
        (unreviewed(), {"action": "accepted", "final_outcome": "false_positive"}, "machine:mtls", "analyst_invalid"),
        (unreviewed(), {"action": "accepted", "final_outcome": "false_positive"}, "analyst-a", "analyst_invalid"),
        (unreviewed(), {"action": "accepted", "final_outcome": "false_positive"}, "user:", "analyst_invalid"),
        (unreviewed(), {"action": "closed", "final_outcome": "false_positive"}, "user:a", "request_invalid"),
        (
            unreviewed(),
            {"action": "accepted", "final_outcome": "false_positive", "analyst_id": "user:forged"},
            "user:a",
            "request_invalid",
        ),
        (
            json.loads(EXAMPLE.read_text(encoding="utf-8")),
            {"action": "accepted", "final_outcome": "false_positive"},
            "user:a",
            "already_reviewed",
        ),
        (
            {**unreviewed(), "comparisons": []},
            {"action": "accepted", "final_outcome": "false_positive"},
            "user:a",
            "disposition_invalid",
        ),
    ],
)
def test_reviews_are_refused_outside_their_rules(
    document: dict[str, Any], submitted: dict[str, Any], analyst: str, reason: str
) -> None:
    with pytest.raises(DispositionReviewError) as refused:
        apply_review(copy.deepcopy(document), submitted, analyst_id=analyst, reviewed_at=AT)
    assert refused.value.reason == reason


def test_review_time_must_carry_an_offset() -> None:
    with pytest.raises(DispositionReviewError, match="reviewed_at_naive"):
        apply_review(
            unreviewed(),
            {"action": "accepted", "final_outcome": "false_positive"},
            analyst_id="user:a",
            reviewed_at=datetime(2026, 9, 1),  # noqa: DTZ001 - a naive time is what is under test
        )


def test_review_does_not_mutate_the_input() -> None:
    document = unreviewed()
    apply_review(
        document, {"action": "accepted", "final_outcome": "false_positive"}, analyst_id="user:a", reviewed_at=AT
    )
    assert document["review"] is None


def test_documented_override_example() -> None:
    disposition = unreviewed()
    authenticated_user_id = "user:analyst-a"
    # docs-snippet: start record-override
    from datetime import UTC, datetime

    from core.agents.screening_disposition import DispositionReviewRequest, apply_review

    submitted = DispositionReviewRequest(
        action="overridden",
        final_outcome="insufficient_information",
        reason="Requesting a certified passport copy.",
    )
    reviewed = apply_review(
        disposition,
        submitted,
        analyst_id=authenticated_user_id,  # from the authenticated session, never the request body
        reviewed_at=datetime.now(UTC),
    )
    # docs-snippet: end record-override
    assert reviewed["review"]["action"] == "overridden"
