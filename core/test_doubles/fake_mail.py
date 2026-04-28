"""Hermetic fake mail (Foundation #7 PR-B).

Replaces the SMTP send in ``core.email.send_email`` with an
in-process capture so tests can assert that an email *would have*
been sent — what the To/Subject/HTML were — without standing up
MailHog or hitting Gmail SMTP.

Activation: ``AGENTICORG_TEST_FAKE_MAIL=1``. The conftest sets
this by default for every test run.

Inspection::

    from core.test_doubles import fake_mail

    fake_mail.outbox()              # list of CapturedEmail
    fake_mail.last()                # most-recent CapturedEmail
    fake_mail.count_to("a@b.com")  # how many sent to that address
    fake_mail.reset()               # clear between tests

The autouse fixture in tests/conftest.py calls ``reset()`` before
each test so captures don't leak across cases.

Domain-validation behavior is preserved — the fake still calls
``validate_email_domain`` first (when the production caller does)
so tests asserting "invalid domain → no send" remain accurate.
That preservation lives at the call-site in ``core/email.py``;
this module is just the capture sink.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field


@dataclass
class CapturedEmail:
    """One captured email. Mirrors the inputs to send_email()."""

    to: str
    subject: str
    html: str
    sender: str = ""
    timestamp: float = field(default_factory=time.time)


_OUTBOX: list[CapturedEmail] = []


def is_active() -> bool:
    """True iff ``AGENTICORG_TEST_FAKE_MAIL`` is truthy."""
    return os.getenv("AGENTICORG_TEST_FAKE_MAIL", "").lower() in (
        "1",
        "true",
        "yes",
    )


def capture(*, to: str, subject: str, html: str, sender: str = "") -> CapturedEmail:
    """Record one captured email and return the record."""
    rec = CapturedEmail(to=to, subject=subject, html=html, sender=sender)
    _OUTBOX.append(rec)
    return rec


def outbox() -> list[CapturedEmail]:
    """Return a copy of the captured emails, oldest first."""
    return list(_OUTBOX)


def last() -> CapturedEmail | None:
    """Return the most-recent captured email, or None if empty."""
    return _OUTBOX[-1] if _OUTBOX else None


def count() -> int:
    """Total emails captured since the last reset."""
    return len(_OUTBOX)


def count_to(addr: str) -> int:
    """Count of captured emails whose ``to`` matches ``addr``."""
    return sum(1 for e in _OUTBOX if e.to == addr)


def reset() -> None:
    """Clear the captured outbox. Call from a test fixture to keep
    captures isolated between cases."""
    _OUTBOX.clear()
