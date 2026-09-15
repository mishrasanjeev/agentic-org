# SPDX-License-Identifier: Apache-2.0
"""Typed field schema per source kind, and strict validation of worker responses.

The worker is the component that touches attacker-controlled bytes, so its
output is not trusted either: the parent re-validates every response against
the same schema and refuses the whole extraction on any deviation.
"""

from __future__ import annotations

import datetime
import enum
import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from core.extraction import _worker

FieldValue = str | int | bool | tuple[str, ...] | None


class SourceKind(enum.StrEnum):
    WEBSITE = "website"
    REGISTRY_DOCUMENT = "registry_document"
    APPLICANT_UPLOAD = "applicant_upload"


class ExtractionFailure(enum.StrEnum):
    """Why an extraction produced no fields. Stable; used as a metric label."""

    INPUT_TOO_LARGE = "extraction_input_too_large"
    UNSUPPORTED_CONTENT_TYPE = "extraction_unsupported_content_type"
    START_FAILED = "extraction_start_failed"
    TIMEOUT = "extraction_timeout"
    CRASHED = "extraction_crashed"
    OUTPUT_TOO_LARGE = "extraction_output_too_large"
    OUTPUT_INVALID = "extraction_output_invalid"
    REQUEST_INVALID = "extraction_request_invalid"
    DECODE_FAILED = "extraction_decode_failed"
    PARSE_FAILED = "extraction_parse_failed"
    ISOLATION_UNAVAILABLE = "extraction_isolation_unavailable"


# Reasons the worker itself may report.
WORKER_REASONS = frozenset(
    {
        ExtractionFailure.INPUT_TOO_LARGE,
        ExtractionFailure.UNSUPPORTED_CONTENT_TYPE,
        ExtractionFailure.REQUEST_INVALID,
        ExtractionFailure.DECODE_FAILED,
        ExtractionFailure.PARSE_FAILED,
        ExtractionFailure.ISOLATION_UNAVAILABLE,
    }
)

ISOLATION_LAYERS = frozenset({"audit_hook", "netns", "rlimits", "seccomp"})
FIELDS: Mapping[SourceKind, Mapping[str, Mapping[str, Any]]] = {
    SourceKind(kind): spec for kind, spec in _worker.FIELDS.items()
}
CONTENT_TYPES: Mapping[SourceKind, tuple[str, ...]] = {
    SourceKind(kind): types for kind, types in _worker.CONTENT_TYPES.items()
}
MAX_EXCERPTS = _worker.MAX_EXCERPTS
MAX_EXCERPT_CHARS = _worker.MAX_EXCERPT_CHARS

# Every value the schema itself can emit as an enum. These carry no source text.
VOCABULARY = frozenset(
    str(choice) for spec in _worker.FIELDS.values() for field in spec.values() for choice in field.get("choices", ())
)


class OutputInvalidError(ValueError):
    """A worker response does not match the schema."""


def untrusted_text_fields(kind: SourceKind) -> tuple[str, ...]:
    """Fields whose string values are free text taken from the source."""
    return tuple(sorted(name for name, spec in FIELDS[kind].items() if spec.get("untrusted_text")))


def _fail(detail: str) -> OutputInvalidError:
    return OutputInvalidError(detail)


def _has_control(text: str) -> bool:
    return any(unicodedata.category(ch).startswith("C") for ch in text)


def _valid_string(value: Any, spec: Mapping[str, Any]) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= spec["max_length"]
        and value == value.strip()
        and "  " not in value
        and not _has_control(value)
        and re.fullmatch(spec["pattern"], value) is not None
    )


def _validate_field(name: str, value: Any, spec: Mapping[str, Any]) -> FieldValue:
    kind = spec["type"]
    if kind == "bool":
        if not isinstance(value, bool):
            raise _fail(f"{name}: expected a boolean")
        return value
    if value is None and kind in ("string", "enum", "int", "date"):
        return None
    if kind == "string":
        if not _valid_string(value, spec):
            raise _fail(f"{name}: string outside its length or character class")
        return value
    if kind == "enum":
        if value not in spec["choices"]:
            raise _fail(f"{name}: not one of the allowed values")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int) or not spec["minimum"] <= value <= spec["maximum"]:
            raise _fail(f"{name}: integer out of range")
        return value
    if kind == "date":
        if not isinstance(value, str) or not re.fullmatch(_worker.DATE_PATTERN, value):
            raise _fail(f"{name}: expected an ISO date")
        try:
            datetime.date.fromisoformat(value)
        except ValueError as exc:
            raise _fail(f"{name}: expected an ISO date") from exc
        return value
    if kind in ("string_list", "enum_list"):
        if not isinstance(value, list) or len(value) > spec["max_items"]:
            raise _fail(f"{name}: expected a list of at most {spec['max_items']} items")
        if value != sorted(set(value)):
            raise _fail(f"{name}: list must be sorted and unique")
        for item in value:
            if kind == "enum_list" and item not in spec["choices"]:
                raise _fail(f"{name}: list item not one of the allowed values")
            if kind == "string_list" and not _valid_string(item, spec):
                raise _fail(f"{name}: list item outside its length or character class")
        return tuple(value)
    raise _fail(f"{name}: unknown field type")  # unreachable: schema types are fixed


def validate_response(
    kind: SourceKind, payload: Any
) -> tuple[dict[str, FieldValue], tuple[str, ...], list[tuple[str, str]], tuple[str, ...]]:
    """Validate a successful worker response: ``(fields, rejected_fields, excerpts, isolation)``."""
    if not isinstance(payload, dict):
        raise _fail("response is not an object")
    expected = {"v", "ok", "isolation", "fields", "rejected_fields", "excerpts"}
    if set(payload) != expected:
        raise _fail(f"response keys {sorted(payload)} differ from {sorted(expected)}")
    if payload["v"] != _worker.PROTOCOL_VERSION or payload["ok"] is not True:
        raise _fail("unexpected protocol version or status")

    isolation = payload["isolation"]
    if not isinstance(isolation, list) or not set(isolation) <= ISOLATION_LAYERS or isolation != sorted(set(isolation)):
        raise _fail("isolation must be a sorted list of known layers")

    spec = FIELDS[kind]
    fields_raw = payload["fields"]
    if not isinstance(fields_raw, dict) or set(fields_raw) != set(spec):
        raise _fail("fields do not match the schema for this source kind")
    fields = {name: _validate_field(name, fields_raw[name], spec[name]) for name in sorted(spec)}

    rejected = payload["rejected_fields"]
    if not isinstance(rejected, list) or rejected != sorted(set(rejected)) or not set(rejected) <= set(spec):
        raise _fail("rejected_fields must be a sorted list of schema fields")

    excerpts_raw = payload["excerpts"]
    if not isinstance(excerpts_raw, list) or len(excerpts_raw) > MAX_EXCERPTS:
        raise _fail(f"excerpts must be a list of at most {MAX_EXCERPTS}")
    excerpts: list[tuple[str, str]] = []
    for item in excerpts_raw:
        if not isinstance(item, dict) or set(item) != {"field", "text"} or item["field"] not in spec:
            raise _fail("excerpt must name a schema field")
        text = item["text"]
        if not isinstance(text, str) or not 0 < len(text) <= MAX_EXCERPT_CHARS or _has_control(text):
            raise _fail("excerpt text outside its length or character class")
        excerpts.append((item["field"], text))
    if excerpts != sorted(set(excerpts)):
        raise _fail("excerpts must be sorted and unique")
    return fields, tuple(rejected), excerpts, tuple(isolation)
