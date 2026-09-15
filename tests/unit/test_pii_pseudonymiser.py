# SPDX-License-Identifier: Apache-2.0
"""Reversible pseudonymisation service (PRD F-5, core/pii/pseudonymiser.py)."""

from __future__ import annotations

import json
import random
import re
import uuid
from typing import Any

import pytest
from prometheus_client import REGISTRY

from core.pii import pseudonymiser as ps
from core.pii.pseudonymiser import (
    PseudonymisationError,
    PseudonymMap,
    PseudonymRestoreError,
    PseudonymSession,
    resolve_case_id,
    structured_values,
)
from core.test_doubles.pseudonym_store import InMemoryPseudonymMapStore
from tests import pseudonymisation_case as case

_TOKEN = re.compile(r"\[\[[A-Z][A-Z0-9_]*_\d+:[0-9a-f]{6}\]\]")


async def _session(store: InMemoryPseudonymMapStore | None = None, case_id: str = case.CASE_ID) -> PseudonymSession:
    session = PseudonymSession(case.TENANT_ID, case_id, store or InMemoryPseudonymMapStore())
    await session.load()
    return session


def _rendered(task: dict[str, Any]) -> str:
    return json.dumps(task)


def _metric(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


# ── Map ─────────────────────────────────────────────────────────────────────


def test_tokens_are_typed_numbered_per_type_and_carry_the_case_tag() -> None:
    pmap = PseudonymMap(tag="0a1b2c")
    assert pmap.token_for(case.APPLICANT_NAME, "PERSON") == "[[PERSON_1:0a1b2c]]"
    assert pmap.token_for(case.SSN, "US_SSN") == "[[US_SSN_1:0a1b2c]]"
    assert pmap.token_for(case.DIRECTOR_NAME, "PERSON") == "[[PERSON_2:0a1b2c]]"
    assert pmap.token_for(case.APPLICANT_NAME, "PERSON") == "[[PERSON_1:0a1b2c]]"
    assert pmap.token_for("x y", "not a type") == "[[PII_1:0a1b2c]]"


def test_map_serialisation_round_trips_and_keeps_numbering() -> None:
    pmap = PseudonymMap.new()
    pmap.token_for(case.APPLICANT_NAME, "PERSON")
    pmap.token_for(case.SSN, "US_SSN")
    restored = PseudonymMap.from_json(pmap.to_json())
    assert restored.by_token == pmap.by_token
    assert restored.token_for(case.DIRECTOR_NAME, "PERSON") == f"[[PERSON_2:{pmap.tag}]]"


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        json.dumps({"format": 99, "tag": "0a1b2c", "entries": []}),
        json.dumps({"format": 1, "tag": "XYZ", "entries": []}),
        json.dumps({"format": 1, "tag": "0a1b2c", "entries": [["[[PERSON_1:ffffff]]", "a b"]]}),
        json.dumps(
            {"format": 1, "tag": "0a1b2c", "entries": [["[[PERSON_1:0a1b2c]]", "a"], ["[[PERSON_2:0a1b2c]]", "a"]]}
        ),
        json.dumps({"format": 1, "tag": "0a1b2c", "entries": [["PERSON_1", "a"]]}),
    ],
)
def test_a_damaged_stored_map_is_rejected_not_partially_used(raw: str) -> None:
    with pytest.raises(PseudonymisationError) as excinfo:
        PseudonymMap.from_json(raw)
    assert excinfo.value.reason == "map_unreadable"


# ── What is pseudonymised ───────────────────────────────────────────────────


def test_structured_values_cover_names_birth_dates_addresses_and_identifiers() -> None:
    found = structured_values(
        {
            "applicant": {
                "full_name": case.APPLICANT_NAME,
                "Date-Of-Birth": case.DATE_OF_BIRTH,
                "address": {"line1": case.ADDRESS_LINE, "postcode": "ZZ1 1ZZ", "country": "GB", "zip": "12345"},
                "ssn": case.SSN,
                "name": "Acme Placeholder Ltd",
            },
            "owners": [{"first_name": "Quinta"}, {"first_name": "Orrin"}],
        }
    )
    assert found == [
        (case.APPLICANT_NAME, "PERSON"),
        (case.DATE_OF_BIRTH, "DATE_OF_BIRTH"),
        (case.ADDRESS_LINE, "ADDRESS"),
        ("ZZ1 1ZZ", "ADDRESS"),
        (case.SSN, "US_SSN"),
        ("Quinta", "PERSON"),
        ("Orrin", "PERSON"),
    ]


