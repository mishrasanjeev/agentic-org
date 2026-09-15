# SPDX-License-Identifier: Apache-2.0
"""A-4 domain schemas: every schema is valid and versioned, every fixture validates, and the
schemas reject the documents the PRD says they must."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from referencing.exceptions import Unresolvable

from core import domain_schemas
from core.domain_schemas import (
    DOCUMENT_SCHEMAS,
    DOMAIN_SCHEMAS,
    DomainSchemaError,
    iter_errors,
    load_schema,
    schema_id,
    validate,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "schemas" / "examples"


class FixtureWithoutSchemaError(AssertionError):
    pass


def discover_example_fixtures(root: Path) -> list[tuple[str, Path]]:
    """Map every file under ``root`` to the schema it must validate against.

    A fixture is ``<root>/<schema name>/<case>.json``. Anything else - a file outside a schema
    directory, a directory named after no document schema, a non-JSON file - fails closed.
    """
    found: list[tuple[str, Path]] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root)
        if len(relative.parts) != 2 or relative.suffix != ".json":
            raise FixtureWithoutSchemaError(f"{relative.as_posix()}: fixtures must be <schema>/<case>.json")
        schema_name = relative.parts[0]
        if schema_name not in DOCUMENT_SCHEMAS:
            raise FixtureWithoutSchemaError(f"{relative.as_posix()}: no document schema named {schema_name!r}")
        found.append((schema_name, path))
    return found


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _example(schema_name: str) -> dict[str, Any]:
    path = next(p for name, p in discover_example_fixtures(EXAMPLES_DIR) if name == schema_name)
    return copy.deepcopy(_load_json(path))


_FIXTURES = discover_example_fixtures(EXAMPLES_DIR)


# --- schemas ------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", DOMAIN_SCHEMAS)
def test_schema_is_draft_2020_12_with_a_versioned_id(name: str) -> None:
    raw = _load_json(REPO_ROOT / "schemas" / f"{name}.schema.json")
    assert raw["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert raw["$id"] == schema_id(name) == f"https://agenticorg.ai/schemas/{name}/1.0.0"
    Draft202012Validator.check_schema(raw)
    assert load_schema(name) == raw


@pytest.mark.parametrize("name", DOCUMENT_SCHEMAS)
def test_document_schemas_reject_unknown_top_level_properties(name: str) -> None:
    assert load_schema(name).get("additionalProperties") is False


def test_every_document_schema_has_an_example_fixture() -> None:
    covered = {name for name, _ in _FIXTURES}
    assert covered == set(DOCUMENT_SCHEMAS)


# --- fixtures -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("schema_name", "path"), _FIXTURES, ids=[p.relative_to(EXAMPLES_DIR).as_posix() for _, p in _FIXTURES]
)
def test_every_fixture_validates_against_its_schema(schema_name: str, path: Path) -> None:
    validate(schema_name, _load_json(path))


def test_a_fixture_without_a_schema_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "ownership_graph").mkdir()
    (tmp_path / "ownership_graph" / "ok.json").write_text("{}", encoding="utf-8")
    (tmp_path / "vendor_scorecard").mkdir()
    (tmp_path / "vendor_scorecard" / "orphan.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FixtureWithoutSchemaError, match="no document schema named 'vendor_scorecard'"):
        discover_example_fixtures(tmp_path)


@pytest.mark.parametrize("relative", ["loose.json", "ownership_graph/notes.txt", "ownership_graph/nested/deep.json"])
def test_a_fixture_outside_the_layout_fails_closed(tmp_path: Path, relative: str) -> None:
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")
    with pytest.raises(FixtureWithoutSchemaError):
        discover_example_fixtures(tmp_path)


# --- validator helper fails closed --------------------------------------------------------------


def test_unknown_schema_name_fails_closed() -> None:
    with pytest.raises(DomainSchemaError) as caught:
        validate("vendor_scorecard", {})
    assert caught.value.reason == "unknown_schema"


def test_shared_definitions_are_not_a_document_type() -> None:
    with pytest.raises(DomainSchemaError) as caught:
        validate("common", {})
    assert caught.value.reason == "unknown_schema"


def test_invalid_document_reports_every_error_with_its_location() -> None:
    doc = _example("ownership_graph")
    doc["as_of"] = "2026-09-01 09:00"
    doc["edges"][0]["share_pct"] = {"min": 50, "max": 175}
    with pytest.raises(DomainSchemaError) as caught:
        validate("ownership_graph", doc)
    assert caught.value.reason == "document_invalid"
    joined = "\n".join(caught.value.errors)
    assert "$.as_of" in joined
    assert "$.edges[0].share_pct" in joined


def test_references_outside_the_domain_schemas_are_never_fetched() -> None:
    registry = domain_schemas._registry()
    with pytest.raises(Unresolvable):
        registry.resolver().lookup("https://example.com/schemas/remote.json")


def test_an_unresolvable_reference_during_validation_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    remote = Draft202012Validator(
        {"$ref": "https://example.com/schemas/remote.json"}, registry=domain_schemas._registry()
    )
    monkeypatch.setattr(domain_schemas, "_validator", lambda name: remote)
    with pytest.raises(DomainSchemaError) as caught:
        validate("policy_result", {})
    assert caught.value.reason == "schema_reference_unresolvable"


def test_a_schema_whose_id_does_not_match_its_name_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _load_json(REPO_ROOT / "schemas" / "policy_result.schema.json")
    raw["$id"] = "https://agenticorg.ai/schemas/policy_result/0.9.0"
    (tmp_path / "policy_result.schema.json").write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(domain_schemas, "SCHEMAS_DIR", tmp_path)
    domain_schemas._load.cache_clear()
    try:
        with pytest.raises(DomainSchemaError) as caught:
            load_schema("policy_result")
        assert caught.value.reason == "schema_id_mismatch"
    finally:
        domain_schemas._load.cache_clear()


def test_an_unreadable_schema_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "policy_result.schema.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(domain_schemas, "SCHEMAS_DIR", tmp_path)
    domain_schemas._load.cache_clear()
    try:
        with pytest.raises(DomainSchemaError) as caught:
            load_schema("policy_result")
        assert caught.value.reason == "schema_unreadable"
    finally:
        domain_schemas._load.cache_clear()


# --- what the schemas must reject ---------------------------------------------------------------


def _errors_after(schema_name: str, mutate: Any) -> list[str]:
    doc = _example(schema_name)
    mutate(doc)
    return iter_errors(schema_name, doc)


def _memo_section(doc: dict[str, Any], section_id: str) -> dict[str, Any]:
    return next(s for s in doc["sections"] if s["section_id"] == section_id)


def test_memo_section_without_evidence_is_rejected() -> None:
    assert _errors_after("underwriting_memo", lambda d: _memo_section(d, "registry").update(evidence=[]))


@pytest.mark.parametrize("missing", ["provider", "record_id", "field", "retrieved_at", "excerpt_ref"])
def test_memo_evidence_entry_requires_every_citation_field(missing: str) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        del _memo_section(doc, "registry")["evidence"][0][missing]

    assert _errors_after("underwriting_memo", mutate)


def test_memo_finding_without_evidence_is_rejected() -> None:
    assert _errors_after(
        "underwriting_memo", lambda d: _memo_section(d, "ownership")["findings"][0].update(evidence=[])
    )


def test_not_available_section_needs_a_reason_and_carries_no_findings() -> None:
    assert _errors_after("underwriting_memo", lambda d: _memo_section(d, "web_presence").pop("not_available_reason"))
    finding = {"code": "x_finding", "severity": "info", "statement": "s", "evidence": []}
    assert _errors_after("underwriting_memo", lambda d: _memo_section(d, "web_presence")["findings"].append(finding))


def test_available_section_cannot_claim_a_not_available_reason() -> None:
    assert _errors_after(
        "underwriting_memo",
        lambda d: _memo_section(d, "registry").update(not_available_reason="capability_not_supported"),
    )


def test_memo_recommendation_always_requires_a_human_decision() -> None:
    assert _errors_after("underwriting_memo", lambda d: d["recommendation"].update(requires_human_decision=False))
    assert _errors_after("underwriting_memo", lambda d: d["recommendation"].update(basis="model_confidence"))


def test_timestamps_without_an_offset_are_rejected() -> None:
    assert _errors_after("screening_result", lambda d: d.update(screened_at="2026-09-01T09:05:00"))


def test_unknown_nested_property_is_rejected() -> None:
    assert _errors_after("screening_result", lambda d: d["hits"][0].update(vendor_score=0.5))


def test_overridden_disposition_requires_a_reason() -> None:
    assert _errors_after("screening_disposition", lambda d: d["review"].update(reason=None))
    assert _errors_after("screening_disposition", lambda d: d["review"].update(reason=""))


def test_accepted_disposition_does_not_need_a_reason() -> None:
    assert not _errors_after(
        "screening_disposition",
        lambda d: d["review"].update(action="accepted", final_outcome="false_positive", reason=None),
    )


def test_disposition_outcome_cannot_be_an_automatic_closure() -> None:
    assert _errors_after("screening_disposition", lambda d: d.update(proposed_outcome="closed"))


def test_decided_case_requires_a_decision_and_undecided_case_forbids_one() -> None:
    assert _errors_after("business_case", lambda d: d.update(state="decided"))
    decided = _load_json(EXAMPLES_DIR / "business_case" / "us_decided_approve.json")
    decided["state"] = "in_progress"
    assert iter_errors("business_case", decided)


def test_policy_result_rejects_an_unknown_tier() -> None:
    assert _errors_after("policy_result", lambda d: d.update(tier="critical"))


def test_completed_case_push_requires_a_memo() -> None:
    assert _errors_after("case_push", lambda d: d.update(memo=None))
    assert not _errors_after("case_push", lambda d: d.update(event_type="case.updated", memo=None))


def test_case_push_event_id_must_be_a_uuid() -> None:
    assert _errors_after("case_push", lambda d: d.update(event_id="evt-1"))


def test_schema_version_is_major_version_one() -> None:
    assert _errors_after("policy_result", lambda d: d.update(schema_version="2.0.0"))
    assert not _errors_after("policy_result", lambda d: d.update(schema_version="1.4.2"))


def test_docs_example_validating_a_document() -> None:
    document = _example("ownership_graph")
    # docs-snippet: start validate-document
    from core.domain_schemas import DomainSchemaError, validate

    validate("ownership_graph", document)  # returns None: the document conforms

    document["as_of"] = "yesterday"
    try:
        validate("ownership_graph", document)
    except DomainSchemaError as exc:
        assert exc.reason == "document_invalid"
        assert exc.errors == ["$.as_of: 'yesterday' is not a 'date-time'"]
    # docs-snippet: end validate-document
    else:
        pytest.fail("an invalid document validated")


# --- the tenant schema registry seed is unchanged -----------------------------------------------


def test_domain_schemas_are_not_seeded_into_tenant_schema_registries() -> None:
    from scripts.seed_data import DOMAIN_SCHEMA_BASE_URI, tenant_entity_schema_files

    assert DOMAIN_SCHEMA_BASE_URI == domain_schemas.SCHEMA_BASE_URI

    seeded = {p.name for p in tenant_entity_schema_files()}
    assert "vendor.schema.json" in seeded
    assert not seeded & {f"{name}.schema.json" for name in DOMAIN_SCHEMAS}


def _iter_example_strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_example_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_example_strings(item)
    elif isinstance(value, str):
        yield value


def test_example_fixtures_use_only_reserved_domains() -> None:
    for _, path in _FIXTURES:
        for text in _iter_example_strings(_load_json(path)):
            if text.startswith(("http://", "https://")):
                host = text.split("/")[2]
                assert host == "example.com" or host.endswith(".example.com"), f"{path.name}: {text}"
