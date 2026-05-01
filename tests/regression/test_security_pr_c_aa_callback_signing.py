"""SEC-2026-05-P1-004 PR-C: AA callback signing + replay protection pins.

Pins the HMAC-SHA256 + timestamp-freshness + nonce-replay defense for
the Account Aggregator consent callback so the bug class — a public,
unsigned callback that updates financial-consent state — can't recur.

Tested behaviors:

- Unsigned callback (missing all three headers) → 403.
- Wrong-secret signature → 403.
- Stale timestamp (outside ±5 min) → 403.
- Replayed nonce → 409.
- Bad nonce shape (empty / too long / non-ASCII) → 403.
- Missing AGENTICORG_AA_CALLBACK_SECRET in non-local env → 403 (fail closed).
- Dev bypass works in local/dev/test env (matches PR-A's env-guard).
- Valid signed callback → ``ok`` verdict (caller proceeds to parse body).
- Constant-time compare is used (source-grep).

Hermetic by design — no real Redis, no real AA provider. Uses an
in-memory async fake that implements only the Redis ``set(key, value,
nx=True, ex=...)`` semantics ``verify_aa_callback`` relies on.
"""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from auth.aa_callback_signing import (
    MAX_TIMESTAMP_SKEW_SECONDS,
    NONCE_TTL_SECONDS,
    SIGNATURE_VERSION,
    _compute_signature,
    verify_aa_callback,
)

# ─────────────────────────────────────────────────────────────────
# Test fixtures: fake Redis + signed-request builder
# ─────────────────────────────────────────────────────────────────


class _FakeRedis:
    """Minimal async Redis double that supports set(nx=, ex=) atomically.

    Real Redis ``SET k v NX EX <ttl>`` is atomic — the fake replicates
    that contract for our replay test by holding a dict + recording
    seen keys. TTL is stored but not enforced (tests are fast enough
    that no key would expire mid-test).
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.fail_next: bool = False

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("simulated redis blip")
        if nx and key in self.store:
            return None
        self.store[key] = value
        # Treat ex as advisory — fake doesn't expire keys.
        _ = ex
        return True

    async def close(self) -> None:
        return None


@pytest.fixture
def redis_client():
    return _FakeRedis()


@pytest.fixture(autouse=True)
def _set_aa_secret(monkeypatch: pytest.MonkeyPatch):
    """Ensure every test starts with a known-good AA secret + test env.

    Tests that explicitly want the secret unset / bypass active
    monkeypatch from there.
    """
    monkeypatch.setenv("AGENTICORG_AA_CALLBACK_SECRET", "test-aa-secret-value")
    monkeypatch.setenv("AGENTICORG_ENV", "test")
    monkeypatch.delenv("AGENTICORG_WEBHOOK_ALLOW_UNSIGNED", raising=False)


def _signed_request(
    body: bytes = b'{"consent_handle":"abc","consent_status":"ACTIVE"}',
    timestamp: int | None = None,
    nonce: str = "test-nonce-001",
    secret: str = "test-aa-secret-value",  # noqa: S107 — test fixture, not a real credential
) -> tuple[str, str, str, bytes]:
    """Return (timestamp_header, nonce_header, signature_header, body) for a valid request."""
    ts = str(timestamp if timestamp is not None else int(time.time()))
    sig = _compute_signature(secret, ts, nonce, body)
    return ts, nonce, sig, body


# ─────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_valid_signed_callback_returns_ok(redis_client: _FakeRedis) -> None:
    ts, nonce, sig, body = _signed_request()
    verdict = await verify_aa_callback(
        timestamp_header=ts,
        nonce_header=nonce,
        signature_header=sig,
        body=body,
        redis_client=redis_client,
    )
    assert verdict == "ok"


# ─────────────────────────────────────────────────────────────────
# Missing / malformed input → 403 verdicts
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unsigned_callback_returns_missing_signature(redis_client: _FakeRedis) -> None:
    """All three headers absent → caller returns 403."""
    verdict = await verify_aa_callback(
        timestamp_header=None,
        nonce_header=None,
        signature_header=None,
        body=b"{}",
        redis_client=redis_client,
    )
    assert verdict == "missing_signature"


@pytest.mark.asyncio
async def test_only_signature_header_returns_missing_signature(
    redis_client: _FakeRedis,
) -> None:
    """One header present, others missing — still 403."""
    verdict = await verify_aa_callback(
        timestamp_header="123",
        nonce_header=None,
        signature_header="v1=deadbeef",
        body=b"{}",
        redis_client=redis_client,
    )
    assert verdict == "missing_signature"


@pytest.mark.asyncio
async def test_wrong_secret_signature_returns_bad_signature(
    redis_client: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compute signature with a different secret → bad_signature."""
    ts, nonce, _good_sig, body = _signed_request()
    bad_sig = _compute_signature("different-secret", ts, nonce, body)
    verdict = await verify_aa_callback(
        timestamp_header=ts,
        nonce_header=nonce,
        signature_header=bad_sig,
        body=body,
        redis_client=redis_client,
    )
    assert verdict == "bad_signature"


