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
    case_key,
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
    assert '"full_name": "[[PERSON_' in once
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


async def test_a_token_issued_to_another_conversation_is_not_restored_even_on_the_same_map() -> None:
    store = InMemoryPseudonymMapStore()
    first = await _session(store)
    await first.pseudonymise_value({"full_name": case.APPLICANT_NAME})
    other = await _session(store)
    token = (await other.pseudonymise_value({"full_name": case.DIRECTOR_NAME}))["full_name"]

    with pytest.raises(PseudonymRestoreError, match="unknown_pseudonym"):
        await first.restore_arguments({"name": token})
    assert first.restore_text(f"see {token}") == f"see {token}"
    assert store.writes == 2  # no reload or extra write was attempted


async def test_resuming_marks_only_the_conversations_tokens_as_issued() -> None:
    from langchain_core.messages import AIMessage, HumanMessage

    store = InMemoryPseudonymMapStore()
    running = await _session(store)
    applicant = (await running.pseudonymise_value({"full_name": case.APPLICANT_NAME}))["full_name"]
    director = (await running.pseudonymise_value({"full_name": case.DIRECTOR_NAME}))["full_name"]

    resumed = await _session(store)
    # Loaded with both tokens in its map, but nothing is issued to this conversation yet.
    assert resumed.restore_text(f"{applicant} {director}") == f"{applicant} {director}"
    resumed.note_issued(
        [
            HumanMessage(content=f"About {applicant}"),
            AIMessage(content="", tool_calls=[{"name": "lookup", "args": {"who": applicant}, "id": "c1"}]),
        ]
    )
    assert await resumed.restore_arguments({"who": applicant}) == {"who": case.APPLICANT_NAME}
    with pytest.raises(PseudonymRestoreError, match="unknown_pseudonym"):
        await resumed.restore_arguments({"who": director})
    assert resumed.restore_text(f"{applicant} {director}") == f"{case.APPLICANT_NAME} {director}"


@pytest.mark.parametrize(
    "damaged",
    [
        "[{inner}]",
        "{inner}",
        "[[{inner}]",
        "{{{{{inner}}}}}",
        "[[ {inner} ]]",
        "[[{lower}]]",
        "tag {tag} on its own",
        "person_1 : {tag}",
    ],
)
async def test_the_case_tag_outside_an_exact_token_is_refused_not_dispatched(damaged: str) -> None:
    session = await _session()
    token = (await session.pseudonymise_value({"full_name": case.APPLICANT_NAME}))["full_name"]
    inner = token[2:-2]
    argument = damaged.format(inner=inner, lower=inner.lower(), tag=inner.split(":")[1])
    with pytest.raises(PseudonymRestoreError) as excinfo:
        await session.restore_arguments({"name": argument})
    assert excinfo.value.reason in {"malformed_pseudonym", "unknown_pseudonym"}


async def test_structured_tool_results_are_pseudonymised_by_field_in_objects_and_json_text() -> None:
    record = {"record": {"full_name": "Zelda Nobodyson", "address": "2 Sample Road, Nowhereville", "dob": "1900-02-02"}}
    session = await _session()
    as_object = json.dumps(await session.pseudonymise_value(record))
    as_text = await session.pseudonymise_text(json.dumps(record))
    for raw in ("Zelda Nobodyson", "2 Sample Road, Nowhereville", "1900-02-02"):
        assert raw not in as_object
        assert raw not in as_text


async def test_many_new_values_in_one_model_call_are_written_to_the_map_once() -> None:
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    store = InMemoryPseudonymMapStore()
    session = await _session(store)
    await session.pseudonymise_messages(
        [
            SystemMessage(content=case.system_prompt()),
            HumanMessage(content=f"SSN {case.SSN}, ITIN {case.ITIN}, VAT {case.VAT}"),
            ToolMessage(content=json.dumps({"full_name": case.APPLICANT_NAME, "iban": case.IBAN}), tool_call_id="c1"),
        ]
    )
    assert store.writes == 1
    assert len(session._map or ()) == 6


