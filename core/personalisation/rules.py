# SPDX-License-Identifier: Apache-2.0
"""The checks and the evaluation of personalisation: subjects, profiles, rules, conditions and templates.

Everything here is pure: no store, no clock beyond what a caller passes.
A subject is the tenant's own customer reference (letters, digits and
``. _ : -``, never an e-mail). A profile is a flat object of attribute
names to scalar values. A rule names a purpose, a priority (lower is
tried first), conditions on attributes, a variant (a plain-text template
with ``{{attribute}}`` placeholders and a label) and the attributes it
may use; its conditions and its template may name no other. A template
renders only when every placeholder is allowed and present in the
profile: a placeholder that is unknown, not allowed or empty is refused,
never rendered blank.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

PURPOSES = ("marketing", "service", "collections", "retention", "onboarding")
OPS = ("eq", "ne", "in", "gte", "lte", "exists")
CHANNELS = ("email", "sms", "push", "in_app", "web", "letter", "whatsapp", "voice", "branch")
MAX_TEMPLATE = 4000  # characters of a template
MAX_OUTPUT = 8000  # characters of rendered content
MAX_PROFILE_ATTRIBUTES = 100
MAX_PROFILE_JSON = 16000  # characters of a profile as JSON
MAX_VALUE = 500  # characters of one string value
MAX_CONDITIONS = 20
MAX_IN_VALUES = 50
MAX_ALLOWED = 50
MAX_RULES = 200  # rules one tenant keeps
MAX_PRIORITY = 10000
MAX_EVIDENCE = 500
MAX_RULE_NAME = 100
MAX_LABEL = 100

NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
SUBJECT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}")


class PersonalisationError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def check_subject(raw: Any) -> str:
    """The tenant's customer reference, whole: never cut, never an e-mail."""
    subject = str(raw or "").strip()
    if not SUBJECT_RE.fullmatch(subject):
        raise PersonalisationError(
            422, "subject_invalid", "subject_ref is the tenant's customer reference: 1 to 128 of A-Z a-z 0-9 . _ : -"
        )
    return subject


def check_purpose(raw: Any) -> str:
    purpose = _text(raw, 64).lower()
    if purpose not in PURPOSES:
        raise PersonalisationError(422, "purpose_unknown", f"purpose is one of {', '.join(PURPOSES)}")
    return purpose


def check_channel(raw: Any) -> str:
    channel = _text(raw, 32).lower()
    if channel not in CHANNELS:
        raise PersonalisationError(422, "channel_unknown", f"channel is one of {', '.join(CHANNELS)}")
    return channel


def check_name(raw: Any, what: str = "attribute") -> str:
    name = str(raw or "").strip()
    if not NAME_RE.fullmatch(name):
        raise PersonalisationError(
            422, "attribute_invalid", f"{what} {name[:70]!r} is a name: a-z first, then a-z 0-9 _, at most 64"
        )
    return name


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, bool, int, float)) and not (isinstance(value, float) and value != value)


def _check_scalar(value: Any, where: str) -> Any:
    if not _is_scalar(value):
        raise PersonalisationError(422, "value_invalid", f"{where} is a string, a number or true/false")
    if isinstance(value, str) and len(value) > MAX_VALUE:
        raise PersonalisationError(422, "value_invalid", f"{where} is at most {MAX_VALUE} characters")
    return value


def check_attributes(raw: Any) -> dict[str, Any]:
    """A profile: flat names to scalars, bounded in count and size."""
    if not isinstance(raw, dict):
        raise PersonalisationError(422, "profile_invalid", "attributes is an object of names to values")
    if len(raw) > MAX_PROFILE_ATTRIBUTES:
        raise PersonalisationError(
            422, "profile_too_large", f"a profile holds at most {MAX_PROFILE_ATTRIBUTES} attributes"
        )
    out: dict[str, Any] = {}
    for key, value in raw.items():
        name = check_name(key)
        out[name] = _check_scalar(value, name)
    if len(json.dumps(out, sort_keys=True)) > MAX_PROFILE_JSON:
        raise PersonalisationError(
            422, "profile_too_large", f"a profile is at most {MAX_PROFILE_JSON} characters of JSON"
        )
    return out


# ---------------------------------------------------------------- templates


