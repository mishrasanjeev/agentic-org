"""Safe condition evaluator — NO eval()."""
from __future__ import annotations
import operator
import re
from typing import Any

OPS = {
    ">": operator.gt, "<": operator.lt, ">=": operator.ge, "<=": operator.le,
    "==": operator.eq, "!=": operator.ne,
}

def evaluate_condition(expression: str, context: dict[str, Any]) -> bool:
    """Evaluate a condition like 'total > 500000 OR status == mismatch'."""
    expression = expression.strip()
    if " OR " in expression:
        parts = expression.split(" OR ")
        return any(evaluate_condition(p.strip(), context) for p in parts)
    if " AND " in expression:
        parts = expression.split(" AND ")
        return all(evaluate_condition(p.strip(), context) for p in parts)
    if expression.startswith("NOT "):
        return not evaluate_condition(expression[4:].strip(), context)

    for op_str, op_func in sorted(OPS.items(), key=lambda x: -len(x[0])):
        if op_str in expression:
            left, right = expression.split(op_str, 1)
            left_val = _resolve(left.strip(), context)
            right_val = _resolve(right.strip(), context)
            try:
                return op_func(float(left_val), float(right_val))
            except (ValueError, TypeError):
                return op_func(str(left_val), str(right_val))
    return bool(_resolve(expression, context))

def _resolve(token: str, context: dict) -> Any:
    token = token.strip().strip("'").strip('"')
    parts = token.split(".")
    val = context
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p, token)
        else:
            return token
    return val
