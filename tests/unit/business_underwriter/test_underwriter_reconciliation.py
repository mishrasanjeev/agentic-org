# SPDX-License-Identifier: Apache-2.0
"""§8.3 unit: ownership normalisation and reconciliation (missing_owner / undeclared_owner)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from connectors.framework.verification_provider import (
    BusinessRef,
    Evidence,
    OwnershipEdge,
    OwnershipGraph,
    OwnershipNode,
    OwnershipNodeKind,
    OwnershipRelationship,
    PercentageRange,
)
from core.agents.business_underwriter.reconciliation import effective_interests, normalise_name, reconcile

AT = datetime(2026, 9, 1, tzinfo=UTC)
EV = (Evidence(provider="mock", record_id="rec-1", field="name", retrieved_at=AT),)


def node(
    node_id: str, name: str, kind: OwnershipNodeKind = OwnershipNodeKind.PERSON, dob: str | None = None
) -> OwnershipNode:
    return OwnershipNode(node_id=node_id, kind=kind, name=name, date_of_birth=dob, evidence=EV)


def edge(
    owner: str,
    owned: str,
    pct: float | None,
    *,
    relationship=OwnershipRelationship.SHAREHOLDING,
    low: float | None = None,
) -> OwnershipEdge:
    share = None if pct is None else PercentageRange(min=pct if low is None else low, max=pct)
    return OwnershipEdge(from_node_id=owner, to_node_id=owned, relationship=relationship, share_pct=share, evidence=EV)


def graph(nodes: list[OwnershipNode], edges: list[OwnershipEdge], completeness: str = "complete") -> OwnershipGraph:
    subject = node("biz-subject", "Example Subject Ltd", OwnershipNodeKind.BUSINESS)
    return OwnershipGraph(
        provider="mock",
        subject=BusinessRef(provider="mock", provider_ref="mock-gb-00000001", jurisdiction="GB"),
        subject_node_id="biz-subject",
        as_of=AT,
        completeness=completeness,  # type: ignore[arg-type]
        nodes=(subject, *nodes),
        edges=tuple(edges),
        evidence=EV,
    )


def owner(name: str, pct: float | None, *, kind: str = "person", dob: str | None = None) -> dict[str, Any]:
    return {"name": name, "kind": kind, "ownership_pct": pct, "date_of_birth": dob}


@pytest.mark.parametrize(
    ("left", "right", "business"),
    [
        ("Orla Venncastle", "VENNCASTLE, Orla", False),
        ("Dr. Orla Venncastle", "orla venncastle", False),
        ("Zoë Brønte-Hale", "Zoe Bronte Hale", False),
        ("Tidewell Ledger Holdings LLC", "Tidewell Ledger Holdings", True),
        ("Brightwater & Sons Limited", "Brightwater and Sons Ltd", True),
    ],
)
def test_names_normalise_to_the_same_tokens(left: str, right: str, business: bool) -> None:
    assert normalise_name(left, business=business) == normalise_name(right, business=business)


def test_different_names_do_not_normalise_together() -> None:
    assert normalise_name("Orla Venncastle") != normalise_name("Orla Vennecastle")


def test_declared_owner_at_or_above_threshold_absent_from_graph_is_missing() -> None:
    g = graph([node("p1", "Tamsin Quellbridge")], [edge("p1", "biz-subject", 60)])
    result = reconcile([owner("Tamsin Quellbridge", 60), owner("Ansel Pikeworth", 25)], g, threshold_pct=25)
    assert [m.declared_index for m in result.missing_owners] == [1]
    assert result.undeclared_owners == ()


def test_declared_owner_below_threshold_absent_from_graph_is_not_missing() -> None:
    g = graph([node("p1", "Tamsin Quellbridge")], [edge("p1", "biz-subject", 90)])
    result = reconcile([owner("Tamsin Quellbridge", 90), owner("Ansel Pikeworth", 10)], g, threshold_pct=25)
    assert result.missing_owners == ()


def test_declared_owner_without_a_stated_share_is_treated_as_reaching_the_threshold() -> None:
    g = graph([], [])
    result = reconcile([owner("Ansel Pikeworth", None)], g)
    assert [m.declared_index for m in result.missing_owners] == [0]


def test_graph_owner_at_threshold_not_declared_is_undeclared() -> None:
    g = graph(
        [node("p1", "Imogen Saltonstall"), node("p2", "Caspian Wrexford")],
        [edge("p1", "biz-subject", 75), edge("p2", "biz-subject", 25)],
    )
    result = reconcile([owner("Imogen Saltonstall", 100)], g, threshold_pct=25)
    assert [u.node_id for u in result.undeclared_owners] == ["p2"]
    assert result.undeclared_owners[0].effective_pct == 25


def test_graph_owner_below_threshold_not_declared_is_not_reported() -> None:
    g = graph(
        [node("p1", "Imogen Saltonstall"), node("p2", "Caspian Wrexford")],
        [edge("p1", "biz-subject", 90), edge("p2", "biz-subject", 10)],
    )
    assert reconcile([owner("Imogen Saltonstall", 90)], g).undeclared_owners == ()


def test_a_reported_band_reaching_the_threshold_counts_as_reaching_it() -> None:
    g = graph([node("p1", "Caspian Wrexford")], [edge("p1", "biz-subject", 50, low=25)])
    assert [u.node_id for u in reconcile([], g, threshold_pct=30).undeclared_owners] == ["p1"]


def test_indirect_ownership_multiplies_along_the_path() -> None:
    g = graph(
        [node("b1", "Holding Example LLC", OwnershipNodeKind.BUSINESS), node("p1", "Marisol Fenwright")],
        [edge("b1", "biz-subject", 50), edge("p1", "b1", 40)],
    )
    interests = effective_interests(g)
    assert interests["p1"][0] == 20
    assert [u.node_id for u in reconcile([], g, threshold_pct=20).undeclared_owners] == ["b1", "p1"]
    assert [u.node_id for u in reconcile([], g, threshold_pct=25).undeclared_owners] == ["b1"]


def test_interest_held_through_a_declared_corporate_owner_is_disclosed_at_that_level() -> None:
    g = graph(
        [node("b1", "Tidewell Ledger Holdings LLC", OwnershipNodeKind.BUSINESS), node("p1", "Marisol Fenwright")],
        [edge("b1", "biz-subject", 60), edge("p1", "b1", 100)],
    )
    result = reconcile([owner("Tidewell Ledger Holdings", 60, kind="business")], g)
    assert result.undeclared_owners == () and result.missing_owners == ()


def test_control_without_a_percentage_cannot_be_ruled_below_threshold() -> None:
    g = graph(
        [node("p1", "Caspian Wrexford")],
        [edge("p1", "biz-subject", None, relationship=OwnershipRelationship.SIGNIFICANT_INFLUENCE)],
    )
    [undeclared] = reconcile([], g).undeclared_owners
    assert undeclared.effective_pct is None


def test_a_date_of_birth_that_disagrees_prevents_a_match() -> None:
    g = graph([node("p1", "Tamsin Quellbridge", dob="1958-02")], [edge("p1", "biz-subject", 100)])
    result = reconcile([owner("Tamsin Quellbridge", 100, dob="1983-11")], g)
    assert len(result.missing_owners) == 1 and len(result.undeclared_owners) == 1
    partial = reconcile([owner("Tamsin Quellbridge", 100, dob="1958")], g)
    assert partial.missing_owners == () and partial.undeclared_owners == ()


def test_a_person_never_matches_a_business_node_of_the_same_name() -> None:
    g = graph([node("b1", "Orla Venncastle", OwnershipNodeKind.BUSINESS)], [edge("b1", "biz-subject", 100)])
    result = reconcile([owner("Orla Venncastle", 100)], g)
    assert len(result.missing_owners) == 1


def test_ended_relationships_and_cycles_are_ignored() -> None:
    ended = edge("p1", "biz-subject", 100).model_copy(update={"ended_on": "2020-01-01"})
    g = graph(
        [node("p1", "Tamsin Quellbridge"), node("b1", "Loop Example LLC", OwnershipNodeKind.BUSINESS)],
        [ended, edge("b1", "biz-subject", 100), edge("biz-subject", "b1", 100)],
    )
    interests = effective_interests(g)
    assert "p1" not in interests and interests["b1"][0] == 100


def test_threshold_must_be_a_percentage() -> None:
    with pytest.raises(ValueError, match="threshold_pct"):
        reconcile([], graph([], []), threshold_pct=0)


def test_reconciliation_is_deterministic() -> None:
    g = graph(
        [node("p2", "B Person"), node("p1", "A Person")], [edge("p2", "biz-subject", 30), edge("p1", "biz-subject", 30)]
    )
    assert reconcile([], g) == reconcile([], g)
    assert [u.node_id for u in reconcile([], g).undeclared_owners] == ["p1", "p2"]
