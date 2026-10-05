# SPDX-License-Identifier: Apache-2.0
"""Structured-output enforcement: an agent that declares an output schema never returns a payload that fails it.

An agent's output schema is either a JSON Schema stored with the agent (its
own, set through the agents API) or the name of one of the platform's
registered document schemas (``core.domain_schemas``). While enforcement is
on, the agent graph validates the agent's final answer against it:

* a valid answer completes as before;
* an invalid answer is sent back to the model with what is wrong, up to
  ``MAX_REPAIRS`` times;
* an answer that is still invalid is not returned as a completed result: the
  run is escalated to a human reviewer with the trigger
  ``output_schema_invalid``;
* a declared schema that cannot be resolved (a name that is not registered,
  a stored schema that is not a schema) escalates the same way with
  ``output_schema_unusable``: an agent that says it has a schema is not
  allowed to run as if it had none.

An agent with no declared schema is not affected. Behind
``AGENTICORG_OUTPUT_SCHEMA_ENFORCED`` (off by default): off, nothing is
validated here and runs end as they did.

Validation errors carry JSON paths and the schema's own messages. They are
sent to the model for the repair and kept on the escalation; a schema message
can quote a short value from the answer, so errors are bounded in number and
length and are not put in metrics.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from core.config import settings

logger = structlog.get_logger()

MAX_REPAIRS = 2
MAX_ERRORS = 10
MAX_ERROR_CHARS = 300
MAX_SCHEMA_BYTES = 32_000
INLINE_KEY = "output_schema_json"
TRIGGER_INVALID = "output_schema_invalid"
TRIGGER_UNUSABLE = "output_schema_unusable"
# Keywords that point at another schema. A stored schema is self-contained: a
# reference could reach a document the platform does not control, and one that
# cannot be resolved fails at validation time.
REFERENCE_KEYWORDS = frozenset({"$ref", "$dynamicRef", "$recursiveRef"})


class OutputSchemaError(ValueError):
    """The declared schema cannot be used."""


def enabled() -> bool:
    return bool(settings.output_schema_enforced)


def declared(name: str | None, inline: Any) -> bool:
    """Whether the agent declares an output schema at all."""
    return bool(inline) or bool((name or "").strip())


def check_inline_schema(schema: Any) -> dict[str, Any]:
    """A JSON Schema an agent may be given: an object schema, valid, and of bounded size."""
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError

    if not isinstance(schema, dict) or not schema:
        raise OutputSchemaError("the schema must be a non-empty JSON object")
    try:
        size = len(json.dumps(schema))
    except (TypeError, ValueError):
        raise OutputSchemaError("the schema must be JSON") from None
    if size > MAX_SCHEMA_BYTES:
        raise OutputSchemaError(f"the schema is larger than {MAX_SCHEMA_BYTES} bytes")
    if schema.get("type") != "object":
        raise OutputSchemaError('the schema must describe an object ("type": "object"): an agent returns a JSON object')
    used = sorted(_reference_keywords(schema))
    if used:
        raise OutputSchemaError(f"the schema must not use {', '.join(used)}")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise OutputSchemaError(f"not a valid JSON Schema: {exc.message}"[:MAX_ERROR_CHARS]) from None
    return schema


def _reference_keywords(node: Any) -> set[str]:
    """The reference keywords used as keys anywhere in the schema."""
    found: set[str] = set()
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            found.update(key for key in current if key in REFERENCE_KEYWORDS)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return found


def _location(path: Any) -> str:
    return "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in path)


def errors_for(name: str | None, inline: Any, document: Any) -> list[str]:
    """What is wrong with ``document`` under the declared schema (empty when it conforms).

    Raises ``OutputSchemaError`` when the schema itself cannot be used.
    """
    if inline:
        from jsonschema import Draft202012Validator, FormatChecker

        schema = check_inline_schema(inline)
        try:
            found = sorted(
                Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
                key=lambda err: (list(map(str, err.absolute_path)), err.message),
            )
        # enterprise-gate: broad-except-ok reason=a-schema-the-validator-cannot-run-fails-closed-as-unusable
        except Exception as exc:
            raise OutputSchemaError(f"the schema could not be applied: {type(exc).__name__}") from None
        messages = [f"{_location(err.absolute_path)}: {err.message}" for err in found]
    else:
        from core import domain_schemas

        try:
            messages = domain_schemas.iter_errors((name or "").strip(), document)
        except domain_schemas.DomainSchemaError as exc:
            raise OutputSchemaError(f"schema {name!r} cannot be used: {exc.reason}") from None
    return [message[:MAX_ERROR_CHARS] for message in messages[:MAX_ERRORS]]


def correction(errors: list[str]) -> str:
    """What the model is told when its answer does not match the schema."""
    listed = "\n".join(f"- {error}" for error in errors)
    return (
        "Your answer does not match the required output schema:\n"
        f"{listed}\n"
        "Return the complete answer again as a single JSON object that satisfies the schema. "
        "Do not add commentary."
    )


def check(name: str | None, inline: Any, output: Any, *, repairs: int) -> dict[str, Any]:
    """What to do with an agent's final answer.

    ``{"action": "accept"}``, ``{"action": "repair", "message": ...}`` or
    ``{"action": "escalate", "trigger": ..., "errors": [...]}``. With
    enforcement off, or no declared schema, the answer is accepted.
    """
    if not enabled() or not declared(name, inline):
        return {"action": "accept"}
    try:
        errors = errors_for(name, inline, output)
    except OutputSchemaError as exc:
        logger.error("output_schema_unusable", schema=name or "inline", reason=str(exc)[:MAX_ERROR_CHARS])
        _meter("unusable")
        return {"action": "escalate", "trigger": TRIGGER_UNUSABLE, "errors": [str(exc)[:MAX_ERROR_CHARS]]}
    if not errors:
        _meter("valid" if repairs == 0 else "repaired")
        return {"action": "accept"}
    if repairs < MAX_REPAIRS:
        _meter("retry")
        return {"action": "repair", "message": correction(errors), "errors": errors}
    logger.warning("output_schema_invalid_after_repairs", schema=name or "inline", errors=len(errors), repairs=repairs)
    _meter("escalated")
    return {"action": "escalate", "trigger": TRIGGER_INVALID, "errors": errors}


def _meter(result: str) -> None:
    try:
        from observability.metrics import output_schema_checks_total

        output_schema_checks_total.labels(result=result).inc()
    # enterprise-gate: broad-except-ok reason=metering-is-best-effort-and-never-fails-a-run-safe-to-skip
    except Exception:  # noqa: S110
        pass