def _parse_template(template: str) -> tuple[list[tuple[int, int, str]], list[str]]:
    """Scan once for placeholder spans and names; never backtrack over an unclosed token."""
    if not isinstance(template, str):
        raise PersonalisationError(422, "template_invalid", "template is non-empty text")
    if len(template) > MAX_TEMPLATE:
        raise PersonalisationError(422, "template_too_long", f"template is at most {MAX_TEMPLATE} characters")
    spans: list[tuple[int, int, str]] = []
    names: list[str] = []
    seen: set[str] = set()
    literals: list[str] = []
    cursor = 0
    opening: int | None = None
    for index, char in enumerate(template):
        if char == "{":
            # Only the last two braces in a run can open a brace-free placeholder.
            opening = index - 1 if index and template[index - 1] == "{" else None
        elif char == "}":
            if opening is not None and template.startswith("}}", index):
                inner = template[opening + 2 : index].strip()
                if not NAME_RE.fullmatch(inner):
                    raise PersonalisationError(
                        422, "placeholder_invalid", f"{{{{{inner[:70]}}}}} is not an attribute name"
                    )
                end = index + 2
                spans.append((opening, end, inner))
                literals.append(template[cursor:opening])
                cursor = end
                if inner not in seen:
                    seen.add(inner)
                    names.append(inner)
            opening = None
    literals.append(template[cursor:])
    # Preserve refusal of double braces left after removing all valid placeholders.
    rest = "".join(literals)
    if "{{" in rest or "}}" in rest:
        raise PersonalisationError(422, "template_invalid", "a placeholder is opened or closed without its pair")
    return spans, names


def placeholders(template: str) -> list[str]:
    """The attribute names a template names, in order of first use; a malformed placeholder is refused."""
    _, names = _parse_template(template)
    return names


def check_template(raw: Any) -> str:
    if not isinstance(raw, str):
        raise PersonalisationError(422, "template_invalid", "template is non-empty text")
    if len(raw) > MAX_TEMPLATE:
        raise PersonalisationError(422, "template_too_long", f"template is at most {MAX_TEMPLATE} characters")
    if not raw.strip():
        raise PersonalisationError(422, "template_invalid", "template is non-empty text")
    placeholders(raw)
    return raw