async def test_fixture_case_round_trips_and_no_raw_value_survives() -> None:
    session = await _session()
    task = case.task_input()
    masked_task = await session.pseudonymise_value(task)
    rendered = await session.pseudonymise_text(_rendered(masked_task))
    prompt = await session.pseudonymise_text(case.system_prompt())

    for raw in case.RAW_VALUES:
        assert raw not in rendered + prompt, raw
    assert session.restore_value(masked_task) == task
    assert json.loads(session.restore_text(rendered)) == task
    assert session.restore_text(prompt) == case.system_prompt()


async def test_a_value_keeps_its_token_for_the_whole_case_and_across_sessions() -> None:
    store = InMemoryPseudonymMapStore()
    first = await _session(store)
    early = await first.pseudonymise_value({"full_name": case.APPLICANT_NAME})
    later = await first.pseudonymise_text(f"Tool result mentions {case.APPLICANT_NAME} again.")
    token = early["full_name"]
    assert _TOKEN.fullmatch(token)
    assert token in later

    resumed = await _session(store)  # a new process resuming the case
    assert await resumed.pseudonymise_text(case.APPLICANT_NAME) == token

    other_case = await _session(store, case_id="case-f5-0002")
    other = await other_case.pseudonymise_value({"full_name": case.APPLICANT_NAME})
    assert other["full_name"] != token


async def test_pseudonymising_twice_changes_nothing() -> None:
    session = await _session()
    once = await session.pseudonymise_text(_rendered(case.task_input()))
    writes = session._store.writes  # type: ignore[attr-defined]
    assert await session.pseudonymise_text(once) == once
    assert session._store.writes == writes  # type: ignore[attr-defined]


async def test_known_names_are_replaced_in_free_text_but_not_inside_other_words() -> None:
    session = await _session()
    await session.register_structured({"first_name": "Quinta"})
    masked = await session.pseudonymise_text("Quinta called about Quintal Road; quinta is lower case.")
    assert "Quinta called" not in masked
    assert "Quintal Road" in masked
    assert "quinta is lower case" in masked


async def test_langchain_messages_are_pseudonymised_including_system_prompt_and_tool_arguments() -> None:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    session = await _session()
    messages = [
        SystemMessage(content=case.system_prompt()),
        HumanMessage(content=[{"type": "text", "text": f"Check SSN {case.SSN}"}]),
        AIMessage(content="", tool_calls=[{"name": "lookup", "args": {"email": case.EMAIL}, "id": "c1"}]),
        ToolMessage(content=json.dumps({"iban": case.IBAN}), tool_call_id="c1"),
    ]
    masked = await session.pseudonymise_messages(messages)
    dumped = json.dumps([m.model_dump() for m in masked], default=str)
    for raw in (case.SYSTEM_PROMPT_EMAIL, case.SSN, case.EMAIL, case.IBAN):
        assert raw not in dumped
    assert masked[2].tool_calls[0]["id"] == "c1"
    assert messages[0].content == case.system_prompt()  # the originals are not mutated


async def test_router_messages_are_pseudonymised() -> None:
    session = await _session()
    masked = await session.pseudonymise_router_messages(
        [{"role": "system", "content": case.system_prompt()}, {"role": "user", "content": f"VAT {case.VAT}"}]
    )
    assert [m["role"] for m in masked] == ["system", "user"]
    assert case.SYSTEM_PROMPT_EMAIL not in masked[0]["content"]
    assert case.VAT not in masked[1]["content"]


async def test_entities_pseudonymised_are_counted_by_type() -> None:
    before = _metric("agenticorg_pii_pseudonymised_total", entity_type="US_SSN")
    session = await _session()
    await session.pseudonymise_text(f"SSN {case.SSN} and again {case.SSN}")
    assert _metric("agenticorg_pii_pseudonymised_total", entity_type="US_SSN") == before + 1


