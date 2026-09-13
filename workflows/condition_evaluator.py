"""Safe condition evaluator — NO eval()."""

from __future__ import annotations

import ast
import operator
from typing import Any

OPS = {
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}

# Sentinel for a dotted path that does not resolve in the context. Any
# comparison against it is False so a missing field never satisfies a
# guard like ``amount > 100000`` or ``plan in ["enterprise"]``.
MISSING: Any = object()


def _split_keyword(expression: str, keyword: str) -> list[str] | None:
    upper = f" {keyword.upper()} "
    lower = f" {keyword.lower()} "
    if upper in expression:
        return expression.split(upper)
    if lower in expression:
        return expression.split(lower)
    return None


def evaluate_condition(expression: str, context: dict[str, Any]) -> bool:
    """Evaluate a condition like 'total > 500000 OR status == mismatch'.

    Supports ``AND``/``OR``/``NOT`` (upper or lower case), the comparison
    operators in ``OPS``, and ``field in [..]`` / ``field not in [..]``.
    Unresolved left-hand fields evaluate to False.
    """
    expression = expression.strip()
    parts = _split_keyword(expression, "or")
    if parts is not None:
        return any(evaluate_condition(p.strip(), context) for p in parts)
    parts = _split_keyword(expression, "and")
    if parts is not None:
        return all(evaluate_condition(p.strip(), context) for p in parts)
    if expression.startswith(("NOT ", "not ")):
        return not evaluate_condition(expression[4:].strip(), context)

    for membership, negate in ((" not in ", True), (" in ", False)):
        if membership in expression:
            left, right = expression.split(membership, 1)
            left_val = _resolve(left.strip(), context)
            if left_val is MISSING:
                return False
            members = _parse_list(right.strip(), context)
            if members is MISSING:
                return False
            found = any(_values_equal(left_val, m) for m in members)
            return (not found) if negate else found

    for op_str, op_func in sorted(OPS.items(), key=lambda x: -len(x[0])):
        if op_str in expression:
            left, right = expression.split(op_str, 1)
            left_val = _resolve(left.strip(), context)
            if left_val is MISSING:
                return False
            right_val = _resolve(right.strip(), context, literal_fallback=True)
            try:
                return op_func(float(left_val), float(right_val))
            except (ValueError, TypeError):
                return op_func(str(left_val), str(right_val))

    val = _resolve(expression, context, literal_fallback=True)
    if val is MISSING:
        return False
    if isinstance(val, str):
        return val.strip().lower() in {"true", "1", "yes"}
    return bool(val)


def _values_equal(a: Any, b: Any) -> bool:
    try:
        return float(a) == float(b)
    except (ValueError, TypeError):
        return str(a) == str(b)


def _parse_list(token: str, context: dict) -> Any:
    """Parse a ``["a", "b"]`` literal or resolve a context path to a list."""
    if token.startswith(("[", "(")):
        try:
            parsed = ast.literal_eval(token)
        except (ValueError, SyntaxError):
            return MISSING
        return list(parsed) if isinstance(parsed, list | tuple | set) else MISSING
    resolved = _resolve(token, context)
    if isinstance(resolved, list | tuple | set):
        return list(resolved)
    return MISSING


def _resolve(token: str, context: dict, *, literal_fallback: bool = False) -> Any:
    """Resolve a dotted path against *context*.

    Quoted tokens are string literals. Unquoted tokens are looked up as a
    dotted path; when the path is absent, return the token text only if
    ``literal_fallback`` is set (right-hand operands), else ``MISSING``.
    """
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
        return token[1:-1]
    parts = token.split(".")
    val: Any = context
    for p in parts:
        if isinstance(val, dict) and p in val:
            val = val[p]
        else:
            return token if literal_fallback else MISSING
    return val