async def test_large_batches_are_scanned_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    real_to_thread = ps.asyncio.to_thread

    async def spy(function: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(function)
        return await real_to_thread(function, *args, **kwargs)

    monkeypatch.setattr(ps, "_OFF_LOOP_CHARS", 100)
    monkeypatch.setattr(ps.asyncio, "to_thread", spy)
    session = await _session()
    masked = await session.pseudonymise_text("filler " * 30 + f"SSN {case.SSN}")
    assert case.SSN not in masked
    assert calls, "detection over a large batch ran on the event loop"


async def test_a_resume_with_no_stored_map_is_refused() -> None:
    with pytest.raises(PseudonymisationError, match="map_missing"):
        await ps.open_session(
            case.TENANT_ID, "case-never-written", store=InMemoryPseudonymMapStore(), require_existing=True
        )


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
    ("server_id", "expected"),
    [
        ("agent-f5:0a1b2c3d", "agent-f5:0a1b2c3d"),
        (
            "tenant:00000000-0000-4000-8000-00000000f005:run:0a1b",
            "tenant:00000000-0000-4000-8000-00000000f005:run:0a1b",
        ),
        ("voice call/with spaces", "sha256:" + __import__("hashlib").sha256(b"voice call/with spaces").hexdigest()),
    ],
)
def test_case_key_comes_from_a_server_id_and_hashes_unstorable_ids(server_id: str, expected: str) -> None:
    assert case_key(server_id) == expected


def test_an_empty_case_key_is_refused() -> None:
    with pytest.raises(PseudonymisationError, match="case_id_invalid"):
        case_key("")


async def test_flag_is_off_without_a_valid_tenant_and_reads_the_named_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.feature_flags import FlagRows

    seen: list[tuple[str, Any]] = []

    async def fake_rows(flag_key: str, *, tenant_id: Any = None) -> FlagRows:
        seen.append((flag_key, tenant_id))
        return FlagRows(global_row=None, tenant_row={"enabled": True, "rollout_percentage": 100})

    monkeypatch.setattr("core.feature_flags.load_flag_rows_strict", fake_rows)
    assert await ps.pseudonymisation_enabled(None) is False
    assert await ps.pseudonymisation_enabled("not-a-uuid") is False
    assert await ps.pseudonymisation_enabled(case.TENANT_ID) is True
    assert seen == [("pseudonymisation.pre_model", uuid.UUID(case.TENANT_ID))]


async def test_flag_defaults_off_when_no_flag_row_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import feature_flags

    async def no_rows(flag_key: str, *, tenant_id: Any) -> feature_flags.FlagRows:
        return feature_flags.FlagRows(global_row=None, tenant_row=None)

    feature_flags.clear_cache()
    monkeypatch.setattr(feature_flags, "load_flag_rows_strict", no_rows)
    assert await ps.pseudonymisation_enabled(case.TENANT_ID) is False
    feature_flags.clear_cache()


@pytest.mark.real_flag_lookup
async def test_a_flag_lookup_failure_refuses_instead_of_turning_pseudonymisation_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import feature_flags

    attempts: list[str] = []

    def database_down(*args: Any, **kwargs: Any) -> Any:
        attempts.append("lookup")
        raise ConnectionError("database unavailable")

    feature_flags.clear_cache()
    monkeypatch.setattr(feature_flags, "get_tenant_session", database_down)
    tenant = str(uuid.uuid4())
    # The lenient lookup caches its failure as "no row"; the strict one must not trust that.
    assert await feature_flags.is_enabled(ps.FLAG_KEY, tenant_id=uuid.UUID(tenant)) is False
    for _ in range(2):
        with pytest.raises(PseudonymisationError, match="flag_lookup_failed"):
            await ps.pseudonymisation_enabled(tenant)
    assert attempts == ["lookup", "lookup", "lookup"]  # failures are never served from the cache
    feature_flags.clear_cache()


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