# ── Restoring tool arguments fails closed ───────────────────────────────────


async def test_tool_arguments_are_restored_exactly() -> None:
    session = await _session()
    masked = await session.pseudonymise_value({"to": case.EMAIL, "body": f"SSN {case.SSN} for {case.EMAIL}"})
    assert await session.restore_arguments({"payload": masked, "count": 2}) == {
        "payload": {"to": case.EMAIL, "body": f"SSN {case.SSN} for {case.EMAIL}"},
        "count": 2,
    }


@pytest.mark.parametrize(
    ("argument", "reason"),
    [
        ("[[PERSON_9:{tag}]]", "unknown_pseudonym"),
        ("[[PERSON_1:ffffff]]", "unknown_pseudonym"),
        ("[[PERSON_1:{tag}]] and [[PERSON_1:{tag}", "malformed_pseudonym"),
        ("[[person_1]]", "malformed_pseudonym"),
        ("[[ PERSON_1:{tag} ]]", "malformed_pseudonym"),
    ],
)
async def test_a_tool_argument_that_cannot_be_fully_restored_is_refused(argument: str, reason: str) -> None:
    session = await _session()
    token = (await session.pseudonymise_value({"full_name": case.APPLICANT_NAME}))["full_name"]
    tag = token.split(":")[1][:6]
    before = _metric("agenticorg_pii_pseudonym_restore_refused_total", reason=reason)
    with pytest.raises(PseudonymRestoreError) as excinfo:
        await session.restore_arguments({"to": argument.format(tag=tag), "cc": token})
    assert excinfo.value.reason == reason
    assert _metric("agenticorg_pii_pseudonym_restore_refused_total", reason=reason) == before + 1


async def test_a_pseudonym_in_an_argument_name_is_refused() -> None:
    session = await _session()
    token = (await session.pseudonymise_value({"full_name": case.APPLICANT_NAME}))["full_name"]
    with pytest.raises(PseudonymRestoreError, match="pseudonym_in_argument_name"):
        await session.restore_arguments({token: "x"})


async def test_restoration_reloads_the_map_once_for_a_token_another_worker_added() -> None:
    store = InMemoryPseudonymMapStore()
    stale = await _session(store)
    await stale.pseudonymise_value({"full_name": case.APPLICANT_NAME})
    worker = await _session(store)
    token = (await worker.pseudonymise_value({"full_name": case.DIRECTOR_NAME}))["full_name"]
    assert await stale.restore_arguments({"name": token}) == {"name": case.DIRECTOR_NAME}


async def test_restoration_is_refused_when_the_map_cannot_be_reloaded() -> None:
    store = InMemoryPseudonymMapStore()
    session = await _session(store)
    await session.pseudonymise_value({"full_name": case.APPLICANT_NAME})
    store.fail_next = "map_store_unavailable"
    with pytest.raises(PseudonymRestoreError) as excinfo:
        await session.restore_arguments({"to": f"[[PERSON_7:{session._map.tag}]]"})  # type: ignore[union-attr]
    assert excinfo.value.reason == "map_store_unavailable"


async def test_a_store_failure_stops_pseudonymisation_so_no_model_call_is_made() -> None:
    store = InMemoryPseudonymMapStore()
    session = await _session(store)
    store.fail_next = "map_store_failed"
    with pytest.raises(PseudonymisationError) as excinfo:
        await session.pseudonymise_text(f"SSN {case.SSN}")
    assert excinfo.value.reason == "map_store_failed"


async def test_output_restoration_leaves_unknown_tokens_visible_rather_than_guessing() -> None:
    session = await _session()
    assert session.restore_text("see [[PERSON_4:abcdef]]") == "see [[PERSON_4:abcdef]]"


def test_refusal_is_a_tool_error_with_a_reason_code() -> None:
    assert ps.refusal(PseudonymRestoreError("unknown_pseudonym")) == {
        "error": {"code": "E1012", "message": "pseudonym_restore_failed: unknown_pseudonym"}
    }


