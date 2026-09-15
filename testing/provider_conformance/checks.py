# SPDX-License-Identifier: Apache-2.0
"""The conformance checks. Each is an async function of a :class:`CheckContext` that returns when the
provider conforms and raises :class:`ConformanceFailure` - with the check id, the provider and a
reason a person can act on - when it does not, or :class:`ConformanceSkip` when the target cannot
exercise it.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, NoReturn

from pydantic import BaseModel

from connectors.framework.verification_provider import (
    CAPABILITY_METHODS,
    BusinessCandidate,
    BusinessQuery,
    BusinessVerification,
    Capability,
    CapabilityNotSupported,
    Deadline,
    Evidence,
    InvalidQuery,
    MonitorHandle,
    MonitorOptions,
    NotFound,
    OwnershipGraph,
    Pending,
    ProviderError,
    ProviderEvent,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
    ScreeningResult,
    ScreenOptions,
    VerificationHandle,
    VerificationProvider,
    VerifyOptions,
    WebPresence,
)

from .target import ConformanceTarget, FaultKindName, WebhookSample, resolve_maybe_awaitable

_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
CALL_SECONDS = 30.0
SHORT_DEADLINE_SECONDS = 0.2


class ConformanceFailure(AssertionError):  # noqa: N818 - reads as a test failure
    def __init__(self, check: str, provider: str, reason: str) -> None:
        self.check = check
        self.provider = provider
        self.reason = reason
        super().__init__(f"[{check}] provider {provider!r}: {reason}")


class ConformanceSkip(Exception):  # noqa: N818 - a skip, not an error
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class CheckContext:
    check: str
    target: ConformanceTarget
    provider: VerificationProvider

    @property
    def name(self) -> str:
        return str(getattr(self.provider, "name", "<unnamed>"))

    def fail(self, reason: str) -> NoReturn:
        raise ConformanceFailure(self.check, self.name, reason)

    def key(self, label: str) -> str:
        return f"{self.target.run_id}-{self.check}-{label}"[:128]

    def declared(self) -> list[Capability]:
        return [c for c in Capability if c in self.provider.capabilities]

    async def inject(self, kind: FaultKindName, capability: Capability | None, delay_seconds: float = 0.0) -> None:
        injector = self.target.fault_injector
        if injector is None:
            raise ConformanceSkip(f"needs a fault_injector that supports {kind!r}")
        await resolve_maybe_awaitable(injector(self.provider, kind, capability, delay_seconds))


def _method(capability: Capability) -> str:
    return CAPABILITY_METHODS[capability][0]


def _probe(ctx: CheckContext, capability: Capability, deadline: Deadline, label: str = "probe") -> Awaitable[Any]:
    """The call that exercises ``capability`` with the target's inputs."""
    p, t = ctx.provider, ctx.target
    if capability is Capability.RESOLVE:
        return p.resolve_business(t.resolvable_query, deadline=deadline)
    if capability is Capability.VERIFY:
        return p.verify_business(t.known_business, VerifyOptions(idempotency_key=ctx.key(label)), deadline=deadline)
    if capability is Capability.OWNERSHIP:
        return p.ownership(t.known_business, deadline=deadline)
    if capability is Capability.SCREEN_PERSON:
        return p.screen_person(t.person, ScreenOptions(idempotency_key=ctx.key(label)), deadline=deadline)
    if capability is Capability.SCREEN_BUSINESS:
        return p.screen_business(t.business, ScreenOptions(idempotency_key=ctx.key(label)), deadline=deadline)
    if capability is Capability.WEB_PRESENCE:
        return p.web_presence(t.known_business, deadline=deadline)
    return p.monitor_enroll(t.known_business, MonitorOptions(idempotency_key=ctx.key(label)), deadline=deadline)


