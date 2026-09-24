# SPDX-License-Identifier: Apache-2.0
"""Screening Disposition test set-up: screen a fixture party on the mock provider and run the agent on a hit."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from connectors.framework.verification_provider import BusinessSubject, Deadline, PersonSubject, ScreenOptions
from connectors.providers.mock import MockConfig, MockProvider
from core.agents.screening_disposition import DispositionConfig, DispositionDependencies, run_screening_disposition
from core.test_doubles.grant_authorizer import ALLOW_PROVIDER_CALLS

FROZEN = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)

#: fixture key -> (party name, kind, associated entity names from the case)
PARTIES: dict[str, tuple[str, str]] = {
    "gb-missing-owner-marlpit": ("Tamsin Quellbridge", "person"),
    "us-false-positive-oakhollow": ("Jorund Halvessen", "person"),
    "gb-true-match-corvane": ("Radomir Vexley", "person"),
    "gb-adversarial-northgate": ("Wilhelmina Strand", "person"),
}


async def screen(
    provider: MockProvider, key: str, *, business: bool = False
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """``(screening_result_json, subject, associated_entities)`` for the fixture's party with a hit."""
    application = provider.fixture(key).application
    associated = [application["legal_name"], *(o["name"] for o in application["declared_owners"])]
    if business:
        subject = {
            "kind": "business",
            "name": application["legal_name"],
            "jurisdiction": application["jurisdiction"],
            "identifiers": application["identifiers"],
            "address": application.get("registered_address"),
        }
        result = await provider.screen_business(
            BusinessSubject(legal_name=subject["name"], jurisdiction=subject["jurisdiction"]),
            ScreenOptions(idempotency_key=f"test.{key}.business"),
            deadline=Deadline.after(5),
        )
        associated = [o["name"] for o in application["declared_owners"]]
    else:
        name, _ = PARTIES[key]
        owner = next(o for o in application["declared_owners"] if o["name"] == name)
        subject = {
            "kind": "person",
            "name": name,
            "date_of_birth": owner.get("date_of_birth"),
            "nationalities": owner.get("nationalities") or [],
            "address": None,
            "identifiers": [],
        }
        result = await provider.screen_person(
            PersonSubject(full_name=name, date_of_birth=subject["date_of_birth"]),
            ScreenOptions(idempotency_key=f"test.{key}.person"),
            deadline=Deadline.after(5),
        )
        associated = [n for n in associated if n != name]
    return result.model_dump(mode="json"), subject, associated


async def dispose(
    key: str,
    *,
    business: bool = False,
    provider: MockProvider | None = None,
    config: DispositionConfig | None = None,
    **deps: Any,
) -> Any:
    backend = MockProvider(MockConfig(clock=lambda: FROZEN))
    result, subject, associated = await screen(backend, key, business=business)
    assert result["hits"], f"{key} has no hit"
    return await run_screening_disposition(
        tenant_id=deps.pop("tenant_id", ""),
        case_id=f"case-{key}",
        screening_result=result,
        hit_id=result["hits"][0]["hit_id"],
        subject=subject,
        associated_entities=associated,
        config=config or DispositionConfig(llm_model="scripted"),
        deps=DispositionDependencies(
            provider=provider or backend,
            clock=lambda: FROZEN,
            authorizer=deps.pop("authorizer", ALLOW_PROVIDER_CALLS),
            **deps,
        ),
        run_id=f"run-{key}",
    )


@pytest.fixture
def run_disposition() -> Callable[..., Any]:
    return dispose