# ── Case ids, the flag and storage decoding ─────────────────────────────────


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ({"case_id": "case-a"}, "case-a"),
        ({"inputs": {"case_id": "case-b"}}, "case-b"),
        ({"context": {"case_id": "case-c"}}, "case-c"),
        ({"inputs": {}}, "thread-1"),
        (None, "thread-1"),
    ],
)
def test_case_id_comes_from_the_task_or_the_run(task: Any, expected: str) -> None:
    assert resolve_case_id(task, fallback="thread-1") == expected


@pytest.mark.parametrize("bad", ["", "has space", "x" * 201, "semi;colon"])
def test_a_malformed_case_id_is_refused(bad: str) -> None:
    with pytest.raises(PseudonymisationError, match="case_id_invalid"):
        resolve_case_id({"case_id": bad} if bad else {"case_id": None}, fallback=bad)


async def test_flag_is_off_without_a_valid_tenant_and_reads_the_named_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, Any]] = []

    async def fake_is_enabled(flag_key: str, *, tenant_id: Any = None, **_: Any) -> bool:
        seen.append((flag_key, tenant_id))
        return True

    monkeypatch.setattr("core.feature_flags.is_enabled", fake_is_enabled)
    assert await ps.pseudonymisation_enabled(None) is False
    assert await ps.pseudonymisation_enabled("not-a-uuid") is False
    assert await ps.pseudonymisation_enabled(case.TENANT_ID) is True
    assert seen == [("pseudonymisation.pre_model", uuid.UUID(case.TENANT_ID))]


async def test_flag_defaults_off_when_no_flag_row_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import feature_flags

    async def no_row(tenant_id: Any, flag_key: str) -> None:
        return None

    monkeypatch.setattr(feature_flags, "_load_flag", no_row)
    assert await ps.pseudonymisation_enabled(case.TENANT_ID) is False


async def test_stored_ciphertext_is_decoded_through_tenant_decryption(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.crypto.credential_vault import encrypt_credential

    pmap = PseudonymMap.new()
    pmap.token_for(case.SSN, "US_SSN")
    ciphertext = encrypt_credential(pmap.to_json())
    assert case.SSN not in ciphertext

    decoded = await ps._decode_stored({"_encrypted": ciphertext})
    assert decoded is not None and decoded.by_token == pmap.by_token
    assert await ps._decode_stored({}) is None
    with pytest.raises(PseudonymisationError, match="map_unreadable"):
        await ps._decode_stored({"other": "shape"})
    with pytest.raises(PseudonymisationError, match="map_unreadable"):
        await ps._decode_stored({"_encrypted": "not-a-ciphertext"})


# ── Property-style round trip (fixed seeds) ─────────────────────────────────

_WORDS = ("review", "the", "file", "for", "and", "noted", "records", "updated", "today", "with")


@pytest.mark.parametrize("seed", range(10))
async def test_property_pseudonymise_then_restore_is_identity(seed: int) -> None:
    rng = random.Random(seed)
    session = await _session(case_id=f"case-prop-{seed}")
    # Shapes recognised without a label, and labelled forms of the others.
    identifiers = [case.SSN, case.ITIN, case.IBAN, case.VAT, case.EMAIL, f"EIN {case.EIN}", f"NINO {case.NINO}"]
    await session.register_structured({"full_name": case.APPLICANT_NAME})
    parts: list[str] = []
    for _ in range(rng.randint(3, 12)):
        parts.append(" ".join(rng.choice(_WORDS) for _ in range(rng.randint(2, 9))))
        choice = rng.randrange(3)
        if choice == 0:
            parts.append(f"SSN {case.SSN}" if rng.random() < 0.5 else case.IBAN)
        elif choice == 1:
            parts.append(case.APPLICANT_NAME)
        else:
            parts.append(f"date of birth: {case.DATE_OF_BIRTH}," if rng.random() < 0.3 else rng.choice(identifiers))
    text = " ".join(parts) + "."

    masked = await session.pseudonymise_text(text)
    assert session.restore_text(masked) == text
    assert await session.restore_arguments(masked) == text
    for raw in [case.SSN, case.ITIN, case.IBAN, case.VAT, case.EMAIL, case.EIN, case.NINO, case.APPLICANT_NAME]:
        assert raw not in masked
