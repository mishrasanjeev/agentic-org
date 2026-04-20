"""Tests for the ABM intent-score seeding introduced for TC_014.

Real intent scores come from the IntentAggregator (Bombora / G2 /
TrustRadius) and are expensive to compute at upload time. We seed a
deterministic per-(tier, domain) placeholder so the dashboard shows
varied, in-band values immediately — the real score replaces it when
the user explicitly triggers ``/accounts/{id}/intent``.
"""

from __future__ import annotations

import pytest

from api.v1.abm import _seed_intent_score


@pytest.mark.parametrize(
    "tier,band_lo,band_hi",
    [
        ("1", 65, 90),
        ("2", 40, 70),
        ("3", 20, 50),
    ],
)
def test_scores_land_in_documented_tier_band(
    tier: str, band_lo: int, band_hi: int,
) -> None:
    for domain in [
        "acme.com", "wipro.in", "tcs.com", "infosys.com", "hcltech.com",
    ]:
        score = _seed_intent_score(tier, domain)
        assert band_lo <= score <= band_hi, (
            f"tier={tier} domain={domain}: {score} outside [{band_lo},{band_hi}]"
        )


def test_scores_are_deterministic_across_calls() -> None:
    """Same (tier, domain) always produces the same score — important
    so re-ingesting a CSV doesn't shuffle dashboard numbers."""
    a = _seed_intent_score("1", "acme.com")
    b = _seed_intent_score("1", "acme.com")
    assert a == b


def test_scores_vary_across_domains() -> None:
    """TC_014 explicitly requires: 'Scores should vary across accounts'."""
    scores = {
        _seed_intent_score("1", d)
        for d in [
            "acme.com", "wipro.in", "tcs.com",
            "infosys.com", "hcltech.com",
        ]
    }
    # At least 3 distinct values across 5 domains.
    assert len(scores) >= 3, f"too little variance: {scores}"


def test_unknown_tier_falls_back_to_tier_2_band() -> None:
    # Defensive — shouldn't happen if validators work, but we want to
    # never raise here because this runs inside the upload loop.
    score = _seed_intent_score("99", "acme.com")
    assert 40 <= score <= 70
