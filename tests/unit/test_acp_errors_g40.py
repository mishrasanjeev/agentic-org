# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""G40: every internal message code and UCP protocol error maps to an ACP ``MessageError`` / ``Error`` with the
exact 2026-04-17 enums; no internal code is left unmapped."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from core.commerce.acp import errors
from core.commerce.acp import headers as hdr

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "core" / "commerce" / "acp" / "schemas" / "2026-04-17" / "schema.agentic_checkout.json").read_text(
        encoding="utf-8"
    )
)


def _valid(definition: str, document: object) -> list[str]:
    validator = Draft202012Validator(
        {"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{definition}"}
    )
    return [e.message for e in validator.iter_errors(document)]


def test_the_enums_are_the_schema_enums() -> None:
    defs = SCHEMA["$defs"]
    assert set(defs["MessageError"]["properties"]["code"]["enum"]) == errors.MESSAGE_ERROR_CODES
    assert set(defs["MessageWarning"]["properties"]["code"]["enum"]) == errors.MESSAGE_WARNING_CODES
    assert tuple(defs["Error"]["properties"]["type"]["enum"]) == errors.ERROR_TYPES


def test_every_mapping_lands_in_the_message_error_enum() -> None:
    for internal, acp_code in errors.MESSAGE_ERROR_MAP.items():
        assert acp_code in errors.MESSAGE_ERROR_CODES, internal
    assert errors.DEFAULT_MESSAGE_ERROR_CODE in errors.MESSAGE_ERROR_CODES


def _internal_error_codes() -> set[str]:
    """Every error code the UCP engine, completion, decision consumption and the ACP binding can emit."""
    codes: set[str] = set()
    sources = [
        *sorted((ROOT / "core" / "commerce" / "ucp").glob("*.py")),
        ROOT / "core" / "commerce" / "arbiter_store.py",
        *sorted((ROOT / "core" / "commerce" / "acp").glob("*.py")),
    ]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        codes |= set(re.findall(r'msg\.error\(\s*"([a-z_0-9]+)"', text))
        codes |= set(re.findall(r'DecisionRefusedError\(\s*"([a-z_0-9]+)"', text))
        codes |= set(re.findall(r'QuantityError\(\s*"([a-z_0-9]+)"', text))
        codes |= set(re.findall(r'"code": "([a-z_0-9]+)"', text))
        codes |= set(re.findall(r'_message\(\s*"([a-z_0-9]+)"', text))
    # Message codes that are not errors (warnings and infos handled by map_message) and catalog outcomes.
    return codes - {
        "quantity_converted",
        "quantity_increment",
        "catalog_unavailable",
        "price_filter_ignored",
        "decision_issued",
        "price_change",
    }


def test_no_internal_error_code_is_unmapped() -> None:
    missing = sorted(code for code in _internal_error_codes() if code not in errors.MESSAGE_ERROR_MAP)
    assert missing == []


@pytest.mark.parametrize("internal", sorted(errors.MESSAGE_ERROR_MAP))
def test_each_error_code_renders_a_valid_message_error(internal: str) -> None:
    rendered = errors.map_message(
        {"type": "error", "code": internal, "content": "x", "severity": "recoverable", "path": "$.line_items[2].item"}
    )
    assert rendered["code"] == errors.MESSAGE_ERROR_MAP[internal]
    assert rendered["param"] == "$.line_items[2]" and rendered["resolution"] == "recoverable"
    assert _valid("MessageError", rendered) == []


def test_unknown_error_code_becomes_invalid() -> None:
    rendered = errors.map_message({"type": "error", "code": "executor_said_no", "content": "x"})
    assert rendered["code"] == "invalid" and _valid("MessageError", rendered) == []


