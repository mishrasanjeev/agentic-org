"""Safe condition evaluator — NO eval()."""

from __future__ import annotations

import ast
import operator
import re
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


_TRUE_WORDS = frozenset({"true", "1", "yes"})
_FALSE_WORDS = frozenset({"false", "0", "no"})
_ORDERING = frozenset({">", "<", ">=", "<="})


# A field is a dotted path; a value is a quoted string with no inner quote of
# its own kind, or one bare token (a word, a number or a dotted path).
_PATH_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*")
_BARE_RE = re.compile(r"[A-Za-z0-9_.\-]+")


def _unbalanced_quotes(part: str) -> bool:
    """True when a quote opened in ``part`` is never closed (an apostrophe inside "..." is fine)."""
    open_quote = ""
    for ch in part:
        if open_quote:
            if ch == open_quote:
                open_quote = ""
        elif ch in "'\"":
            open_quote = ch
    return bool(open_quote)


def _well_formed_value(token: str) -> bool:
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
        return token[0] not in token[1:-1]
    return bool(_BARE_RE.fullmatch(token))


def evaluate_condition_strict(expression: str, context: dict[str, Any]) -> bool | None:
    """Evaluate the same grammar as :func:`evaluate_condition`, answering ``None`` when it cannot decide.

    For authority decisions, where "no match" must not stand in for "unknown".
    ``None`` means a field the expression names is absent, a list does not
    parse, an ordering comparison is not numeric, or the expression is not in
    the grammar. ``AND``/``OR``/``NOT`` combine by Kleene logic, so
    ``NOT <missing>`` stays unknown instead of becoming a match, while
    ``True OR <unknown>`` is still ``True`` and ``False AND <unknown>`` still
    ``False``.
    """
    expression = expression.strip()
    if not expression:
        return None
    parts = _split_keyword(expression, "or")
    if parts is not None:
        if any(_unbalanced_quotes(p) for p in parts):
            return None  # the keyword was inside a quoted string (FINDINGS A-63)
        values = [evaluate_condition_strict(p, context) for p in parts]
        if True in values:
            return True
        return None if None in values else False
    parts = _split_keyword(expression, "and")
    if parts is not None:
        if any(_unbalanced_quotes(p) for p in parts):
            return None
        values = [evaluate_condition_strict(p, context) for p in parts]
        if False in values:
            return False
        return None if None in values else True
    if expression.startswith(("NOT ", "not ")):
        inner = evaluate_condition_strict(expression[4:], context)
        return None if inner is None else not inner

    for membership, negate in ((" not in ", True), (" in ", False)):
        if membership in expression:
            left, right = expression.split(membership, 1)
            if not _PATH_RE.fullmatch(left.strip()):
                return None
            left_val = _resolve(left.strip(), context)
            members = _parse_list(right.strip(), context)
            if left_val is MISSING or members is MISSING:
                return None
            found = any(_values_equal(left_val, m) for m in members)
            return (not found) if negate else found

    for op_str, op_func in sorted(OPS.items(), key=lambda x: -len(x[0])):
        if op_str in expression:
            left, right = expression.split(op_str, 1)
            # A malformed operand (``status ==``, ``status === ok``, an
            # unterminated quote) is not a comparison the author could mean.
            if not _PATH_RE.fullmatch(left.strip()) or not _well_formed_value(right):
                return None
            left_val = _resolve(left.strip(), context)
            if left_val is MISSING:
                return None
            right_val = _resolve(right.strip(), context, literal_fallback=True)
            try:
                return bool(op_func(float(left_val), float(right_val)))
            except (ValueError, TypeError):
                if op_str in _ORDERING:
                    return None
                return bool(op_func(str(left_val), str(right_val)))

    val = _resolve(expression, context)
    if val is MISSING:
        word = expression.lower()
        return True if word in _TRUE_WORDS else False if word in _FALSE_WORDS else None
    if isinstance(val, str):
        word = val.strip().lower()
        return True if word in _TRUE_WORDS else False if word in _FALSE_WORDS else None
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
