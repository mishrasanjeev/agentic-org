# SPDX-License-Identifier: Apache-2.0
"""Typed prompt parameters: what a template's placeholders are, and what may be put in them.

A prompt template carries ``{{name}}`` placeholders. Until now a template
listed its variables by name only and substitution replaced whatever it was
handed, so a missing value left ``{{name}}`` in the prompt a model then read,
and nothing said a value had to be a number or one of a few choices.

A parameter is a declared placeholder: a name, a type (``string``,
``integer``, ``number``, ``boolean`` or ``enum``), whether it is required, a
default, and bounds (length and a pattern for a string, a range for a number,
the choices for an enum). ``check_template`` holds a template's text against
its declared parameters; ``resolve`` holds supplied values against the
declarations, fills defaults and refuses anything missing, unknown or of the
wrong type; ``render`` substitutes the resolved values and leaves no
placeholder behind.

A variable declared the old way (a name, with or without a description) is a
required string parameter, so existing templates read as they did.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.governance.guardrails.schema import safe_pattern

TYPES: tuple[str, ...] = ("string", "integer", "number", "boolean", "enum")
MAX_PARAMETERS = 50
MAX_STRING_VALUE = 20_000
MAX_CHOICES = 100
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
# {{ name }} with optional spaces; a tool reference ({{tool:name}}, {{tools.name}}) is not a parameter.
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_KEYS: tuple[str, ...] = (
    "name",
    "type",
    "description",
    "required",
    "default",
    "choices",
    "min",
    "max",
    "max_length",
    "pattern",
)
_TRUE = {"true", "yes", "1"}
_FALSE = {"false", "no", "0"}


class ParameterError(ValueError):
    """One or more problems with a template's parameters or with the values supplied for them."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = list(problems)


@dataclass(frozen=True)
class Parameter:
    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    default: Any = None
    choices: tuple[str, ...] = ()
    min: float | None = None
    max: float | None = None
    max_length: int | None = None
    pattern: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "type": self.type, "required": self.required}
        if self.description:
            out["description"] = self.description
        if self.default is not None:
            out["default"] = self.default
        if self.choices:
            out["choices"] = list(self.choices)
        for key in ("min", "max", "max_length", "pattern"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out


@dataclass
class TemplateCheck:
    """A template's text held against its declared parameters."""

    placeholders: list[str] = field(default_factory=list)
    undeclared: list[str] = field(default_factory=list)
    unused: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.undeclared

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "placeholders": list(self.placeholders),
            "undeclared": list(self.undeclared),
            "unused": list(self.unused),
        }


def placeholders(template_text: str) -> list[str]:
    """The parameter names a template's text uses, in order of first use."""
    seen: dict[str, None] = {}
    for match in _PLACEHOLDER.finditer(template_text or ""):
        seen.setdefault(match.group(1), None)
    return list(seen)


def _coerce(parameter: Parameter, value: Any, label: str) -> Any:
    """``value`` as the parameter's type, or ValueError saying what is wrong."""
    kind = parameter.type
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in _TRUE | _FALSE:
            return value.strip().lower() in _TRUE
        raise ValueError(f"{label} must be true or false")
    if kind in ("integer", "number"):
        if isinstance(value, bool):
            raise ValueError(f"{label} must be a number")
        try:
            number: float = float(value) if not isinstance(value, int | float) else value
        except (TypeError, ValueError):
            raise ValueError(f"{label} must be a number") from None
        if number != number or number in (float("inf"), float("-inf")):
            raise ValueError(f"{label} must be a finite number")
        if kind == "integer":
            if float(number) != int(number):
                raise ValueError(f"{label} must be a whole number")
            number = int(number)
        if parameter.min is not None and number < parameter.min:
            raise ValueError(f"{label} must be at least {parameter.min:g}")
        if parameter.max is not None and number > parameter.max:
            raise ValueError(f"{label} must be at most {parameter.max:g}")
        return number
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    if kind == "enum":
        if value not in parameter.choices:
            raise ValueError(f"{label} must be one of {', '.join(parameter.choices)}")
        return value
    limit = parameter.max_length or MAX_STRING_VALUE
    if len(value) > limit:
        raise ValueError(f"{label} must be at most {limit} characters")
    if parameter.pattern and not re.fullmatch(parameter.pattern, value):
        raise ValueError(f"{label} does not match the required pattern")
    return value


