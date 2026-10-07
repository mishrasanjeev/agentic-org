# SPDX-License-Identifier: Apache-2.0
"""Schema-validated tool registration and the execution envelope the gateway holds a registered tool to.

A tenant registers a tool by name (``connector:tool``, or a plain tool name)
with a JSON Schema for its inputs, optionally one for its outputs, a risk
class, and an envelope: the longest a call may take, the most output it may
return, and whether its output is treated as untrusted content. While
``AGENTICORG_TOOL_REGISTRY_ENABLED`` is on, the gateway checks every call to
a registered tool against its input schema **before the call leaves the
gateway**: inputs that fail are refused with the errors named, audited and
never dispatched; a call that runs is held to the envelope (a timeout, an
output cap, the output screened against its schema when one is declared),
and with ``AGENTICORG_TOOL_REGISTRY_REQUIRE_REGISTRATION`` on, an
unregistered tool is refused too.

Schemas are checked at registration (a valid JSON Schema, bounded, without
``$ref``), so a bad schema never reaches the request path. In-process
connector code is not process-isolated by this envelope; the extraction
worker (``core/extraction/sandbox.py``) remains the out-of-process sandbox
for untrusted content, and the envelope marks tool output as untrusted so
the model's guardrails treat it so.

Off, no call is checked or enveloped, and the endpoints are not found.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

from core.config import settings

logger = structlog.get_logger()

RISKS: tuple[str, ...] = ("read", "draft", "internal-write", "customer-write", "money", "destructive")
MAX_SCHEMA_BYTES = 32_000
MAX_NAME = 160
MAX_ERRORS = 10
MAX_ERROR_CHARS = 300
DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 300
DEFAULT_MAX_OUTPUT_BYTES = 256_000
MAX_OUTPUT_BYTES = 4_000_000
CACHE_SECONDS = 15.0
REFERENCE_KEYWORDS = frozenset({"$ref", "$dynamicRef", "$recursiveRef"})
ERROR_CODE = "E1012"
_NAME = re.compile(r"^[a-z0-9_.-]+(?::[a-z0-9_.-]+){0,2}$")


class RegistryError(ValueError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def enabled() -> bool:
    return bool(settings.tool_registry_enabled)


def registration_required() -> bool:
    return enabled() and bool(settings.tool_registry_require_registration)


def normalise_name(value: Any) -> str:
    name = str(value or "").strip().lower()
    if not name or len(name) > MAX_NAME or not _NAME.match(name):
        raise RegistryError(
            422, "name", "a tool name is letters, digits, dots, dashes and underscores, with at most two colons"
        )
    return name


def _references(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key in REFERENCE_KEYWORDS:
                found.add(key)
            found |= _references(value)
    elif isinstance(node, list):
        for item in node:
            found |= _references(item)
    return found


def check_schema(schema: Any, *, label: str) -> dict[str, Any]:
    """A usable JSON Schema for inputs or outputs: an object, bounded, without references, valid for draft 2020-12."""
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError

    if not isinstance(schema, dict) or not schema:
        raise RegistryError(422, label, f"{label} is a JSON Schema object")
    if len(json.dumps(schema, separators=(",", ":")).encode("utf-8")) > MAX_SCHEMA_BYTES:
        raise RegistryError(422, label, f"{label} is at most {MAX_SCHEMA_BYTES} bytes")
    refs = _references(schema)
    if refs:
        raise RegistryError(422, label, f"{label} must not use {', '.join(sorted(refs))}")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise RegistryError(
            422, label, f"{label} is not a valid JSON Schema: {str(exc.message)[:MAX_ERROR_CHARS]}"
        ) from None
    return dict(schema)


@dataclass(frozen=True)
class Registration:
    name: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    risk: str = "read"
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    untrusted_output: bool = True
    enabled: bool = True
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema) if self.output_schema else None,
            "risk": self.risk,
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "untrusted_output": self.untrusted_output,
            "enabled": self.enabled,
        }


def parse_fields(raw: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """The fields of a registration as they are stored; every value checked, unknown keys refused."""
    allowed = {
        "name",
        "description",
        "input_schema",
        "output_schema",
        "risk",
        "timeout_seconds",
        "max_output_bytes",
        "untrusted_output",
        "enabled",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise RegistryError(422, "unknown_field", f"unknown registration fields: {', '.join(unknown)}")
    fields: dict[str, Any] = {}
    if "name" in raw or not partial:
        fields["name"] = normalise_name(raw.get("name"))
    if "description" in raw:
        fields["description"] = str(raw.get("description") or "")[:500]
    if "input_schema" in raw or not partial:
        fields["input_schema"] = check_schema(raw.get("input_schema"), label="input_schema")
    if "output_schema" in raw:
        fields["output_schema"] = (
            None if raw.get("output_schema") is None else check_schema(raw["output_schema"], label="output_schema")
        )
    if "risk" in raw or not partial:
        risk = str(raw.get("risk") or "read").strip().lower()
        if risk not in RISKS:
            raise RegistryError(422, "risk", f"risk is one of {', '.join(RISKS)}")
        fields["risk"] = risk
    if "timeout_seconds" in raw or not partial:
        timeout = raw.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
            raise RegistryError(422, "timeout_seconds", f"timeout_seconds is 1 to {MAX_TIMEOUT_SECONDS}")
        fields["timeout_seconds"] = timeout
    if "max_output_bytes" in raw or not partial:
        cap = raw.get("max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES)
        if isinstance(cap, bool) or not isinstance(cap, int) or not 1_000 <= cap <= MAX_OUTPUT_BYTES:
            raise RegistryError(422, "max_output_bytes", f"max_output_bytes is 1000 to {MAX_OUTPUT_BYTES}")
        fields["max_output_bytes"] = cap
    if "untrusted_output" in raw or not partial:
        fields["untrusted_output"] = bool(raw.get("untrusted_output", True))
    if "enabled" in raw or not partial:
        fields["enabled"] = bool(raw.get("enabled", True))
    return fields


def registration_of(row: Any) -> Registration:
    return Registration(
        name=row.name,
        input_schema=dict(row.input_schema or {}),
        output_schema=dict(row.output_schema) if row.output_schema else None,
        risk=row.risk,
        timeout_seconds=int(row.timeout_seconds or DEFAULT_TIMEOUT_SECONDS),
        max_output_bytes=int(row.max_output_bytes or DEFAULT_MAX_OUTPUT_BYTES),
        untrusted_output=bool(row.untrusted_output),
        enabled=bool(row.enabled),
        description=str(row.description or ""),
    )


def errors_for(schema: dict[str, Any], value: Any) -> list[str]:
    """What is wrong with a value under a schema, in words, bounded; empty when it conforms."""
    from jsonschema import Draft202012Validator

    found = []
    for error in sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: list(e.path)):
        where = "/".join(str(p) for p in error.path) or "(root)"
        found.append(f"{where}: {str(error.message)[:MAX_ERROR_CHARS]}")
        if len(found) >= MAX_ERRORS:
            break
    return found


def tool_names(connector_name: str | None, tool_name: str) -> list[str]:
    """The names a registration may be stored under for a call: ``connector:tool`` first, then the bare tool."""
    tool = str(tool_name or "").strip().lower()
    names = []
    if connector_name:
        names.append(f"{str(connector_name).strip().lower()}:{tool}")
    if ":" not in tool:
        names.append(tool)
    else:
        names.append(tool)
    return [n for n in dict.fromkeys(names) if n]


@dataclass
class _Cache:
    loaded_at: float = 0.0
    entries: dict[str, Registration] = field(default_factory=dict)


_CACHE: dict[str, _Cache] = {}


def invalidate(tenant_id: Any = None) -> None:
    if tenant_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(str(tenant_id), None)


async def load(tenant_id: Any) -> dict[str, Registration]:
    """The tenant's enabled registrations by name, through a short cache."""
    key = str(tenant_id)
    cached = _CACHE.get(key)
    now = time.monotonic()
    if cached is not None and now - cached.loaded_at < CACHE_SECONDS:
        return cached.entries
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.tool_registration import ToolRegistration

    tid = uuid.UUID(key)
    async with get_tenant_session(tid) as session:
        rows = list(
            (
                await session.execute(
                    select(ToolRegistration).where(
                        ToolRegistration.tenant_id == tid, ToolRegistration.enabled.is_(True)
                    )
                )
            )
            .scalars()
            .all()
        )
    entries = {row.name: registration_of(row) for row in rows}
    _CACHE[key] = _Cache(loaded_at=now, entries=entries)
    return entries


