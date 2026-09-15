# SPDX-License-Identifier: Apache-2.0
"""Load and validate documents against the governed-case domain schemas.

The schemas live in ``schemas/<name>.schema.json`` (JSON Schema 2020-12). Each
has a versioned ``$id`` such as ``https://agenticorg.ai/schemas/ownership_graph/1.0.0``
and cross-file references resolve locally, never over the network.

Validation fails closed: an unknown schema name, a schema that does not load,
an unresolvable reference or any validation error raises
:class:`DomainSchemaError` with a reason code and every error found::

    from core.domain_schemas import validate

    validate("ownership_graph", document)

See ``docs/schemas/domain-schemas.md``.
"""

from __future__ import annotations

import json
import re
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "schemas"
SCHEMA_BASE_URI = "https://agenticorg.ai/schemas/"
SCHEMA_VERSION = "1.0.0"

#: Document schemas, in dependency order. ``common`` holds shared definitions only.
DOCUMENT_SCHEMAS: tuple[str, ...] = (
    "ownership_graph",
    "screening_result",
    "screening_disposition",
    "policy_result",
    "underwriting_memo",
    "business_case",
    "case_push",
)
SHARED_SCHEMAS: tuple[str, ...] = ("common",)
DOMAIN_SCHEMAS: tuple[str, ...] = SHARED_SCHEMAS + DOCUMENT_SCHEMAS


class DomainSchemaError(ValueError):
    """A schema could not be used, or a document does not conform to it."""

    def __init__(self, reason: str, schema: str, errors: list[str]) -> None:
        self.reason = reason
        self.schema = schema
        self.errors = errors
        summary = "; ".join(errors[:5]) + (f"; and {len(errors) - 5} more" if len(errors) > 5 else "")
        super().__init__(f"{reason}: {schema}: {summary}")


def schema_id(name: str, version: str = SCHEMA_VERSION) -> str:
    return f"{SCHEMA_BASE_URI}{name}/{version}"


_RFC3339_DATE_TIME = re.compile(
    r"^(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})[Tt](?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?P<fraction>\.[0-9]+)?(?P<offset>[Zz]|[+-](?P<oh>[0-9]{2}):(?P<om>[0-9]{2}))$"
)


def _check_date_time(value: object) -> bool:
    """RFC 3339 ``date-time``: a full date, ``T``, a full time with seconds and an explicit offset."""
    if not isinstance(value, str):
        return True
    match = _RFC3339_DATE_TIME.match(value)
    if match is None:
        return False
    try:
        date.fromisoformat(match["date"])
    except ValueError:
        return False
    hour, minute, second = int(match["hour"]), int(match["minute"]), int(match["second"])
    if hour > 23 or minute > 59 or second > 60:  # 60 is a leap second
        return False
    if match["oh"] is not None and (int(match["oh"]) > 23 or int(match["om"]) > 59):
        return False
    return True


def _check_uri(value: object) -> bool:
    if not isinstance(value, str):
        return True
    parts = urlsplit(value)
    return bool(parts.scheme and parts.netloc) and not any(ch.isspace() for ch in value)


def _format_checker() -> FormatChecker:
    checker = FormatChecker(formats=("uuid", "date"))
    checker.checks("date-time")(_check_date_time)
    checker.checks("uri")(_check_uri)
    return checker


@cache
def _load(name: str) -> dict[str, Any]:
    if name not in DOMAIN_SCHEMAS:
        raise DomainSchemaError("unknown_schema", name, [f"no domain schema named {name!r}"])
    path = SCHEMAS_DIR / f"{name}.schema.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DomainSchemaError("schema_unreadable", name, [f"{path.name}: {exc}"]) from exc
    expected = schema_id(name)
    if document.get("$id") != expected:
        raise DomainSchemaError("schema_id_mismatch", name, [f"$id is {document.get('$id')!r}, expected {expected!r}"])
    try:
        Draft202012Validator.check_schema(document)
    except SchemaError as exc:
        raise DomainSchemaError("schema_invalid", name, [exc.message]) from exc
    return document


def load_schema(name: str) -> dict[str, Any]:
    """Return a copy of the named schema. Raises :class:`DomainSchemaError` when it is unknown or invalid."""
    return json.loads(json.dumps(_load(name)))


def _retrieve(uri: str) -> Resource[Any]:
    # Only local domain schemas resolve; any other reference fails closed.
    raise Unresolvable(ref=uri)


@cache
def _registry() -> Registry[Any]:
    resources = [(schema_id(name), DRAFT202012.create_resource(_load(name))) for name in DOMAIN_SCHEMAS]
    return Registry(retrieve=_retrieve).with_resources(resources)  # type: ignore[call-arg]


@cache
def _validator(name: str) -> Draft202012Validator:
    if name not in DOCUMENT_SCHEMAS:
        raise DomainSchemaError("unknown_schema", name, [f"{name!r} is not a document schema"])
    return Draft202012Validator(_load(name), registry=_registry(), format_checker=_format_checker())


def _location(path: Any) -> str:
    rendered = "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in path)
    return "$" + rendered


def iter_errors(name: str, document: Any) -> list[str]:
    """Every validation error for ``document``, as ``<json path>: <message>``, sorted by location."""
    validator = _validator(name)
    try:
        errors = sorted(
            validator.iter_errors(document), key=lambda err: (list(map(str, err.absolute_path)), err.message)
        )
    except Unresolvable as exc:
        raise DomainSchemaError("schema_reference_unresolvable", name, [str(exc)]) from exc
    return [f"{_location(err.absolute_path)}: {err.message}" for err in errors]


def validate(name: str, document: Any) -> None:
    """Raise :class:`DomainSchemaError` (reason ``document_invalid``) unless ``document`` conforms."""
    errors = iter_errors(name, document)
    if errors:
        raise DomainSchemaError("document_invalid", name, errors)