def _show(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def render_template(template: str, attributes: dict[str, Any], allowed: Any) -> tuple[str, list[str]]:
    """The content and the attribute names it used; any placeholder not allowed or not present refuses it."""
    spans, names = _parse_template(template)
    allowed_set = set(allowed or ())
    refused = [name for name in names if name not in allowed_set]
    if refused:
        raise PersonalisationError(422, "placeholder_not_allowed", f"not allowed here: {', '.join(refused[:10])}")
    missing = [name for name in names if attributes.get(name) is None or attributes.get(name) == ""]
    if missing:
        raise PersonalisationError(
            422, "placeholder_unresolved", f"the profile does not hold: {', '.join(missing[:10])}"
        )
    parts: list[str] = []
    cursor = 0
    size = 0
    for start, end, name in spans:
        value = _show(attributes[name])
        size += start - cursor + len(value)
        if size > MAX_OUTPUT:
            raise PersonalisationError(422, "output_too_long", f"the content is at most {MAX_OUTPUT} characters")
        parts.extend((template[cursor:start], value))
        cursor = end
    size += len(template) - cursor
    if size > MAX_OUTPUT:
        raise PersonalisationError(422, "output_too_long", f"the content is at most {MAX_OUTPUT} characters")
    parts.append(template[cursor:])
    return "".join(parts), names


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- conditions and rules


def check_condition(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PersonalisationError(422, "condition_invalid", "each condition is an object")
    attribute = check_name(raw.get("attribute"))
    op = _text(raw.get("op"), 8).lower()
    if op not in OPS:
        raise PersonalisationError(422, "condition_invalid", f"op is one of {', '.join(OPS)}")
    value = raw.get("value")
    where = f"the value of the condition on {attribute}"
    if op == "exists":
        if value is None:
            value = True
        if not isinstance(value, bool):
            raise PersonalisationError(422, "condition_invalid", f"{where} is true or false")
    elif op == "in":
        if not isinstance(value, list) or not 1 <= len(value) <= MAX_IN_VALUES:
            raise PersonalisationError(422, "condition_invalid", f"{where} is a list of 1 to {MAX_IN_VALUES} values")
        value = [_check_scalar(item, where) for item in value]
    elif op in ("gte", "lte"):
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise PersonalisationError(422, "condition_invalid", f"{where} is a number or text")
        value = _check_scalar(value, where)
    else:
        value = _check_scalar(value, where)
    return {"attribute": attribute, "op": op, "value": value}


def _same_kind(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return True
    return isinstance(left, str) and isinstance(right, str)


def condition_matches(condition: dict[str, Any], attributes: dict[str, Any]) -> bool:
    """One condition against a profile; a condition on an absent attribute holds only for ``exists`` false."""
    op = condition["op"]
    value = condition.get("value")
    present = attributes.get(condition["attribute"]) not in (None, "")
    if op == "exists":
        return present is bool(value)
    if not present:
        return False
    actual = attributes[condition["attribute"]]
    if op == "in":
        return any(_same_kind(actual, item) and actual == item for item in value or [])
    if not _same_kind(actual, value):
        return False
    if op == "eq":
        return bool(actual == value)
    if op == "ne":
        return bool(actual != value)
    if op == "gte":
        return bool(actual >= value)
    return bool(actual <= value)


def rule_matches(rule: dict[str, Any], attributes: dict[str, Any]) -> bool:
    return all(condition_matches(condition, attributes) for condition in rule.get("conditions") or [])


def condition_attributes(rule: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for condition in rule.get("conditions") or []:
        if condition["attribute"] not in out:
            out.append(condition["attribute"])
    return out


def check_rule(raw: Any) -> dict[str, Any]:
    """A whole rule as the store keeps it, or why it cannot be."""
    if not isinstance(raw, dict):
        raise PersonalisationError(422, "rule_invalid", "a rule is an object")
    name = _text(raw.get("name"), MAX_RULE_NAME + 1)
    if not name or len(name) > MAX_RULE_NAME:
        raise PersonalisationError(422, "rule_invalid", f"name is 1 to {MAX_RULE_NAME} characters")
    purpose = check_purpose(raw.get("purpose"))
    priority = raw.get("priority", 100)
    if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= MAX_PRIORITY:
        raise PersonalisationError(422, "rule_invalid", f"priority is a whole number from 0 to {MAX_PRIORITY}")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise PersonalisationError(422, "rule_invalid", "enabled is true or false")
    conditions = raw.get("conditions") or []
    if not isinstance(conditions, list) or len(conditions) > MAX_CONDITIONS:
        raise PersonalisationError(422, "rule_invalid", f"conditions is a list of at most {MAX_CONDITIONS}")
    checked_conditions = [check_condition(item) for item in conditions]
    variant = raw.get("variant")
    if not isinstance(variant, dict):
        raise PersonalisationError(422, "rule_invalid", "variant is an object with a template and a label")
    template = check_template(variant.get("template"))
    label = _text(variant.get("label"), MAX_LABEL) or name[:MAX_LABEL]
    allowed = raw.get("allowed_attributes") or []
    if not isinstance(allowed, list) or len(allowed) > MAX_ALLOWED:
        raise PersonalisationError(422, "rule_invalid", f"allowed_attributes is a list of at most {MAX_ALLOWED}")
    allowed_names: list[str] = []
    for item in allowed:
        attribute = check_name(item)
        if attribute not in allowed_names:
            allowed_names.append(attribute)
    out = {
        "name": name,
        "purpose": purpose,
        "priority": priority,
        "enabled": enabled,
        "conditions": checked_conditions,
        "variant": {"template": template, "label": label},
        "allowed_attributes": allowed_names,
    }
    # A rule reads only what it declares: its conditions and its template both.
    undeclared = [name for name in condition_attributes(out) + placeholders(template) if name not in set(allowed_names)]
    if undeclared:
        raise PersonalisationError(
            422,
            "attribute_not_allowed",
            f"the rule uses attributes it does not declare in allowed_attributes: {', '.join(sorted(set(undeclared)))}",
        )
    return out


def parse_time(value: Any, what: str) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise PersonalisationError(422, "time_invalid", f"{what} is an ISO 8601 time") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