@dataclass(frozen=True)
class Check:
    registration: Registration | None
    errors: list[str]
    unregistered: bool = False

    @property
    def refused(self) -> bool:
        return bool(self.errors) or self.unregistered


def check_call(entries: dict[str, Registration], connector_name: str | None, tool_name: str, params: Any) -> Check:
    """The registration for a call and what is wrong with its inputs; unregistered is a refusal only when required."""
    registration = None
    for name in tool_names(connector_name, tool_name):
        registration = entries.get(name)
        if registration is not None:
            break
    if registration is None:
        return Check(None, [], unregistered=registration_required())
    return Check(registration, errors_for(registration.input_schema, params if params is not None else {}))


def refusal(check: Check, tool_name: str) -> dict[str, Any]:
    """The gateway's answer for a refused call, in the shape every other refusal takes."""
    if check.unregistered:
        message = f"tool_unregistered: {tool_name} is not registered for this tenant"
    else:
        message = f"tool_input_invalid: {'; '.join(check.errors)}"
    return {"error": {"code": ERROR_CODE, "message": message, "tool_input_errors": list(check.errors)}}


async def enveloped(registration: Registration, call: Any) -> dict[str, Any]:
    """Run a dispatched call under the registration's envelope: the timeout, the output cap and the output schema."""
    try:
        result = await asyncio.wait_for(call(), timeout=registration.timeout_seconds)
    except TimeoutError:
        return {
            "error": {
                "code": ERROR_CODE,
                "message": f"tool_timeout: {registration.name} exceeded its {registration.timeout_seconds}s envelope",
            }
        }
    try:
        size = len(json.dumps(result, default=str, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        size = len(str(result).encode("utf-8"))
    if size > registration.max_output_bytes:
        return {
            "error": {
                "code": ERROR_CODE,
                "message": (
                    f"tool_output_too_large: {registration.name} returned {size} bytes, "
                    f"more than its {registration.max_output_bytes}"
                ),
            }
        }
    if registration.output_schema and isinstance(result, dict) and "error" not in result:
        problems = errors_for(registration.output_schema, result)
        if problems:
            return {
                "error": {
                    "code": ERROR_CODE,
                    "message": f"tool_output_invalid: {'; '.join(problems)}",
                    "tool_output_errors": problems,
                }
            }
    if registration.untrusted_output and isinstance(result, dict) and "error" not in result:
        result = {**result, "_untrusted": True}
    return result