_EXPECTED_TYPE: dict[Capability, type] = {
    Capability.RESOLVE: list,
    Capability.VERIFY: VerificationHandle,
    Capability.OWNERSHIP: OwnershipGraph,
    Capability.SCREEN_PERSON: ScreeningResult,
    Capability.SCREEN_BUSINESS: ScreeningResult,
    Capability.WEB_PRESENCE: WebPresence,
    Capability.MONITOR: MonitorHandle,
}


def _absent_verification(ctx: CheckContext) -> VerificationHandle:
    return VerificationHandle(
        provider=ctx.name if _NAME.match(ctx.name) else "unnamed_provider",
        verification_id=f"{ctx.target.run_id}-absent-verification",
        ref=ctx.target.known_business,
        requested_at=datetime.now(UTC),
    )


def _absent_monitor(ctx: CheckContext) -> MonitorHandle:
    return MonitorHandle(
        provider=ctx.name if _NAME.match(ctx.name) else "unnamed_provider",
        monitor_id=f"{ctx.target.run_id}-absent-monitor",
        ref=ctx.target.known_business,
        enrolled_at=datetime.now(UTC),
    )


async def _expect_error(
    ctx: CheckContext, label: str, call: Callable[[], Awaitable[Any]], expected: type[ProviderError]
) -> ProviderError:
    try:
        await asyncio.wait_for(call(), timeout=CALL_SECONDS)
    except expected as exc:
        if exc.provider != ctx.name:
            ctx.fail(f"{label} raised {exc.reason} naming provider {exc.provider!r}; errors must name this provider")
        return exc
    except ProviderError as exc:
        ctx.fail(f"{label} raised {exc.reason}; expected {expected.reason}")
    except TimeoutError:
        ctx.fail(f"{label} did not answer within {CALL_SECONDS:.0f}s")
    except NotImplementedError:
        ctx.fail(f"{label} raised NotImplementedError; expected {expected.reason}")
    except Exception as exc:  # noqa: BLE001 - any other exception is exactly what this check reports
        ctx.fail(f"{label} raised {type(exc).__name__}, which is not a ProviderError; expected {expected.reason}")
    ctx.fail(f"{label} answered; expected {expected.reason}")


async def _call(ctx: CheckContext, label: str, call: Awaitable[Any]) -> Any:
    try:
        return await asyncio.wait_for(call, timeout=CALL_SECONDS)
    except ProviderError as exc:
        ctx.fail(f"{label} raised {exc.reason} for the target's inputs: {exc}")
    except TimeoutError:
        ctx.fail(f"{label} did not answer within {CALL_SECONDS:.0f}s")
    except Exception as exc:  # noqa: BLE001 - any other exception is exactly what this check reports
        ctx.fail(f"{label} raised {type(exc).__name__}, which is not a ProviderError: {exc}")


async def _poll_to_completion(ctx: CheckContext, handle: VerificationHandle) -> tuple[BusinessVerification, int]:
    give_up = time.monotonic() + ctx.target.poll_timeout_seconds
    pending = 0
    while True:
        try:
            result = await asyncio.wait_for(
                ctx.provider.verification_result(handle, deadline=Deadline.after(CALL_SECONDS)), timeout=CALL_SECONDS
            )
        except ProviderError as exc:
            ctx.fail(
                f"verification_result raised {exc.reason} while polling; an unfinished verification must return Pending"
            )
        except Exception as exc:  # noqa: BLE001 - any other exception is exactly what this check reports
            ctx.fail(f"verification_result raised {type(exc).__name__} while polling; return Pending instead")
        if isinstance(result, BusinessVerification):
            return result, pending
        if not isinstance(result, Pending):
            ctx.fail(f"verification_result returned {type(result).__name__}; expected Pending or BusinessVerification")
        if result.handle != handle:
            ctx.fail("Pending carries a different handle from the one being polled")
        pending += 1
        if time.monotonic() > give_up:
            ctx.fail(f"verification still Pending after {ctx.target.poll_timeout_seconds:.0f}s ({pending} polls)")
        await asyncio.sleep(min(result.retry_after_seconds, 1.0))


