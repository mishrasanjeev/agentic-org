# SPDX-License-Identifier: Apache-2.0
"""Map a verified OACP ``policy`` artifact payload to UCP ``policies[]``.

UCP 2026-08-25 ``common/types/policy.json``: each policy carries ``type`` (an
open reverse-DNS vocabulary), ``description`` (``plain`` / ``markdown`` /
``html``, at least one), optional ``applies_to`` (RFC 9535 JSONPath targets
relative to the embedding response root; absent means response-wide) and an
optional ``url``. Well-known types are ``dev.ucp.shopping.policy.return`` and
``dev.ucp.shopping.policy.warranty``; other terms use the business's own
namespace (overview, Policy types). ``policies[]`` exists on catalog, cart,
checkout and order responses in 2026-08-25 only; 2026-04-08 has no such field.

This module is pure: no I/O, no clock. Catalog responses call it now; the cart,
checkout and order renderers call the same function.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from core.commerce.ucp.authority import is_reverse_domain_name, is_ucp_governed

RETURN_POLICY_TYPE = "dev.ucp.shopping.policy.return"
WARRANTY_POLICY_TYPE = "dev.ucp.shopping.policy.warranty"

# OACP policy payload field -> (UCP policy type, or the suffix under the
# merchant namespace; companion URL field).
_WELL_KNOWN_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("return_policy_summary", RETURN_POLICY_TYPE, "return_policy_url"),
    ("warranty_policy_summary", WARRANTY_POLICY_TYPE, "warranty_policy_url"),
)
_NAMESPACED_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("fulfillment_policy_summary", "policy.fulfillment", "fulfillment_policy_url"),
    ("cancellation_policy_summary", "policy.cancellation", "cancellation_policy_url"),
    ("support_policy_summary", "policy.support", "support_policy_url"),
)
MAX_POLICY_TEXT = 2000
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TAG_RE = re.compile(r"<[^>]+>")


def map_oacp_policy_to_ucp_policies(
    policy_payload: Mapping[str, Any] | None,
    *,
    vendor_namespace: str | None = None,
    applies_to: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """UCP ``policies[]`` from an OACP ``policy`` payload.

    Return and warranty terms map to the well-known ``dev.ucp.shopping.policy.*``
    types. Fulfillment, cancellation and support terms have no well-known type;
    they map to ``<vendor_namespace>.policy.<kind>`` only when the caller passes
    the merchant's attested namespace, and are omitted otherwise. Empty or
    non-text summaries are omitted; URLs are kept only when ``https`` without
    userinfo. ``applies_to`` is copied onto every policy when given (omitted
    means response-wide). The result is ordered as above and never contains a
    type twice.
    """
    if not isinstance(policy_payload, Mapping):
        return []
    namespace = _valid_namespace(vendor_namespace)
    targets = _valid_targets(applies_to)
    policies: list[dict[str, Any]] = []
    for field_name, policy_type, url_field in _WELL_KNOWN_FIELDS:
        policy = _policy(policy_payload, field_name, policy_type, url_field, targets)
        if policy is not None:
            policies.append(policy)
    if namespace is not None:
        for field_name, suffix, url_field in _NAMESPACED_FIELDS:
            policy = _policy(policy_payload, field_name, f"{namespace}.{suffix}", url_field, targets)
            if policy is not None:
                policies.append(policy)
    return policies


def _policy(
    payload: Mapping[str, Any],
    field_name: str,
    policy_type: str,
    url_field: str,
    targets: list[str] | None,
) -> dict[str, Any] | None:
    text = _plain_text(payload.get(field_name))
    if text is None:
        return None
    policy: dict[str, Any] = {"type": policy_type, "description": {"plain": text}}
    if targets:
        policy["applies_to"] = list(targets)
    url = _https_url(payload.get(url_field))
    if url is not None:
        policy["url"] = url
    return policy


def _plain_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = _CONTROL_RE.sub("", _TAG_RE.sub(" ", value))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_POLICY_TEXT] or None


def _https_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = urlsplit(text)
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    return text


def _valid_namespace(value: str | None) -> str | None:
    if not value:
        return None
    namespace = value.strip().lower()
    if not is_reverse_domain_name(namespace) or is_ucp_governed(namespace):
        return None
    return namespace


def _valid_targets(value: Sequence[str] | None) -> list[str] | None:
    if not value:
        return None
    targets = [item for item in value if isinstance(item, str) and item.startswith("$")]
    return targets or None
