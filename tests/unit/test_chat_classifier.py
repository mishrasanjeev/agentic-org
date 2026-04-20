"""Unit tests for the NL query domain classifier (TC_004).

The classifier feeds both routing AND the hardcoded confidence fallback
in ``api/v1/chat.py``. Before PR-F a query like "What is our cash
runway" fell through to domain=="general" because "cash" and "runway"
weren't in any keyword list, so the response got the hardcoded 60%
confidence and a generic placeholder answer. These tests pin down the
keyword-level expectations so the regression can't sneak back.
"""

from __future__ import annotations

import pytest

from api.v1.chat import _classify_domain


@pytest.mark.parametrize(
    "query",
    [
        "What is our cash runway",
        "cash runway for next quarter",
        "what's our burn rate",
        "show MRR trend",
        "EBITDA this year",
        "how much liquidity do we have",
        "working capital situation",
        "ARR projections",
    ],
)
def test_finance_queries_route_to_finance(query: str) -> None:
    assert _classify_domain(query) == "finance", (
        f"{query!r} should route to finance"
    )


@pytest.mark.parametrize(
    "query,expected",
    [
        ("supply chain delays", "operations"),
        ("ticket status", "operations"),
        ("send email to the team", "communications"),
        ("schedule a meeting", "communications"),
    ],
)
def test_other_domains_still_route_correctly(query: str, expected: str) -> None:
    """Adding finance keywords must not steal matches from other
    domains."""
    assert _classify_domain(query) == expected


def test_truly_ambiguous_query_stays_general() -> None:
    """Queries with no domain signals should still fall back to
    'general' — we'd rather answer honestly than mis-route."""
    assert _classify_domain("hello there") == "general"