def _evidence(value: Any) -> Iterator[Evidence]:
    if isinstance(value, Evidence):
        yield value
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from _evidence(getattr(value, name))
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _evidence(item)


# --- checks -------------------------------------------------------------------------------------


async def check_identity(ctx: CheckContext) -> None:
    if not isinstance(ctx.provider, VerificationProvider):
        ctx.fail(f"factory returned {type(ctx.provider).__name__}, which is not a VerificationProvider")
    if not _NAME.match(ctx.name):
        ctx.fail(f"name must match {_NAME.pattern}")
    capabilities = getattr(ctx.provider, "capabilities", None)
    if not isinstance(capabilities, frozenset) or not all(isinstance(c, Capability) for c in capabilities):
        ctx.fail(f"capabilities must be a frozenset of Capability, got {capabilities!r}")


async def check_capability_honesty(ctx: CheckContext) -> None:
    for capability in Capability:
        declared = capability in ctx.provider.capabilities
        method = _method(capability)
        try:
            result = await asyncio.wait_for(_probe(ctx, capability, Deadline.after(CALL_SECONDS)), timeout=CALL_SECONDS)
        except CapabilityNotSupported as exc:
            if declared:
                ctx.fail(f"declares {capability.value} but {method} raised CapabilityNotSupported")
            if exc.capability is not capability:
                ctx.fail(f"{method} raised CapabilityNotSupported for {exc.capability}, expected {capability.value}")
            await _undeclared_follow_up(ctx, capability)
            continue
        except NotImplementedError:
            ctx.fail(
                f"{method} raised NotImplementedError; a capability that is not offered must raise "
                "CapabilityNotSupported"
            )
        except ProviderError as exc:
            if declared:
                ctx.fail(f"declares {capability.value} but {method} failed with {exc.reason} for the target's inputs")
            ctx.fail(
                f"does not declare {capability.value} but {method} raised {exc.reason}, not CapabilityNotSupported"
            )
        except TimeoutError:
            ctx.fail(f"{method} did not answer within {CALL_SECONDS:.0f}s")
        except Exception as exc:  # noqa: BLE001 - any other exception is exactly what this check reports
            ctx.fail(f"{method} raised {type(exc).__name__}, which is not a ProviderError")
        if not declared:
            ctx.fail(f"does not declare {capability.value} but {method} answered; raise CapabilityNotSupported")
        expected = _EXPECTED_TYPE[capability]
        if not isinstance(result, expected):
            ctx.fail(f"{method} returned {type(result).__name__}, expected {expected.__name__}")
        if isinstance(result, list) and not all(isinstance(c, BusinessCandidate) for c in result):
            ctx.fail("resolve_business returned something other than BusinessCandidate values")


async def _undeclared_follow_up(ctx: CheckContext, capability: Capability) -> None:
    follow_up: Callable[[], Awaitable[Any]] | None = None
    if capability is Capability.VERIFY:
        handle = _absent_verification(ctx)

        def follow_up() -> Awaitable[Any]:
            return ctx.provider.verification_result(handle, deadline=Deadline.after(CALL_SECONDS))

    elif capability is Capability.MONITOR:
        monitor = _absent_monitor(ctx)

        def follow_up() -> Awaitable[Any]:
            return ctx.provider.monitor_result(monitor, deadline=Deadline.after(CALL_SECONDS))

    if follow_up is not None:
        await _expect_error(ctx, CAPABILITY_METHODS[capability][1], follow_up, CapabilityNotSupported)


