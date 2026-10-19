# SPDX-License-Identifier: Apache-2.0
"""The server-managed UCP state inside a merchant commerce config.

Stored under ``public_publishing["ucp"]`` of ``commerce_c6z_merchant_configs``
(tenant table, FORCE row-level security). The merchant-config upsert never
accepts it from a request body and carries it over unchanged
(:func:`carry_ucp_state`); only the UCP operator actions write it. Shape::

    {
      "serving_host": "shop.example.com",
      "challenge":   {method, host, record_name, record_type, record_value, created_at, created_by},
      "attestation": {method, host, record_name, record_value_sha256, verified_at, verified_by},
      "namespace":   "com.example.shop",
      "published":   {published_at, published_by, namespace} | None,
      "signing_keys": [{kid, alg, public_jwk, private_key_ciphertext, status, created_at, ...}],
      "verified_policy": {artifact_id, verifier_result_ref, captured_at, policy: {...}},
      "verified_public_discovery": {artifact_id, verifier_result_ref, captured_at,
                                    publication_allowed, serving_host, namespace_attestation_ref}
    }

Publishing is the merchant's opt-in to public discovery: the Grantex authority
request then carries ``public_discovery_requested`` with the attested
``serving_host`` and ``namespace_attestation_ref``, and the profile is served
only while the verified ``public_discovery_state`` artifact Grantex issues in
return allows publication for that host and attestation.

:func:`public_ucp_state_view` is the only form that leaves the server (no
private-key ciphertext).
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from core.commerce.merchant_keys import public_key_records_view

UCP_STATE_KEY = "ucp"


def ucp_state(public_publishing: Mapping[str, Any] | None) -> dict[str, Any]:
    """A deep copy of the UCP state (empty when absent)."""
    if not isinstance(public_publishing, Mapping):
        return {}
    state = public_publishing.get(UCP_STATE_KEY)
    return copy.deepcopy(dict(state)) if isinstance(state, Mapping) else {}


def with_ucp_state(public_publishing: Mapping[str, Any] | None, state: Mapping[str, Any]) -> dict[str, Any]:
    """A new ``public_publishing`` dict carrying ``state`` (assign it; JSON columns need a new object)."""
    updated = dict(public_publishing or {})
    updated[UCP_STATE_KEY] = copy.deepcopy(dict(state))
    return updated


def carry_ucp_state(
    existing_public_publishing: Mapping[str, Any] | None,
    new_public_publishing: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """``new_public_publishing`` with the stored UCP state carried over and any client-supplied one dropped."""
    updated = dict(new_public_publishing or {})
    updated.pop(UCP_STATE_KEY, None)
    state = ucp_state(existing_public_publishing)
    if state:
        updated[UCP_STATE_KEY] = state
    return updated


def public_ucp_state_view(state: Mapping[str, Any]) -> dict[str, Any]:
    view = copy.deepcopy(dict(state))
    view["signing_keys"] = public_key_records_view(view.get("signing_keys") or [])
    discovery = view.get("verified_public_discovery")
    if isinstance(discovery, Mapping):
        view["verified_public_discovery"] = {
            key: discovery[key]
            for key in ("artifact_id", "captured_at", "publication_allowed", "serving_host")
            if key in discovery
        }
    policy = view.get("verified_policy")
    if isinstance(policy, Mapping):
        view["verified_policy"] = {key: policy[key] for key in ("artifact_id", "captured_at") if key in policy}
    return view


def public_publishing_view(public_publishing: Mapping[str, Any] | None) -> dict[str, Any]:
    """``public_publishing`` for API responses: the UCP state without key ciphertext."""
    view = dict(public_publishing or {})
    if UCP_STATE_KEY in view:
        view[UCP_STATE_KEY] = public_ucp_state_view(ucp_state(public_publishing))
    return view


def attested_host(state: Mapping[str, Any]) -> str | None:
    """The serving host whose DNS attestation is recorded, if it matches ``serving_host``."""
    attestation = state.get("attestation")
    host = state.get("serving_host")
    if not isinstance(attestation, Mapping) or not isinstance(host, str):
        return None
    if attestation.get("host") != host or not attestation.get("verified_at"):
        return None
    return host


def is_published(state: Mapping[str, Any]) -> bool:
    published = state.get("published")
    return isinstance(published, Mapping) and bool(published.get("published_at")) and attested_host(state) is not None


def public_discovery_opt_in(state: Mapping[str, Any]) -> dict[str, str] | None:
    """The authority-request opt-in fields, for a published merchant with an attested host; else None."""
    host = attested_host(state)
    attestation = state.get("attestation")
    if host is None or not is_published(state) or not isinstance(attestation, Mapping):
        return None
    ref = attestation.get("ref")
    if not isinstance(ref, str) or not ref:
        return None
    return {"serving_host": host, "namespace_attestation_ref": ref}


def public_discovery_allows(state: Mapping[str, Any], *, host: str, live_artifact_ids: set[str]) -> bool:
    """Whether the verified ``public_discovery_state`` snapshot allows publishing on ``host`` right now.

    The snapshot must allow publication, name this host and this merchant's
    attestation ref, and come from an artifact that is still a verified, fresh
    and unrevoked cache record (``live_artifact_ids``).
    """
    snapshot = state.get("verified_public_discovery")
    opt_in = public_discovery_opt_in(state)
    if not isinstance(snapshot, Mapping) or opt_in is None:
        return False
    return (
        snapshot.get("publication_allowed") is True
        and snapshot.get("serving_host") == host == opt_in["serving_host"]
        and snapshot.get("namespace_attestation_ref") == opt_in["namespace_attestation_ref"]
        and snapshot.get("artifact_id") in live_artifact_ids
    )
