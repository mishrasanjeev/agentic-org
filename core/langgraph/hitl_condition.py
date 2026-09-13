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
"""

from __future__ import annotations

import ast
import operator
import re
from typing import Any

import structlog

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


class HITLConditionError(ValueError):
    """Raised when a condition cannot be evaluated against the output."""


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
        logger.warning(
            "hitl_condition_eval_failed_fail_closed",
            condition=condition,
            error=str(exc),
        )
        return True, f"condition unevaluable (fail closed): {condition} ({exc})"

    if matched:
        return True, f"condition matched: {condition}"
    return False, ""
