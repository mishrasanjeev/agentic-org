# SPDX-License-Identifier: Apache-2.0
"""Conformance suite for :class:`~connectors.framework.verification_provider.VerificationProvider`.

Published as ``agenticorg.testing.provider_conformance``. A provider package runs it against itself::

    class TestAcmeKybConformance(ProviderConformanceSuite):
        @pytest.fixture
        def conformance_target(self) -> ConformanceTarget: ...

Checks: identity, capability honesty, pending-then-result, expired and overrun deadlines,
cancellation, error taxonomy, webhook verification including forged payloads, pagination,
idempotency of repeated calls, and conformance of outputs to the published schemas. See
``docs/providers/writing-a-verification-provider.md``.

Imports inside this package are relative so it works both from a source checkout
(``testing.provider_conformance``) and installed under ``agenticorg.testing``.
"""

from __future__ import annotations

from typing import Any

from .checks import CHECKS, CheckContext, ConformanceFailure, ConformanceSkip, arun_check, run_check
from .target import ConformanceTarget, FaultInjector, WebhookSample


def __getattr__(name: str) -> Any:
    # The pytest class is loaded on demand so importing the checks does not require pytest.
    if name == "ProviderConformanceSuite":
        from .suite import ProviderConformanceSuite  # noqa: PLC0415

        return ProviderConformanceSuite
    raise AttributeError(name)


__all__ = [
    "CHECKS",
    "CheckContext",
    "ConformanceFailure",
    "ConformanceSkip",
    "ConformanceTarget",
    "FaultInjector",
    "ProviderConformanceSuite",
    "WebhookSample",
    "arun_check",
    "run_check",
]