async def check_pending_then_result(ctx: CheckContext) -> None:
    if Capability.VERIFY not in ctx.provider.capabilities:
        raise ConformanceSkip("the provider does not declare verify")
    handle = await _call(ctx, "verify_business", _probe(ctx, Capability.VERIFY, Deadline.after(CALL_SECONDS)))
    if not isinstance(handle, VerificationHandle) or handle.provider != ctx.name:
        ctx.fail("verify_business must return a VerificationHandle naming this provider")
    result, pending = await _poll_to_completion(ctx, handle)
    if ctx.target.expects_pending and pending == 0:
        ctx.fail("the target expects at least one Pending before the result, but the first poll returned the result")
    if result.handle != handle:
        ctx.fail("the result carries a different handle from the one polled")
    for _ in range(2):
        again = await _call(
            ctx, "verification_result", ctx.provider.verification_result(handle, deadline=Deadline.after(CALL_SECONDS))
        )
        if isinstance(again, Pending):
            ctx.fail("verification_result went back to Pending after returning a result")
        if again != result:
            ctx.fail("verification_result changed after returning a result")


async def check_deadline_expired(ctx: CheckContext) -> None:
    grace = ctx.target.grace_seconds
    for capability in ctx.declared():
        method = _method(capability)
        started = time.monotonic()
        try:
            await asyncio.wait_for(_probe(ctx, capability, Deadline(time.monotonic() - 1)), timeout=grace * 5)
        except ProviderTimeout:
            elapsed = time.monotonic() - started
            if elapsed > grace:
                ctx.fail(f"{method} took {elapsed:.2f}s to report an already-expired deadline")
            continue
        except TimeoutError:
            ctx.fail(f"{method} kept running past an already-expired deadline")
        except ProviderError as exc:
            ctx.fail(f"{method} with an expired deadline raised {exc.reason}; expected provider_timeout")
        except Exception as exc:  # noqa: BLE001 - any other exception is exactly what this check reports
            ctx.fail(f"{method} with an expired deadline raised {type(exc).__name__}; expected ProviderTimeout")
        ctx.fail(f"{method} answered although its deadline had already passed; expected ProviderTimeout")


async def check_deadline_overrun(ctx: CheckContext) -> None:
    grace = ctx.target.grace_seconds
    for capability in ctx.declared():
        method = _method(capability)
        await ctx.inject("hang", capability)
        started = time.monotonic()
        try:
            await asyncio.wait_for(
                _probe(ctx, capability, Deadline.after(SHORT_DEADLINE_SECONDS), label=f"overrun-{capability.value}"),
                timeout=SHORT_DEADLINE_SECONDS + grace * 5,
            )
        except ProviderTimeout:
            elapsed = time.monotonic() - started
            if elapsed > SHORT_DEADLINE_SECONDS + grace:
                ctx.fail(f"{method} reported the timeout {elapsed:.2f}s after a {SHORT_DEADLINE_SECONDS}s deadline")
            continue
        except TimeoutError:
            ctx.fail(
                f"{method} ignored its deadline and was still running {SHORT_DEADLINE_SECONDS + grace * 5:.1f}s later"
            )
        except ProviderError as exc:
            ctx.fail(f"{method} raised {exc.reason} when its deadline passed; expected provider_timeout")
        except Exception as exc:  # noqa: BLE001 - any other exception is exactly what this check reports
            ctx.fail(f"{method} raised {type(exc).__name__} when its deadline passed; expected ProviderTimeout")
        ctx.fail(f"{method} answered although the provider was made to hang; the fault injector may not work")


async def check_cancellation(ctx: CheckContext) -> None:
    declared = ctx.declared()
    if not declared:
        raise ConformanceSkip("the provider declares no capabilities")
    capability = Capability.OWNERSHIP if Capability.OWNERSHIP in declared else declared[0]
    method = _method(capability)
    grace = ctx.target.grace_seconds
    await ctx.inject("slow", capability, delay_seconds=5.0)
    task = asyncio.ensure_future(_probe(ctx, capability, Deadline.after(CALL_SECONDS), label="cancellation"))
    await asyncio.sleep(0.1)
    if task.done():
        ctx.fail(f"{method} finished although the provider was made to answer slowly; the fault injector may not work")
    task.cancel()
    done, _ = await asyncio.wait({task}, timeout=grace)
    if not done:
        ctx.fail(f"{method} was still running {grace:.1f}s after it was cancelled")
    if not task.cancelled():
        exc = task.exception()
        if exc is None:
            ctx.fail(f"{method} swallowed the cancellation and returned a value")
        ctx.fail(f"{method} turned the cancellation into {type(exc).__name__}; let CancelledError propagate")
    await _call(
        ctx,
        f"{method} after a cancelled call",
        _probe(ctx, capability, Deadline.after(CALL_SECONDS), label="after-cancel"),
    )


