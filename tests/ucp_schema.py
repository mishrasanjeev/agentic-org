# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Validate UCP payloads against the published JSON Schemas (tests/fixtures/ucp/<version>/schemas).

The fixture files are the schemas published at ``https://ucp.dev/<version>/schemas/``
(Universal Commerce Protocol, Apache License 2.0), copied unchanged: every
``$id`` and ``$ref`` is an absolute ucp.dev URL, so they resolve from a local
registry without network access.

Requests are validated against the operation's request view: the
``ucp_request`` annotation of every property is applied for the operation
(``omit`` forbids the field, ``required`` adds it to ``required``), which is
what the ``ucp-schema resolve --request --op <op>`` tool does. Responses
apply ``ucp_response`` annotations the same way (``omit`` forbids). A resource
with extensions is validated against the base schema and each extension's
composed ``$defs['<capability>']`` schema.
"""

from __future__ import annotations

import copy
import json
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).parent / "fixtures" / "ucp"


_SCHEMA_MAPS = ("properties", "$defs", "patternProperties", "dependentSchemas")
_SCHEMA_VALUES = ("items", "additionalProperties", "not", "if", "then", "else", "contains", "propertyNames")
_SCHEMA_LISTS = ("allOf", "anyOf", "oneOf", "prefixItems")


def _apply(node: Any, key: str, op: str) -> Any:
    """Apply ``ucp_request``/``ucp_response`` annotations for ``op`` to a schema node (recursively)."""
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for name, value in node.items():
        if name in ("ucp_request", "ucp_response"):
            continue
        if name in _SCHEMA_MAPS and isinstance(value, dict):
            out[name] = {k: _apply(v, key, op) for k, v in value.items()}
        elif name in _SCHEMA_VALUES:
            out[name] = _apply(value, key, op)
        elif name in _SCHEMA_LISTS and isinstance(value, list):
            out[name] = [_apply(v, key, op) for v in value]
        else:
            out[name] = value
    props = node.get("properties")
    if isinstance(props, dict):
        required = list(out.get("required") or []) if isinstance(out.get("required"), list) else []
        new_props = dict(out["properties"])
        for name, schema in props.items():
            mode = _mode(schema.get(key) if isinstance(schema, dict) else None, op)
            if mode == "omit":
                new_props[name] = False
                if name in required:
                    required.remove(name)
            elif mode == "required" and name not in required:
                required.append(name)
            elif mode == "optional" and name in required and key == "ucp_request":
                required.remove(name)
        out["properties"] = new_props
        if required or isinstance(out.get("required"), list):
            out["required"] = required
    return out


def _mode(annotation: Any, op: str) -> str | None:
    if isinstance(annotation, str):
        return annotation
    if isinstance(annotation, dict):
        value = annotation.get(op)
        if isinstance(value, dict) and "transition" in value:
            return value["transition"].get("to")
        return value if isinstance(value, str) else None
    return None


@cache
def _documents(version: str) -> dict[str, dict[str, Any]]:
    docs = {}
    for path in (ROOT / version).rglob("*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        docs[doc["$id"]] = doc
    return docs


@cache
def _registry(version: str, key: str, op: str) -> Registry:
    resources = []
    for uri, doc in _documents(version).items():
        resources.append((uri, Resource.from_contents(_apply(copy.deepcopy(doc), key, op))))
    return Registry().with_resources(resources)


def _schema_uri(version: str, path: str) -> str:
    return f"https://ucp.dev/{version}/schemas/{path}"


def errors(version: str, path: str, instance: Any, *, request_op: str | None = None, pointer: str = "") -> list[str]:
    """Validation errors of ``instance`` against ``schemas/<path>`` (+ ``#<pointer>``)."""
    key, op = ("ucp_request", request_op) if request_op else ("ucp_response", "read")
    registry = _registry(version, key, op)
    schema = {"$ref": _schema_uri(version, path) + (f"#{pointer}" if pointer else "")}
    validator = Draft202012Validator(schema, registry=registry)
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in validator.iter_errors(instance)]


def assert_valid(version: str, path: str, instance: Any, *, request_op: str | None = None, pointer: str = "") -> None:
    problems = errors(version, path, instance, request_op=request_op, pointer=pointer)
    assert not problems, f"{path}#{pointer} ({request_op or 'response'}): {problems[:5]}"


EXTENSIONS = {
    "2026-04-08": {
        "dev.ucp.shopping.fulfillment": "shopping/fulfillment.json",
        "dev.ucp.shopping.buyer_consent": "shopping/buyer_consent.json",
        "dev.ucp.common.loyalty": "common/loyalty.json",
        "dev.ucp.shopping.ap2_mandate": "shopping/ap2_mandate.json",
    },
    "2026-08-25": {
        "dev.ucp.shopping.fulfillment": "shopping/fulfillment.json",
        "dev.ucp.shopping.buyer_consent": "shopping/buyer_consent.json",
        "dev.ucp.common.loyalty": "common/loyalty.json",
        "dev.ucp.common.payment.ap2_mandate": "common/payment_ap2_mandate.json",
        "dev.ucp.common.payment.authentication": "common/payment_authentication.json",
        "dev.ucp.common.payment.split_payments": "common/payment_split_payments.json",
        "dev.ucp.common.payment.terms": "common/payment_terms.json",
    },
}


def assert_valid_checkout(version: str, instance: Any, *, request_op: str | None = None) -> None:
    """The base checkout schema and every negotiated extension's composed checkout schema."""
    assert_valid(version, "shopping/checkout.json", instance, request_op=request_op)
    active = (instance.get("ucp") or {}).get("capabilities") or {} if request_op is None else EXTENSIONS[version]
    for name, path in EXTENSIONS[version].items():
        if name in active:
            assert_valid(version, path, instance, request_op=request_op, pointer="/$defs/dev.ucp.shopping.checkout")


def assert_valid_cart(version: str, instance: Any, *, request_op: str | None = None) -> None:
    assert_valid(version, "shopping/cart.json", instance, request_op=request_op)
    active = (instance.get("ucp") or {}).get("capabilities") or {} if request_op is None else {}
    if "dev.ucp.common.loyalty" in active:
        assert_valid(version, "common/loyalty.json", instance, pointer="/$defs/dev.ucp.shopping.cart")
    if version >= "2026-08-25" and "dev.ucp.shopping.buyer_consent" in active:
        assert_valid(version, "shopping/buyer_consent.json", instance, pointer="/$defs/dev.ucp.shopping.cart")
