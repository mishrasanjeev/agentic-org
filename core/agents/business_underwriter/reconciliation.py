# SPDX-License-Identifier: Apache-2.0
"""Reconcile the owners an applicant declared against a provider's ownership graph.

Pure and deterministic: no model, no I/O. Names are compared after normalisation (Unicode NFKC,
case-folding, accents and punctuation removed, honorifics and legal-form suffixes dropped, word
order ignored), and a date of birth, when both sides have one, must agree to the precision both
give.

- ``missing_owner``: a declared owner whose declared share is at or above the threshold (or not
  stated) and who matches no node in the graph.
- ``undeclared_owner``: a node that owns or controls the subject, directly or through other
  nodes, at or above the threshold (or through a control relationship with no percentage) and
  that matches no declared owner. An interest held through a declared owner (the people behind a
  declared corporate shareholder) is disclosed at that level and is not reported.

Effective ownership through intermediate nodes multiplies shares along each path and takes the
largest path, using the upper bound of a reported band, so a band that reaches the threshold is
treated as reaching it.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from connectors.framework.verification_provider import (
    Evidence,
    OwnershipEdge,
    OwnershipGraph,
    OwnershipNode,
    OwnershipNodeKind,
)

DEFAULT_THRESHOLD_PCT = 25.0

_HONORIFICS = frozenset({"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "sir", "dame", "lord", "lady"})
_LEGAL_FORMS = frozenset(
    {
        "ltd", "limited", "llc", "llp", "lp", "inc", "incorporated", "corp", "corporation", "co", "company",
        "plc", "cic", "gmbh", "sa", "sarl", "bv", "nv", "ag", "pty", "the",
    }
)  # fmt: skip
_MAX_DEPTH = 10
# Letters that do not decompose into a base letter and a combining mark.
_FOLD = str.maketrans({"ø": "o", "æ": "ae", "œ": "oe", "ß": "ss", "đ": "d", "ł": "l", "þ": "th", "ı": "i"})


def normalise_name(name: str, *, business: bool = False) -> tuple[str, ...]:
    """Sorted name tokens for comparison. ``business`` also drops legal-form words such as ``Ltd``."""
    text = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", name))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold().translate(_FOLD)
    text = text.replace("&", " and ")
    tokens = [token for token in re.split(r"[^\w]+|_", text) if token]
    drop = _LEGAL_FORMS if business else _HONORIFICS
    kept = [token for token in tokens if token not in drop]
    return tuple(sorted(kept or tokens))


def _dates_agree(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return True
    shared = min(len(left), len(right))
    return left[:shared] == right[:shared]


@dataclass(frozen=True, slots=True)
class DeclaredOwner:
    index: int
    name: str
    kind: str
    date_of_birth: str | None
    ownership_pct: float | None

    @classmethod
    def from_application(cls, index: int, owner: dict[str, Any]) -> DeclaredOwner:
        pct = owner.get("ownership_pct")
        return cls(
            index=index,
            name=str(owner["name"]),
            kind=str(owner.get("kind") or "person"),
            date_of_birth=owner.get("date_of_birth"),
            ownership_pct=float(pct) if isinstance(pct, int | float) and not isinstance(pct, bool) else None,
        )


@dataclass(frozen=True, slots=True)
class OwnerMatch:
    declared_index: int
    node_id: str


@dataclass(frozen=True, slots=True)
class MissingOwner:
    declared_index: int
    declared_pct: float | None


@dataclass(frozen=True, slots=True)
class UndeclaredOwner:
    node_id: str
    kind: str
    effective_pct: float | None
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class Reconciliation:
    threshold_pct: float
    matches: tuple[OwnerMatch, ...]
    missing_owners: tuple[MissingOwner, ...]
    undeclared_owners: tuple[UndeclaredOwner, ...]
    graph_completeness: str


def _node_matches(declared: DeclaredOwner, node: OwnershipNode) -> bool:
    business = node.kind is not OwnershipNodeKind.PERSON
    if declared.kind == "person" and node.kind not in (OwnershipNodeKind.PERSON, OwnershipNodeKind.UNKNOWN):
        return False
    if declared.kind == "business" and node.kind is OwnershipNodeKind.PERSON:
        return False
    if normalise_name(declared.name, business=business) != normalise_name(node.name, business=business):
        return False
    return _dates_agree(declared.date_of_birth, node.date_of_birth)


def effective_interests(graph: OwnershipGraph) -> dict[str, tuple[float | None, tuple[OwnershipEdge, ...]]]:
    """For each node that owns or controls the subject: its largest effective share and the edges on that path.

    The share is ``None`` when the path runs through a control relationship without a percentage.
    """
    incoming: dict[str, list[OwnershipEdge]] = {}
    for edge in graph.edges:
        if edge.ended_on is None:
            incoming.setdefault(edge.to_node_id, []).append(edge)

    best: dict[str, tuple[float | None, tuple[OwnershipEdge, ...]]] = {}

    def edge_share(edge: OwnershipEdge) -> float | None:
        bands = [band.max for band in (edge.share_pct, edge.voting_pct) if band is not None]
        if bands:
            return max(bands)
        return None

    def visit(node_id: str, share: float | None, path: tuple[OwnershipEdge, ...], seen: frozenset[str]) -> None:
        if len(path) >= _MAX_DEPTH:
            return
        for edge in sorted(incoming.get(node_id, ()), key=lambda e: (e.from_node_id, e.relationship.value)):
            owner = edge.from_node_id
            if owner in seen:
                continue
            own = edge_share(edge)
            combined = None if share is None or own is None else share * own / 100
            new_path = (*path, edge)
            current = best.get(owner)
            if current is None or _larger(combined, current[0]):
                best[owner] = (combined, new_path)
            visit(owner, combined, new_path, seen | {owner})

    visit(graph.subject_node_id, 100.0, (), frozenset({graph.subject_node_id}))
    return best


def _larger(candidate: float | None, current: float | None) -> bool:
    # An unquantified control interest ranks above any quantified one: it cannot be ruled below the threshold.
    if current is None:
        return False
    if candidate is None:
        return True
    return candidate > current


def reconcile(
    declared_owners: list[dict[str, Any]], graph: OwnershipGraph, *, threshold_pct: float = DEFAULT_THRESHOLD_PCT
) -> Reconciliation:
    if not 0 < threshold_pct <= 100:
        raise ValueError("threshold_pct must be in (0, 100]")
    declared = [DeclaredOwner.from_application(index, owner) for index, owner in enumerate(declared_owners)]
    nodes = {node.node_id: node for node in graph.nodes if node.node_id != graph.subject_node_id}
    interests = effective_interests(graph)

    matches: list[OwnerMatch] = []
    matched_nodes: set[str] = set()
    missing: list[MissingOwner] = []
    for owner in declared:
        candidates = sorted(node_id for node_id, node in nodes.items() if _node_matches(owner, node))
        if candidates:
            matches.extend(OwnerMatch(owner.index, node_id) for node_id in candidates)
            matched_nodes.update(candidates)
        elif owner.ownership_pct is None or owner.ownership_pct >= threshold_pct:
            missing.append(MissingOwner(owner.index, owner.ownership_pct))

    undeclared: list[UndeclaredOwner] = []
    for node_id in sorted(interests):
        if node_id in matched_nodes or node_id not in nodes:
            continue
        share, path = interests[node_id]
        if share is not None and share < threshold_pct:
            continue
        if any(edge.to_node_id in matched_nodes for edge in path):
            # Held through an owner the applicant declared: disclosed at that level.
            continue
        node = nodes[node_id]
        evidence = tuple(node.evidence) + tuple(e for edge in path for e in edge.evidence)
        undeclared.append(UndeclaredOwner(node_id, node.kind.value, share, evidence or tuple(graph.evidence)))

    return Reconciliation(
        threshold_pct=threshold_pct,
        matches=tuple(matches),
        missing_owners=tuple(missing),
        undeclared_owners=tuple(undeclared),
        graph_completeness=graph.completeness,
    )