async def check_error_taxonomy(ctx: CheckContext) -> None:
    p, t = ctx.provider, ctx.target
    caps = p.capabilities
    unknown = t.unknown_business
    cases: list[tuple[str, Callable[[], Awaitable[Any]], type[ProviderError]]] = []
    if Capability.RESOLVE in caps:
        cases.append(
            (
                "resolve_business with neither a name nor an identifier",
                lambda: p.resolve_business(BusinessQuery(), deadline=Deadline.after(CALL_SECONDS)),
                InvalidQuery,
            )
        )
    if Capability.VERIFY in caps:
        cases.append(
            (
                "verify_business for unknown_business",
                lambda: p.verify_business(
                    unknown, VerifyOptions(idempotency_key=ctx.key("unknown")), deadline=Deadline.after(CALL_SECONDS)
                ),
                NotFound,
            )
        )
        cases.append(
            (
                "verification_result for a handle that was never issued",
                lambda: p.verification_result(_absent_verification(ctx), deadline=Deadline.after(CALL_SECONDS)),
                NotFound,
            )
        )
    if Capability.OWNERSHIP in caps:
        cases.append(
            (
                "ownership for unknown_business",
                lambda: p.ownership(unknown, deadline=Deadline.after(CALL_SECONDS)),
                NotFound,
            )
        )
    if Capability.WEB_PRESENCE in caps:
        cases.append(
            (
                "web_presence for unknown_business",
                lambda: p.web_presence(unknown, deadline=Deadline.after(CALL_SECONDS)),
                NotFound,
            )
        )
    if Capability.MONITOR in caps:
        cases.append(
            (
                "monitor_enroll for unknown_business",
                lambda: p.monitor_enroll(
                    unknown, MonitorOptions(idempotency_key=ctx.key("unknown")), deadline=Deadline.after(CALL_SECONDS)
                ),
                NotFound,
            )
        )
        cases.append(
            (
                "monitor_result for a handle that was never issued",
                lambda: p.monitor_result(_absent_monitor(ctx), deadline=Deadline.after(CALL_SECONDS)),
                NotFound,
            )
        )
    for label, call, expected in cases:
        await _expect_error(ctx, label, call, expected)

    if t.fault_injector is None or not ctx.declared():
        return
    capability = ctx.declared()[0]
    method = _method(capability)
    await ctx.inject("unavailable", capability)
    unavailable = await _expect_error(
        ctx,
        f"{method} while unavailable",
        lambda: _probe(ctx, capability, Deadline.after(CALL_SECONDS), "unavailable"),
        ProviderUnavailable,
    )
    if not unavailable.retryable:
        ctx.fail("ProviderUnavailable must be retryable")
    await ctx.inject("rate_limited", capability)
    limited = await _expect_error(
        ctx,
        f"{method} while rate limited",
        lambda: _probe(ctx, capability, Deadline.after(CALL_SECONDS), "limited"),
        ProviderRateLimited,
    )
    retry_after = getattr(limited, "retry_after_seconds", None)
    if retry_after is not None and retry_after < 0:
        ctx.fail("ProviderRateLimited.retry_after_seconds must not be negative")


def _verify(ctx: CheckContext, label: str, headers: Any, body: Any) -> ProviderEvent | None:
    try:
        event = ctx.provider.verify_webhook(headers, body)
    except Exception as exc:  # noqa: BLE001 - verify_webhook must never raise; reporting that is the check
        ctx.fail(f"verify_webhook raised {type(exc).__name__} for {label}; it must return None instead")
    if event is not None and not isinstance(event, ProviderEvent):
        ctx.fail(f"verify_webhook returned {type(event).__name__} for {label}; expected ProviderEvent or None")
    return event


