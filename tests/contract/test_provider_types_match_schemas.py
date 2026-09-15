# SPDX-License-Identifier: Apache-2.0
"""The provider domain types serialise to the published A-4 schemas and share their vocabularies."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest

from connectors.framework.verification_provider import (
    Evidence,
    ListType,
    OwnershipGraph,
    OwnershipNodeKind,
    OwnershipRelationship,
    PartyKind,
    RegistryStatus,
    ScreeningResult,
)
from core.domain_schemas import load_schema, validate

EXAMPLES = Path(__file__).resolve().parents[2] / "schemas" / "examples"


def _example(schema: str, name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / schema / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("model", "schema", "fixture"),
    [
        (OwnershipGraph, "ownership_graph", "gb_two_person_owners.json"),
        (ScreeningResult, "screening_result", "person_probable_false_positive.json"),
    ],
)
def test_domain_type_round_trips_the_schema_fixture(model: Any, schema: str, fixture: str) -> None:
    document = _example(schema, fixture)
    parsed = model.model_validate(document)
    dumped = parsed.model_dump(mode="json")
    validate(schema, dumped)
    assert dumped == document


def test_evidence_serialises_every_citation_key() -> None:
    from datetime import UTC, datetime

    evidence = Evidence(
        provider="acme_kyb", record_id="acme:1", field="status", retrieved_at=datetime(2026, 9, 1, tzinfo=UTC)
    )
    common = load_schema("common")["$defs"]["evidence"]
    assert set(evidence.model_dump(mode="json")) == set(common["required"]) == set(common["properties"])
    assert evidence.model_dump(mode="json")["excerpt_ref"] is None


def _schema_enum(schema: str, *path: str) -> set[str]:
    node: Any = load_schema(schema)
    for key in path:
        node = node[key]
    return set(node["enum"])


@pytest.mark.parametrize(
    ("enum", "schema", "path"),
    [
        (RegistryStatus, "common", ("$defs", "registry_status")),
        (PartyKind, "common", ("$defs", "party_kind")),
        (ListType, "screening_result", ("$defs", "list_type")),
        (OwnershipNodeKind, "ownership_graph", ("$defs", "node", "properties", "kind")),
        (OwnershipRelationship, "ownership_graph", ("$defs", "edge", "properties", "relationship")),
    ],
)
def test_vocabularies_match_the_schemas(enum: type[StrEnum], schema: str, path: tuple[str, ...]) -> None:
    assert {member.value for member in enum} == _schema_enum(schema, *path)


def test_evidence_accepts_the_same_excerpt_references_as_the_schema() -> None:
    from datetime import UTC, datetime

    from core.extraction import excerpt_ref

    ref = excerpt_ref("website", "declared_activity", "Hand-finished brass lanterns")
    evidence = Evidence(
        provider="acme_kyb",
        record_id="acme:1",
        field="content",
        retrieved_at=datetime(2026, 9, 1, tzinfo=UTC),
        excerpt_ref=ref,
    )
    memo_evidence = {"$ref": "https://agenticorg.ai/schemas/common/1.0.0#/$defs/evidence"}
    from jsonschema import Draft202012Validator

    from core import domain_schemas

    validator = Draft202012Validator(memo_evidence, registry=domain_schemas._registry())
    assert list(validator.iter_errors(evidence.model_dump(mode="json"))) == []
