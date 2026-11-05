# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Where the shopping network stands with each outside program, and whether a shop qualifies.

The programs are the ChatGPT app directory (``chatgpt``), Google Merchant Center (``google``,
one multi-client account with a sub-account per shop), Microsoft Merchant Center for Copilot
shopping (``microsoft``) and Perplexity's merchant program (``perplexity``). AgenticOrg registers
the network with each one; the operator records the outcome in
``AGENTICORG_NETWORK_REGISTRATIONS`` (``settings.network_registrations``)::

    {"chatgpt": "submitted", "google": {"status": "live", "shops": {"tea-corner": "submitted"}}}

A program value is ``not_started``, ``submitted`` or ``live``, or an object with that ``status``
and optional per-shop values that win over it (a Merchant Center sub-account is approved shop by
shop). A missing, unknown or malformed value counts as ``not_started``.

A shop qualifies for a program only while it is listed, its web address answers buyer agents and
its feed has at least one eligible product (``core.commerce.shop_feed``). The Sell everywhere page
then shows ``registration_pending`` (the network has not registered yet), ``submitted`` or
``live``; a shop that does not qualify shows ``not_eligible`` with the reason.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import structlog

from core.config import settings

logger = structlog.get_logger()

PROGRAMS: tuple[str, ...] = ("chatgpt", "google", "microsoft", "perplexity")
# Programs that read the shop's product feed.
FEED_PROGRAMS: frozenset[str] = frozenset({"google", "microsoft", "perplexity"})
PROGRAM_STATES: frozenset[str] = frozenset({"not_started", "submitted", "live"})
_SHOP_STATUS = {"not_started": "registration_pending", "submitted": "submitted", "live": "live"}

# Why a shop does not qualify yet, in the order they are checked.
NOT_LISTED = "not_listed"
WEB_ADDRESS_NOT_LIVE = "web_address_not_live"
CATALOG_NOT_VERIFIED = "catalog_not_verified"
NO_ELIGIBLE_PRODUCTS = "no_eligible_products"


@dataclass(frozen=True)
class ProgramState:
    status: str
    shops: Mapping[str, str]

    def for_shop(self, slug: str | None) -> str:
        if slug and slug in self.shops:
            return self.shops[slug]
        return self.status


@dataclass(frozen=True)
class FeedSummary:
    """What a shop's feed holds now: ``catalog_verified`` False means the verified catalog is unavailable."""

    catalog_verified: bool
    eligible_items: int
    feed_url: str | None = None


def _state(value: Any) -> str | None:
    return value if isinstance(value, str) and value in PROGRAM_STATES else None


def parse_registrations(raw: str | None) -> dict[str, ProgramState]:
    """Every program's state from the setting text; anything unreadable counts as ``not_started``."""
    out = {program: ProgramState("not_started", {}) for program in PROGRAMS}
    text = (raw or "").strip()
    if not text:
        return out
    try:
        document = json.loads(text)
    except ValueError:
        logger.warning("network_registrations_invalid", reason="not_json")
        return out
    if not isinstance(document, Mapping):
        logger.warning("network_registrations_invalid", reason="not_an_object")
        return out
    for program in PROGRAMS:
        value = document.get(program)
        if value is None:
            continue
        if isinstance(value, Mapping):
            status = _state(value.get("status")) or "not_started"
            shops_raw = value.get("shops")
            shops = (
                {str(slug): state for slug, raw_state in shops_raw.items() if (state := _state(raw_state))}
                if isinstance(shops_raw, Mapping)
                else {}
            )
            out[program] = ProgramState(status, shops)
        elif _state(value):
            out[program] = ProgramState(str(value), {})
        else:
            logger.warning("network_registrations_invalid", reason="unknown_state", program=program)
    return out


def registrations() -> dict[str, ProgramState]:
    return parse_registrations(getattr(settings, "network_registrations", ""))


def ineligible_reason(*, listed: bool, web_live: bool, feed: FeedSummary | None) -> str | None:
    """Why the shop does not qualify for any program yet, or None once it does."""
    if not listed:
        return NOT_LISTED
    if not web_live:
        return WEB_ADDRESS_NOT_LIVE
    if feed is None or not feed.catalog_verified:
        return CATALOG_NOT_VERIFIED
    if feed.eligible_items <= 0:
        return NO_ELIGIBLE_PRODUCTS
    return None


def program_view(
    program: str,
    *,
    slug: str | None,
    listed: bool,
    web_live: bool,
    feed: FeedSummary | None,
    states: Mapping[str, ProgramState] | None = None,
) -> dict[str, Any]:
    """One program's row for the Sell everywhere page: ``status``, ``reason`` and, for feed programs, ``feed_url``."""
    state = (states or registrations()).get(program) or ProgramState("not_started", {})
    reason = ineligible_reason(listed=listed, web_live=web_live, feed=feed)
    view: dict[str, Any] = {
        "status": "not_eligible" if reason else _SHOP_STATUS[state.for_shop(slug)],
        "reason": reason,
    }
    if program in FEED_PROGRAMS:
        view["feed_url"] = feed.feed_url if feed is not None and reason is None else None
    return view
