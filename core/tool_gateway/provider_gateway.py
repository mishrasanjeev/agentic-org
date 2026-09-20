# SPDX-License-Identifier: Apache-2.0
"""The tool gateway between a governed case agent and a :class:`VerificationProvider`.

Every provider call a reference agent makes goes through :class:`ProviderToolGateway`, which:

1. refuses any tool outside the agent's tool set. A tool set can only contain the read tools in
   :data:`READ_TOOLS`, so no agent built on this gateway can approve, decline, close, file,
   delete or enrol anything - there is no such tool to call;
2. asks the run's :class:`ToolAuthorizer` (the grant check) before the call and fails closed when
   it refuses or cannot answer;
3. degrades an undeclared capability to :class:`NotAvailable` through ``call_capability``;
4. records each call - tool, capability, outcome, SHA-256 of the canonical request and response
   and the upstream record identifiers the response cites - for the case record and evidence
   package, and counts it in low-cardinality metrics (provider latency and outcome by capability).

Grant enforcement (PRD F-1) plugs in as the authorizer. With no authorizer configured the gateway
behaves like ``grants.enforce_closed=off``: calls are not grant-checked.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from prometheus_client import Counter, Histogram
from pydantic import BaseModel

from connectors.framework.verification_provider import (
    BusinessCandidate,
    BusinessQuery,
    BusinessRef,
    BusinessSubject,
    BusinessVerification,
    Capability,
    Deadline,
    NotAvailable,
    OwnershipGraph,
    Pending,
    PersonSubject,
    ProviderError,
    ScreeningResult,
    ScreenOptions,
    VerificationHandle,
    VerificationProvider,
    VerifyOptions,
    WebPresence,
    call_capability,
)

logger = structlog.get_logger()

#: The only tools a governed case agent may hold, and the capability each one needs.
READ_TOOLS: Mapping[str, Capability] = {
    "resolve_business": Capability.RESOLVE,
    "verify_business": Capability.VERIFY,
    "verification_result": Capability.VERIFY,
    "ownership": Capability.OWNERSHIP,
    "screen_person": Capability.SCREEN_PERSON,
    "screen_business": Capability.SCREEN_BUSINESS,
    "web_presence": Capability.WEB_PRESENCE,
}

TOOL_NOT_IN_TOOL_SET = "tool_not_in_agent_tool_set"
AUTHORIZATION_UNAVAILABLE = "authorization_unavailable"

_provider_calls_total = Counter(
    "agenticorg_provider_calls_total",
    "Verification provider calls made through the tool gateway, by capability and outcome",
    ["capability", "outcome"],
)
_provider_call_seconds = Histogram(
    "agenticorg_provider_call_duration_seconds",
    "Latency of verification provider calls made through the tool gateway, by capability",
    ["capability"],
)


@dataclass(frozen=True, slots=True)
class ToolDecision:
    """An authorizer's answer. ``reason`` is a stable denial code (Appendix B) when not allowed."""

    allowed: bool
    reason: str = ""
    sub_reason: str = ""


class ToolAuthorizer(Protocol):
    """Checks one tool call against the run's grant. Must fail closed: refuse when unsure."""

    async def authorize(self, *, connector: str, tool: str) -> ToolDecision: ...


class ToolRefusedError(RuntimeError):
    """A tool call was refused before it reached the provider."""

    def __init__(self, tool: str, reason: str, sub_reason: str = "") -> None:
        self.tool = tool
        self.reason = reason
        self.sub_reason = sub_reason
        super().__init__(f"{reason}: {tool}" + (f" ({sub_reason})" if sub_reason else ""))


