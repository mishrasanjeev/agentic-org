# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Outside programs on the Sell everywhere page: the operator's AGENTICORG_NETWORK_REGISTRATIONS setting
and each shop's eligibility (core.commerce.network_registrations, network.surfaces_view)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.commerce import network as net
from core.commerce import network_registrations as reg
from core.config import settings

FEED = reg.FeedSummary(catalog_verified=True, eligible_items=3, feed_url="https://tea.shops.example/feeds/google.xml")


def test_an_empty_or_broken_setting_means_not_started_everywhere() -> None:
    for raw in ("", "  ", "not json", "[1, 2]", json.dumps({"google": "approved", "microsoft": 5})):
        states = reg.parse_registrations(raw)
        assert set(states) == set(reg.PROGRAMS)
        assert all(state.status == "not_started" and not state.shops for state in states.values()), raw


def test_program_values_and_per_shop_overrides() -> None:
    states = reg.parse_registrations(
        json.dumps(
            {
                "chatgpt": "submitted",
                "google": {"status": "live", "shops": {"tea-corner": "submitted", "bad": "approved"}},
                "perplexity": {"status": "nonsense"},
                "unknown_program": "live",
            }
        )
    )
    assert states["chatgpt"].status == "submitted"
    assert states["google"].for_shop("tea-corner") == "submitted"
    assert states["google"].for_shop("other-shop") == "live"
    assert dict(states["google"].shops) == {"tea-corner": "submitted"}
    assert states["perplexity"].status == "not_started"
    assert states["microsoft"].status == "not_started"


@pytest.mark.parametrize(
    ("listed", "web_live", "feed", "reason"),
    [
        (False, True, FEED, reg.NOT_LISTED),
        (True, False, FEED, reg.WEB_ADDRESS_NOT_LIVE),
        (True, True, None, reg.CATALOG_NOT_VERIFIED),
        (True, True, reg.FeedSummary(False, 0), reg.CATALOG_NOT_VERIFIED),
        (True, True, reg.FeedSummary(True, 0), reg.NO_ELIGIBLE_PRODUCTS),
        (True, True, FEED, None),
    ],
)
def test_eligibility_reasons_in_order(listed: bool, web_live: bool, feed: reg.FeedSummary | None, reason: str) -> None:
    assert reg.ineligible_reason(listed=listed, web_live=web_live, feed=feed) == reason
    view = reg.program_view("google", slug="tea-corner", listed=listed, web_live=web_live, feed=feed, states={})
    if reason:
        assert view == {"status": "not_eligible", "reason": reason, "feed_url": None}
    else:
        assert view == {"status": "registration_pending", "reason": None, "feed_url": FEED.feed_url}


@pytest.mark.parametrize(
    ("state", "shown"), [("not_started", "registration_pending"), ("submitted", "submitted"), ("live", "live")]
)
def test_an_eligible_shop_shows_the_program_state(state: str, shown: str) -> None:
    states = {"perplexity": reg.ProgramState(state, {})}
    view = reg.program_view("perplexity", slug="tea-corner", listed=True, web_live=True, feed=FEED, states=states)
    assert view["status"] == shown
    chatgpt = reg.program_view("chatgpt", slug="tea-corner", listed=True, web_live=True, feed=FEED, states={})
    assert "feed_url" not in chatgpt


def test_surfaces_view_reports_every_program(monkeypatch: pytest.MonkeyPatch) -> None:
    host = "tea.shops.example"
    monkeypatch.setattr(net, "published_host", lambda _config: host)
    monkeypatch.setattr(
        settings, "network_registrations", json.dumps({"google": {"status": "submitted", "shops": {"tea": "live"}}})
    )
    listing = SimpleNamespace(slug="tea", status="listed")
    config = SimpleNamespace(provider_policy={})
    view = net.surfaces_view(listing, config, None, feed=FEED)
    assert view["google"] == {"status": "live", "reason": None, "feed_url": FEED.feed_url}
    assert view["microsoft"] == {"status": "registration_pending", "reason": None, "feed_url": FEED.feed_url}
    assert view["chatgpt"] == {"status": "registration_pending", "reason": None}
    # The address is still being certified: no program can read the shop yet.
    waiting = net.surfaces_view(listing, config, SimpleNamespace(host=host, status="pending_certificate"), feed=FEED)
    assert waiting["perplexity"]["status"] == "not_eligible"
    assert waiting["perplexity"]["reason"] == reg.WEB_ADDRESS_NOT_LIVE
    # Without a feed read, a listed live shop waits for its verified catalog.
    assert net.surfaces_view(listing, config, None)["google"]["reason"] == reg.CATALOG_NOT_VERIFIED
    assert net.surfaces_view(None, config, None)["google"] == {
        "status": "not_eligible",
        "reason": reg.NOT_LISTED,
        "feed_url": None,
    }
