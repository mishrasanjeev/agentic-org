# SPDX-License-Identifier: Apache-2.0
"""The refusal every spend service raises, and the guard that a change names who made it."""

from __future__ import annotations

ACTOR_MAX = 128


class SpendError(Exception):
    """A refusal with an HTTP status, a stable code and a message for the caller."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def require_actor(actor: str | None) -> str:
    """The acting user's id; a change to reference data refuses when nobody is identified."""
    who = str(actor or "").strip()
    if not who:
        raise SpendError(401, "actor_required", "this change needs an identified user")
    return who[:ACTOR_MAX]
