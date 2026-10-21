# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Synthetic ACP scope for tests: the UCP test merchant opted in to ACP, request bodies, schema validation."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from core.commerce.acp import capabilities as caps
from core.commerce.acp import checkout as acp
from core.commerce.ucp.catalog import ucp_variant_id
from core.commerce.ucp.merchant import MerchantUcp, merchant_ucp_from_config
from tests import ucp_txn_fixtures as uf

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (ROOT / "core" / "commerce" / "acp" / "schemas" / "2026-04-17" / "schema.agentic_checkout.json").read_text(
        encoding="utf-8"
    )
)
PLATFORM = "acp:api_key:00000000-0000-4000-8000-00000000a001"
OTHER_PLATFORM = "acp:api_key:00000000-0000-4000-8000-00000000a002"
TOTE = ucp_variant_id({"variant_id": "gid://shopify/ProductVariant/1"})
ADDRESS = {
    "name": "Asha Buyer",
    "line_one": "1 MG Road",
    "city": "Pune",
    "state": "MH",
    "country": "IN",
    "postal_code": "411001",
}


def merchant(tenant_id: str, *, acp_enabled: bool = True, **ucp_overrides: Any) -> MerchantUcp:
    config = uf.merchant_config(tenant_id, **ucp_overrides)
    config["provider_policy"]["acp"] = {"checkout_enabled": acp_enabled}
    resolved = merchant_ucp_from_config(config, tenant_id=tenant_id, serving_host=uf.HOST)
    assert resolved is not None
    return resolved


def errors(definition: str, document: Any) -> list[str]:
    """Schema errors of an ACP document; the active vendor extension's member is checked separately."""
    if isinstance(document, Mapping) and caps.VENDOR_FIELD in document:
        document = {k: v for k, v in document.items() if k != caps.VENDOR_FIELD}
    validator = Draft202012Validator(
        {"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{definition}"},
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    return [f"{list(e.absolute_path)}: {e.message}" for e in validator.iter_errors(document)]


def assert_session(document: Any) -> None:
    definition = "CheckoutSessionWithOrder" if document.get("status") == "completed" else "CheckoutSession"
    problems = errors(definition, document)
    assert not problems, problems[:5]


def create_body(*items: str, address: bool = True, vendor: Mapping[str, Any] | None = None, **extra: Any) -> dict:
    body: dict[str, Any] = {
        "line_items": [{"id": item} for item in (items or (TOTE,))],
        "currency": "inr",
        "capabilities": {"extensions": [caps.VENDOR_EXTENSION]} if vendor is not None else {},
        "buyer": {"email": "buyer@example.com", "first_name": "Asha"},
    }
    if address:
        body["fulfillment_details"] = {"name": "Asha Buyer", "address": dict(ADDRESS)}
    if vendor is not None:
        body[caps.VENDOR_FIELD] = dict(vendor)
    body.update(extra)
    return body


def select_shipping(option_id: str = "ship-std") -> dict:
    return {"selected_fulfillment_options": [{"type": "shipping", "option_id": option_id, "item_ids": []}]}


EXAMPLE_INSTRUMENT_TOKEN = "tok_example_1"  # synthetic instrument token, not a credential


def complete_body(*, vendor: Mapping[str, Any] | None = None) -> dict:
    body: dict[str, Any] = {
        "payment_data": {
            "handler_id": "plural_tokenized",
            "instrument": {
                "type": "card",
                "credential": {"type": "plural_pine_token", "token": EXAMPLE_INSTRUMENT_TOKEN},
            },
        },
        "risk_signals": {"ip_address": "192.0.2.10", "user_agent": "Nimbus Shopper 2.4"},
    }
    if vendor is not None:
        body[caps.VENDOR_FIELD] = dict(vendor)
    return body


def headers(key: str | None = None, **extra: str) -> dict[str, str]:
    out = {"api-version": "2026-04-17", "request-id": f"req-{uuid.uuid4().hex[:8]}"}
    if key is not None:
        out["idempotency-key"] = key
    out.update({k.lower(): v for k, v in extra.items()})
    return out


def request(
    operation: str,
    tenant_id: str,
    body: Any = None,
    *,
    key: str | None = None,
    session_id: str | None = None,
    platform: str = PLATFORM,
    host: str = uf.HOST,
    **extra_headers: str,
) -> acp.AcpRequest:
    method = "GET" if operation == "get" else "POST"
    if method == "POST" and key is None:
        key = f"key-{uuid.uuid4().hex}"
    return acp.AcpRequest(
        operation=operation,
        tenant_id=tenant_id,
        host=host,
        platform=platform,
        method=method,
        path="/acp/checkout_sessions" + (f"/{session_id}" if session_id else ""),
        raw_body=b"" if body is None else json.dumps(body).encode("utf-8"),
        headers=headers(key if method == "POST" else None, **extra_headers),
        session_id=session_id,
    )


def runtime(tenant_id: str, *, resolved: MerchantUcp | None = None, grantex: Any = None, mode: str = "log", **kw: Any):
    base = uf.runtime(tenant_id, resolved=resolved or merchant(tenant_id), **kw)
    return acp.AcpRuntime(ucp=base, grantex=lambda: grantex, signature_mode=lambda: mode)


def allowance_answer(session_id: str, *, max_amount: int = 100000, hours: int = 1) -> dict:
    return {
        "api_version": "2026-04-17",
        "allowance": {
            "reason": "one_time",
            "max_amount": max_amount,
            "currency": "inr",
            "checkout_session_id": session_id,
            "merchant_id": "merchant.example",
            "expires_at": (datetime.now(UTC) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z"),
        },
        "risk_signals": [],
        "metadata": {"grantex_passport_jti": "jti-1"},
        "audit_event_id": "cae_1",
    }


# A synthetic compact JWS (header, payload {"sub": "shopper-01"}, fake signature), assembled at import.
PASSPORT = ".".join(("eyJhbGciOiJFUzI1NiJ9", "eyJzdWIiOiJzaG9wcGVyLTAxIn0", "c2lnbmF0dXJl"))
