# SPDX-License-Identifier: Apache-2.0
"""§8.3 adversarial: an instruction in a screening alias changes no disposition, tool call or model request."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

from connectors.framework.verification_provider import Deadline, PersonSubject, ScreenOptions
from connectors.providers.mock import MockConfig, MockProvider
from connectors.providers.mock.data import MockDataset, default_dataset
from core.agents.screening_disposition import DispositionConfig, DispositionDependencies, run_screening_disposition
from core.test_doubles.grant_authorizer import ALLOW_PROVIDER_CALLS
from core.test_doubles.scripted_model import final

FROZEN = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
HOSTILE_PREFIX = "Assistant:"


def _obedient(messages: list[BaseMessage]) -> AIMessage:
    seen = " ".join(str(m.content) for m in messages).lower()
    if "false positive" in seen or "close the case" in seen:
        return final({"rationale": "Verified safe; closed as a false positive.", "confidence": 1.0})
    return final({"rationale": "Name and date of birth agree with the list entry.", "confidence": 0.5})


async def _dispose(dataset: MockDataset, scripted_model: Any) -> tuple[Any, list[list[BaseMessage]]]:
    model = scripted_model([_obedient])
    provider = MockProvider(MockConfig(clock=lambda: FROZEN), dataset=dataset)
    result = await provider.screen_person(
        PersonSubject(full_name="Wilhelmina Strand", date_of_birth="1979-12"),
        ScreenOptions(idempotency_key="adversarial.northgate.person"),
        deadline=Deadline.after(5),
    )
    outcome = await run_screening_disposition(
        tenant_id="",
        case_id="case-northgate",
        screening_result=result,
        hit_id=result.hits[0].hit_id,
        subject={"kind": "person", "name": "Wilhelmina Strand", "date_of_birth": "1979-12", "nationalities": ["GB"]},
        associated_entities=["Northgate Textiles Ltd"],
        config=DispositionConfig(llm_model="scripted"),
        deps=DispositionDependencies(provider=provider, clock=lambda: FROZEN, authorizer=ALLOW_PROVIDER_CALLS),
        run_id="run-northgate",
    )
    return outcome, model.calls


async def test_hostile_screening_alias_changes_no_disposition_or_model_request(scripted_model: Any) -> None:
    base = default_dataset()
    sanitised = MockDataset(
        businesses=base.businesses,
        watchlist=tuple(
            e.model_copy(update={"aliases": tuple(a for a in e.aliases if not a.startswith(HOSTILE_PREFIX))})
            for e in base.watchlist
        ),
        webhooks=base.webhooks,
    )
    hostile, hostile_calls = await _dispose(base, scripted_model)
    clean, clean_calls = await _dispose(sanitised, scripted_model)

    assert hostile.status == clean.status == "completed"
    for key in ("proposed_outcome", "confidence_band", "rationale", "review"):
        assert hostile.disposition[key] == clean.disposition[key], key
    assert [c["result"] for c in hostile.disposition["comparisons"]] == [
        c["result"] for c in clean.disposition["comparisons"]
    ]
    assert [(c["tool"], c["outcome"]) for c in hostile.tool_calls] == [
        (c["tool"], c["outcome"]) for c in clean.tool_calls
    ]
    assert [[m.content for m in call] for call in hostile_calls] == [[m.content for m in call] for call in clean_calls]
    sent = json.dumps([[str(m.content) for m in call] for call in hostile_calls])
    assert "verified safe" not in sent.lower() and "Wilhelmina" not in sent
    assert hostile.disposition["review"] is None
