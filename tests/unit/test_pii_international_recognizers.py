# SPDX-License-Identifier: Apache-2.0
"""United States, United Kingdom and European identifier recognisers (PRD F-5).

Every identifier in this file is reserved, never issued, or built on an
all-zero bank or registry code with computed check digits; none belongs to a
real person or organisation.
"""

from __future__ import annotations

import random
import re
import string

import pytest

from core.pii import international_recognizers as ir
from core.pii.deanonymizer import deanonymize
from core.pii.international_recognizers import find_identifiers, iban_is_valid, vat_is_valid


def _iban(country: str, bban: str) -> str:
    """Build an IBAN with ISO 13616 check digits, independently of the module under test."""
    numeric = "".join(str(int(char, 36)) for char in bban + country + "00")
    return f"{country}{98 - int(numeric) % 97:02d}{bban}"


def _only(text: str) -> ir.IdentifierMatch:
    matches = find_identifiers(text)
    assert len(matches) == 1, f"expected one identifier in {text!r}, got {matches}"
    match = matches[0]
    assert text[match.start : match.end] == match.text
    return match


def _pseudonymise(text: str) -> tuple[str, dict[str, str]]:
    counters: dict[str, int] = {}
    token_map: dict[str, str] = {}
    pieces: list[str] = []
    cursor = 0
    for match in find_identifiers(text):
        counters[match.entity_type] = counters.get(match.entity_type, 0) + 1
        token = f"<{match.entity_type}_{counters[match.entity_type]}>"
        token_map[token] = match.text
        pieces += [text[cursor : match.start], token]
        cursor = match.end
    pieces.append(text[cursor:])
    return "".join(pieces), token_map


# ── Recognition of each scheme ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "entity", "value"),
    [
        ("Applicant SSN 000-00-0001 on file", ir.US_SSN, "000-00-0001"),
        ("dashed form needs no label: 000-12-0000", ir.US_SSN, "000-12-0000"),
        ("social security number 000 00 0001", ir.US_SSN, "000 00 0001"),
        ("SSN: 000000001", ir.US_SSN, "000000001"),
        ("ITIN 900-70-0000", ir.US_ITIN, "900-70-0000"),
        ("taxpayer identification number 999940000", ir.US_ITIN, "999940000"),
        ("EIN 00-0000001", ir.US_EIN, "00-0000001"),
        ("employer identification number 000000001", ir.US_EIN, "000000001"),
    ],
)
def test_us_ssn_itin_and_ein_are_recognised(text: str, entity: str, value: str) -> None:
    match = _only(text)
    assert (match.entity_type, match.text) == (entity, value)


@pytest.mark.parametrize(
    ("text", "entity", "value"),
    [
        ("National Insurance number QQ 12 34 56 C", ir.UK_NINO, "QQ 12 34 56 C"),
        ("nino: qq123456a", ir.UK_NINO, "qq123456a"),
        ("Company number 00000001", ir.UK_COMPANY_NUMBER, "00000001"),
        ("Companies House no. SC000001", ir.UK_COMPANY_NUMBER, "SC000001"),
        ("registered_number=OC000001", ir.UK_COMPANY_NUMBER, "OC000001"),
    ],
)
def test_uk_nino_and_company_numbers_are_recognised(text: str, entity: str, value: str) -> None:
    match = _only(text)
    assert (match.entity_type, match.text) == (entity, value)


@pytest.mark.parametrize(
    "iban",
    [
        _iban("DE", "000000000000000001"),
        _iban("GB", "ZZZZ00000000000001"),
        _iban("NL", "ZZZZ0000000001"),
        "DE09 0000 0000 0000 0000 01",
    ],
)
def test_iban_with_valid_mod97_is_recognised(iban: str) -> None:
    match = _only(f"Pay to {iban}, thanks.")
    assert (match.entity_type, match.text) == (ir.IBAN_CODE, iban)


@pytest.mark.parametrize(
    "vat",
    [
        "ATU00000015",
        "BE0000000097",
        "DE000000011",
        "DE 000 000 011",
        "DK00000019",
        "FI00000019",
        "FR15000000001",
        "GB000000140",
        "IT00000000018",
        "NL000000012B01",
        "PL0000000017",
        "SE000000001801",
    ],
)
def test_vat_number_with_valid_check_digits_is_recognised_without_a_label(vat: str) -> None:
    assert vat_is_valid(vat) is True
    match = _only(f"supplier {vat} registered")
    assert (match.entity_type, match.text) == (ir.EU_VAT, vat)


def test_vat_number_without_a_checksum_rule_needs_a_label() -> None:
    assert vat_is_valid("CZ00000001") is None
    assert find_identifiers("reference CZ00000001") == []
    assert _only("VAT: CZ00000001").text == "CZ00000001"


# ── Checksums limit false positives ─────────────────────────────────────────


def test_iban_with_wrong_check_digits_is_rejected() -> None:
    valid = _iban("DE", "000000000000000001")
    tampered = valid[:-1] + ("2" if valid[-1] != "2" else "3")
    assert iban_is_valid(valid)
    assert not iban_is_valid(tampered)
    assert not iban_is_valid(valid[:-1])  # wrong length for the country
    assert not iban_is_valid("ZZ" + valid[2:])  # unknown country
    assert find_identifiers(f"IBAN {tampered}") == []


