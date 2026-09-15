# SPDX-License-Identifier: Apache-2.0
"""Strict loader for versioned YAML policy files.

Every problem with a policy file is found here and raised as a
:class:`~core.policy.types.PolicyLoadError` carrying a stable reason code, the
file and the location inside it. Nothing about a policy's shape is left for the
engine to discover while evaluating a case. See ``docs/policies/authoring.md``.

YAML is parsed with a restricted safe loader: aliases and merge keys are
refused, duplicate keys are refused rather than silently overwritten, only
``true``/``false`` are booleans (``yes``, ``no``, ``on`` and ``off`` stay
strings) and dates stay strings.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NoReturn

import structlog
import yaml
from prometheus_client import Counter

from core.policy.types import (
    DEFAULT_TIER_SCORE,
    MAX_SAFE_INTEGER,
    MAX_SCORE,
    AllOf,
    AnyOf,
    Compare,
    Condition,
    Effect,
    Not,
    Operator,
    Policy,
    PolicyLoadError,
    PolicyLoadReason,
    PolicyStatus,
    Rule,
    Scalar,
    Tier,
)

logger = structlog.get_logger()

MAX_POLICY_BYTES = 256 * 1024
MAX_RULES = 500
MAX_CONDITION_DEPTH = 12
MAX_COMBINATOR_ITEMS = 50
MAX_LIST_OPERAND_ITEMS = 200
MAX_PATH_DEPTH = 8
MAX_PATH_CHARS = 200
MAX_STRING_OPERAND_CHARS = 200
MAX_REASON_CHARS = 500
MAX_TEXT_CHARS = 2000
POLICY_SUFFIXES = (".yaml", ".yml")

_POLICY_KEYS = frozenset({"policy", "version", "status", "reviewed_by", "description", "score_thresholds", "rules"})
_RULE_KEYS = frozenset({"id", "description", "when", "effect"})
_EFFECT_KEYS = frozenset({"tier", "reason", "score"})

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SEGMENT = r"[a-z][a-z0-9_]*"
_PATH_RE = re.compile(rf"^{_SEGMENT}(?:\.{_SEGMENT})*$")
# Semantic Versioning 2.0.0.
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_NUMERIC_OPERATORS = frozenset({Operator.GT, Operator.GTE, Operator.LT, Operator.LTE})
_LIST_OPERATORS = frozenset({Operator.IN, Operator.NOT_IN})
_PRESENCE_OPERATORS = frozenset({Operator.EXISTS, Operator.MISSING})

_policy_load_total = Counter(
    "agenticorg_policy_load_total",
    "Policy files loaded, by outcome and reason",
    ["outcome", "reason"],
)


# ── YAML ────────────────────────────────────────────────────────────────────


class _YamlRejectedError(Exception):
    def __init__(self, reason: PolicyLoadReason, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class _StrictLoader(yaml.SafeLoader):
    """Safe loader without aliases, merge keys, duplicate keys, YAML 1.1 booleans or timestamps."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            event = self.peek_event()
            raise _YamlRejectedError(
                PolicyLoadReason.YAML_ALIAS,
                f"aliases are not allowed (line {event.start_mark.line + 1})",
            )
        event = self.peek_event()
        if getattr(event, "tag", None) is not None:
            # Explicit tags (``!!int``, ``!!timestamp``, ``!custom``, ``!``) select
            # constructors that can fail in ways a policy file has no need for.
            raise _YamlRejectedError(
                PolicyLoadReason.YAML_INVALID,
                f"explicit YAML tags are not allowed (line {event.start_mark.line + 1})",
            )
        return super().compose_node(parent, index)

    def construct_mapping(self, node: Any, deep: bool = False) -> Any:
        seen: set[Any] = set()
        for key_node, _value_node in node.value:
            line = key_node.start_mark.line + 1
            if key_node.tag == "tag:yaml.org,2002:merge":
                raise _YamlRejectedError(PolicyLoadReason.YAML_ALIAS, f"merge keys are not allowed (line {line})")
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise _YamlRejectedError(PolicyLoadReason.INVALID_VALUE, f"mapping keys must be strings (line {line})")
            if key in seen:
                raise _YamlRejectedError(PolicyLoadReason.DUPLICATE_KEY, f"duplicate key {key!r} (line {line})")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


