# SPDX-License-Identifier: Apache-2.0
"""Recognisers for United States, United Kingdom and European identifiers.

Covers United States Social Security numbers (``US_SSN``), Individual
Taxpayer Identification numbers (``US_ITIN``) and Employer Identification
numbers (``US_EIN``); United Kingdom National Insurance numbers
(``UK_NINO``) and Companies House company numbers (``UK_COMPANY_NUMBER``);
European VAT numbers (``EU_VAT``) and IBANs (``IBAN_CODE``). They sit
alongside the Indian recognisers in ``core.pii.india_recognizers``.

Recognition is pure Python and deterministic, so it behaves the same with or
without the NLP stack. To limit false positives each match needs one of:

* a checksum that passes: IBAN (ISO 13616 mod 97) and VAT numbers for the
  countries in ``_VAT_CHECKSUMS``;
* a shape specific enough on its own: a dash-delimited SSN or ITIN, a
  dash-delimited EIN with an IRS-issued prefix, a NINO with an allocated
  prefix; or
* a label nearby (``_CONTEXT_WINDOW`` characters before the value), such as
  "SSN", "company number" or "VAT", for shapes that are otherwise ordinary
  numbers: bare nine-digit numbers, Companies House numbers, EINs with a
  prefix the IRS does not issue, NINOs with a reserved prefix, and VAT
  numbers for countries whose check digit is not verified here.

Issuance rules (for example SSN area ``000``) are deliberately not applied:
this module decides what to mask, and a mistyped or never-issued number in a
recognisable shape is still masked.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

US_SSN = "US_SSN"
US_ITIN = "US_ITIN"
US_EIN = "US_EIN"
UK_NINO = "UK_NINO"
UK_COMPANY_NUMBER = "UK_COMPANY_NUMBER"
EU_VAT = "EU_VAT"
IBAN_CODE = "IBAN_CODE"

ENTITY_TYPES: frozenset[str] = frozenset({US_SSN, US_ITIN, US_EIN, UK_NINO, UK_COMPANY_NUMBER, EU_VAT, IBAN_CODE})

# How far before a value a label may appear, in characters.
_CONTEXT_WINDOW = 48


@dataclass(frozen=True, slots=True)
class IdentifierMatch:
    """One recognised identifier: ``text == source[start:end]``."""

    start: int
    end: int
    entity_type: str
    text: str


def _context_pattern(*labels: str, exact_case: Iterable[str] = ()) -> re.Pattern[str]:
    """Match any label as a whole word; words in a label may be joined by spaces, ``_`` or ``-``.

    ``exact_case`` labels only match as written, for abbreviations that are
    also ordinary words in some languages (the German article "ein").
    """
    alternatives = [r"[\s_-]*".join(re.escape(word) for word in label.split()) for label in labels]
    alternatives += [f"(?-i:{re.escape(label)})" for label in exact_case]
    return re.compile(rf"(?<![a-z0-9])(?:{'|'.join(alternatives)})(?![a-z0-9])", re.IGNORECASE)


def _has_context(text: str, start: int, pattern: re.Pattern[str]) -> bool:
    return pattern.search(text, max(0, start - _CONTEXT_WINDOW), start) is not None


# ── Check-digit helpers ────────────────────────────────────────────────────


def _luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _iso7064_mod11_10_valid(digits: str) -> bool:
    product = 10
    for char in digits[:-1]:
        total = (int(char) + product) % 10 or 10
        product = (2 * total) % 11
    return (11 - product) % 10 == int(digits[-1])


def _weighted_sum(digits: str, weights: Iterable[int]) -> int:
    return sum(int(char) * weight for char, weight in zip(digits, weights, strict=False))


# ── United States ──────────────────────────────────────────────────────────

# ITINs begin with 9 and use these group (middle) numbers.
_ITIN_GROUPS = frozenset(range(50, 66)) | frozenset(range(70, 89)) | {90, 91, 92} | frozenset(range(94, 100))

# EIN prefixes the IRS assigns. Others (00, 07-09, 17-19, 28, 29, 49, 69, 70,
# 78, 79, 89, 96, 97) are still masked when an EIN label is nearby.
_EIN_PREFIXES = frozenset(
    [*range(1, 7), *range(10, 17), *range(20, 28), *range(30, 49), *range(50, 69)]
    + [*range(71, 78), *range(80, 89), *range(90, 96), 98, 99]
)

_US_SSN_DASHED = re.compile(r"(?<![\w-])(\d{3})-(\d{2})-(\d{4})(?![\w-])")
_US_SSN_SPACED = re.compile(r"(?<![\w-])(\d{3}) (\d{2}) (\d{4})(?![\w-])")
_US_EIN_DASHED = re.compile(r"(?<![\w-])(\d{2})-(\d{7})(?![\w-])")
_US_NINE_DIGITS = re.compile(r"(?<![\w-])\d{9}(?![\w-])")

_SSN_CONTEXT = _context_pattern("ssn", "social security")
_ITIN_CONTEXT = _context_pattern("itin", "individual taxpayer")
_EIN_CONTEXT = _context_pattern("employer identification", "federal tax id", exact_case=("EIN", "FEIN"))
_TIN_CONTEXT = _context_pattern("taxpayer identification", "tax id", exact_case=("TIN",))


def _ssn_or_itin(area: str, group: str) -> str:
    return US_ITIN if area.startswith("9") and int(group) in _ITIN_GROUPS else US_SSN


def _find_us(text: str) -> Iterable[IdentifierMatch]:
    for match in _US_SSN_DASHED.finditer(text):
        yield IdentifierMatch(match.start(), match.end(), _ssn_or_itin(match[1], match[2]), match[0])
    for match in _US_SSN_SPACED.finditer(text):
        if _has_context(text, match.start(), _SSN_CONTEXT) or _has_context(text, match.start(), _ITIN_CONTEXT):
            yield IdentifierMatch(match.start(), match.end(), _ssn_or_itin(match[1], match[2]), match[0])
    for match in _US_EIN_DASHED.finditer(text):
        if int(match[1]) in _EIN_PREFIXES or _has_context(text, match.start(), _EIN_CONTEXT):
            yield IdentifierMatch(match.start(), match.end(), US_EIN, match[0])
    for match in _US_NINE_DIGITS.finditer(text):
        value, start = match[0], match.start()
        if _has_context(text, start, _EIN_CONTEXT):
            entity = US_EIN
        elif _has_context(text, start, _ITIN_CONTEXT):
            entity = US_ITIN
        elif _has_context(text, start, _SSN_CONTEXT) or _has_context(text, start, _TIN_CONTEXT):
            entity = _ssn_or_itin(value[:3], value[3:5])
        else:
            continue
        yield IdentifierMatch(start, match.end(), entity, value)


# ── United Kingdom ─────────────────────────────────────────────────────────

# HMRC never allocates D, F, I, Q, U or V as the first letter, D, F, I, O, Q,
# U or V as the second, or the prefixes below.
_NINO_ALLOCATED = re.compile(
    r"(?<![\w-])([A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]) ?(\d{2}) ?(\d{2}) ?(\d{2}) ?([A-D])(?![\w-])"
)
_NINO_ANY_PREFIX = re.compile(r"(?<![\w-])([A-Z]{2}) ?(\d{2}) ?(\d{2}) ?(\d{2}) ?([A-D])(?![\w-])", re.IGNORECASE)
_NINO_UNALLOCATED_PREFIXES = frozenset({"BG", "GB", "KN", "NK", "NT", "TN", "ZZ"})
_NINO_CONTEXT = _context_pattern("nino", "national insurance", "ni number", "ni no")

_COMPANY_PREFIXES = (
    "AC", "CE", "CS", "ES", "FC", "FE", "GE", "GS", "IC", "IP", "LP", "NA", "NC", "NF", "NI", "NL", "NO",
    "NP", "NR", "NZ", "OC", "PC", "R0", "RC", "RS", "SA", "SC", "SE", "SF", "SG", "SI", "SL", "SO", "SP",
    "SR", "SZ", "ZC",
)  # fmt: skip
_UK_COMPANY_NUMBER = re.compile(rf"(?<![\w-])(?:(?:{'|'.join(_COMPANY_PREFIXES)})\d{{6}}|\d{{8}})(?![\w-])")
_COMPANY_CONTEXT = _context_pattern(
    "company number",
    "company no",
    "company reg",
    "company registration",
    "companies house",
    "registered number",
    "registered no",
    "registration number",
    "crn",
)


def _find_uk(text: str) -> Iterable[IdentifierMatch]:
    for match in _NINO_ALLOCATED.finditer(text):
        if match[1] not in _NINO_UNALLOCATED_PREFIXES:
            yield IdentifierMatch(match.start(), match.end(), UK_NINO, match[0])
    for match in _NINO_ANY_PREFIX.finditer(text):
        if _has_context(text, match.start(), _NINO_CONTEXT):
            yield IdentifierMatch(match.start(), match.end(), UK_NINO, match[0])
    for match in _UK_COMPANY_NUMBER.finditer(text):
        if _has_context(text, match.start(), _COMPANY_CONTEXT):
            yield IdentifierMatch(match.start(), match.end(), UK_COMPANY_NUMBER, match[0])


# ── IBAN ───────────────────────────────────────────────────────────────────

# Total IBAN length per country (ISO 13616 registry).
IBAN_LENGTHS: dict[str, int] = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16, "BG": 22, "BH": 22, "BR": 29,
    "BY": 28, "CH": 21, "CR": 22, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "DO": 28, "EE": 20, "EG": 29,
    "ES": 24, "FI": 18, "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23, "GL": 18, "GR": 27, "GT": 28,
    "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IQ": 23, "IS": 26, "IT": 27, "JO": 30, "KW": 30, "KZ": 20,
    "LB": 28, "LC": 32, "LI": 21, "LT": 20, "LU": 20, "LV": 21, "MC": 27, "MD": 24, "ME": 22, "MK": 19,
    "MR": 27, "MT": 31, "MU": 30, "NL": 18, "NO": 15, "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29,
    "RO": 24, "RS": 22, "SA": 24, "SC": 31, "SE": 24, "SI": 19, "SK": 24, "SM": 27, "ST": 25, "SV": 28,
    "TL": 23, "TN": 24, "TR": 26, "UA": 29, "VA": 22, "VG": 24, "XK": 20,
}  # fmt: skip

_IBAN_START = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2})\d{2}")


def iban_is_valid(value: str) -> bool:
    """Return whether ``value`` (spaces allowed) is a well-formed IBAN whose mod-97 check passes."""
    compact = value.replace(" ", "")
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", compact):
        return False
    if IBAN_LENGTHS.get(compact[:2]) != len(compact):
        return False
    rearranged = compact[4:] + compact[:4]
    return int("".join(str(int(char, 36)) for char in rearranged)) % 97 == 1


def _take_compact(text: str, start: int, count: int) -> int | None:
    """End offset after ``count`` upper-case alphanumerics from ``start``, allowing single spaces between them.

    Returns ``None`` when fewer characters are available or the value runs on
    into further alphanumerics without a separator.
    """
    taken, index, length = 0, start, len(text)
    while taken < count:
        if index < length and (text[index].isdigit() or "A" <= text[index] <= "Z"):
            taken += 1
            index += 1
        elif taken and index + 1 < length and text[index] == " " and text[index + 1].isalnum():
            index += 1
        else:
            return None
    if index < length and text[index].isalnum():
        return None
    return index


def _find_iban(text: str) -> Iterable[IdentifierMatch]:
    for match in _IBAN_START.finditer(text):
        length = IBAN_LENGTHS.get(match[1])
        if length is None:
            continue
        end = _take_compact(text, match.start(), length)
        if end is not None and iban_is_valid(text[match.start() : end]):
            yield IdentifierMatch(match.start(), end, IBAN_CODE, text[match.start() : end])


# ── European VAT ───────────────────────────────────────────────────────────


def _vat_at(body: str) -> bool:
    digits = body[1:]
    total = int(digits[0]) + int(digits[2]) + int(digits[4]) + int(digits[6])
    for char in digits[1:7:2]:
        doubled = 2 * int(char)
        total += doubled // 10 + doubled % 10
    return (10 - (total + 4) % 10) % 10 == int(digits[7])


def _vat_be(body: str) -> bool:
    return (int(body[:8]) + int(body[8:])) % 97 == 0


def _vat_dk(body: str) -> bool:
    return _weighted_sum(body, (2, 7, 6, 5, 4, 3, 2, 1)) % 11 == 0


def _vat_fi(body: str) -> bool:
    remainder = _weighted_sum(body, (7, 9, 10, 5, 8, 4, 2)) % 11
    return remainder != 1 and (11 - remainder) % 11 == int(body[7])


def _vat_fr(body: str) -> bool | None:
    if not body[:2].isdigit():
        return None  # letter keys use a different scheme; fall back to a label
    return (12 + 3 * (int(body[2:]) % 97)) % 97 == int(body[:2])


def _vat_gb(body: str) -> bool | None:
    if not body[:9].isdigit():
        return None  # government department (GD) and health authority (HA) numbers
    # Remainder 0 is the original scheme, 42 the "+55" scheme introduced in 2010; 55 is also in use.
    return (_weighted_sum(body, (8, 7, 6, 5, 4, 3, 2)) + int(body[7:9])) % 97 in (0, 42, 55)


def _vat_hu(body: str) -> bool:
    return _weighted_sum(body, (9, 7, 3, 1, 9, 7, 3, 1)) % 10 == 0


def _vat_lu(body: str) -> bool:
    return int(body[:6]) % 89 == int(body[6:])


def _vat_nl(body: str) -> bool:
    digits = body[:9]
    eleven_proof = (_weighted_sum(digits, (9, 8, 7, 6, 5, 4, 3, 2)) - int(digits[8])) % 11 == 0
    # Numbers issued since 2020 satisfy ISO 7064 mod 97 over "NL" + number instead.
    as_number = "".join(str(int(char, 36)) for char in "NL" + body)
    return eleven_proof or int(as_number) % 97 == 1


def _vat_pl(body: str) -> bool:
    return _weighted_sum(body, (6, 5, 7, 2, 3, 4, 5, 6, 7)) % 11 == int(body[9])


def _vat_pt(body: str) -> bool:
    remainder = _weighted_sum(body, (9, 8, 7, 6, 5, 4, 3, 2)) % 11
    return (0 if remainder < 2 else 11 - remainder) == int(body[8])


def _vat_si(body: str) -> bool:
    check = 11 - _weighted_sum(body, (8, 7, 6, 5, 4, 3, 2)) % 11
    return check != 11 and check % 10 == int(body[7])


def _vat_se(body: str) -> bool:
    return _luhn_valid(body[:10])


# A checksum returns True/False, or None when it does not apply to this form.
_VAT_CHECKSUMS: dict[str, Callable[[str], bool | None]] = {
    "AT": _vat_at,
    "BE": _vat_be,
    "DE": _iso7064_mod11_10_valid,
    "DK": _vat_dk,
    "FI": _vat_fi,
    "FR": _vat_fr,
    "GB": _vat_gb,
    "HR": _iso7064_mod11_10_valid,
    "HU": _vat_hu,
    "IT": _luhn_valid,
    "LU": _vat_lu,
    "NL": _vat_nl,
    "PL": _vat_pl,
    "PT": _vat_pt,
    "SE": _vat_se,
    "SI": _vat_si,
    "XI": _vat_gb,
}

# Shape of the number after the country prefix, separators removed.
_VAT_FORMATS: dict[str, re.Pattern[str]] = {
    country: re.compile(shape)
    for country, shape in {
        "AT": r"U\d{8}",
        "BE": r"[01]\d{9}",
        "BG": r"\d{9,10}",
        "CY": r"\d{8}[A-Z]",
        "CZ": r"\d{8,10}",
        "DE": r"\d{9}",
        "DK": r"\d{8}",
        "EE": r"\d{9}",
        "EL": r"\d{9}",
        "ES": r"[A-Z0-9]\d{7}[A-Z0-9]",
        "FI": r"\d{8}",
        "FR": r"[0-9A-HJ-NP-Z]{2}\d{9}",
        "GB": r"\d{9}|\d{12}|(?:GD|HA)\d{3}",
        "HR": r"\d{11}",
        "HU": r"\d{8}",
        "IE": r"\d{7}[A-W][A-I]?|\d[A-Z+*]\d{5}[A-W]",
        "IT": r"\d{11}",
        "LT": r"\d{9}|\d{12}",
        "LU": r"\d{8}",
        "LV": r"\d{11}",
        "MT": r"\d{8}",
        "NL": r"\d{9}B\d{2}",
        "PL": r"\d{10}",
        "PT": r"\d{9}",
        "RO": r"[1-9]\d{1,9}",
        "SE": r"\d{10}01",
        "SI": r"\d{8}",
        "SK": r"\d{10}",
        "XI": r"\d{9}|\d{12}|(?:GD|HA)\d{3}",
    }.items()
}

_VAT_START = re.compile(rf"(?<![A-Za-z0-9])({'|'.join(sorted(_VAT_FORMATS))})[ -]?(?=[0-9A-Z])")
_VAT_CONTEXT = _context_pattern(
    "vat", "tva", "iva", "btw", "mwst", "ust", "ust-idnr", "umsatzsteuer", "moms", "alv", "dph", "ddv",
    "pdv", "fpa", "pvm", "pvn", "nif", "nipc", "cif", "partita", "tax number", "tax id",
)  # fmt: skip


def vat_is_valid(value: str) -> bool | None:
    """Check a prefixed VAT number (separators allowed).

    Returns ``False`` when the shape or check digits are wrong, ``True`` when
    they are right, and ``None`` when the shape is right but this module has
    no check-digit rule for that country or form.
    """
    compact = re.sub(r"[ .-]", "", value)
    country, body = compact[:2], compact[2:]
    shape = _VAT_FORMATS.get(country)
    if shape is None or not shape.fullmatch(body):
        return False
    checksum = _VAT_CHECKSUMS.get(country)
    return None if checksum is None else checksum(body)


def _vat_run_ends(text: str, start: int) -> list[int]:
    """Candidate end offsets for a VAT body beginning at ``start``, longest first.

    The body may contain single spaces or dots between groups; a candidate
    ends only where a group ends, never inside a run of characters.
    """
    ends: list[int] = []
    index, length, taken = start, len(text), 0
    while index < length and taken <= 14:
        char = text[index]
        if char.isdigit() or "A" <= char <= "Z" or char in "+*":
            index += 1
            taken += 1
            continue
        if char in " ." and index + 1 < length and (text[index + 1].isdigit() or "A" <= text[index + 1] <= "Z"):
            ends.append(index)
            index += 1
            continue
        break
    if index >= length or not text[index].isalnum():
        ends.append(index)  # otherwise the last group runs on into other text
    return sorted(set(ends), reverse=True)


def _find_vat(text: str) -> Iterable[IdentifierMatch]:
    for match in _VAT_START.finditer(text):
        for end in _vat_run_ends(text, match.end()):
            candidate = text[match.start() : end]
            verdict = vat_is_valid(candidate)
            if verdict is True or (verdict is None and _has_context(text, match.start(), _VAT_CONTEXT)):
                yield IdentifierMatch(match.start(), end, EU_VAT, candidate)
                break


# ── Public API ─────────────────────────────────────────────────────────────


def resolve_overlaps(matches: Iterable[IdentifierMatch]) -> list[IdentifierMatch]:
    """Keep the longest of any overlapping matches (earliest first on ties), in text order."""
    kept: list[IdentifierMatch] = []
    for candidate in sorted(matches, key=lambda m: (-(m.end - m.start), m.start)):
        if all(candidate.end <= other.start or candidate.start >= other.end for other in kept):
            kept.append(candidate)
    return sorted(kept, key=lambda m: m.start)


def find_identifiers(text: str) -> list[IdentifierMatch]:
    """Return the non-overlapping identifiers recognised in ``text``, in order."""
    if not text:
        return []
    return resolve_overlaps([*_find_us(text), *_find_uk(text), *_find_iban(text), *_find_vat(text)])
