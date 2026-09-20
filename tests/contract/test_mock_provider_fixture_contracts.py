# SPDX-License-Identifier: Apache-2.0
"""The mock provider's fixtures validate against the domain schemas and are plainly synthetic."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from connectors.framework.verification_provider import Deadline, PersonSubject, ScreenOptions
from connectors.providers.mock import MockConfig, MockProvider
from connectors.providers.mock.data import FIXTURES_DIR, load_dataset
from core.domain_schemas import validate

DATASET = load_dataset()
BUSINESS_CASE_TEMPLATE = json.loads(
    (
        Path(__file__).resolve().parents[2] / "schemas" / "examples" / "business_case" / "gb_awaiting_decision.json"
    ).read_text(encoding="utf-8")
)
_RESERVED_IDENTIFIERS = {
    "gb_company_number": re.compile(r"^0000000[0-9]$"),
    "us_ein": re.compile(r"^00-000000[0-9]$"),
    "us_state_file_number": re.compile(r"^00000[0-9]{2}$"),
}


def test_every_fixture_file_is_recognised_by_the_loader() -> None:
    files = {p for p in FIXTURES_DIR.rglob("*") if p.is_file()}
    assert len(files) == len(DATASET.businesses) + 1 + len(DATASET.webhooks)


@pytest.mark.parametrize("key", [b.key for b in DATASET.businesses])
def test_sample_application_validates_as_a_business_case(key: str) -> None:
    document = copy.deepcopy(BUSINESS_CASE_TEMPLATE)
    document.update(case_id=f"case-{key}", state="submitted", subject=None, decision=None)
    document["application"] = DATASET.business(key).application
    validate("business_case", document)


@pytest.mark.parametrize("key", [b.key for b in DATASET.businesses if b.registry is not None])
async def test_ownership_graph_and_officer_screenings_validate_against_their_schemas(key: str) -> None:
    mock = MockProvider(MockConfig(clock=lambda: datetime(2026, 9, 1, 9, 0, tzinfo=UTC)))
    graph = await mock.ownership(mock.ref_for(key), deadline=Deadline.after(5))
    validate("ownership_graph", graph.model_dump(mode="json"))
    registry = mock.fixture(key).registry
    assert registry is not None
    for index, officer in enumerate(registry.officers):
        result = await mock.screen_person(
            PersonSubject(full_name=officer.name, date_of_birth=officer.date_of_birth),
            ScreenOptions(idempotency_key=f"{key}-contract-{index}"),
            deadline=Deadline.after(5),
        )
        validate("screening_result", result.model_dump(mode="json"))


def _is_reserved_domain(domain: str) -> bool:
    labels = domain.lower().split(".")
    return labels[-2:] in (["example", "com"], ["example", "org"])


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


def _identifiers(value: Any) -> Iterator[dict[str, str]]:
    if isinstance(value, dict):
        if set(value) == {"scheme", "value"}:
            yield value
        for item in value.values():
            yield from _identifiers(item)
    elif isinstance(value, list):
        for item in value:
            yield from _identifiers(item)


@pytest.mark.parametrize("path", sorted(FIXTURES_DIR.rglob("*.json")), ids=lambda p: p.name)
def test_fixtures_use_only_reserved_identifiers_and_example_domains(path: Path) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    for identifier in _identifiers(document):
        assert _RESERVED_IDENTIFIERS[identifier["scheme"]].match(identifier["value"]), identifier
    for text in _strings(document):
        for host in re.findall(r"https?://([^/\s\"]+)", text):
            assert _is_reserved_domain(host), host
        for domain in re.findall(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|co\.uk)\b", text):
            assert _is_reserved_domain(domain), domain
        for phone in re.findall(r"\b\d{3}-\d{4}\b", text):
            assert phone.startswith("555-01"), phone
