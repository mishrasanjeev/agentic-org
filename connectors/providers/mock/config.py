# SPDX-License-Identifier: Apache-2.0
"""Configuration for the mock provider: latency, failure injection, polling and webhook signing.

Everything random is derived from ``seed`` and the call itself (not from call order), so a run is
reproducible even when calls interleave.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from connectors.framework.verification_provider import Capability

#: Development-only placeholder. Any real deployment of the mock sets its own value.
DEV_WEBHOOK_SECRET = "mock-provider-dev-only-webhook-signing-value"


class FaultKind(StrEnum):
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    AUTHENTICATION_FAILED = "authentication_failed"
    RESPONSE_INVALID = "response_invalid"
    #: Never answers; the caller's deadline turns it into ``ProviderTimeout``.
    HANG = "hang"
    #: Answers normally after ``delay_seconds``.
    SLOW = "slow"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class MockConfig:
    seed: int = 0
    #: Uniform latency range per call, in milliseconds.
    latency_ms: tuple[int, int] = (0, 0)
    #: Probability that a call fails with one of ``failure_kinds``.
    failure_rate: float = 0.0
    failure_kinds: tuple[FaultKind, ...] = (FaultKind.UNAVAILABLE, FaultKind.RATE_LIMITED)
    #: How many polls return ``Pending`` before a verification completes.
    polls_until_ready: int = 1
    capabilities: frozenset[Capability] = field(default_factory=lambda: frozenset(Capability))
    webhook_secret: str = DEV_WEBHOOK_SECRET
    webhook_tolerance_seconds: int = 300
    clock: Callable[[], datetime] = _utc_now

    def __post_init__(self) -> None:
        low, high = self.latency_ms
        if not 0 <= low <= high <= 60_000:
            raise ValueError("latency_ms must be 0 <= min <= max <= 60000")
        if not 0.0 <= self.failure_rate <= 1.0:
            raise ValueError("failure_rate must be between 0 and 1")
        if self.failure_rate and not self.failure_kinds:
            raise ValueError("failure_kinds must not be empty when failure_rate is set")
        if not 0 <= self.polls_until_ready <= 100:
            raise ValueError("polls_until_ready must be between 0 and 100")
        if len(self.webhook_secret) < 16:
            raise ValueError("webhook_secret must be at least 16 characters")
        if not 0 < self.webhook_tolerance_seconds <= 3600:
            raise ValueError("webhook_tolerance_seconds must be between 1 and 3600")
        if not all(isinstance(c, Capability) for c in self.capabilities):
            raise ValueError("capabilities must be Capability values")


def unit_draw(seed: int, *parts: str) -> float:
    """A deterministic number in [0, 1) from the seed and the given parts."""
    digest = hashlib.sha256("\x1f".join((str(seed), *parts)).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


class MockProviderSettings(BaseSettings):
    """Environment configuration (``AGENTICORG_MOCK_PROVIDER_*``) for the registered ``mock`` provider and service."""

    model_config = SettingsConfigDict(env_prefix="AGENTICORG_MOCK_PROVIDER_", extra="ignore")

    #: When set, the registered ``mock`` provider talks to the mock service at this URL.
    url: str = ""
    seed: int = 0
    latency_ms: str = "0-0"
    failure_rate: float = Field(0.0, ge=0.0, le=1.0)
    polls_until_ready: int = Field(1, ge=0, le=100)
    capabilities: str = ",".join(c.value for c in Capability)
    webhook_secret: str = DEV_WEBHOOK_SECRET
    #: Enables the service's fault-injection, event and reset endpoints.
    admin: bool = False

    @field_validator("latency_ms")
    @classmethod
    def _latency(cls, value: str) -> str:
        parts = value.split("-")
        if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
            raise ValueError("latency_ms must look like '<min>-<max>' in milliseconds")
        return value

    def capability_set(self) -> frozenset[Capability]:
        names = [item.strip() for item in self.capabilities.split(",") if item.strip()]
        return frozenset(Capability(name) for name in names)

    def to_config(self) -> MockConfig:
        low, high = (int(p) for p in self.latency_ms.split("-"))
        return MockConfig(
            seed=self.seed,
            latency_ms=(low, high),
            failure_rate=self.failure_rate,
            polls_until_ready=self.polls_until_ready,
            capabilities=self.capability_set(),
            webhook_secret=self.webhook_secret,
        )
