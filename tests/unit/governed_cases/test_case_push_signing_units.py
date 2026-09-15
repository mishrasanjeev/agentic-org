# SPDX-License-Identifier: Apache-2.0
"""Case push signing, receiver verification, retry schedule and event typing, without a database."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from core.cases.push import (
    EVENT_ID_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    PushSettings,
    SigningKey,
    event_id_for,
    event_type_for,
    payload_bytes,
    retry_delay,
    sign,
    verify_signature,
)
from core.cases.states import CaseError, CaseState

NOW = 1_788_000_000
ACTIVE = SigningKey("k_active", "active-secret-placeholder-value")
PREVIOUS = SigningKey("k_previous", "previous-secret-placeholder-value")
BODY = payload_bytes({"event_id": "e", "case": {"case_id": "case_1"}})


def test_signed_delivery_verifies_with_the_receivers_key() -> None:
    import time

    secret = ACTIVE.secret
    raw_body = BODY
    request_headers = sign(
        raw_body, event_id="evt-1", event_type="case.completed", timestamp=int(time.time()), keys=[ACTIVE]
    )
    # docs-snippet: start verify-case-push
    from core.cases.push import verify_signature

    reason = verify_signature(request_headers, raw_body, keys={"k_active": secret}, now=int(time.time()))
    # reason is "" or signature_missing, timestamp_invalid, timestamp_outside_tolerance, signature_mismatch
    accepted = reason == ""
    # docs-snippet: end verify-case-push
    assert accepted, reason


def _headers(keys: list[SigningKey] | None = None, timestamp: int = NOW, event_id: str = "evt-1") -> dict[str, str]:
    return sign(BODY, event_id=event_id, event_type="case.completed", timestamp=timestamp, keys=keys or [ACTIVE])


def test_a_valid_signature_verifies() -> None:
    headers = _headers()
    assert headers[SIGNATURE_HEADER].startswith("v1=k_active:")
    assert verify_signature(headers, BODY, keys={"k_active": ACTIVE.secret}, now=NOW) == ""


def test_header_names_are_case_insensitive_for_receivers() -> None:
    headers = {k.lower(): v for k, v in _headers().items()}
    assert verify_signature(headers, BODY, keys={"k_active": ACTIVE.secret}, now=NOW + 10) == ""


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda h, b: ({k: v for k, v in h.items() if k != SIGNATURE_HEADER}, b), "signature_missing"),
        (lambda h, b: ({**h, TIMESTAMP_HEADER: "soon"}, b), "timestamp_invalid"),
        (lambda h, b: ({**h, TIMESTAMP_HEADER: str(NOW - 301)}, b), "timestamp_outside_tolerance"),
        (lambda h, b: (h, b.replace(b"case_1", b"case_2")), "signature_mismatch"),
        (lambda h, b: ({**h, EVENT_ID_HEADER: "evt-2"}, b), "signature_mismatch"),
        (
            lambda h, b: ({**h, SIGNATURE_HEADER: h[SIGNATURE_HEADER].replace("k_active", "k_unknown")}, b),
            "signature_mismatch",
        ),
        (lambda h, b: ({**h, SIGNATURE_HEADER: "v2=" + h[SIGNATURE_HEADER][3:]}, b), "signature_mismatch"),
    ],
)
def test_forged_tampered_or_stale_deliveries_are_refused(mutate, reason: str) -> None:
    headers, body = mutate(_headers(), BODY)
    assert verify_signature(headers, body, keys={"k_active": ACTIVE.secret}, now=NOW) == reason


def test_rotation_signs_with_both_keys_so_receivers_can_switch_first() -> None:
    headers = _headers([ACTIVE, PREVIOUS])
    assert headers[SIGNATURE_HEADER].count("v1=") == 2
    assert verify_signature(headers, BODY, keys={"k_previous": PREVIOUS.secret}, now=NOW) == ""
    assert verify_signature(headers, BODY, keys={"k_active": ACTIVE.secret}, now=NOW) == ""
    assert (
        verify_signature(_headers([ACTIVE]), BODY, keys={"k_previous": PREVIOUS.secret}, now=NOW)
        == "signature_mismatch"
    )


def test_signing_without_keys_is_refused() -> None:
    with pytest.raises(ValueError):
        sign(BODY, event_id="e", event_type="case.completed", timestamp=NOW, keys=[])


def test_retry_delay_doubles_with_bounded_deterministic_jitter() -> None:
    settings = PushSettings(base_delay_seconds=2.0, max_delay_seconds=900.0)
    event = uuid.UUID(int=7)
    delays = [retry_delay(attempt, event, settings) for attempt in range(1, 12)]
    for attempt, delay in enumerate(delays, start=1):
        base = min(2.0 * 2 ** (attempt - 1), 900.0)
        assert 0.8 * base <= delay <= 1.2 * base
    assert delays == [retry_delay(attempt, event, settings) for attempt in range(1, 12)]
    assert sum(delays[:4]) < 60  # four quick retries still land inside the 60-second hand-off window


def test_event_types_follow_the_lifecycle() -> None:
    first = SimpleNamespace(agent_records=[{"agent": "business_underwriter"}])
    again = SimpleNamespace(agent_records=[{"agent": "business_underwriter"}, {"agent": "business_underwriter"}])
    assert event_type_for(first, CaseState.AWAITING_DECISION) == "case.completed"
    assert event_type_for(again, CaseState.AWAITING_DECISION) == "case.updated"
    assert event_type_for(first, CaseState.DECIDED) == "case.decided"
    assert event_type_for(first, CaseState.WITHDRAWN) == "case.updated"
    assert event_type_for(first, CaseState.IN_PROGRESS) is None


def test_event_ids_are_stable_per_case_version_and_type() -> None:
    case = SimpleNamespace(id=uuid.UUID(int=1), version=4)
    assert event_id_for(case, "case.completed") == event_id_for(case, "case.completed")
    assert event_id_for(case, "case.completed") != event_id_for(case, "case.updated")
    assert event_id_for(case, "case.completed") != event_id_for(
        SimpleNamespace(id=case.id, version=5), "case.completed"
    )


@pytest.mark.parametrize(
    "url", ["ftp://receiver.example.com/hook", "https://user:pass@receiver.example.com/", "https://"]
)
def test_endpoint_urls_must_be_http_without_credentials(url: str) -> None:
    from core.cases.push import validate_endpoint_url

    with pytest.raises(CaseError, match="endpoint_url_invalid"):
        validate_endpoint_url(url)


def test_strict_runtimes_require_https_to_a_public_host(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import config
    from core.cases.push import validate_endpoint_url

    monkeypatch.setattr(config.settings, "env", "production")
    for url in ("http://receiver.example.com/hook", "https://127.0.0.1/hook", "https://10.0.0.5/hook"):
        with pytest.raises(CaseError, match="endpoint_url_invalid"):
            validate_endpoint_url(url)


async def test_kicking_delivery_never_blocks_the_event_loop_or_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import threading

    from core.cases import push
    from core.tasks import case_push_tasks

    sent: list[tuple[str, bool]] = []
    done = threading.Event()

    class Task:
        @staticmethod
        def delay(tenant_id: str) -> None:
            sent.append((tenant_id, threading.current_thread() is threading.main_thread()))
            done.set()
            raise ConnectionError("broker unreachable")

    monkeypatch.setattr(case_push_tasks, "dispatch_case_pushes", Task)
    tenant = uuid.UUID(int=3)
    push.kick_dispatch(tenant)
    assert await asyncio.to_thread(done.wait, 5)
    assert sent == [(str(tenant), False)]


async def test_the_sweep_is_a_no_op_until_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import config
    from core.tasks import case_push_tasks

    monkeypatch.setattr(config.settings, "case_push_sweep_enabled", False)
    assert case_push_tasks.sweep_case_pushes.run() == {"skipped": "case_push_sweep_disabled"}