class ToolSetError(ValueError):
    """An agent was configured with a tool that is not a read tool."""


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """One gateway call, as recorded in the case record and evidence package.

    ``outcome`` is ``ok``, ``pending``, ``not_available``, ``denied`` or ``error``; ``reason`` is a
    denial code or provider error reason. Hashes are ``sha256:`` over canonical JSON; untrusted
    text inside a response is hashed in its redacted form, which carries the content's own digest.
    """

    sequence: int
    tool: str
    capability: str
    provider: str
    outcome: str
    reason: str
    input_sha256: str
    output_sha256: str | None
    record_ids: tuple[str, ...]
    started_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "tool": self.tool,
            "capability": self.capability,
            "provider": self.provider,
            "outcome": self.outcome,
            "reason": self.reason,
            "input_sha256": self.input_sha256,
            "output_sha256": self.output_sha256,
            "record_ids": list(self.record_ids),
            "started_at": self.started_at,
        }


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if isinstance(value, NotAvailable):
        return {"not_available": value.capability.value}
    return value


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def cited_evidence(value: Any) -> tuple[tuple[str, str, str], ...]:
    """Every ``(provider, record_id, field)`` cited by an ``evidence`` list anywhere in a response."""
    found: set[tuple[str, str, str]] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                if key == "evidence" and isinstance(item, list):
                    found.update(
                        (str(e.get("provider")), str(e["record_id"]), str(e.get("field")))
                        for e in item
                        if isinstance(e, dict) and "record_id" in e
                    )
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(_plain(value))
    return tuple(sorted(found))