@pytest.mark.asyncio
async def test_tampered_body_returns_bad_signature(
    redis_client: _FakeRedis,
) -> None:
    """Body changed after signing → bad_signature. Pins that the body
    is part of the canonical string (not just headers)."""
    ts, nonce, sig, _good_body = _signed_request()
    tampered_body = b'{"consent_handle":"DIFFERENT","consent_status":"ACTIVE"}'
    verdict = await verify_aa_callback(
        timestamp_header=ts,
        nonce_header=nonce,
        signature_header=sig,
        body=tampered_body,
        redis_client=redis_client,
    )
    assert verdict == "bad_signature"


# ─────────────────────────────────────────────────────────────────
# Timestamp freshness
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stale_timestamp_returns_stale_timestamp(redis_client: _FakeRedis) -> None:
    """Timestamp older than the skew window → stale_timestamp."""
    old_ts = int(time.time()) - (MAX_TIMESTAMP_SKEW_SECONDS + 60)
    ts, nonce, sig, body = _signed_request(timestamp=old_ts)
    verdict = await verify_aa_callback(
        timestamp_header=ts,
        nonce_header=nonce,
        signature_header=sig,
        body=body,
        redis_client=redis_client,
    )
    assert verdict == "stale_timestamp"


@pytest.mark.asyncio
async def test_future_timestamp_outside_window_returns_stale(redis_client: _FakeRedis) -> None:
    """Timestamps too far in the FUTURE are also rejected — protects
    against attacker-controlled clocks."""
    future_ts = int(time.time()) + (MAX_TIMESTAMP_SKEW_SECONDS + 60)
    ts, nonce, sig, body = _signed_request(timestamp=future_ts)
    verdict = await verify_aa_callback(
        timestamp_header=ts,
        nonce_header=nonce,
        signature_header=sig,
        body=body,
        redis_client=redis_client,
    )
    assert verdict == "stale_timestamp"


@pytest.mark.asyncio
async def test_garbage_timestamp_returns_stale(redis_client: _FakeRedis) -> None:
    """Non-numeric timestamp → stale_timestamp (not 500 / not bad_signature)."""
    _, nonce, sig, body = _signed_request()
    verdict = await verify_aa_callback(
        timestamp_header="not-a-number",
        nonce_header=nonce,
        signature_header=sig,
        body=body,
        redis_client=redis_client,
    )
    assert verdict == "stale_timestamp"


# ─────────────────────────────────────────────────────────────────
# Nonce shape + replay defense
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_nonce_returns_missing_signature(redis_client: _FakeRedis) -> None:
    """An empty header is treated the same as a missing one — falls
    into ``missing_signature`` before the nonce-shape check. That's
    correct: empty is functionally absent, not 'bad_nonce'."""
    ts, _nonce, _sig, body = _signed_request(nonce="")
    sig = _compute_signature("test-aa-secret-value", ts, "", body)
    verdict = await verify_aa_callback(
        timestamp_header=ts,
        nonce_header="",
        signature_header=sig,
        body=body,
        redis_client=redis_client,
    )
    assert verdict == "missing_signature"


@pytest.mark.asyncio
async def test_non_ascii_nonce_returns_bad_nonce(redis_client: _FakeRedis) -> None:
    """Non-ASCII nonce → bad_nonce. Pins that the shape check runs
    after the presence check."""
    bad_nonce = "néonce"  # contains non-ASCII char
    ts, _, _, body = _signed_request(nonce=bad_nonce)
    sig = _compute_signature("test-aa-secret-value", ts, bad_nonce, body)
    verdict = await verify_aa_callback(
        timestamp_header=ts,
        nonce_header=bad_nonce,
        signature_header=sig,
        body=body,
        redis_client=redis_client,
    )
    assert verdict == "bad_nonce"


@pytest.mark.asyncio
async def test_overlong_nonce_returns_bad_nonce(redis_client: _FakeRedis) -> None:
    """Nonce > 128 chars → bad_nonce. Protects Redis key namespace
    against attacker key-explosion attempts."""
    long_nonce = "a" * 200
    ts, _, _, body = _signed_request(nonce=long_nonce)
    sig = _compute_signature("test-aa-secret-value", ts, long_nonce, body)
    verdict = await verify_aa_callback(
        timestamp_header=ts,
        nonce_header=long_nonce,
        signature_header=sig,
        body=body,
        redis_client=redis_client,
    )
    assert verdict == "bad_nonce"


