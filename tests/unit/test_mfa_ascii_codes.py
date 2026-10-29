# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""ASCII-only MFA input, with unchanged valid-code normalization and replay rules."""

from __future__ import annotations

import base64
import hashlib

import pytest

from auth import totp

RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()
RECOVERY_CODE = "abkde-fgh23"


class _NotText:
    def __str__(self):
        raise AssertionError("MFA input must not be coerced to text")

    def __bool__(self):
        raise AssertionError("MFA input must be type-checked before use")


NON_TEXT = [
    pytest.param(None, id="none"),
    pytest.param(False, id="false"),
    pytest.param(True, id="true"),
    pytest.param(0, id="zero"),
    pytest.param(287082, id="integer"),
    pytest.param(287082.0, id="float"),
    pytest.param(b"287082", id="bytes"),
    pytest.param(bytearray(b"abkde-fgh23"), id="bytearray"),
    pytest.param([], id="empty-list"),
    pytest.param(list("287082"), id="list"),
    pytest.param(("287082",), id="tuple"),
    pytest.param({"code": "287082"}, id="mapping"),
    pytest.param({"287082"}, id="set"),
    pytest.param(_NotText(), id="non-coercible"),
]
NON_ASCII_SEPARATORS = [
    "\u00a0",
    "\u1680",
    "\u2003",
    "\u2028",
    "\u202f",
    "\u3000",
    "\u200b",
    "\ufeff",
    "\u2010",
    "\u2011",
    "\u2212",
]
NON_ASCII_TOTP = [
    pytest.param("\u0662\u0668\u0667\u0660\u0668\u0662", id="arabic-indic"),
    pytest.param("\u06f2\u06f8\u06f7\u06f0\u06f8\u06f2", id="extended-arabic-indic"),
    pytest.param("\u0968\u096e\u096d\u0966\u096e\u0968", id="devanagari"),
    pytest.param("\uff12\uff18\uff17\uff10\uff18\uff12", id="full-width"),
    pytest.param("\u00b2\u2078\u2077\u2070\u2078\u00b2", id="superscript"),
    pytest.param("\U0001d7d0\U0001d7d6\U0001d7d5\U0001d7ce\U0001d7d6\U0001d7d0", id="math-bold"),
    pytest.param("28708\u0662", id="mixed-script"),
    pytest.param("\u00b2" * 6, id="superscript-repeat"),
    pytest.param("28708\ud800", id="unpaired-surrogate"),
]
MALFORMED_TOTP = [
    "",
    " -\t\r\n",
    "28708",
    "2870820",
    "abcdef",
    "28708a",
    "+287082",
    "28708.2",
    "287_082",
    "287/082",
    "287082\x00",
    "\x00287082",
    "287082\x7f",
]
MALFORMED_RECOVERY = [
    "",
    " -\t\r\n",
    "abkdefgh2",
    "abkdefgh234",
    "123456",
    "abkde-fgh20",
    "abkde-fgh21",
    "abkde-fgh2o",
    "abkde-fgh2i",
    "abkde-fgh2l",
    "abkde_fgh23",
    "abkde/fgh23",
    "abkde-fgh23\x00",
    "abkde-fgh23\x7f",
    "ab\u212ade-fgh23",
    "AB\u212aDE-FGH23",
    "\uff41bkde-fgh23",
    "\u0430bkde-fgh23",
    "\u03b1bkde-fgh23",
    "abkde-fgh\uff12\uff13",
    "abkde-fgh\u0662\u0663",
    "abkde-fgh\u0968\u0969",
    "abkde-fgh\u00b2\u00b3",
    "abkde-fgh2\ud800",
]


@pytest.mark.parametrize("code", NON_TEXT + NON_ASCII_TOTP)
def test_normalization_refuses_non_text_and_non_ascii(code) -> None:
    assert totp.normalize_code(code) == ""


@pytest.mark.parametrize("code", NON_TEXT + NON_ASCII_TOTP + MALFORMED_TOTP)
def test_invalid_authenticator_codes_are_refused_without_exceptions(code) -> None:
    assert totp.verify(RFC_SECRET, code, now=59) is None


