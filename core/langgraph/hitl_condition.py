"""Fail-closed evaluator for agent ``hitl_condition`` expressions.

Shared by the LangGraph runtime (``core.langgraph.agent_graph``) and the
legacy ``core.agents.base.BaseAgent`` so both paths apply the same
semantics:

* Supports ``and`` / ``or`` / ``not`` (case-insensitive), chained
  comparisons on numbers, strings and booleans, ``in`` / ``not in``
  against list literals, and dotted / subscript access into the output.
* Any parse failure, unsupported syntax, type error, or reference to a
  field missing from the agent output TRIGGERS HITL. A human review is
  the safe default when the guard cannot be evaluated.

``validate_hitl_condition`` checks a condition against the grammar without
an output, so the agent and SOP APIs can refuse one when it is saved
(``screen_condition_on_save``) instead of letting it trigger on every run.
The saved grammar is stricter than the run-time evaluator in one way: every
operand of ``and`` / ``or`` / ``not`` must be a comparison, so a bare label
such as ``high_value_procurement`` is refused rather than read as an output
key that is almost never there. ``always`` and ``always_<label>`` remain the
explicit way to require review on every run.
"""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass
from typing import Any

import structlog

from observability.metrics import hitl_condition_parse_failures_total

logger = structlog.get_logger()

