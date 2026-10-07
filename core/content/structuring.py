# SPDX-License-Identifier: Apache-2.0
"""Narrative to payload: a schema-shaped JSON object from free text, and the same object as XML.

The target schema is given inline, named from the tenant's schema registry,
or named from the built-in domain schemas. The model places what the text
says into the schema; the payload is validated against the schema and the
result says what failed. The XML rendering is deterministic (element names
from keys, list items under the singular of the key) and parsed back to prove
it is well formed. With ``strict`` on, an invalid payload is refused.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Literal
from xml.etree import ElementTree as ET  # nosec B405 - used to build and re-parse our own output only

from pydantic import BaseModel, Field, model_validator

from core.content import services
from core.content.services import GuardrailProfile, Service
from core.content.sources import Source

SCHEMA_ORIGIN = "schema"
MAX_SCHEMA_BYTES = 60_000
_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]")


class StructureIn(BaseModel):
    model_config = {"extra": "forbid"}

    text: str = Field(..., min_length=1, max_length=40_000)
    schema_name: str | None = Field(None, max_length=100)
    schema_: dict[str, Any] | None = Field(None, alias="schema")
    format: Literal["json", "xml", "both"] = "json"
    root_element: str = Field("document", min_length=1, max_length=64)
    strict: bool = False
    language: str = Field("en", max_length=16)

    @model_validator(mode="after")
    def _one_schema(self) -> StructureIn:
        if (self.schema_name is None) == (self.schema_ is None):
            raise ValueError("give exactly one of schema_name or schema")
        if self.schema_ is not None and len(json.dumps(self.schema_)) > MAX_SCHEMA_BYTES:
            raise ValueError("schema is too large")
        return self


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["payload"],
    "properties": {
        "payload": {"type": "object"},
        "unplaced": {"type": "array", "maxItems": 50, "items": {"type": "string", "maxLength": 300}},
        "assumptions": {"type": "array", "maxItems": 30, "items": {"type": "string", "maxLength": 300}},
    },
}


async def resolve_schema(tenant_id: uuid.UUID, name: str) -> dict[str, Any]:
    """The tenant's registered schema by name (the tenant's own row first, then a global one), else a built-in."""
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.schema_registry import SchemaRegistry

    async with get_tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    select(SchemaRegistry).where(
                        SchemaRegistry.name == name,
                        (SchemaRegistry.tenant_id == tenant_id) | (SchemaRegistry.tenant_id.is_(None)),
                    )
                )
            )
            .scalars()
            .all()
        )
    chosen = next((r for r in rows if r.tenant_id == tenant_id), None) or next(iter(rows), None)
    if chosen is not None and isinstance(chosen.json_schema, dict):
        return dict(chosen.json_schema)
    from core import domain_schemas

    if name in getattr(domain_schemas, "DOMAIN_SCHEMAS", ()):
        try:
            return domain_schemas.load_schema(name)
        except domain_schemas.DomainSchemaError as exc:
            raise services.ContentError(422, "schema_invalid", str(exc)) from None
    raise services.ContentError(404, "schema_unknown", f"No schema named {name!r}")


def schema_of(sources: list[Source]) -> dict[str, Any]:
    for source in sources:
        if source.origin == SCHEMA_ORIGIN:
            return json.loads(source.text)
    raise services.ContentError(422, "schema_missing", "No target schema")


async def resolve_sources(tenant_id: uuid.UUID, payload: StructureIn, domains: list[str] | None) -> list[Source]:
    schema = (
        payload.schema_ if payload.schema_ is not None else await resolve_schema(tenant_id, str(payload.schema_name))
    )
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise services.ContentError(422, "schema_invalid", exc.message[:300]) from None
    name = payload.schema_name or str(schema.get("title") or "schema")
    return [Source(id="schema", title=name, text=json.dumps(schema, ensure_ascii=False), origin=SCHEMA_ORIGIN)]


def messages(payload: StructureIn, sources: list[Source]) -> list[dict[str, str]]:
    schema = schema_of(sources)
    system = (
        "You convert a narrative into a structured payload for a bank. Fill only what the text states; leave out "
        "fields the text does not give rather than guessing, and list what the text says that has no place in the "
        "schema. Answer with one JSON object and nothing else: {payload: <an object matching the schema>, "
        "unplaced: [facts with no field], assumptions: [any normalisation you applied, such as date formats]}."
    )
    schema_text = json.dumps(schema, ensure_ascii=False)[:MAX_SCHEMA_BYTES]
    user = f"Target JSON schema:\n{schema_text}\n\nNarrative:\n{payload.text}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _tag(name: Any) -> str:
    tag = _NAME_RE.sub("_", str(name)).strip("_-.") or "item"
    return tag if not tag[0].isdigit() else f"_{tag}"


def _singular(name: str) -> str:
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith("ses") or name.endswith("xes"):
        return name[:-2]
    return name[:-1] if name.endswith("s") and len(name) > 1 else name


def _build(parent: ET.Element, value: Any, name: str) -> None:
    if isinstance(value, dict):
        element = ET.SubElement(parent, _tag(name))
        for key, item in value.items():
            if item is None:
                continue
            _build(element, item, str(key))
    elif isinstance(value, list):
        element = ET.SubElement(parent, _tag(name))
        child = _singular(_tag(name))
        for item in value:
            if item is None:
                continue
            _build(element, item, child)
    else:
        element = ET.SubElement(parent, _tag(name))
        element.text = "true" if value is True else "false" if value is False else str(value)


def to_xml(payload: dict[str, Any], root: str = "document") -> str:
    """The payload as XML: keys become elements, list items the singular of their key, nulls are left out."""
    root_element = ET.Element(_tag(root))
    for key, item in payload.items():
        if item is None:
            continue
        _build(root_element, item, str(key))
    text = ET.tostring(root_element, encoding="unicode")
    ET.fromstring(text)  # noqa: S314  # nosec B314 - re-parsing our own serialisation to prove it is well formed
    return '<?xml version="1.0" encoding="UTF-8"?>' + text


def finish(payload: StructureIn, sources: list[Source], answer: dict[str, Any]) -> dict[str, Any]:
    """Validate the payload against the schema; render XML when asked; refuse an invalid payload under strict."""
    schema = schema_of(sources)
    data = answer.get("payload") if isinstance(answer.get("payload"), dict) else {}
    errors = services.schema_errors(data, schema)
    if payload.strict and errors:
        raise services.ContentError(422, "payload_invalid", "The payload does not match the schema", errors)
    out: dict[str, Any] = {
        "schema": sources[0].title if sources else None,
        "payload": data,
        "validation": {"valid": not errors, "errors": errors},
        "unplaced": [str(u) for u in (answer.get("unplaced") or [])],
        "assumptions": [str(a) for a in (answer.get("assumptions") or [])],
        "format": payload.format,
    }
    if payload.format in ("xml", "both"):
        out["xml"] = to_xml(data, payload.root_element)
    return out


def rendered(output: dict[str, Any]) -> str:
    return json.dumps(output.get("payload") or {}, ensure_ascii=False)


def apply_text(output: dict[str, Any], text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except ValueError:
        return output
    if not isinstance(data, dict):
        return output
    updated = {**output, "payload": data}
    if "xml" in output:
        updated["xml"] = to_xml(data, str(output.get("root_element") or "document"))
    return updated


DATASET_CASES: list[dict[str, Any]] = [
    {
        "id": "invoice-narrative",
        "input": "Structure as an invoice: Vendor Example Supplies, invoice EX-1042 dated 3 March 2026, due in 30 "
        "days, two items of 1,200 each, GST 18 percent, total 2,832.",
        "contains": ["EX-1042", "2832"],
    },
    {
        "id": "address-change",
        "input": "Structure as a service request: customer wants the address on the account ending 4421 changed "
        "to the new address given in the attached proof.",
        "contains": ["4421"],
    },
    {
        "id": "nothing-to-place",
        "input": "Structure as an invoice: Thanks for the quick help yesterday.",
        "not_contains": ["invoice_id"],
    },
]

SERVICE = services.register(
    Service(
        name="structure",
        title="Narrative to payload",
        description="A schema-shaped JSON object from free text, validated against the schema, rendered as XML "
        "on request; what the text says that has no field is listed, never dropped silently.",
        input_model=StructureIn,
        output_schema=OUTPUT_SCHEMA,
        guardrails=GuardrailProfile(input=True, output=True, grounded=False),
        dataset_name="content: narrative to payload",
        dataset_cases=DATASET_CASES,
        messages=messages,
        finish=finish,
        rendered=rendered,
        apply_text=apply_text,
        resolve_sources=resolve_sources,
    )
)