@pytest.mark.asyncio
async def test_replayed_nonce_returns_replay(redis_client: _FakeRedis) -> None:
    """Same nonce twice in the freshness window → second call is replay."""
    ts, nonce, sig, body = _signed_request()

    first = await verify_aa_callback(
        timestamp_header=ts,
        nonce_header=nonce,
        signature_header=sig,
        body=body,
        redis_client=redis_client,
    )
    assert first == "ok"

    # Same nonce, same body — second attempt is a replay.
    second = await verify_aa_callback(
        timestamp_header=ts,
        nonce_header=nonce,
        signature_header=sig,
        body=body,
        redis_client=redis_client,
    )
    assert second == "replay"


@pytest.mark.asyncio
async def test_redis_outage_falls_back_to_timestamp_only(
    redis_client: _FakeRedis,
) -> None:
    """Redis unreachable → degrade to timestamp-freshness defense, do
    NOT refuse all callbacks. Pins the explicit best-effort design
    note in verify_aa_callback's docstring."""
    redis_client.fail_next = True
    ts, nonce, sig, body = _signed_request()
    verdict = await verify_aa_callback(
        timestamp_header=ts,
        nonce_header=nonce,
        signature_header=sig,
        body=body,
        redis_client=redis_client,
    )
    # Replay defense degraded but the timestamp + signature checks
    # still passed — caller proceeds.
    assert verdict == "ok"


# ─────────────────────────────────────────────────────────────────
# Secret-missing fail-closed + dev-bypass behavior
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_secret_in_test_env_without_bypass_returns_missing_secret(
    redis_client: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``AGENTICORG_AA_CALLBACK_SECRET`` unset + bypass not active →
    missing_secret (caller returns 403). Fails CLOSED."""
    monkeypatch.delenv("AGENTICORG_AA_CALLBACK_SECRET", raising=False)
    monkeypatch.delenv("AGENTICORG_WEBHOOK_ALLOW_UNSIGNED", raising=False)
    ts, nonce, sig, body = _signed_request()
    verdict = await verify_aa_callback(
        timestamp_header=ts,
        nonce_header=nonce,
        signature_header=sig,
        body=body,
        redis_client=redis_client,
    )
    assert verdict == "missing_secret"


@pytest.mark.asyncio
async def test_missing_secret_in_local_with_bypass_returns_ok(
    redis_client: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Local dev with the bypass flag → secret unset is acceptable.
    Mirrors the SendGrid/Mailchimp/MoEngage convention."""
    monkeypatch.delenv("AGENTICORG_AA_CALLBACK_SECRET", raising=False)
    monkeypatch.setenv("AGENTICORG_ENV", "local")
    monkeypatch.setenv("AGENTICORG_WEBHOOK_ALLOW_UNSIGNED", "1")
    ts, nonce, sig, body = _signed_request()
    verdict = await verify_aa_callback(
        timestamp_header=ts,
        nonce_header=nonce,
        signature_header=sig,
        body=body,
        redis_client=redis_client,
    )
    assert verdict == "ok"


# ─────────────────────────────────────────────────────────────────
# Source pins — protect the contract from silent regression
# ─────────────────────────────────────────────────────────────────


def test_signing_module_uses_constant_time_compare() -> None:
    """SEC-2026-05-P1-004: a regular ``==`` on the signature would leak
    bytes via timing. Pin that ``hmac.compare_digest`` is the only
    primitive used."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "auth" / "aa_callback_signing.py"
    ).read_text(encoding="utf-8")
    assert "hmac.compare_digest" in src
    # Forbid a literal ``signature == expected`` style anywhere.
    forbidden = ("signature == ", "expected == ", "== signature_header", "== expected")
    for needle in forbidden:
        assert needle not in src, (
            f"auth/aa_callback_signing.py contains forbidden pattern "
            f"{needle!r} — use hmac.compare_digest for constant-time compare."
        )


def test_canonical_string_includes_version_prefix() -> None:
    """Pin that the signature scheme is versioned. The ``v1.`` prefix
    lets us rotate the algorithm later (e.g. add a body-hash step)
    without ambiguity — the verifier checks the prefix on the
    signature header."""
    sig = _compute_signature("k", "1700000000", "abc", b"body")
    assert sig.startswith(f"{SIGNATURE_VERSION}=")


def test_signature_is_hmac_sha256() -> None:
    """Defensive pin: confirm the algorithm matches the docstring + the
    SendGrid pattern. Future contributors can't silently swap in
    SHA-1 / MD5 without breaking this test."""
    body = b"hello"
    expected = hmac.new(b"k", b"v1.1700000000.n.hello", hashlib.sha256).hexdigest()
    actual = _compute_signature("k", "1700000000", "n", body)
    assert actual == f"{SIGNATURE_VERSION}={expected}"


def test_nonce_ttl_covers_2x_skew_window() -> None:
    """The replay-store TTL must be ≥ 2× the timestamp skew so a
    replay inside the freshness window can't slip through after the
    nonce key expires."""
    assert NONCE_TTL_SECONDS >= 2 * MAX_TIMESTAMP_SKEW_SECONDS
