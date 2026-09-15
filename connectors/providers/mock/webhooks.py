# SPDX-License-Identifier: Apache-2.0
"""HMAC-signed webhook events for the mock provider.

A delivery carries three headers:

- ``X-Mock-Event-Id`` - the event id, repeated in the body;
- ``X-Mock-Timestamp`` - Unix seconds when it was signed;
- ``X-Mock-Signature`` - ``v1=<hex HMAC-SHA256 of "<timestamp>." + body>``; several comma-separated
  values are accepted so a secret can be rotated.

Verification fails closed: a missing or malformed header, a timestamp outside the tolerance, a
signature that matches no configured secret, a body that is not a valid event, or an event id that
differs between header and body all yield ``None``. Nothing here raises.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from datetime import datetime

from pydantic import ValidationError

from connectors.framework.verification_types import BusinessRef, ProviderEvent, ProviderEventType

EVENT_ID_HEADER = "x-mock-event-id"
TIMESTAMP_HEADER = "x-mock-timestamp"
SIGNATURE_HEADER = "x-mock-signature"
SIGNATURE_SCHEME = "v1"
MIN_SECRET_LENGTH = 16


def _digest(secret: str, timestamp: int, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def event_body(
    *,
    provider: str,
    event_id: str,
    event_type: ProviderEventType,
    occurred_at: datetime,
    subject: BusinessRef | None,
    monitor_id: str | None = None,
) -> bytes:
    event = ProviderEvent(
        provider=provider,
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_at,
        subject=subject,
        monitor_id=monitor_id,
    )
    return json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(secret: str, body: bytes, *, event_id: str, timestamp: int) -> dict[str, str]:
    if len(secret) < MIN_SECRET_LENGTH:
        raise ValueError(f"webhook secret must be at least {MIN_SECRET_LENGTH} characters")
    return {
        "X-Mock-Event-Id": event_id,
        "X-Mock-Timestamp": str(timestamp),
        "X-Mock-Signature": f"{SIGNATURE_SCHEME}={_digest(secret, timestamp, body)}",
    }


def verify(
    headers: Mapping[str, str],
    body: bytes,
    *,
    secrets: Sequence[str],
    provider: str,
    now: int,
    tolerance_seconds: int,
) -> ProviderEvent | None:
    usable = [s for s in secrets if len(s) >= MIN_SECRET_LENGTH]
    if not usable or not isinstance(body, bytes | bytearray):
        return None
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    event_id = lowered.get(EVENT_ID_HEADER)
    raw_timestamp = lowered.get(TIMESTAMP_HEADER, "")
    raw_signature = lowered.get(SIGNATURE_HEADER)
    if not event_id or not raw_signature or not (raw_timestamp.isascii() and raw_timestamp.isdigit()):
        return None
    if len(raw_timestamp) > 12:
        return None
    timestamp = int(raw_timestamp)
    if abs(now - timestamp) > tolerance_seconds:
        return None
    offered = [
        part.strip().removeprefix(f"{SIGNATURE_SCHEME}=").encode("utf-8", "replace")
        for part in raw_signature.split(",")
        if part.strip().startswith(f"{SIGNATURE_SCHEME}=")
    ]
    expected = [_digest(secret, timestamp, bytes(body)).encode("ascii") for secret in usable]
    if not any(hmac.compare_digest(o, e) for o in offered for e in expected):
        return None
    try:
        event = ProviderEvent.model_validate_json(bytes(body))
    except (ValidationError, ValueError):
        return None
    if event.provider != provider or event.event_id != event_id:
        return None
    return event