_StrictLoader.yaml_implicit_resolvers = {
    first: [
        (tag, pattern)
        for tag, pattern in resolvers
        if tag not in ("tag:yaml.org,2002:bool", "tag:yaml.org,2002:timestamp")
    ]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_StrictLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


# ── Validation helpers ──────────────────────────────────────────────────────


class _Context:
    def __init__(self, source: str) -> None:
        self.source = source

    def fail(self, reason: PolicyLoadReason, detail: str, location: str = "") -> NoReturn:
        raise PolicyLoadError(reason, detail, source=self.source, location=location)


def _text(ctx: _Context, value: Any, *, location: str, max_chars: int) -> str:
    if not isinstance(value, str) or not value.strip():
        ctx.fail(PolicyLoadReason.INVALID_VALUE, "must be a non-empty string", location)
    if len(value) > max_chars:
        ctx.fail(PolicyLoadReason.LIMIT_EXCEEDED, f"longer than {max_chars} characters", location)
    if _CONTROL_RE.search(value):
        ctx.fail(PolicyLoadReason.INVALID_VALUE, "contains control characters", location)
    return value


def _identifier(ctx: _Context, value: Any, *, location: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        ctx.fail(
            PolicyLoadReason.INVALID_VALUE,
            "must be lower-case snake_case: a letter, then up to 63 letters, digits or underscores",
            location,
        )
    return value


def _check_keys(ctx: _Context, mapping: Mapping[str, Any], allowed: frozenset[str], *, location: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        ctx.fail(
            PolicyLoadReason.UNKNOWN_KEY,
            f"unknown key(s) {unknown}; allowed: {sorted(allowed)}",
            location,
        )


def _require(ctx: _Context, mapping: Mapping[str, Any], key: str, *, location: str) -> Any:
    if key not in mapping or mapping[key] is None:
        ctx.fail(PolicyLoadReason.MISSING_FIELD, f"{key!r} is required", location)
    return mapping[key]


_PLACEHOLDER_REVIEWERS = frozenset(
    {
        "anonymous",
        "changeme",
        "example",
        "fixme",
        "na",
        "nobody",
        "none",
        "notreviewed",
        "null",
        "pending",
        "placeholder",
        "reviewer",
        "someone",
        "tba",
        "tbc",
        "tbd",
        "test",
        "todo",
        "unknown",
        "unreviewed",
        "xxx",
        "yourname",
    }
)


def _is_placeholder_reviewer(value: str) -> bool:
    folded = "".join(ch for ch in value.casefold() if ch.isalnum())
    letters = sum(1 for ch in folded if ch.isalpha())
    return letters < 2 or folded in _PLACEHOLDER_REVIEWERS or len(set(folded)) == 1


def _kind(value: Any) -> str | None:
    """The comparison kind of a scalar, or None when it is not a usable scalar."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "number" if -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER else None
    if isinstance(value, float):
        return "number" if math.isfinite(value) else None
    if isinstance(value, str):
        return "string"
    return None


def _scalar_operand(ctx: _Context, value: Any, *, location: str) -> Scalar:
    kind = _kind(value)
    if kind is None:
        ctx.fail(
            PolicyLoadReason.INVALID_OPERAND,
            f"operand must be a string, a finite number within +/-2**53 or a boolean, got {type(value).__name__}",
            location,
        )
    if kind == "string" and len(value) > MAX_STRING_OPERAND_CHARS:
        ctx.fail(PolicyLoadReason.LIMIT_EXCEEDED, f"string operand longer than {MAX_STRING_OPERAND_CHARS}", location)
    return value


def _compile_compare(ctx: _Context, path: str, spec: Any, *, location: str) -> Compare:
    if len(path) > MAX_PATH_CHARS or not _PATH_RE.match(path):
        ctx.fail(
            PolicyLoadReason.INVALID_PATH,
            f"{path!r} is not a dotted path of lower-case snake_case segments",
            location,
        )
    segments = tuple(path.split("."))
    if len(segments) > MAX_PATH_DEPTH:
        ctx.fail(PolicyLoadReason.INVALID_PATH, f"{path!r} is deeper than {MAX_PATH_DEPTH} segments", location)
    if not isinstance(spec, Mapping) or len(spec) != 1:
        ctx.fail(
            PolicyLoadReason.INVALID_CONDITION,
            f"{path!r} must map to exactly one operator, e.g. {{{path}: {{eq: value}}}}",
            location,
        )
    ((op_name, operand),) = spec.items()
    op_location = f"{location}.{path}.{op_name}"
    try:
        op = Operator(op_name)
    except ValueError:
        ctx.fail(
            PolicyLoadReason.UNKNOWN_OPERATOR,
            f"unknown operator {op_name!r}; allowed: {sorted(o.value for o in Operator)}",
            op_location,
        )

    compiled: Scalar | tuple[Scalar, ...]
    if op in _PRESENCE_OPERATORS:
        if operand is not True:
            ctx.fail(PolicyLoadReason.INVALID_OPERAND, f"{op.value} takes the operand true", op_location)
        compiled = True
    elif op in _NUMERIC_OPERATORS:
        if _kind(operand) != "number":
            ctx.fail(PolicyLoadReason.INVALID_OPERAND, f"{op.value} takes a finite number within +/-2**53", op_location)
        compiled = operand
    elif op in _LIST_OPERATORS:
        if not isinstance(operand, list) or not operand:
            ctx.fail(PolicyLoadReason.INVALID_OPERAND, f"{op.value} takes a non-empty list", op_location)
        if len(operand) > MAX_LIST_OPERAND_ITEMS:
            ctx.fail(PolicyLoadReason.LIMIT_EXCEEDED, f"more than {MAX_LIST_OPERAND_ITEMS} items", op_location)
        items = tuple(
            _scalar_operand(ctx, item, location=f"{op_location}[{index}]") for index, item in enumerate(operand)
        )
        if len({_kind(item) for item in items}) != 1:
            ctx.fail(PolicyLoadReason.INVALID_OPERAND, f"{op.value} items must all be the same type", op_location)
        compiled = items
    else:
        compiled = _scalar_operand(ctx, operand, location=op_location)
    return Compare(path=path, segments=segments, op=op, operand=compiled)


def _compile_condition(ctx: _Context, node: Any, *, location: str, depth: int) -> Condition:
    if depth > MAX_CONDITION_DEPTH:
        ctx.fail(PolicyLoadReason.LIMIT_EXCEEDED, f"conditions nested deeper than {MAX_CONDITION_DEPTH}", location)
    if not isinstance(node, Mapping) or len(node) != 1:
        ctx.fail(
            PolicyLoadReason.INVALID_CONDITION,
            "a condition is a mapping with exactly one key: a dotted path, 'all', 'any' or 'not'",
            location,
        )
    ((key, value),) = node.items()
    if key in ("all", "any"):
        if not isinstance(value, list) or not value:
            ctx.fail(PolicyLoadReason.INVALID_CONDITION, f"{key!r} takes a non-empty list of conditions", location)
        if len(value) > MAX_COMBINATOR_ITEMS:
            ctx.fail(PolicyLoadReason.LIMIT_EXCEEDED, f"{key!r} has more than {MAX_COMBINATOR_ITEMS} items", location)
        items = tuple(
            _compile_condition(ctx, item, location=f"{location}.{key}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        )
        return AllOf(items) if key == "all" else AnyOf(items)
    if key == "not":
        return Not(_compile_condition(ctx, value, location=f"{location}.not", depth=depth + 1))
    return _compile_compare(ctx, key, value, location=location)


def _collect_paths(condition: Condition, into: set[str]) -> None:
    if isinstance(condition, Compare):
        into.add(condition.path)
    elif isinstance(condition, Not):
        _collect_paths(condition.item, into)
    else:
        for item in condition.items:
            _collect_paths(item, into)


def _compile_rule(ctx: _Context, raw: Any, *, location: str) -> Rule:
    if not isinstance(raw, Mapping):
        ctx.fail(PolicyLoadReason.INVALID_VALUE, "a rule must be a mapping", location)
    _check_keys(ctx, raw, _RULE_KEYS, location=location)
    rule_id = _identifier(ctx, _require(ctx, raw, "id", location=location), location=f"{location}.id")
    description = None
    if raw.get("description") is not None:
        description = _text(ctx, raw["description"], location=f"{location}.description", max_chars=MAX_TEXT_CHARS)
    when = _compile_condition(ctx, _require(ctx, raw, "when", location=location), location=f"{location}.when", depth=1)

    effect_location = f"{location}.effect"
    effect_raw = _require(ctx, raw, "effect", location=location)
    if not isinstance(effect_raw, Mapping):
        ctx.fail(PolicyLoadReason.INVALID_VALUE, "effect must be a mapping", effect_location)
    _check_keys(ctx, effect_raw, _EFFECT_KEYS, location=effect_location)
    tier_raw = _require(ctx, effect_raw, "tier", location=effect_location)
    try:
        tier = Tier(tier_raw)
    except ValueError:
        ctx.fail(
            PolicyLoadReason.INVALID_VALUE,
            f"unknown tier {tier_raw!r}; allowed: {[t.value for t in Tier]}",
            f"{effect_location}.tier",
        )
    reason = _text(
        ctx,
        _require(ctx, effect_raw, "reason", location=effect_location),
        location=f"{effect_location}.reason",
        max_chars=MAX_REASON_CHARS,
    )
    score = DEFAULT_TIER_SCORE[tier]
    if effect_raw.get("score") is not None:
        score = effect_raw["score"]
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= MAX_SCORE:
            ctx.fail(
                PolicyLoadReason.INVALID_VALUE,
                f"score must be a whole number from 0 to {MAX_SCORE}",
                f"{effect_location}.score",
            )
    return Rule(
        rule_id=rule_id, when=when, effect=Effect(tier=tier, reason=reason, score=score), description=description
    )


def _compile_thresholds(ctx: _Context, raw: Any) -> tuple[tuple[Tier, int], ...]:
    location = "score_thresholds"
    if raw is None:
        return ()
    if not isinstance(raw, Mapping) or not raw:
        ctx.fail(
            PolicyLoadReason.INVALID_VALUE, "score_thresholds must be a non-empty mapping of tier to score", location
        )
    pairs: list[tuple[Tier, int]] = []
    for name, value in raw.items():
        try:
            tier = Tier(name)
        except ValueError:
            ctx.fail(PolicyLoadReason.UNKNOWN_KEY, f"unknown tier {name!r}", location)
        if tier is Tier.LOW:
            ctx.fail(PolicyLoadReason.INVALID_VALUE, "low is the floor and takes no threshold", f"{location}.{name}")
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_SCORE:
            ctx.fail(
                PolicyLoadReason.INVALID_VALUE,
                f"threshold must be a whole number from 1 to {MAX_SCORE}",
                f"{location}.{name}",
            )
        pairs.append((tier, value))
    pairs.sort(key=lambda pair: pair[0].rank)
    for (lower_tier, lower), (higher_tier, higher) in zip(pairs, pairs[1:], strict=False):
        if higher <= lower:
            ctx.fail(
                PolicyLoadReason.INVALID_VALUE,
                f"{higher_tier.value} threshold ({higher}) must be greater than {lower_tier.value} ({lower})",
                location,
            )
    return tuple(pairs)


def _compile_policy(ctx: _Context, document: Any, content_hash: str) -> Policy:
    if not isinstance(document, Mapping):
        ctx.fail(PolicyLoadReason.INVALID_VALUE, "a policy file must contain one YAML mapping")
    _check_keys(ctx, document, _POLICY_KEYS, location="")

    policy_id = _identifier(ctx, _require(ctx, document, "policy", location=""), location="policy")
    version = _require(ctx, document, "version", location="")
    if not isinstance(version, str) or not _SEMVER_RE.match(version):
        ctx.fail(
            PolicyLoadReason.INVALID_VERSION,
            f'version must be a semantic version string such as "1.2.0", got {version!r}',
            "version",
        )

    status_raw = document.get("status")
    status = PolicyStatus.EXAMPLE
    if status_raw is not None:
        try:
            status = PolicyStatus(status_raw)
        except ValueError:
            ctx.fail(
                PolicyLoadReason.INVALID_VALUE,
                f"status must be one of {[s.value for s in PolicyStatus]}, got {status_raw!r}",
                "status",
            )

    reviewed_by = None
    if document.get("reviewed_by") is not None:
        reviewed_by = _text(ctx, document["reviewed_by"], location="reviewed_by", max_chars=MAX_STRING_OPERAND_CHARS)
        if _is_placeholder_reviewer(reviewed_by):
            ctx.fail(
                PolicyLoadReason.PRODUCTION_UNREVIEWED
                if status is PolicyStatus.PRODUCTION
                else PolicyLoadReason.INVALID_VALUE,
                f"reviewed_by {reviewed_by!r} is a placeholder, not a named reviewer",
                "reviewed_by",
            )
    if status is PolicyStatus.PRODUCTION and reviewed_by is None:
        ctx.fail(
            PolicyLoadReason.PRODUCTION_UNREVIEWED,
            "a production policy must name its compliance reviewer in reviewed_by",
            "reviewed_by",
        )

    description = None
    if document.get("description") is not None:
        description = _text(ctx, document["description"], location="description", max_chars=MAX_TEXT_CHARS)

    thresholds = _compile_thresholds(ctx, document.get("score_thresholds"))

    rules_raw = _require(ctx, document, "rules", location="")
    if not isinstance(rules_raw, list) or not rules_raw:
        ctx.fail(PolicyLoadReason.INVALID_VALUE, "rules must be a non-empty list", "rules")
    if len(rules_raw) > MAX_RULES:
        ctx.fail(PolicyLoadReason.LIMIT_EXCEEDED, f"more than {MAX_RULES} rules", "rules")
    rules: list[Rule] = []
    seen: dict[str, int] = {}
    for index, raw in enumerate(rules_raw):
        rule = _compile_rule(ctx, raw, location=f"rules[{index}]")
        if rule.rule_id in seen:
            ctx.fail(
                PolicyLoadReason.DUPLICATE_RULE_ID,
                f"rule id {rule.rule_id!r} is already used by rules[{seen[rule.rule_id]}]",
                f"rules[{index}].id",
            )
        seen[rule.rule_id] = index
        rules.append(rule)

    paths: set[str] = set()
    for rule in rules:
        _collect_paths(rule.when, paths)

    return Policy(
        policy_id=policy_id,
        version=version,
        status=status,
        reviewed_by=reviewed_by,
        description=description,
        rules=tuple(rules),
        score_thresholds=thresholds,
        content_hash=content_hash,
        source=ctx.source,
        referenced_paths=tuple(sorted(paths)),
    )


# ── Public API ──────────────────────────────────────────────────────────────


def load_policy_bytes(data: bytes, *, source: str = "<memory>", require_production: bool = False) -> Policy:
    """Compile a policy from the bytes of a policy file.

    Raises :class:`PolicyLoadError` for anything that is not a valid policy.
    ``require_production`` additionally refuses a policy whose status is not
    ``production``. Loading an example policy logs ``policy_example_loaded``.
    """
    try:
        policy = _load(data, source=source, require_production=require_production)
    except PolicyLoadError as exc:
        _policy_load_total.labels(outcome="rejected", reason=exc.reason.value).inc()
        logger.error(
            "policy_load_rejected",
            source=exc.source,
            reason=exc.reason.value,
            location=exc.location,
            detail=exc.detail,
        )
        raise
    _policy_load_total.labels(outcome="loaded", reason="none").inc()
    if policy.status is PolicyStatus.EXAMPLE:
        logger.warning(
            "policy_example_loaded",
            policy=policy.policy_id,
            version=policy.version,
            source=policy.source,
            detail=(
                "this is an example policy; it has not been reviewed by a compliance owner "
                "and must not be used to make real onboarding recommendations"
            ),
        )
    else:
        logger.info(
            "policy_loaded",
            policy=policy.policy_id,
            version=policy.version,
            source=policy.source,
            reviewed_by=policy.reviewed_by,
            content_hash=policy.content_hash,
        )
    return policy


def _load(data: bytes, *, source: str, require_production: bool) -> Policy:
    ctx = _Context(source)
    if not isinstance(data, bytes):
        ctx.fail(PolicyLoadReason.INVALID_VALUE, "policy content must be bytes")
    if len(data) > MAX_POLICY_BYTES:
        ctx.fail(PolicyLoadReason.TOO_LARGE, f"{len(data)} bytes exceeds the {MAX_POLICY_BYTES}-byte limit")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        ctx.fail(PolicyLoadReason.ENCODING_INVALID, f"not valid UTF-8: {exc.reason} at byte {exc.start}")
    try:
        loader = _StrictLoader(text)
        try:
            document = loader.get_single_data()
        finally:
            loader.dispose()
    except _YamlRejectedError as exc:
        ctx.fail(exc.reason, exc.detail)
    except yaml.YAMLError as exc:
        ctx.fail(PolicyLoadReason.YAML_INVALID, " ".join(str(exc).split()))
    except RecursionError:
        ctx.fail(PolicyLoadReason.LIMIT_EXCEEDED, "YAML nesting is too deep")
    except (ValueError, TypeError, AttributeError, OverflowError) as exc:
        # A scalar the YAML constructors could not build (for example an integer
        # longer than Python's digit limit). Never let it escape without a reason.
        ctx.fail(PolicyLoadReason.YAML_INVALID, f"a value could not be read: {type(exc).__name__}")
    content_hash = "sha256:" + hashlib.sha256(data).hexdigest()
    policy = _compile_policy(ctx, document, content_hash)
    if require_production and policy.status is not PolicyStatus.PRODUCTION:
        ctx.fail(
            PolicyLoadReason.NOT_PRODUCTION,
            f"policy {policy.policy_id} {policy.version} has status {policy.status.value}; production is required",
            "status",
        )
    return policy


def load_policy(path: str | os.PathLike[str], *, require_production: bool = False) -> Policy:
    """Load one policy file. See :func:`load_policy_bytes`."""
    source = str(path)
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_POLICY_BYTES + 1)
    except OSError as exc:
        error = PolicyLoadError(PolicyLoadReason.FILE_UNREADABLE, exc.strerror or type(exc).__name__, source=source)
        _policy_load_total.labels(outcome="rejected", reason=error.reason.value).inc()
        logger.error("policy_load_rejected", source=source, reason=error.reason.value, detail=error.detail)
        raise error from exc
    return load_policy_bytes(data, source=source, require_production=require_production)


def load_policies(directory: str | os.PathLike[str], *, require_production: bool = False) -> dict[str, Policy]:
    """Load every ``*.yaml``/``*.yml`` file in ``directory``, keyed by policy id.

    Fails closed: a missing or empty directory, any invalid file, or two files
    declaring the same policy id refuses the whole set.
    """
    root = Path(directory)
    source = str(root)
    if not root.is_dir():
        raise PolicyLoadError(PolicyLoadReason.DIRECTORY_INVALID, "not a directory", source=source)
    files = sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in POLICY_SUFFIXES)
    if not files:
        raise PolicyLoadError(PolicyLoadReason.DIRECTORY_INVALID, "contains no policy files", source=source)
    policies: dict[str, Policy] = {}
    for file in files:
        policy = load_policy(file, require_production=require_production)
        if policy.policy_id in policies:
            raise PolicyLoadError(
                PolicyLoadReason.DUPLICATE_POLICY,
                f"policy id {policy.policy_id!r} is also declared by {policies[policy.policy_id].source}",
                source=str(file),
            )
        policies[policy.policy_id] = policy
    return policies


EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"
