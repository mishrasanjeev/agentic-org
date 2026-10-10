# SPDX-License-Identifier: Apache-2.0
"""The refusal every spend service raises, the guard that a change names who made it, and which
database errors are worth another try."""

from __future__ import annotations

ACTOR_MAX = 128
# SQLSTATEs a later attempt can get past: lock_not_available (a lock timeout), deadlock_detected,
# serialization_failure and query_canceled (a statement timeout). Under asyncpg these arrive as a
# plain ``DBAPIError``, not an ``OperationalError``.
RETRYABLE_SQLSTATES = frozenset({"55P03", "40P01", "40001", "57014"})


class SpendError(Exception):
    """A refusal with an HTTP status, a stable code and a message for the caller.

    ``extra`` carries structured detail the caller can act on (the refused
    lines of an invoice); the routes add it beside ``error`` and ``message``.
    """

    def __init__(self, status: int, code: str, message: str, *, extra: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = dict(extra or {})


def require_actor(actor: str | None) -> str:
    """The acting user's id; a change to reference data refuses when nobody is identified."""
    who = str(actor or "").strip()
    if not who:
        raise SpendError(401, "actor_required", "this change needs an identified user")
    return who[:ACTOR_MAX]


def retryable(exc: BaseException) -> bool:
    """Whether ``exc`` is a transient database failure: a dropped or refused connection, a pool or
    network timeout, a lock or statement timeout, a deadlock or a serialization failure."""
    from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
    from sqlalchemy.exc import TimeoutError as PoolTimeout

    if isinstance(exc, (OSError, TimeoutError, OperationalError, InterfaceError, PoolTimeout)):
        return True
    if isinstance(exc, DBAPIError):
        orig = exc.orig
        code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
        return code in RETRYABLE_SQLSTATES
    return False