@pytest.mark.parametrize("separator", NON_ASCII_SEPARATORS)
def test_non_ascii_separators_are_not_removed_from_valid_codes(separator: str) -> None:
    authenticator = f"287{separator}082"
    recovery = f"abkde{separator}fgh23"
    assert totp.normalize_code(authenticator) == ""
    assert totp.verify(RFC_SECRET, authenticator, now=59) is None
    assert not totp.looks_like_recovery_code(recovery)
    with pytest.raises(ValueError, match="Invalid recovery code"):
        totp.hash_recovery_code("user-a", recovery)


@pytest.mark.parametrize("code", NON_TEXT + MALFORMED_RECOVERY)
def test_invalid_recovery_codes_are_refused_without_exceptions(code) -> None:
    assert not totp.looks_like_recovery_code(code)


@pytest.mark.parametrize("code", NON_TEXT + MALFORMED_RECOVERY)
def test_invalid_recovery_codes_are_not_hashed(code, monkeypatch) -> None:
    def unexpected_scrypt(*args, **kwargs):
        raise AssertionError("Malformed recovery input must not reach scrypt")

    monkeypatch.setattr(totp.hashlib, "scrypt", unexpected_scrypt)
    with pytest.raises(ValueError, match="Invalid recovery code"):
        totp.hash_recovery_code("user-a", code)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (59, "287082"),
        (1111111109, "081804"),
        (1111111111, "050471"),
        (1234567890, "005924"),
        (2000000000, "279037"),
        (20000000000, "353130"),
    ],
)
def test_rfc6238_sha1_six_digit_controls(now: int, expected: str) -> None:
    # RFC 6238 appendix B: the final six digits of each SHA-1 vector.
    step = now // 30
    assert totp.code_at(RFC_SECRET, step) == expected
    assert totp.verify(RFC_SECRET, expected, now=now) == step
    assert totp.verify(RFC_SECRET, expected, last_used_step=step, now=now) is None


@pytest.mark.parametrize("code", ["287082", "287-082", " 287 082 ", "\t287\r08\n2\f\v", "--287---082--"])
def test_ascii_authenticator_normalization_is_preserved(code: str) -> None:
    assert totp.normalize_code(code) == "287082"
    assert totp.verify(RFC_SECRET, code, now=59) == 1


@pytest.mark.parametrize("offset", [-2, -1, 0, 1, 2])
def test_drift_window_and_replay_watermark_are_preserved(offset: int) -> None:
    now = 1_800_000_000
    step = totp.current_step(now) + offset
    code = totp.code_at(RFC_SECRET, step)
    if abs(offset) > 1:
        assert totp.verify(RFC_SECRET, code, now=now) is None
    else:
        assert totp.verify(RFC_SECRET, code, last_used_step=step - 1, now=now) == step
        assert totp.verify(RFC_SECRET, code, last_used_step=step, now=now) is None
        assert totp.verify(RFC_SECRET, code, last_used_step=step + 1, now=now) is None


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_every_window_is_compared_even_after_a_match(offset: int, monkeypatch) -> None:
    now = 1_800_000_000
    step = totp.current_step(now)
    code_at = totp.code_at
    code = code_at(RFC_SECRET, step + offset)
    probes = []

    def record_probe(secret: str, probe: int) -> str:
        probes.append(probe)
        return code_at(secret, probe)

    monkeypatch.setattr(totp, "code_at", record_probe)
    assert totp.verify(RFC_SECRET, code, now=now) == step + offset
    assert probes == [step - 1, step, step + 1]


@pytest.mark.parametrize(
    "code",
    [RECOVERY_CODE, "ABKDE-FGH23", "abkdefgh23", "ABKDE FGH23", "\tabkde\r-fg\nh23\f\v", "--a-b-k-d-e-f-g-h-2-3--"],
)
def test_ascii_recovery_normalization_and_hash_contract_are_preserved(code: str) -> None:
    expected = hashlib.scrypt(
        b"abkdefgh23",
        salt=b"agenticorg-mfa-recovery:user-a",
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    ).hex()
    assert totp.looks_like_recovery_code(code)
    assert totp.hash_recovery_code("user-a", code) == expected
    assert totp.hash_recovery_code("user-b", code) != expected


def test_generated_recovery_codes_still_meet_the_contract() -> None:
    codes = totp.generate_recovery_codes()
    assert len(codes) == 10
    assert len(set(codes)) == 10
    assert all(code.isascii() and len(code) == 11 and code[5] == "-" for code in codes)
    assert all(totp.looks_like_recovery_code(code) for code in codes)
    assert all(len(totp.hash_recovery_code("user-a", code)) == 64 for code in codes)