async def _samples(ctx: CheckContext, source: Any) -> list[WebhookSample]:
    if source is None:
        return []
    return list(await resolve_maybe_awaitable(source(ctx.provider)))


async def check_webhook_verification(ctx: CheckContext) -> None:
    genuine = await _samples(ctx, ctx.target.genuine_webhooks)
    forged = await _samples(ctx, ctx.target.forged_webhooks)
    for sample in genuine:
        label = sample.description or "a genuine delivery"
        event = _verify(ctx, label, sample.headers, sample.body)
        if event is None:
            ctx.fail(f"rejected {label}")
        if event.provider != ctx.name:
            ctx.fail(f"a verified event names provider {event.provider!r}")
        for variant, headers in (
            ("lower-case", {k.lower(): v for k, v in sample.headers.items()}),
            ("upper-case", {k.upper(): v for k, v in sample.headers.items()}),
        ):
            if _verify(ctx, f"{label} with {variant} header names", headers, sample.body) is None:
                ctx.fail(f"rejected {label} with {variant} header names; header names are case-insensitive")
        if sample.body:
            tampered = bytearray(sample.body)
            tampered[len(tampered) // 2] ^= 0x01
            if _verify(ctx, f"{label} with one body byte changed", sample.headers, bytes(tampered)) is not None:
                ctx.fail(f"accepted {label} after one byte of the body was changed")
        if _verify(ctx, f"{label} without headers", {}, sample.body) is not None:
            ctx.fail(f"accepted the body of {label} with no headers")
        if _verify(ctx, f"{label} with an empty body", sample.headers, b"") is not None:
            ctx.fail(f"accepted the headers of {label} with an empty body")
    for sample in forged:
        label = sample.description or "a forged delivery"
        if _verify(ctx, label, sample.headers, sample.body) is not None:
            ctx.fail(f"accepted {label}")
    malformed: list[tuple[str, Any, Any]] = [
        ("an empty delivery", {}, b""),
        ("an unsigned JSON body", {"content-type": "application/json"}, b"{}"),
        ("binary garbage", {"x-signature": "é" * 8}, b"\xff\xfe\x00\x01"),
        ("a header value of zeroes", {"x-signature": "0" * 64, "x-timestamp": "0"}, b'{"event_id": "x"}'),
    ]
    for label, headers, body in malformed:
        if _verify(ctx, label, headers, body) is not None:
            ctx.fail(f"accepted {label}")


async def check_pagination(ctx: CheckContext) -> None:
    if Capability.RESOLVE not in ctx.provider.capabilities:
        raise ConformanceSkip("the provider does not declare resolve")
    query = ctx.target.resolvable_query.model_copy(update={"offset": 0, "limit": 100})
    full = await _call(
        ctx, "resolve_business", ctx.provider.resolve_business(query, deadline=Deadline.after(CALL_SECONDS))
    )
    if len(full) < 2:
        ctx.fail(f"resolvable_query returned {len(full)} candidate(s); checking pages needs a query with at least 2")
    paged: list[BusinessCandidate] = []
    page_query = query.model_copy(update={"limit": 1})
    for _ in range(len(full) + 1):
        page = await _call(
            ctx, "resolve_business", ctx.provider.resolve_business(page_query, deadline=Deadline.after(CALL_SECONDS))
        )
        if len(page) > page_query.limit:
            ctx.fail(f"resolve_business returned {len(page)} candidates for limit={page_query.limit}")
        paged.extend(page)
        if len(page) < page_query.limit:
            break
        page_query = page_query.next_page(page)
    if paged != full:
        ctx.fail("walking pages of limit=1 by offset did not reproduce the unpaged result in the same order")
    beyond = await _call(
        ctx,
        "resolve_business",
        ctx.provider.resolve_business(
            query.model_copy(update={"offset": len(full)}), deadline=Deadline.after(CALL_SECONDS)
        ),
    )
    if beyond:
        ctx.fail(f"an offset past the last candidate returned {len(beyond)} candidate(s); expected none")


async def check_idempotency(ctx: CheckContext) -> None:
    for capability in ctx.declared():
        method = _method(capability)
        first = await _call(ctx, method, _probe(ctx, capability, Deadline.after(CALL_SECONDS), label="repeat"))
        second = await _call(ctx, method, _probe(ctx, capability, Deadline.after(CALL_SECONDS), label="repeat"))
        if first != second:
            detail = (
                "the same idempotency key started a second job"
                if capability in {Capability.VERIFY, Capability.MONITOR}
                else "a repeated call returned a different answer"
            )
            ctx.fail(f"{method}: {detail}")
        if capability is Capability.VERIFY:
            result, _ = await _poll_to_completion(ctx, first)
            again = await _call(
                ctx,
                "verification_result",
                ctx.provider.verification_result(first, deadline=Deadline.after(CALL_SECONDS)),
            )
            if again != result:
                ctx.fail("verification_result: repeated polls of a completed verification differ")
        if capability is Capability.MONITOR:
            alerts = await _call(
                ctx, "monitor_result", ctx.provider.monitor_result(first, deadline=Deadline.after(CALL_SECONDS))
            )
            again = await _call(
                ctx, "monitor_result", ctx.provider.monitor_result(first, deadline=Deadline.after(CALL_SECONDS))
            )
            if alerts != again:
                ctx.fail("monitor_result: repeated reads of the same page differ")


async def check_schema_conformance(ctx: CheckContext) -> None:
    from core.domain_schemas import iter_errors  # noqa: PLC0415 - only this check needs jsonschema

    for capability in ctx.declared():
        method = _method(capability)
        result = await _call(ctx, method, _probe(ctx, capability, Deadline.after(CALL_SECONDS), label="schema"))
        schema = {
            Capability.OWNERSHIP: "ownership_graph",
            Capability.SCREEN_PERSON: "screening_result",
            Capability.SCREEN_BUSINESS: "screening_result",
        }.get(capability)
        if schema is not None:
            errors = iter_errors(schema, result.model_dump(mode="json"))
            if errors:
                ctx.fail(f"{method} output does not match the {schema} schema: {'; '.join(errors[:3])}")
        for evidence in _evidence(result):
            if evidence.provider != ctx.name:
                ctx.fail(f"{method} cites evidence from provider {evidence.provider!r}; cite this provider's records")


Check = Callable[[CheckContext], Awaitable[None]]

#: Every check, in the order the suite runs them.
CHECKS: dict[str, Check] = {
    "identity": check_identity,
    "capability_honesty": check_capability_honesty,
    "pending_then_result": check_pending_then_result,
    "deadline_expired": check_deadline_expired,
    "deadline_overrun": check_deadline_overrun,
    "cancellation": check_cancellation,
    "error_taxonomy": check_error_taxonomy,
    "webhook_verification": check_webhook_verification,
    "pagination": check_pagination,
    "idempotency": check_idempotency,
    "schema_conformance": check_schema_conformance,
}


async def arun_check(name: str, target: ConformanceTarget) -> None:
    """Run one check inside the current event loop."""
    check = CHECKS.get(name)
    if check is None:
        raise KeyError(f"no conformance check named {name!r}; known checks: {', '.join(CHECKS)}")
    provider = await resolve_maybe_awaitable(target.factory())
    try:
        await check(CheckContext(check=name, target=target, provider=provider))
    finally:
        if target.close is not None:
            await target.close(provider)


def run_check(name: str, target: ConformanceTarget) -> None:
    """Run one check in a fresh event loop. Raises ConformanceFailure or ConformanceSkip."""
    asyncio.run(arun_check(name, target))
