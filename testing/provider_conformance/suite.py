# SPDX-License-Identifier: Apache-2.0
"""The pytest face of the conformance suite: subclass it and provide ``conformance_target``."""

from __future__ import annotations

import pytest

from .checks import ConformanceSkip, run_check
from .target import ConformanceTarget


class ProviderConformanceSuite:
    """Run every conformance check against one provider.

    Subclass with a name pytest collects (``class TestAcmeKybConformance(ProviderConformanceSuite)``)
    and override the ``conformance_target`` fixture. The tests are synchronous and each runs its
    check in a fresh event loop, so no async pytest plugin is needed. A check that the target cannot
    exercise (for example one that needs a fault injector) is skipped with the reason.
    """

    @pytest.fixture
    def conformance_target(self) -> ConformanceTarget:
        message = "override the conformance_target fixture to return a ConformanceTarget"
        pytest.fail(message)
        raise AssertionError(message)  # pytest.fail never returns; this keeps type checkers without pytest honest

    @staticmethod
    def _run(name: str, target: ConformanceTarget) -> None:
        try:
            run_check(name, target)
        except ConformanceSkip as skip:
            pytest.skip(f"[{name}] {skip.reason}")

    def test_identity(self, conformance_target: ConformanceTarget) -> None:
        self._run("identity", conformance_target)

    def test_capability_honesty(self, conformance_target: ConformanceTarget) -> None:
        self._run("capability_honesty", conformance_target)

    def test_pending_then_result(self, conformance_target: ConformanceTarget) -> None:
        self._run("pending_then_result", conformance_target)

    def test_deadline_expired(self, conformance_target: ConformanceTarget) -> None:
        self._run("deadline_expired", conformance_target)

    def test_deadline_overrun(self, conformance_target: ConformanceTarget) -> None:
        self._run("deadline_overrun", conformance_target)

    def test_cancellation(self, conformance_target: ConformanceTarget) -> None:
        self._run("cancellation", conformance_target)

    def test_error_taxonomy(self, conformance_target: ConformanceTarget) -> None:
        self._run("error_taxonomy", conformance_target)

    def test_webhook_verification(self, conformance_target: ConformanceTarget) -> None:
        self._run("webhook_verification", conformance_target)

    def test_webhook_replay_protection(self, conformance_target: ConformanceTarget) -> None:
        self._run("webhook_replay_protection", conformance_target)

    def test_pagination(self, conformance_target: ConformanceTarget) -> None:
        self._run("pagination", conformance_target)

    def test_idempotency(self, conformance_target: ConformanceTarget) -> None:
        self._run("idempotency", conformance_target)

    def test_schema_conformance(self, conformance_target: ConformanceTarget) -> None:
        self._run("schema_conformance", conformance_target)
