"""Unit tests for the Tally auto-detect request alias (BUG-005).

The Aishwarya 2026-04-20 Auto-Detect failure after a successful Test
Connection was caused by a field-name mismatch: the wizard sent
``bridge_url`` / ``bridge_id`` (matching /test-tally) while
``TallyDetectRequest`` required ``tally_bridge_url`` /
``tally_bridge_id``. The model now accepts both shapes via
``populate_by_name`` + aliases. Lock that in so it can't regress.
"""

from __future__ import annotations

import pytest

from api.v1.companies import TallyDetectRequest


class TestTallyDetectRequestAlias:
    def test_accepts_short_bridge_keys(self) -> None:
        body = TallyDetectRequest(bridge_url="https://abc.ngrok.app", bridge_id="bid-1")
        assert body.bridge_url == "https://abc.ngrok.app"
        assert body.bridge_id == "bid-1"

    def test_accepts_legacy_tally_prefix(self) -> None:
        body = TallyDetectRequest(
            tally_bridge_url="https://abc.ngrok.app",
            tally_bridge_id="bid-1",
        )
        assert body.bridge_url == "https://abc.ngrok.app"
        assert body.bridge_id == "bid-1"

    def test_bridge_url_is_required(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            TallyDetectRequest()

    def test_bridge_id_defaults_to_empty(self) -> None:
        body = TallyDetectRequest(bridge_url="https://x.y.com")
        assert body.bridge_id == ""
