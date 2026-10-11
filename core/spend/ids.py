# SPDX-License-Identifier: Apache-2.0
"""Time-ordered ids for usage records.

A usage record's id is a UUID in the version 7 layout: 48 bits of Unix
milliseconds, the version and variant bits, and 74 random bits. Ids made
later sort later, so inserts land at the right edge of each partition's
indexes and ``(event_time, id)`` gives a stable order. ``uuid.uuid7`` only
exists from Python 3.14, and the project supports 3.12.
"""

from __future__ import annotations

import secrets
import time
import uuid

_MS_MASK = (1 << 48) - 1
_RAND_A_MASK = (1 << 12) - 1
_RAND_B_MASK = (1 << 62) - 1


def time_uuid(unix_ms: int | None = None) -> uuid.UUID:
    """A version 7 layout UUID for ``unix_ms`` (now when omitted)."""
    ms = int(time.time() * 1000) if unix_ms is None else int(unix_ms)
    random_bits = secrets.randbits(74)
    value = (ms & _MS_MASK) << 80
    value |= 0x7 << 76
    value |= ((random_bits >> 62) & _RAND_A_MASK) << 64
    value |= 0b10 << 62
    value |= random_bits & _RAND_B_MASK
    return uuid.UUID(int=value)


def unix_ms_of(value: uuid.UUID) -> int:
    """The millisecond timestamp a ``time_uuid`` carries."""
    return value.int >> 80