def test_warnings_outside_the_acp_enum_become_info_and_known_ones_stay_warnings() -> None:
    info = errors.map_message(
        {"type": "warning", "code": "quantity_increment", "content": "x", "path": "$.line_items[0].quantity"}
    )
    assert info["type"] == "info" and "code" not in info and _valid("MessageInfo", info) == []
    warning = errors.map_message({"type": "warning", "code": "price_change", "content": "x"})
    assert warning == {"type": "warning", "code": "price_change", "content_type": "plain", "content": "x"}
    assert _valid("MessageWarning", warning) == []
    plain = errors.map_message({"type": "info", "code": "decision_issued", "content": "x"})
    assert plain == {"type": "info", "content_type": "plain", "content": "x"}


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        ("recoverable", {"resolution": "recoverable"}),
        ("requires_buyer_input", {"resolution": "requires_buyer_input"}),
        ("requires_buyer_review", {"resolution": "requires_buyer_review"}),
        ("unrecoverable", {"severity": "critical"}),
        (None, {}),
    ],
)
def test_ucp_severity_becomes_acp_resolution_or_severity(severity, expected) -> None:
    rendered = errors.map_message({"type": "error", "code": "missing", "content": "x", "severity": severity})
    assert {k: rendered[k] for k in ("resolution", "severity") if k in rendered} == expected
    assert _valid("MessageError", rendered) == []


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("$.line_items[3]", "$.line_items[3]"),
        ("$.line_items[0].item.quantity_unit", "$.line_items[0]"),
        ("$.fulfillment", "$.fulfillment_details"),
        ("$.fulfillment.methods[0].selected_destination_id", "$.fulfillment_details"),
        ("$.fulfillment.methods[1].groups[0].selected_option_id", "$.selected_fulfillment_options"),
        ("$.payment", "$.payment_data"),
        ("$.payment.instruments[0].credential", "$.payment_data"),
        ("$.buyer.email", "$.buyer"),
        ("$.status", "$.status"),
        ("$.currency", "$.currency"),
        ("$.totals", "$.totals"),
        ("$.agenticorg_commerce.passport_jwt", "$.agenticorg_commerce"),
        ("$.capabilities", "$.capabilities"),
        ("$.ap2.checkout_mandate", "$"),
        ("$", "$"),
        ("not a path", "$"),
        (None, "$"),
    ],
)
def test_internal_paths_map_to_acp_fields(path, expected) -> None:
    assert errors.map_param(path) == expected


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (400, "invalid_request", (400, "invalid_request", "invalid")),
        (404, "not_found", (404, "invalid_request", "not_found")),
        (409, "invalid_state", (405, "invalid_request", "session_not_modifiable")),
        (503, "idempotency_unavailable", (503, "service_unavailable", "idempotency_unavailable")),
        (422, "something_else", (422, "invalid_request", "invalid")),
        (500, "boom", (500, "processing_error", "processing_error")),
    ],
)
def test_protocol_errors_map_to_acp_error_objects(status, code, expected) -> None:
    body = {
        "ucp": {"version": "2026-04-08", "status": "error"},
        "messages": [{"type": "error", "code": code, "content": "c"}],
    }
    acp_status, error = errors.protocol_error(status, body)
    assert (acp_status, error["type"], error["code"]) == expected
    assert _valid("Error", error) == []
    assert "ucp" not in error and "messages" not in error


def test_protocol_error_without_messages_is_still_an_error_object() -> None:
    status, error = errors.protocol_error(400, {})
    assert status == 400 and _valid("Error", error) == []


def test_header_errors_are_schema_valid_errors() -> None:
    for status, error in (
        hdr.api_version_error(None),
        hdr.api_version_error("2026-01-30"),
        hdr.idempotency_key_error(None),
        hdr.idempotency_key_error("k" * 256),
        hdr.signature_error(),
    ):
        assert status in (400, 401)
        assert _valid("Error", error) == []
    assert hdr.api_version_error("2026-04-17") is None
    assert hdr.api_version_error(None)[1]["supported_versions"] == ["2026-04-17"]
    with pytest.raises(ValueError):
        errors.acp_error("rate_limit_exceeded", "x", "y")