def cited_record_ids(value: Any) -> tuple[str, ...]:
    """Every ``evidence[].record_id`` anywhere in a response, sorted and unique."""
    return tuple(sorted({record_id for _, record_id, _ in cited_evidence(value)}))


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class ProviderToolGateway:
    """Mediates one agent run's calls to one provider. Not shared across runs."""

    provider: VerificationProvider
    agent: str
    tool_set: frozenset[str]
    authorizer: ToolAuthorizer | None = None
    clock: Callable[[], datetime] = _utc_now
    records: list[ToolCallRecord] = field(default_factory=list)
    #: ``(provider, record_id, field)`` of every evidence entry in every response received.
    retrieved_evidence: set[tuple[str, str, str]] = field(default_factory=set)

    def __post_init__(self) -> None:
        unknown = sorted(set(self.tool_set) - set(READ_TOOLS))
        if unknown:
            raise ToolSetError(f"{self.agent}: tools {unknown} are not read tools; a case agent cannot hold them")
        self.tool_set = frozenset(self.tool_set)

    @property
    def connector(self) -> str:
        return self.provider.name

    def _record(self, tool: str, outcome: str, reason: str, request: Any, response: Any, started: datetime) -> None:
        self.records.append(
            ToolCallRecord(
                sequence=len(self.records) + 1,
                tool=tool,
                capability=READ_TOOLS[tool].value if tool in READ_TOOLS else "",
                provider=self.provider.name,
                outcome=outcome,
                reason=reason,
                input_sha256=canonical_sha256(request),
                output_sha256=None if response is None else canonical_sha256(response),
                record_ids=() if response is None else cited_record_ids(response),
                started_at=started.isoformat(),
            )
        )

    async def call[T](self, tool: str, request: Any, invoke: Callable[[], Awaitable[T]]) -> T | NotAvailable:
        """Authorize, run and record ``invoke`` as a call to ``tool``."""
        started = self.clock()
        if tool not in self.tool_set:
            self._record(tool, "denied", TOOL_NOT_IN_TOOL_SET, request, None, started)
            _provider_calls_total.labels(capability=READ_TOOLS.get(tool, "none"), outcome="denied").inc()
            logger.warning("provider_tool_refused", agent=self.agent, tool=tool, reason=TOOL_NOT_IN_TOOL_SET)
            raise ToolRefusedError(tool, TOOL_NOT_IN_TOOL_SET)
        capability = READ_TOOLS[tool]

        if self.authorizer is not None:
            try:
                decision = await self.authorizer.authorize(connector=self.connector, tool=tool)
            except asyncio.CancelledError:
                raise
            # enterprise-gate: broad-except-ok reason=authorizer-failure-refuses-the-call-fail-closed
            except Exception as exc:
                logger.error(
                    "provider_tool_authorization_failed", agent=self.agent, tool=tool, error=type(exc).__name__
                )
                decision = ToolDecision(allowed=False, reason=AUTHORIZATION_UNAVAILABLE)
            if not isinstance(decision, ToolDecision) or not decision.allowed:
                reason = decision.reason if isinstance(decision, ToolDecision) and decision.reason else "grant_denied"
                sub_reason = decision.sub_reason if isinstance(decision, ToolDecision) else ""
                self._record(tool, "denied", reason, request, None, started)
                _provider_calls_total.labels(capability=capability.value, outcome="denied").inc()
                logger.warning(
                    "provider_tool_refused", agent=self.agent, tool=tool, reason=reason, sub_reason=sub_reason
                )
                raise ToolRefusedError(tool, reason, sub_reason)

        begun = time.monotonic()
        try:
            result = await call_capability(self.provider, capability, invoke)
        except ProviderError as exc:
            _provider_call_seconds.labels(capability=capability.value).observe(time.monotonic() - begun)
            _provider_calls_total.labels(capability=capability.value, outcome="error").inc()
            self._record(tool, "error", exc.reason, request, None, started)
            raise
        if isinstance(result, NotAvailable):
            _provider_calls_total.labels(capability=capability.value, outcome="not_available").inc()
            self._record(tool, "not_available", result.reason, request, None, started)
            return result
        _provider_call_seconds.labels(capability=capability.value).observe(time.monotonic() - begun)
        self.retrieved_evidence.update(cited_evidence(result))
        outcome = "pending" if isinstance(result, Pending) else "ok"
        _provider_calls_total.labels(capability=capability.value, outcome=outcome).inc()
        self._record(tool, outcome, "", request, result, started)
        return result

    # --- typed tools ---------------------------------------------------------------------------

    async def resolve_business(self, q: BusinessQuery, *, deadline: Deadline) -> list[BusinessCandidate] | NotAvailable:
        return await self.call("resolve_business", q, lambda: self.provider.resolve_business(q, deadline=deadline))

    async def verify_business(
        self, ref: BusinessRef, opts: VerifyOptions, *, deadline: Deadline
    ) -> VerificationHandle | NotAvailable:
        return await self.call(
            "verify_business", (ref, opts), lambda: self.provider.verify_business(ref, opts, deadline=deadline)
        )

    async def verification_result(
        self, h: VerificationHandle, *, deadline: Deadline
    ) -> BusinessVerification | Pending | NotAvailable:
        return await self.call(
            "verification_result", h, lambda: self.provider.verification_result(h, deadline=deadline)
        )

    async def ownership(self, ref: BusinessRef, *, deadline: Deadline) -> OwnershipGraph | NotAvailable:
        return await self.call("ownership", ref, lambda: self.provider.ownership(ref, deadline=deadline))

    async def screen_person(
        self, s: PersonSubject, opts: ScreenOptions, *, deadline: Deadline
    ) -> ScreeningResult | NotAvailable:
        return await self.call(
            "screen_person", (s, opts), lambda: self.provider.screen_person(s, opts, deadline=deadline)
        )

    async def screen_business(
        self, s: BusinessSubject, opts: ScreenOptions, *, deadline: Deadline
    ) -> ScreeningResult | NotAvailable:
        return await self.call(
            "screen_business", (s, opts), lambda: self.provider.screen_business(s, opts, deadline=deadline)
        )

    async def web_presence(self, ref: BusinessRef, *, deadline: Deadline) -> WebPresence | NotAvailable:
        return await self.call("web_presence", ref, lambda: self.provider.web_presence(ref, deadline=deadline))


__all__ = [
    "AUTHORIZATION_UNAVAILABLE",
    "READ_TOOLS",
    "TOOL_NOT_IN_TOOL_SET",
    "ProviderToolGateway",
    "ToolAuthorizer",
    "ToolCallRecord",
    "ToolDecision",
    "ToolRefusedError",
    "ToolSetError",
    "canonical_sha256",
    "cited_evidence",
    "cited_record_ids",
]
