# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""G21: the buyer authority and vendor member of a UCP complete body are validated field by field (400 on any
malformed value), and an AP2 checkout mandate stands in for the mandate reference by its SHA-256."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime

import pytest

from core.commerce.ucp.completion import (
    VENDOR_MEMBER,
    AuthorityRequestError,
    ap2_mandate_evidence,
    parse_buyer_authority,
    vendor_member,
)


def _segment(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


def test_absent_authority_is_none_and_null_fields_are_dropped() -> None:
    assert parse_buyer_authority(None) is None
    parsed = parse_buyer_authority(
        {
            "mandate_ref": "mandate-1",
            "mandate_hash": "a" * 64,
            "mandate_expires_at": "2026-10-21T00:00:00Z",
            "consent_ref": None,
            "passport_max_amount_minor_units": 0,
            "passport_currency": "INR",
        }
    )
    assert parsed == {
        "mandate_ref": "mandate-1",
        "mandate_hash": "a" * 64,
        "mandate_expires_at": "2026-10-21T00:00:00Z",
        "passport_max_amount_minor_units": 0,
        "passport_currency": "INR",
    }


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        (["not", "an", "object"], "must be an object"),
        ({"surprise": "x"}, "buyer_authority.surprise is not a known field"),
        ({"passport_max_amount_minor_units": -1}, "non-negative integer"),
        ({"passport_max_amount_minor_units": True}, "non-negative integer"),
        ({"passport_max_amount_minor_units": "100"}, "non-negative integer"),
        ({"mandate_ref": 7}, "at most 200 characters"),
        ({"passport_currency": "RUPEE"}, "at most 3 characters"),
        ({"mandate_hash": "A" * 64}, "lowercase hex SHA-256"),
        ({"mandate_hash": "z" * 64}, "lowercase hex SHA-256"),
        ({"passport_expires_at": "tomorrow"}, "passport_expires_at must be an RFC 3339 time"),
    ],
)
def test_malformed_authority_is_a_request_error(raw, fragment) -> None:
    with pytest.raises(AuthorityRequestError, match=fragment):
        parse_buyer_authority(raw)


def test_vendor_member_defaults_and_parses_the_authority() -> None:
    assert vendor_member({}) == {}
    member = vendor_member(
        {VENDOR_MEMBER: {"decision_id": "dec_1", "decide_only": True, "buyer_authority": {"consent_status": "granted"}}}
    )
    assert member == {
        "decision_id": "dec_1",
        "decide_only": True,
        "buyer_authority": {"consent_status": "granted"},
        "mandate_request": None,
    }
    assert vendor_member({VENDOR_MEMBER: {}}) == {
        "decision_id": None,
        "decide_only": False,
        "buyer_authority": None,
        "mandate_request": None,
    }


@pytest.mark.parametrize(
    ("member", "fragment"),
    [
        ("not-an-object", "must be an object"),
        ({"decision_id": ""}, "decision_id must be a string"),
        ({"decision_id": "d" * 81}, "decision_id must be a string"),
        ({"decision_id": 12}, "decision_id must be a string"),
        ({"decide_only": "yes"}, "decide_only must be a boolean"),
        ({"buyer_authority": {"unknown": 1}}, "not a known field"),
    ],
)
def test_malformed_vendor_member_is_a_request_error(member, fragment) -> None:
    with pytest.raises(AuthorityRequestError, match=fragment):
        vendor_member({VENDOR_MEMBER: member})


def test_ap2_checkout_mandate_is_referenced_by_its_digest_and_reads_exp_unverified() -> None:
    exp = int(datetime(2026, 10, 21, 12, 0, tzinfo=UTC).timestamp())
    mandate = ".".join((_segment({"alg": "ES256"}), _segment({"exp": exp, "sub": "buyer"}), "c2ln")) + "~ZGlzYw"
    evidence = ap2_mandate_evidence(mandate)
    digest = hashlib.sha256(mandate.encode("ascii")).hexdigest()
    assert evidence == {
        "mandate_ref": f"ap2:checkout_mandate:{digest[:32]}",
        "mandate_hash": digest,
        "mandate_expires_at": "2026-10-21T12:00:00+00:00",
    }
    # The evidence stays a valid buyer authority (the hash is lowercase hex, the time has an offset).
    assert parse_buyer_authority(evidence) == evidence


@pytest.mark.parametrize(
    "payload",
    [
        "no-dots-at-all",
        "aGVhZGVy.%%%not-base64%%%.c2ln",
        ".".join(("aGVhZGVy", _segment(["a", "list"]), "c2ln")),
        ".".join(("aGVhZGVy", _segment({"exp": "soon"}), "c2ln")),
        ".".join(("aGVhZGVy", _segment({"exp": True}), "c2ln")),
        ".".join(("aGVhZGVy", _segment({"exp": 10**20}), "c2ln")),
    ],
)
def test_unreadable_or_absent_exp_leaves_the_expiry_unset(payload) -> None:
    evidence = ap2_mandate_evidence(payload)
    assert evidence["mandate_expires_at"] is None
    assert evidence["mandate_hash"] == hashlib.sha256(payload.encode("ascii")).hexdigest()
