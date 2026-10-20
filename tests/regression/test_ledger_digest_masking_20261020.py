# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Commerce ledger details keep digest values intact while personal data is still masked.

Replays the failure seen when sealing mandate hashes: the PII masker's phone
pattern (ten digits starting 6-9, no word boundary) matched a digit run inside a
hex SHA-256 and a hex decision id, so the ledger sealed ``...190******981...``
and the mandate hash could no longer be matched to its mandate (about one hash
in five has such a run).
"""

from __future__ import annotations

from core.commerce.ledger import _mask_details
from core.tool_gateway.pii_masker import mask_pii

MANDATE_HASH = "f5bdb621793c27f296b9231111543e9a58919ccbc404cef291cc974d1cee9d7b"
DIGIT_RUN_HASH = "ab" + "9876543210" + "c" * 52
DECISION_ID = "arbdec_78c924613654819098173539817353a39417125a"


def test_the_masker_alone_corrupts_a_digest_with_a_digit_run() -> None:
    assert mask_pii(DIGIT_RUN_HASH) != DIGIT_RUN_HASH
    assert mask_pii(DECISION_ID) != DECISION_ID


def test_ledger_details_keep_digests_and_digest_ids() -> None:
    details = {
        "decision_id": DECISION_ID,
        "hashes": {"intent": MANDATE_HASH, "cart": DIGIT_RUN_HASH, "payment": "sha256:" + DIGIT_RUN_HASH},
        "list": [DIGIT_RUN_HASH],
        "reference": "aoc-" + "9876543210" + "d" * 34,
    }
    assert _mask_details(details) == details


def test_personal_data_is_still_masked() -> None:
    masked = _mask_details({"note": "call 9876543210", "email": "buyer@example.com", "nested": ["9876543210"]})
    assert "9876543210" not in str(masked)
    assert "buyer@example.com" not in str(masked)
