# SPDX-License-Identifier: Apache-2.0
"""A-2: the conformance suite passes a conforming provider and fails each deliberately broken one
with a reason a person can act on."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from connectors.framework.verification_provider import (
    BusinessCandidate,
    BusinessQuery,
    BusinessRef,
    BusinessSubject,
    BusinessVerification,
    Capability,
    Deadline,
    Evidence,
    Identifier,
    MonitorAlert,
    MonitorHandle,
    NotFound,
    OwnershipGraph,
    Pending,
    PersonSubject,
    ProviderEvent,
    ProviderEventType,
    ProviderUnavailable,
    ScreeningResult,
    ScreenOptions,
    VerificationHandle,
    VerificationProvider,
    VerifyOptions,
)
from connectors.providers.mock import FaultKind, MockConfig, MockProvider, webhooks
from testing.provider_conformance import (
    CHECKS,
    ConformanceFailure,
    ConformanceSkip,
    ConformanceTarget,
    WebhookSample,
    run_check,
)

KNOWN = BusinessRef(
    provider="mock",
    provider_ref="mock-gb-00000001",
    jurisdiction="GB",
    identifiers=(Identifier(scheme="gb_company_number", value="00000001"),),
)


def target_for(
    provider_cls: type[MockProvider] = MockProvider, *, injector: bool = True, strict: bool = True, **config: Any
) -> ConformanceTarget:
    def genuine(provider: VerificationProvider) -> list[WebhookSample]:
        assert isinstance(provider, MockProvider)
        headers, body = provider.emit_event(KNOWN, ProviderEventType.BUSINESS_DISSOLVED)
        return [WebhookSample(headers, body, "a signed event")]

    def forged(provider: VerificationProvider) -> list[WebhookSample]:
        [sample] = genuine(provider)
        return [WebhookSample(sample.headers, sample.body.replace(b"00000001", b"00000002"), "a forged payload")]

    def stale(provider: VerificationProvider) -> list[WebhookSample]:
        assert isinstance(provider, MockProvider)
        _, body = provider.emit_event(KNOWN, ProviderEventType.BUSINESS_DISSOLVED)
        event_id = json.loads(body)["event_id"]
        headers = webhooks.sign(
            provider.config.webhook_secret, body, event_id=event_id, timestamp=int(time.time()) - 7200
        )
        return [WebhookSample(headers, body, "a delivery signed two hours ago")]

    def prepare(provider: VerificationProvider, handle: MonitorHandle) -> None:
        assert isinstance(provider, MockProvider)
        for _ in range(3):
            provider.emit_event(handle.ref, ProviderEventType.OFFICERS_CHANGED)

    def inject(provider: VerificationProvider, kind: str, capability: Capability | None, delay: float) -> None:
        assert isinstance(provider, MockProvider)
        provider.inject_fault(FaultKind(kind), capability=capability, delay_seconds=delay)

    settings = {"polls_until_ready": 1} | config
    return ConformanceTarget(
        factory=lambda: provider_cls(MockConfig(**settings)),
        known_business=KNOWN,
        resolvable_query=BusinessQuery(
            identifiers=tuple(Identifier(scheme="gb_company_number", value=f"0000000{n}") for n in (1, 2, 3))
        ),
        unknown_business=KNOWN.model_copy(update={"provider_ref": "mock-gb-99999999"}),
        person=PersonSubject(full_name="Radomir Vexley"),
        business=BusinessSubject(legal_name="Corvane Maritime Logistics Ltd"),
        genuine_webhooks=genuine,
        forged_webhooks=forged,
        stale_webhooks=stale,
        fault_injector=inject if injector else None,
        prepare_monitor_alerts=prepare,
        expects_pending=True,
        grace_seconds=0.3,
        strict=strict,
    )


def failure(check: str, target: ConformanceTarget) -> str:
    with pytest.raises(ConformanceFailure) as caught:
        run_check(check, target)
    assert caught.value.check == check
    message = str(caught.value)
    assert message.startswith(f"[{check}] provider ")
    return message


# --- a conforming provider ----------------------------------------------------------------------


@pytest.mark.parametrize("check", list(CHECKS))
def test_the_mock_provider_passes_every_check(check: str) -> None:
    run_check(check, target_for())


@pytest.mark.parametrize("check", list(CHECKS))
def test_a_provider_declaring_only_resolve_and_verify_still_conforms(check: str) -> None:
    narrow = target_for(capabilities=frozenset({Capability.RESOLVE, Capability.VERIFY}))
    run_check(check, narrow)


@pytest.mark.parametrize("check", ["deadline_overrun", "cancellation"])
def test_checks_that_need_a_fault_injector_are_skipped_with_the_reason(check: str) -> None:
    with pytest.raises(ConformanceSkip, match="needs a fault_injector"):
        run_check(check, target_for(injector=False, strict=False))


@pytest.mark.parametrize("check", ["deadline_overrun", "cancellation"])
def test_strict_mode_fails_a_check_it_would_otherwise_skip(check: str) -> None:
    message = failure(check, target_for(injector=False, strict=True))
    assert "skipped in strict mode: needs a fault_injector" in message


def test_monitor_paging_without_an_alert_preparer_is_skipped_or_fails_in_strict_mode() -> None:
    lenient = replace(target_for(strict=False), prepare_monitor_alerts=None)
    with pytest.raises(ConformanceSkip, match="prepare_monitor_alerts"):
        run_check("pagination", lenient)
    assert "skipped in strict mode" in failure("pagination", replace(lenient, strict=True))


def test_unknown_check_names_are_rejected() -> None:
    with pytest.raises(KeyError, match="no conformance check named 'speed'"):
        run_check("speed", target_for())


# --- deliberately broken providers --------------------------------------------------------------


class BadlyNamed(MockProvider):
    name = "Mock Provider"


def test_identity_reports_a_malformed_name() -> None:
    assert "name must match" in failure("identity", target_for(BadlyNamed))


class RaisesNotImplemented(MockProvider):
    async def ownership(self, ref: BusinessRef, *, deadline: Deadline) -> OwnershipGraph:
        raise NotImplementedError


def test_capability_honesty_reports_not_implemented_error() -> None:
    message = failure("capability_honesty", target_for(RaisesNotImplemented))
    assert "ownership raised NotImplementedError" in message
    assert "must raise CapabilityNotSupported" in message


class Overclaims(MockProvider):
    web_presence = VerificationProvider.web_presence  # declared by the config, but not implemented


def test_capability_honesty_reports_a_declared_capability_that_is_not_supported() -> None:
    message = failure("capability_honesty", target_for(Overclaims))
    assert "declares web_presence but web_presence raised CapabilityNotSupported" in message


class Underclaims(MockProvider):
    def require(self, capability: Capability) -> None:  # never refuses
        return None


def test_capability_honesty_reports_an_undeclared_capability_that_answers() -> None:
    target = target_for(Underclaims, capabilities=frozenset(Capability) - {Capability.MONITOR})
    assert "does not declare monitor but monitor_enroll answered" in failure("capability_honesty", target)


class RaisesWhilePending(MockProvider):
    async def verification_result(self, h: VerificationHandle, *, deadline: Deadline) -> BusinessVerification | Pending:
        result = await super().verification_result(h, deadline=deadline)
        if isinstance(result, Pending):
            raise ProviderUnavailable(self.name, "not ready yet")
        return result


def test_pending_then_result_reports_a_pending_state_raised_as_an_error() -> None:
    message = failure("pending_then_result", target_for(RaisesWhilePending))
    assert "raised provider_unavailable while polling; an unfinished verification must return Pending" in message


class FlipFlops(MockProvider):
    async def verification_result(self, h: VerificationHandle, *, deadline: Deadline) -> BusinessVerification | Pending:
        result = await super().verification_result(h, deadline=deadline)
        if isinstance(result, BusinessVerification) and getattr(self, "_answered", False):
            return Pending(handle=h, state="in_progress", retry_after_seconds=0)
        self._answered = isinstance(result, BusinessVerification)
        return result


def test_pending_then_result_reports_a_result_that_reverts_to_pending() -> None:
    assert "went back to Pending" in failure("pending_then_result", target_for(FlipFlops))


class IgnoresDeadlines(MockProvider):
    async def ownership(self, ref: BusinessRef, *, deadline: Deadline) -> OwnershipGraph:
        return await super().ownership(ref, deadline=Deadline.after(60))


def test_deadline_expired_reports_a_provider_that_answers_anyway() -> None:
    assert "ownership answered although its deadline had already passed" in failure(
        "deadline_expired", target_for(IgnoresDeadlines)
    )


def test_deadline_overrun_reports_a_provider_that_keeps_running() -> None:
    assert "ownership ignored its deadline" in failure("deadline_overrun", target_for(IgnoresDeadlines))


class SwallowsCancellation(MockProvider):
    async def ownership(self, ref: BusinessRef, *, deadline: Deadline) -> OwnershipGraph:
        try:
            return await super().ownership(ref, deadline=deadline)
        except asyncio.CancelledError:
            return await super().ownership(ref, deadline=Deadline.after(5))


def test_cancellation_reports_a_swallowed_cancellation() -> None:
    assert "ownership swallowed the cancellation and returned a value" in failure(
        "cancellation", target_for(SwallowsCancellation)
    )


class LeaksRawErrors(MockProvider):
    async def ownership(self, ref: BusinessRef, *, deadline: Deadline) -> OwnershipGraph:
        if ref.provider_ref not in self._records:
            raise KeyError(ref.provider_ref)
        return await super().ownership(ref, deadline=deadline)


def test_error_taxonomy_reports_an_exception_outside_the_taxonomy() -> None:
    assert (
        "ownership for unknown_business raised KeyError, which is not a ProviderError; expected not_found"
        in failure("error_taxonomy", target_for(LeaksRawErrors))
    )


class MisreportsNotFound(MockProvider):
    async def web_presence(self, ref: BusinessRef, *, deadline: Deadline) -> Any:
        try:
            return await super().web_presence(ref, deadline=deadline)
        except NotFound as exc:
            raise ProviderUnavailable(self.name, "lookup failed") from exc


def test_error_taxonomy_reports_the_wrong_reason() -> None:
    assert "web_presence for unknown_business raised provider_unavailable; expected not_found" in failure(
        "error_taxonomy", target_for(MisreportsNotFound)
    )


class TrustsAnyPayload(MockProvider):
    def verify_webhook(self, headers: Any, body: bytes) -> ProviderEvent | None:
        try:
            return ProviderEvent.model_validate_json(body)
        except ValueError:
            return None


def test_webhook_verification_reports_an_accepted_forgery() -> None:
    assert "accepted the body of a signed event with no headers" in failure(
        "webhook_verification", target_for(TrustsAnyPayload)
    )


class RaisesOnGarbage(MockProvider):
    def verify_webhook(self, headers: Any, body: bytes) -> ProviderEvent | None:
        json.loads(body)
        return super().verify_webhook(headers, body)


def test_webhook_verification_reports_a_verifier_that_raises() -> None:
    assert "verify_webhook raised JSONDecodeError" in failure("webhook_verification", target_for(RaisesOnGarbage))


class CaseSensitiveHeaders(MockProvider):
    def verify_webhook(self, headers: Any, body: bytes) -> ProviderEvent | None:
        if "X-Mock-Signature" not in headers:
            return None
        return super().verify_webhook(headers, body)


def test_webhook_verification_reports_case_sensitive_header_names() -> None:
    assert "header names are case-insensitive" in failure("webhook_verification", target_for(CaseSensitiveHeaders))


class IgnoresOffset(MockProvider):
    async def resolve_business(self, q: BusinessQuery, *, deadline: Deadline) -> list[BusinessCandidate]:
        return await super().resolve_business(q.model_copy(update={"offset": 0}), deadline=deadline)


def test_pagination_reports_ignored_offsets() -> None:
    assert "did not reproduce the unpaged result" in failure("pagination", target_for(IgnoresOffset))


class StartsAgainEachTime(MockProvider):
    async def verify_business(self, ref: BusinessRef, opts: VerifyOptions, *, deadline: Deadline) -> VerificationHandle:
        calls = getattr(self, "_calls", 0) + 1
        self._calls = calls
        fresh = opts.model_copy(update={"idempotency_key": f"{opts.idempotency_key}-{calls}"})
        return await super().verify_business(ref, fresh, deadline=deadline)


def test_idempotency_reports_a_repeated_start_that_starts_a_new_job() -> None:
    assert "verify_business: the same idempotency key started a second job" in failure(
        "idempotency", target_for(StartsAgainEachTime)
    )


class CitesSomeoneElse(MockProvider):
    async def screen_person(self, s: PersonSubject, opts: ScreenOptions, *, deadline: Deadline) -> ScreeningResult:
        result = await super().screen_person(s, opts, deadline=deadline)
        borrowed = Evidence(provider="acme_kyb", record_id="acme:1", field="hits", retrieved_at=result.screened_at)
        return result.model_copy(update={"evidence": (borrowed,)})


def test_schema_conformance_reports_evidence_cited_from_another_provider() -> None:
    assert "screen_person cites evidence from provider 'acme_kyb'" in failure(
        "schema_conformance", target_for(CitesSomeoneElse)
    )


class OffSchema(MockProvider):
    async def ownership(self, ref: BusinessRef, *, deadline: Deadline) -> OwnershipGraph:
        graph = await super().ownership(ref, deadline=deadline)
        return OwnershipGraph.model_construct(**{**dict(graph), "schema_version": "2.0.0"})


def test_schema_conformance_reports_output_that_breaks_the_published_schema() -> None:
    assert "ownership output does not match the ownership_graph schema" in failure(
        "schema_conformance", target_for(OffSchema)
    )


# --- the pytest suite class ---------------------------------------------------------------------


from testing.provider_conformance import ProviderConformanceSuite  # noqa: E402 - loaded on demand by the package


class TestMockProviderConformanceInProcess(ProviderConformanceSuite):
    """The suite class itself, run in this process against the mock."""

    @pytest.fixture
    def conformance_target(self) -> ConformanceTarget:
        return target_for()


def test_the_suite_class_turns_an_unexercisable_check_into_a_pytest_skip() -> None:
    with pytest.raises(pytest.skip.Exception, match=r"\[cancellation\] needs a fault_injector"):
        ProviderConformanceSuite._run("cancellation", target_for(injector=False, strict=False))


def test_the_suite_class_reports_failures_as_assertion_errors() -> None:
    with pytest.raises(AssertionError, match=r"^\[identity\] provider 'Mock Provider': name must match"):
        ProviderConformanceSuite._run("identity", target_for(BadlyNamed))


def test_the_package_exposes_only_known_names() -> None:
    import testing.provider_conformance as suite

    with pytest.raises(AttributeError):
        _ = suite.NoSuchThing  # type: ignore[attr-defined]


# --- review follow-ups: expired deadlines on polls, replay protection, alert paging, timestamps --


class PollsIgnoreDeadlines(MockProvider):
    async def verification_result(self, h: VerificationHandle, *, deadline: Deadline) -> BusinessVerification | Pending:
        return await super().verification_result(h, deadline=Deadline.after(60))


def test_deadline_expired_probes_verification_result() -> None:
    assert "verification_result answered although its deadline had already passed" in failure(
        "deadline_expired", target_for(PollsIgnoreDeadlines)
    )


class AlertReadsIgnoreDeadlines(MockProvider):
    async def monitor_result(self, h: MonitorHandle, *, deadline: Deadline) -> list[MonitorAlert]:
        return await super().monitor_result(h, deadline=Deadline.after(60))


def test_deadline_expired_probes_monitor_result() -> None:
    assert "monitor_result answered although its deadline had already passed" in failure(
        "deadline_expired", target_for(AlertReadsIgnoreDeadlines)
    )


class IgnoresSignatureAge(MockProvider):
    def verify_webhook(self, headers: Any, body: bytes) -> ProviderEvent | None:
        return webhooks.verify(
            headers,
            body,
            secrets=(self.config.webhook_secret,),
            provider=self.name,
            now=int(time.time()),
            tolerance_seconds=10**9,
        )


def test_webhook_replay_protection_reports_an_accepted_stale_delivery() -> None:
    assert "accepted a delivery signed two hours ago; a signed delivery outside the time window is a replay" in failure(
        "webhook_replay_protection", target_for(IgnoresSignatureAge)
    )


class UnstableEventIds(MockProvider):
    def verify_webhook(self, headers: Any, body: bytes) -> ProviderEvent | None:
        event = super().verify_webhook(headers, body)
        if event is None:
            return None
        count = getattr(self, "_seen", 0) + 1
        self._seen = count
        return event.model_copy(update={"event_id": f"{event.event_id}-{count}"})


def test_webhook_replay_protection_reports_unstable_event_ids() -> None:
    assert "the event_id must be stable so a replay is detectable" in failure(
        "webhook_replay_protection", target_for(UnstableEventIds)
    )


class IgnoresAlertOffset(MockProvider):
    async def monitor_result(self, h: MonitorHandle, *, deadline: Deadline) -> list[MonitorAlert]:
        return await super().monitor_result(h.model_copy(update={"offset": 0}), deadline=deadline)


def test_pagination_reports_ignored_alert_offsets() -> None:
    assert "walking monitor_result pages of limit=1 by offset did not reproduce" in failure(
        "pagination", target_for(IgnoresAlertOffset)
    )


class ReadsAtCallTime(MockProvider):
    """Stamps every read with the time of the call, as a live source would."""

    async def ownership(self, ref: BusinessRef, *, deadline: Deadline) -> OwnershipGraph:
        graph = await super().ownership(ref, deadline=deadline)
        now = datetime.now(UTC)
        stamp = {"retrieved_at": now}
        return graph.model_copy(
            update={"as_of": now, "evidence": tuple(e.model_copy(update=stamp) for e in graph.evidence)}
        )


def test_idempotency_ignores_when_an_unkeyed_read_happened() -> None:
    run_check("idempotency", target_for(ReadsAtCallTime))


class ReordersOwners(MockProvider):
    async def ownership(self, ref: BusinessRef, *, deadline: Deadline) -> OwnershipGraph:
        graph = await super().ownership(ref, deadline=deadline)
        calls = getattr(self, "_calls", 0) + 1
        self._calls = calls
        return graph if calls % 2 else graph.model_copy(update={"nodes": tuple(reversed(graph.nodes))})


def test_idempotency_still_reports_a_changed_answer() -> None:
    assert "ownership: a repeated call returned a different answer" in failure(
        "idempotency", target_for(ReordersOwners)
    )