def test_vat_number_with_wrong_check_digits_is_rejected_even_with_a_label() -> None:
    assert vat_is_valid("DE000000012") is False
    assert find_identifiers("VAT number DE000000012") == []


@pytest.mark.parametrize(
    "text",
    [
        "Order 123456789 shipped on 2026-09-15T10:30:00Z",
        "Call 555-0100 or +1 202 555 0143",
        "Invoice INV-2025-000123 for 12,345.67 GBP",
        "Request id 123e4567-e89b-12d3-a456-426614174000",
        "Reference 12345678 attached",
        "Ticket 00-0000001 reopened",
        "ISBN 978-3-16-148410-0",
        "ZZ 12 34 56 C is a reserved prefix without a label",
        "ein Konto 123456789 in German prose",
        "Martin paid 123456789 yen",
        "DE 2026 roadmap, IT 12345678901 is not Luhn-valid",
        "Aadhaar style 1234 5678 9012",
        "GB29 ZZZZ 0000 0000 0000 02",
        "vat included; amount 000000011",
        "The SSN field is blank; balance 000 000 0001",
    ],
)
def test_ordinary_text_is_not_recognised(text: str) -> None:
    assert find_identifiers(text) == []


def test_identifier_embedded_in_a_longer_token_is_not_recognised() -> None:
    assert find_identifiers("ref000-00-0001") == []
    assert find_identifiers("000-00-0001-7") == []
    assert find_identifiers("x" + _iban("DE", "000000000000000001")) == []


# ── Round trip ──────────────────────────────────────────────────────────────


def test_pseudonymise_then_restore_is_identity_for_a_mixed_document() -> None:
    document = (
        "Applicant SSN 000-00-0001, ITIN 900-70-0000, employer EIN 00-0000001.\n"
        "National Insurance number QQ 12 34 56 C; Company number 00000001.\n"
        f"Settle to {_iban('GB', 'ZZZZ00000000000001')} and bill VAT FR15000000001."
    )
    masked, token_map = _pseudonymise(document)
    assert len(token_map) == 7
    for original in token_map.values():
        assert original not in masked
    assert deanonymize(masked, token_map) == document


# ── Property-style checks (fixed seeds) ─────────────────────────────────────

_FILLER = (
    "the", "applicant", "submitted", "forms", "for", "review", "and", "noted", "that", "records",
    "were", "updated", "on", "file", "with", "no", "further", "action", "required", "today",
)  # fmt: skip
# Keeps one identifier's label outside the context window of the next.
_CONTEXT_GAP = ir._CONTEXT_WINDOW + 16
_ITIN_GROUP_CHOICES = sorted(ir._ITIN_GROUPS)
_UNISSUED_EIN_PREFIXES = sorted(set(range(100)) - ir._EIN_PREFIXES)


def _digits(rng: random.Random, count: int) -> str:
    return "".join(rng.choice(string.digits) for _ in range(count))


def _filler(rng: random.Random) -> str:
    """Plain words and short numbers, starting and ending with a word, longer than the context window."""
    words: list[str] = [rng.choice(_FILLER)]
    while sum(len(word) + 1 for word in words) < _CONTEXT_GAP:
        words.append(rng.choice(_FILLER) if rng.random() < 0.8 else _digits(rng, rng.randint(1, 4)))
    words.append(rng.choice(_FILLER))
    return " ".join(words)


def _reserved_identifier(rng: random.Random) -> tuple[str, str, str]:
    """Return (label, value, entity) for a reserved or never-issued identifier."""
    kind = rng.randrange(8)
    if kind == 0:
        return "", f"000-{_digits(rng, 2)}-{_digits(rng, 4)}", ir.US_SSN
    if kind == 1:
        return "SSN ", f"000{_digits(rng, 6)}", ir.US_SSN
    if kind == 2:
        return "ITIN ", f"9{_digits(rng, 2)}-{rng.choice(_ITIN_GROUP_CHOICES)}-0000", ir.US_ITIN
    if kind == 3:
        return "EIN ", f"{rng.choice(_UNISSUED_EIN_PREFIXES):02d}-{_digits(rng, 7)}", ir.US_EIN
    if kind == 4:
        sep = rng.choice(["", " "])
        return (
            "NINO ",
            sep.join(["QQ", _digits(rng, 2), _digits(rng, 2), _digits(rng, 2), rng.choice("ABCD")]),
            ir.UK_NINO,
        )
    if kind == 5:
        return "company number ", f"0000{_digits(rng, 4)}", ir.UK_COMPANY_NUMBER
    if kind == 6:
        country = rng.choice(sorted(ir.IBAN_LENGTHS))
        bban = "0" * (ir.IBAN_LENGTHS[country] - 8) + _digits(rng, 4)
        return "", _iban(country, bban), ir.IBAN_CODE
    return "", rng.choice(["DE000000011", "FR15000000001", "NL000000012B01", "GB000000140"]), ir.EU_VAT