def _parameter(raw: Any, index: int) -> Parameter:
    if not isinstance(raw, dict):
        raise ValueError(f"parameter {index + 1} must be an object")
    unknown = sorted(set(raw) - set(_KEYS))
    if unknown:
        raise ValueError(f"parameter {index + 1} has unknown keys: {', '.join(unknown)}")
    name = str(raw.get("name") or "").strip()
    if not _NAME.match(name):
        raise ValueError(
            f"parameter {index + 1} needs a name of letters, digits and underscores, not starting with a digit"
        )
    kind = str(raw.get("type") or "string").strip().lower()
    if kind not in TYPES:
        raise ValueError(f"{name}: type must be one of {', '.join(TYPES)}")
    choices: tuple[str, ...] = ()
    if kind == "enum":
        listed = raw.get("choices")
        if not isinstance(listed, list) or not listed or len(listed) > MAX_CHOICES:
            raise ValueError(f"{name}: an enum needs a list of 1 to {MAX_CHOICES} choices")
        if any(not isinstance(choice, str) or not choice.strip() for choice in listed):
            raise ValueError(f"{name}: every choice is non-empty text")
        choices = tuple(dict.fromkeys(choice.strip() for choice in listed))
    elif raw.get("choices") is not None:
        raise ValueError(f"{name}: choices apply to an enum only")
    bounds: dict[str, float | None] = {"min": None, "max": None}
    for key in bounds:
        value = raw.get(key)
        if value is None:
            continue
        if kind not in ("integer", "number"):
            raise ValueError(f"{name}: {key} applies to a number only")
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"{name}: {key} must be a number")
        bounds[key] = value
    if bounds["min"] is not None and bounds["max"] is not None and bounds["min"] > bounds["max"]:
        raise ValueError(f"{name}: min cannot exceed max")
    max_length = raw.get("max_length")
    pattern = raw.get("pattern")
    if max_length is not None:
        if kind != "string":
            raise ValueError(f"{name}: max_length applies to a string only")
        if isinstance(max_length, bool) or not isinstance(max_length, int) or not 1 <= max_length <= MAX_STRING_VALUE:
            raise ValueError(f"{name}: max_length is a whole number between 1 and {MAX_STRING_VALUE}")
    if pattern is not None:
        if kind != "string":
            raise ValueError(f"{name}: pattern applies to a string only")
        if not isinstance(pattern, str) or not pattern:
            raise ValueError(f"{name}: pattern must be a regular expression")
        try:
            pattern = safe_pattern(pattern)
        except ValueError as exc:
            raise ValueError(f"{name}: {exc}") from None
    default = raw.get("default")
    required_raw = raw.get("required")
    if required_raw is not None and not isinstance(required_raw, bool):
        raise ValueError(f"{name}: required must be true or false")
    required = (default is None) if required_raw is None else required_raw
    parameter = Parameter(
        name=name,
        type=kind,
        description=str(raw.get("description") or "").strip(),
        required=required,
        choices=choices,
        min=bounds["min"],
        max=bounds["max"],
        max_length=max_length,
        pattern=pattern,
    )
    if default is None:
        return parameter
    if required:
        raise ValueError(f"{name}: a required parameter has no default")
    checked = _coerce(parameter, default, f"{name}: the default")
    return Parameter(**{**parameter.__dict__, "default": checked})


def parse_parameters(raw: Any) -> list[Parameter]:
    """A template's declared parameters, checked; ``ParameterError`` lists every problem."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ParameterError(["variables must be a list of parameters"])
    if len(raw) > MAX_PARAMETERS:
        raise ParameterError([f"a template has at most {MAX_PARAMETERS} parameters"])
    problems: list[str] = []
    parameters: list[Parameter] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        try:
            parameter = _parameter(item, index)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if parameter.name in seen:
            problems.append(f"{parameter.name}: declared more than once")
            continue
        seen.add(parameter.name)
        parameters.append(parameter)
    if problems:
        raise ParameterError(problems)
    return parameters


def check_template(template_text: str, parameters: list[Parameter]) -> TemplateCheck:
    """Which placeholders the text uses, which are not declared, and which declarations it never uses."""
    used = placeholders(template_text)
    declared = [parameter.name for parameter in parameters]
    return TemplateCheck(
        placeholders=used,
        undeclared=[name for name in used if name not in declared],
        unused=[name for name in declared if name not in used],
    )


def resolve(parameters: list[Parameter], values: dict[str, Any] | None) -> dict[str, Any]:
    """The value of every parameter: supplied and checked, or its default; ``ParameterError`` lists every problem."""
    supplied = dict(values or {})
    problems: list[str] = []
    declared = {parameter.name for parameter in parameters}
    unknown = sorted(set(supplied) - declared)
    if unknown:
        problems.append(f"unknown parameters: {', '.join(unknown)}")
    resolved: dict[str, Any] = {}
    for parameter in parameters:
        if parameter.name in supplied and supplied[parameter.name] is not None:
            try:
                resolved[parameter.name] = _coerce(parameter, supplied[parameter.name], parameter.name)
            except ValueError as exc:
                problems.append(str(exc))
        elif parameter.default is not None:
            resolved[parameter.name] = parameter.default
        elif parameter.required:
            problems.append(f"{parameter.name} is required")
        else:
            resolved[parameter.name] = ""
    if problems:
        raise ParameterError(problems)
    return resolved


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def render(template_text: str, parameters: list[Parameter], values: dict[str, Any] | None) -> str:
    """The template with every placeholder replaced by its resolved value.

    Refuses a template that uses a placeholder it does not declare, so no
    ``{{name}}`` is ever left for a model to read. A value is inserted as
    text and never read as a placeholder itself.
    """
    check = check_template(template_text, parameters)
    if check.undeclared:
        raise ParameterError([f"the template uses undeclared parameters: {', '.join(check.undeclared)}"])
    resolved = resolve(parameters, values)
    return _PLACEHOLDER.sub(lambda match: _text(resolved[match.group(1)]), template_text)
