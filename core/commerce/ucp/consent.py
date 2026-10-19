# SPDX-License-Identifier: Apache-2.0
"""Buyer consent (``dev.ucp.shopping.buyer_consent``): the ``dev.ucp.consent.*`` purpose map.

2026-08-25: ``buyer.consent`` maps reverse-DNS purposes to ``{granted, source,
description, links?, segments?}`` where ``source`` is ``business`` or
``platform``; the well-known purposes are ``dev.ucp.consent.analytics``,
``.marketing`` (segments ``.marketing.email``, ``.marketing.sms``),
``.preferences`` and ``.sale_or_sharing``. 2026-04-08: ``buyer.consent`` is four
booleans (``analytics``, ``preferences``, ``marketing``, ``sale_of_data``).
The stored state uses the 2026-08-25 keys; the 2026-04-08 renderer maps back.

Source of the values: AgenticOrg holds no server-side copy of the buyer's
Grantex consent record (Grantex consent reaches AgenticOrg only as a
reference and status asserted by the caller, which the arbiter seals as
evidence). The map is therefore rendered from the consent the platform sends
on the session and marked ``source: platform``; nothing is marked
``business`` until a business-held consent record exists.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

ANALYTICS = "dev.ucp.consent.analytics"
MARKETING = "dev.ucp.consent.marketing"
PREFERENCES = "dev.ucp.consent.preferences"
SALE_OR_SHARING = "dev.ucp.consent.sale_or_sharing"
MARKETING_EMAIL = "dev.ucp.consent.marketing.email"
MARKETING_SMS = "dev.ucp.consent.marketing.sms"

DESCRIPTIONS = {
    ANALYTICS: "Measure how the store is used to improve it.",
    MARKETING: "Promotional communications from the store.",
    PREFERENCES: "Remember the buyer's preferences across visits.",
    SALE_OR_SHARING: "Sale or sharing of personal data with third parties.",
    MARKETING_EMAIL: "Promotional email.",
    MARKETING_SMS: "Promotional text messages.",
}
R08_KEYS = {"analytics": ANALYTICS, "preferences": PREFERENCES, "marketing": MARKETING, "sale_of_data": SALE_OR_SHARING}
_PURPOSE = re.compile(r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9_]*)+$")
SOURCES = ("business", "platform")


class ConsentRequestError(ValueError):
    """A malformed consent object (protocol error, 400)."""


def parse_consent(raw: Any, *, version: str) -> dict[str, Any] | None:
    """The version-neutral consent map from a request's ``buyer.consent`` (None when absent)."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ConsentRequestError("buyer.consent must be an object")
    out: dict[str, Any] = {}
    if version < "2026-08-25":
        for key, value in raw.items():
            purpose = R08_KEYS.get(str(key))
            if purpose is None:
                raise ConsentRequestError(f"unknown consent field {key}")
            if not isinstance(value, bool):
                raise ConsentRequestError(f"buyer.consent.{key} must be a boolean")
            out[purpose] = {"granted": value, "source": "platform"}
        return out
    for key, value in raw.items():
        purpose = str(key)
        if not _PURPOSE.match(purpose) or not isinstance(value, Mapping):
            raise ConsentRequestError("buyer.consent maps reverse-DNS purposes to objects")
        out[purpose] = _purpose_entry(value, purpose)
    return out


def _purpose_entry(value: Mapping[str, Any], purpose: str) -> dict[str, Any]:
    granted = value.get("granted")
    if not isinstance(granted, bool):
        raise ConsentRequestError(f"buyer.consent['{purpose}'].granted must be a boolean")
    source = value.get("source", "platform")
    # A platform cannot assert the business's own records.
    if source not in SOURCES or source == "business":
        source = "platform"
    entry: dict[str, Any] = {"granted": granted, "source": source}
    segments = value.get("segments")
    if isinstance(segments, Mapping):
        entry["segments"] = {}
        for key, segment in segments.items():
            if not _PURPOSE.match(str(key)) or not isinstance(segment, Mapping):
                raise ConsentRequestError("consent segments map reverse-DNS keys to objects")
            entry["segments"][str(key)] = {
                k: val for k, val in _purpose_entry(segment, str(key)).items() if k != "segments"
            }
    return entry


def render_consent(state: Mapping[str, Any] | None, *, version: str) -> dict[str, Any] | None:
    if not state:
        return None
    if version < "2026-08-25":
        reverse = {purpose: key for key, purpose in R08_KEYS.items()}
        return {reverse[p]: bool(e.get("granted")) for p, e in state.items() if p in reverse}
    out: dict[str, Any] = {}
    for purpose, entry in state.items():
        rendered: dict[str, Any] = {
            "granted": bool(entry.get("granted")),
            "source": entry.get("source", "platform"),
            "description": DESCRIPTIONS.get(purpose, "Consent recorded for this purpose."),
        }
        segments = entry.get("segments")
        if segments:
            rendered["segments"] = {
                key: {
                    "granted": bool(seg.get("granted")),
                    "source": seg.get("source", "platform"),
                    "description": DESCRIPTIONS.get(key, "Consent recorded for this segment."),
                }
                for key, seg in segments.items()
            }
        out[purpose] = rendered
    return out