@pytest.mark.parametrize("seed", range(20))
def test_property_every_reserved_identifier_is_found_and_round_trips(seed: int) -> None:
    rng = random.Random(seed)
    parts: list[str] = []
    expected: list[tuple[int, str, str]] = []
    cursor = 0
    for _ in range(rng.randint(1, 8)):
        label, value, entity = _reserved_identifier(rng)
        prefix = " " + _filler(rng) + " " + label
        parts += [prefix, value]
        expected.append((cursor + len(prefix), value, entity))
        cursor += len(prefix) + len(value)
    parts.append(" " + _filler(rng))
    document = "".join(parts)

    found = [(m.start, m.text, m.entity_type) for m in find_identifiers(document)]
    assert found == expected

    masked, token_map = _pseudonymise(document)
    assert deanonymize(masked, token_map) == document
    assert not any(value in masked for _, value, _ in expected)


@pytest.mark.parametrize("seed", range(20))
def test_property_near_miss_shapes_are_not_recognised(seed: int) -> None:
    rng = random.Random(1000 + seed)
    shapes = (
        lambda: _digits(rng, rng.choice([5, 6, 7, 10, 11])),
        lambda: f"{_digits(rng, 4)}-{_digits(rng, 2)}-{_digits(rng, 2)}",
        lambda: f"{_digits(rng, 2)}:{_digits(rng, 2)}",
        lambda: f"{_digits(rng, 3)}-{_digits(rng, 4)}",
        lambda: f"{rng.choice(['EUR', 'GBP', 'USD'])} {_digits(rng, 3)}.{_digits(rng, 2)}",
        lambda: f"{_digits(rng, 9)}",  # no label nearby
        lambda: f"{_digits(rng, 8)}",  # no label nearby
    )
    document = " ".join(f"{_filler(rng)} {rng.choice(shapes)()}" for _ in range(10))
    assert find_identifiers(document) == []


def test_iban_generator_matches_module_validator() -> None:
    rng = random.Random(7)
    for country, length in sorted(ir.IBAN_LENGTHS.items()):
        iban = _iban(country, "0" * (length - 8) + _digits(rng, 4))
        assert iban_is_valid(iban), iban
        assert re.fullmatch(r"[A-Z]{2}\d{2}0+\d{4}", iban)


# ── Case, allocation and scale ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("prefix", "allocated"),
    [("AB", True), ("ab", True), ("QQ", False), ("ZZ", False), ("TN", False), ("DA", False), ("AO", False)],
)
def test_nino_prefix_allocation_rule(prefix: str, allocated: bool) -> None:
    assert ir._nino_prefix_allocated(prefix) is allocated


def test_nino_with_an_allocated_prefix_needs_no_label_in_either_case(monkeypatch: pytest.MonkeyPatch) -> None:
    # Treat the reserved HMRC example prefix as allocated so no issuable number is used here.
    monkeypatch.setattr(ir, "_nino_prefix_allocated", lambda prefix: True)
    assert _only("reference qq 12 34 56 c attached").text == "qq 12 34 56 c"
    assert _only("reference QQ123456C attached").text == "QQ123456C"


@pytest.mark.parametrize(
    ("text", "entity", "value"),
    [
        (
            "pay to " + _iban("DE", "000000000000000001").lower(),
            ir.IBAN_CODE,
            _iban("DE", "000000000000000001").lower(),
        ),
        ("pay to gb29 zzzz 0000 0000 0000 01", ir.IBAN_CODE, "gb29 zzzz 0000 0000 0000 01"),
        ("supplier de000000011 registered", ir.EU_VAT, "de000000011"),
        ("supplier nl000000012b01 registered", ir.EU_VAT, "nl000000012b01"),
    ],
)
def test_lower_case_iban_and_vat_numbers_are_recognised_without_a_label(text: str, entity: str, value: str) -> None:
    match = _only(text)
    assert (match.entity_type, match.text) == (entity, value)


def test_lower_case_country_words_with_failing_checksums_are_not_recognised() -> None:
    assert find_identifiers("it 12345678901 and de 123456789 and gb29 zzzz 0000 0000 0000 02") == []


def test_resolve_overlaps_keeps_the_longest_and_scales(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    match = ir.IdentifierMatch
    small = [match(0, 4, "A", "xxxx"), match(2, 10, "B", "x" * 8), match(10, 12, "C", "xx"), match(11, 13, "D", "xx")]
    assert [m.entity_type for m in ir.resolve_overlaps(small)] == ["B", "C"]

    many = [match(i * 3, i * 3 + 2, "A", "xx") for i in range(8000)]
    many += [match(i * 3 + 1, i * 3 + 3, "B", "xx") for i in range(8000)]
    started = time.perf_counter()
    kept = ir.resolve_overlaps(many)
    elapsed = time.perf_counter() - started
    assert len(kept) == 8000 and all(m.entity_type == "A" for m in kept)
    assert elapsed < 0.5, f"resolve_overlaps took {elapsed:.2f}s for 16,000 matches"