_COMPARE_OPS: dict[type, Any] = {
    ast.Gt: operator.gt,
    ast.Lt: operator.lt,
    ast.GtE: operator.ge,
    ast.LtE: operator.le,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

_NUMERIC_RE = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")
_ALWAYS_RE = re.compile(r"^always(_\w+)?$", re.IGNORECASE)

# Reason codes (also the ``reason`` metric label, so keep the set closed).
REASON_SYNTAX_ERROR = "syntax_error"
REASON_UNSUPPORTED_SYNTAX = "unsupported_syntax"
REASON_UNSUPPORTED_OPERATOR = "unsupported_operator"
REASON_NOT_A_COMPARISON = "not_a_comparison"
REASON_INVALID_MODE = "invalid_mode"


class HITLConditionError(ValueError):
    """Raised when a condition cannot be evaluated against the output."""


@dataclass(frozen=True)
class ConditionParseResult:
    """Outcome of checking a condition against the grammar."""

    ok: bool
    reason: str = ""
    detail: str = ""


class _GrammarError(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def _normalize(condition: str) -> str:
    # Authored conditions commonly use SQL-style upper-case keywords.
    text = re.sub(r"\bOR\b", "or", condition)
    text = re.sub(r"\bAND\b", "and", text)
    text = re.sub(r"\bNOT\b", "not", text)
    return text.strip()


def _coerce_pair(left: Any, right: Any) -> tuple[Any, Any]:
    """Coerce numeric-looking strings so ``"12" > 5`` compares numerically."""
    if isinstance(left, bool) or isinstance(right, bool):
        return left, right
    if isinstance(left, int | float) and isinstance(right, str) and _NUMERIC_RE.match(right.strip()):
        return left, float(right)
    if isinstance(right, int | float) and isinstance(left, str) and _NUMERIC_RE.match(left.strip()):
        return float(left), right
    return left, right


def _eval(node: ast.AST, ctx: dict[str, Any]) -> Any:
    if isinstance(node, ast.BoolOp):
        # Evaluate every operand so a missing field anywhere fails closed.
        values = [_eval(v, ctx) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
        raise HITLConditionError(f"unsupported boolean operator {type(node.op).__name__}")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval(node.operand, ctx)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval(node.operand, ctx)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, ctx)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            fn = _COMPARE_OPS.get(type(op))
            if fn is None:
                raise HITLConditionError(f"unsupported comparison {type(op).__name__}")
            right = _eval(comparator, ctx)
            if not isinstance(op, ast.In | ast.NotIn):
                left, right = _coerce_pair(left, right)
            if not fn(left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Name):
        if node.id in ctx:
            return ctx[node.id]
        raise HITLConditionError(f"field '{node.id}' missing from output")
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return [_eval(e, ctx) for e in node.elts]
    if isinstance(node, ast.Attribute):
        base = _eval(node.value, ctx)
        if isinstance(base, dict) and node.attr in base:
            return base[node.attr]
        raise HITLConditionError(f"field '{node.attr}' missing from output")
    if isinstance(node, ast.Subscript):
        base = _eval(node.value, ctx)
        key = _eval(node.slice, ctx)
        try:
            return base[key]
        except (KeyError, IndexError, TypeError) as exc:
            raise HITLConditionError(f"field {key!r} missing from output") from exc
    raise HITLConditionError(f"unsupported syntax {type(node).__name__}")


def _check_value(node: ast.AST) -> None:
    """Operands of a comparison: output keys, literals and lists of them."""
    if isinstance(node, ast.Name | ast.Constant):
        return
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        for elt in node.elts:
            _check_value(elt)
        return
    if isinstance(node, ast.Attribute):
        _check_value(node.value)
        return
    if isinstance(node, ast.Subscript):
        _check_value(node.value)
        _check_value(node.slice)
        return
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        _check_value(node.operand)
        return
    raise _GrammarError(REASON_UNSUPPORTED_SYNTAX, f"unsupported syntax {type(node).__name__}")


def _check_boolean(node: ast.AST) -> None:
    """``and`` / ``or`` / ``not`` over comparisons."""
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            _check_boolean(value)
        return
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        _check_boolean(node.operand)
        return
    if isinstance(node, ast.Compare):
        for op in node.ops:
            if type(op) not in _COMPARE_OPS:
                raise _GrammarError(REASON_UNSUPPORTED_OPERATOR, f"unsupported comparison {type(op).__name__}")
        _check_value(node.left)
        for comparator in node.comparators:
            _check_value(comparator)
        return
    if isinstance(node, ast.Name | ast.Attribute | ast.Subscript | ast.Constant):
        label = ast.unparse(node)
        raise _GrammarError(
            REASON_NOT_A_COMPARISON,
            f"'{label}' is not a comparison; compare an output key, e.g. {label} == True",
        )
    raise _GrammarError(REASON_UNSUPPORTED_SYNTAX, f"unsupported syntax {type(node).__name__}")


def validate_hitl_condition(condition: object) -> ConditionParseResult:
    """Check *condition* against the grammar without evaluating it.

    An empty condition (no extra guard) and ``always`` / ``always_<label>``
    are valid. Never raises: every failure is returned with a reason code.
    """
    if condition is None:
        return ConditionParseResult(ok=True)
    if not isinstance(condition, str):
        return ConditionParseResult(False, REASON_SYNTAX_ERROR, "condition must be a string")
    text = condition.strip()
    if not text:
        return ConditionParseResult(ok=True)
    normalized = _normalize(text)
    if _ALWAYS_RE.match(normalized):
        return ConditionParseResult(ok=True)
    try:
        tree = ast.parse(normalized, mode="eval")
    except SyntaxError as exc:
        where = f" at column {exc.offset}" if exc.offset else ""
        return ConditionParseResult(False, REASON_SYNTAX_ERROR, f"syntax error{where}: {exc.msg}")
    except (ValueError, RecursionError, MemoryError) as exc:
        return ConditionParseResult(False, REASON_SYNTAX_ERROR, f"syntax error: {type(exc).__name__}")
    try:
        _check_boolean(tree.body)
    except _GrammarError as exc:
        return ConditionParseResult(False, exc.reason, exc.detail)
    except RecursionError:
        return ConditionParseResult(False, REASON_SYNTAX_ERROR, "syntax error: expression nested too deeply")
    return ConditionParseResult(ok=True)


def screen_condition_on_save(
    condition: object,
    *,
    mode: str,
    surface: str,
) -> ConditionParseResult | None:
    """Apply the save-time *mode* to *condition*.

    Returns the failed parse result when the save must be refused, otherwise
    ``None``. ``warn`` logs and counts a failure but accepts it; ``off`` does
    nothing. An unrecognised mode refuses every save (fail closed).
    """
    if mode == "off":
        return None
    if mode not in ("warn", "reject"):
        logger.error("hitl_condition_validation_mode_invalid", mode=str(mode)[:32], surface=surface)
        hitl_condition_parse_failures_total.labels(stage="save", reason=REASON_INVALID_MODE, outcome="rejected").inc()
        return ConditionParseResult(False, REASON_INVALID_MODE, "HITL condition validation mode is misconfigured")
    result = validate_hitl_condition(condition)
    if result.ok:
        return None
    outcome = "warned" if mode == "warn" else "rejected"
    hitl_condition_parse_failures_total.labels(stage="save", reason=result.reason, outcome=outcome).inc()
    logger.warning(
        "hitl_condition_unparseable_on_save",
        surface=surface,
        mode=mode,
        reason=result.reason,
        detail=result.detail,
        condition=str(condition)[:500],
    )
    return result if mode == "reject" else None


def evaluate_hitl_condition(
    condition: str,
    output: Any,
    confidence: float | None = None,
) -> tuple[bool, str]:
    """Return ``(triggered, reason)`` for *condition* against *output*.

    ``confidence`` (when given) is exposed to the expression as the
    ``confidence`` name unless the output already carries one. Evaluation
    failures of any kind trigger HITL (fail closed) and are logged.
    """
    condition = (condition or "").strip()
    if not condition:
        return False, ""

    normalized = _normalize(condition)
    if normalized.lower().startswith("always"):
        return True, f"condition matched: {condition}"

    ctx: dict[str, Any] = dict(output) if isinstance(output, dict) else {}
    if confidence is not None:
        ctx.setdefault("confidence", confidence)

    try:
        tree = ast.parse(normalized, mode="eval")
        matched = bool(_eval(tree.body, ctx))
    # enterprise-gate: broad-except-ok reason=hitl-guard-unevaluable-fails-closed-to-human-review
    except Exception as exc:  # noqa: BLE001 - any evaluation failure must fail closed.
        parsed = validate_hitl_condition(condition)
        if not parsed.ok:
            hitl_condition_parse_failures_total.labels(stage="run", reason=parsed.reason, outcome="fail_closed").inc()
        logger.warning(
            "hitl_condition_eval_failed_fail_closed",
            condition=condition,
            error=str(exc),
        )
        return True, f"condition unevaluable (fail closed): {condition} ({exc})"

    if matched:
        return True, f"condition matched: {condition}"
    return False, ""
