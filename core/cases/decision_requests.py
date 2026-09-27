# SPDX-License-Identifier: Apache-2.0
"""Decision requests: asking a named person to decide a governed case (PRD G-3).

AgenticOrg never collects an approval itself. It asks the Grantex auth service for a *decision
request* for one semantic action, sends the approver to the service's own approval page, and later
consumes the decision grants that page minted. Step-up authentication, the dwell measurement and
the four-eyes rule all happen on that page, in the approver's browser, on the auth service's
origin; this module only creates the request, reports its status and consumes the grants.

The calls are behind :class:`DecisionGrantService` so the console and the case runtime never talk
to the auth service directly, and so tests can run against
``core.test_doubles.fake_decision_grants.FakeDecisionGrantService``.

The endpoint shapes below follow the Grantex decision-grant API as published (``/v1/decisions/...``,
``spec/decision-grant.md``). They are exercised against a real auth service by
``ui/e2e/decision-grants.spec.ts`` (``make e2e-decisions``); the Python SDK's
``Grantex(...).decisions`` client covers the same endpoints from grantex 0.6, which is not released
yet. When it is, replace :class:`GrantexDecisionGrantService`'s request building with the SDK
client (called off the event loop with ``asyncio.to_thread``) and keep this interface.

Everything here is inert unless a tenant has ``governed_cases.enabled`` *and* the deployment sets
``AGENTICORG_CASE_DECISION_SERVICE=grantex``. Without it, decision requests are refused with
``decision_service_not_configured`` and the case decision route keeps refusing every decision with
``decision_required``: no decision grant, no decision.

A case decision is this platform's own: a person decides it, no agent does, and the request names
no agent (no ``agentId`` or ``grantId``). With ``AGENTICORG_CASE_DECISION_GRANT_RELEASE`` on, it is
consumed at the issuer by its request id, and this platform never asks for, presents, stores or
forwards one of its grants; readiness comes from the request's own state and
``decisionGrantsReady``. Until the issuer turns ``DECISION_GRANT_AGENT_BINDING`` on, its status
answer still carries the grants once a request is approved; they are parsed with that answer and
dropped. Once it is on, the issuer never sends a decision grant to the developer API key at all and
releases one only to the agent a request names, which makes consumption by request id the only way
to record a case decision. Off, the grants are read from the request's status and presented for
consumption, as before.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
import structlog
from prometheus_client import Counter, Histogram

from core.cases.decisions import DecisionCheck, semantic_action
from core.models.governed_case import GovernedCase

logger = structlog.get_logger()

#: Outcomes a decision request may ask for. A request never asks for anything else.
DECISION_OUTCOMES = ("approve", "decline")

MAX_MEMO_CHARS = 60_000
MAX_POLICY_BYTES = 30_000

decision_requests_total = Counter(
    "agenticorg_case_decision_requests_total",
    "Decision requests created through the case API, by outcome and result",
    ["outcome", "result"],
)
case_version_announcements_total = Counter(
    "agenticorg_case_version_announcements_total",
    "Case versions registered with the decision-grant issuer, by result. A rising failure count "
    "means the issuer is not superseding stale requests, which AgenticOrg still refuses locally.",
    ["result"],
)
console_dwell_seconds = Histogram(
    "agenticorg_case_console_dwell_seconds",
    "Advisory console dwell (case screen render to submit) by stage. Never the authoritative dwell: "
    "that is measured by the approval page and carried in the decision grant.",
    ["stage"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 900),
)
decision_grants_consumed_total = Counter(
    "agenticorg_case_decision_grants_consumed_total",
    "Decision grant consumption attempts at the issuer, by outcome and result",
    ["outcome", "result"],
)
decision_dwell_seconds = Histogram(
    "agenticorg_case_decision_dwell_seconds",
    "Dwell between the approval page rendering and the approver submitting, as the issuer measured "
    "it, recorded when the decision is recorded. ``dwell_source=server`` is the authoritative "
    "series and the only one an alert may read; any other value means the issuer did not measure "
    "it and the number is not evidence of anything.",
    ["dwell_source", "approval_stage"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 900),
)


class DecisionServiceError(RuntimeError):
    """The decision service refused or could not answer. ``reason`` is a stable code."""

    def __init__(self, reason: str, detail: str = "", *, status: int = 502) -> None:
        self.reason = reason
        self.detail = detail
        self.status = status
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True, slots=True)
class Approval:
    """One approval already collected on the approval page."""

    approver: str
    approver_auth: str
    #: Dwell the *service* measured between rendering and submitting the approval page.
    dwell_ms: int | None
    dwell_source: str
    position: int
    issued_at: str
    consumed_at: str | None = None


@dataclass(frozen=True, slots=True)
class DecisionRequestView:
    """A decision request as the console shows it. Carries no grant token."""

    request_id: str
    status: str
    approval_page: str
    action: dict[str, Any]
    action_hash: str
    case_version: str
    approvals_required: int
    approvals: tuple[Approval, ...] = ()
    expires_at: str = ""
    #: The issuer's ``decisionGrantsReady``, when it sends one (its agent binding is on).
    issuer_grants_ready: bool | None = None

    @property
    def approvals_received(self) -> int:
        return len(self.approvals)

    @property
    def grants_ready(self) -> bool:
        # The issuer's own answer can only withhold readiness, never grant it: it also knows
        # whether a grant has been consumed, revoked or has expired, which counting approvals
        # does not, and where the two disagree the answer that refuses is the one that holds.
        return (
            self.status == "approved"
            and self.approvals_received >= self.approvals_required
            and self.issuer_grants_ready is not False
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status,
            "approval_page": self.approval_page,
            "action": self.action,
            "action_hash": self.action_hash,
            "case_version": self.case_version,
            "approvals_required": self.approvals_required,
            "approvals_received": self.approvals_received,
            "grants_ready": self.grants_ready,
            "expires_at": self.expires_at,
            "approvals": [
                {
                    "approver": a.approver,
                    "approver_auth": a.approver_auth,
                    "dwell_ms": a.dwell_ms,
                    "dwell_source": a.dwell_source,
                    "position": a.position,
                    "issued_at": a.issued_at,
                    "consumed_at": a.consumed_at,
                }
                for a in self.approvals
            ],
        }


@dataclass(frozen=True, slots=True)
class ConsumedDecision:
    """What the issuer recorded when it consumed the grants for one action."""

    request_id: str
    #: ``(approver subject, decision grant id)`` per consumed grant.
    approvers: tuple[tuple[str, str], ...]
    action_hash: str


class DecisionGrantService(Protocol):
    """Decision requests and grant consumption at their issuer."""

    async def set_case_version(self, case_id: str, case_version: str) -> None:
        """Register the case's current version; the issuer supersedes open requests bound to older ones."""
        ...

    async def create_request(
        self,
        *,
        action: Mapping[str, Any],
        case_version: str,
        memo: str,
        policy_score: Mapping[str, Any],
        four_eyes_on: Sequence[str],
        memo_ref: str = "",
        policy_score_ref: str = "",
    ) -> DecisionRequestView: ...

    async def get_request(self, request_id: str) -> DecisionRequestView: ...

    async def grants(self, request_id: str) -> list[str]:
        """The minted decision grants, or an empty list while the request is not fully approved."""
        ...

    async def consume(
        self, *, grants: Sequence[str], action: Mapping[str, Any], case_version: str
    ) -> ConsumedDecision: ...

    #: Decisions are consumed by request id and their grants are never read
    #: (``AGENTICORG_CASE_DECISION_GRANT_RELEASE``).
    consume_by_request_id: bool

    async def consume_request(
        self, *, request_id: str, action: Mapping[str, Any], case_version: str
    ) -> ConsumedDecision:
        """Consume the grants of this platform's own request where the issuer holds them."""
        ...


def _text(value: Any, limit: int) -> str:
    text = str(value or "")
    return text[:limit]


def render_memo_for_approval(case: GovernedCase, outcome: str, override_reason: str = "") -> str:
    """The memo as the approval page shows it: plain text, no markup, bounded.

    The approver reads this on the auth service's page, so it has to stand on its own: what is
    being decided, what the policy said, what each section found and what is missing.
    """
    memo: Mapping[str, Any] = case.memo or {}
    policy: Mapping[str, Any] = case.policy_result or {}
    application: Mapping[str, Any] = case.application or {}
    recommendation = (memo.get("recommendation") or {}).get("proposed", "none")
    lines = [
        f"Case {case.case_ref} — {application.get('legal_name', 'unknown business')}"
        f" ({application.get('jurisdiction', 'unknown jurisdiction')})",
        f"Requested decision: {outcome}",
        f"Agent recommendation: {recommendation} (proposal only; the policy result gates it)",
        f"Policy: {(policy.get('policy') or {}).get('policy_id', 'none')}"
        f" {(policy.get('policy') or {}).get('version', '')}"
        f" — tier {policy.get('tier', 'none')}, score {policy.get('score', 'none')}",
    ]
    if (policy.get("policy") or {}).get("example"):
        lines.append("WARNING: this policy is a shipped example and has not been reviewed for real use.")
    if override_reason:
        lines.append(f"The requester's reason for a decision other than the recommendation: {override_reason}")
    reasons = policy.get("reasons") or []
    if reasons:
        lines.append("")
        lines.append("Fired policy rules:")
        lines += [f"  - {r.get('rule_id')}: {r.get('reason')} (tier {r.get('tier')})" for r in reasons]
    lines.append("")
    lines.append("Memo sections:")
    for section in memo.get("sections") or []:
        status = section.get("status")
        detail = section.get("not_available_reason") or section.get("error_reason") or ""
        lines.append(
            f"  - {section.get('section_id')}: {status}{f' ({detail})' if detail else ''};"
            f" {len(section.get('findings') or [])} finding(s),"
            f" {len(section.get('evidence') or [])} cited record(s)"
        )
        for finding in section.get("findings") or []:
            lines.append(f"      * [{finding.get('severity')}] {finding.get('code')}: {finding.get('statement')}")
    missing = memo.get("missing_items") or []
    if missing:
        lines.append("")
        lines.append("Missing items:")
        lines += [f"  - {item.get('item')}: {item.get('reason')}" for item in missing]
    dispositions = case.screening_dispositions or []
    if dispositions:
        lines.append("")
        lines.append("Screening dispositions:")
        for disposition in dispositions:
            review = disposition.get("review") or {}
            state = (
                f"{review.get('action')} as {review.get('final_outcome')} by {review.get('analyst_id')}"
                if review
                else "awaiting analyst review"
            )
            proposed = disposition.get("proposed_outcome")
            lines.append(f"  - hit {disposition.get('hit_id')}: proposed {proposed}; {state}")
    lines.append("")
    lines.append("No agent may approve, decline, close or file this case.")
    return _text("\n".join(lines), MAX_MEMO_CHARS)


def policy_score_for_approval(case: GovernedCase) -> dict[str, Any]:
    """The policy result bound into the decision grant, trimmed to the issuer's size limit.

    The policy's identity - id, version, whether it is an example, the inputs digest, the score and
    the tier - is never dropped; only rule detail is, and a trim says so.
    """
    policy = dict(case.policy_result or {})
    if _canonical_size(policy) <= MAX_POLICY_BYTES:
        return policy
    trimmed: dict[str, Any] = {
        "schema_version": policy.get("schema_version", "1.0.0"),
        "policy": policy.get("policy"),
        "inputs_digest": policy.get("inputs_digest"),
        "score": policy.get("score"),
        "tier": policy.get("tier"),
        "reasons": [],
        "truncated": True,
    }
    kept: list[dict[str, Any]] = []
    omitted = 0
    for reason in policy.get("reasons") or []:
        candidate = {
            "rule_id": str(reason.get("rule_id", ""))[:128],
            "tier": reason.get("tier"),
            "reason": str(reason.get("reason", ""))[:200],
        }
        if _canonical_size({**trimmed, "reasons": [*kept, candidate], "omitted_reasons": omitted}) > MAX_POLICY_BYTES:
            omitted += 1
            continue
        kept.append(candidate)
    trimmed["reasons"] = kept
    if omitted:
        trimmed["omitted_reasons"] = omitted
    return trimmed


def _canonical_size(document: Mapping[str, Any]) -> int:
    return len(json.dumps(document, separators=(",", ":"), sort_keys=True, default=str).encode())


def case_action(case: GovernedCase, outcome: str) -> dict[str, Any]:
    """The semantic action a decision grant is bound to, qualified by tenant.

    ``case_ref`` is unique per tenant, not per issuer developer, so the tenant goes into the
    action's manifest-declared ``extra`` fields. Both the request and the consumption use this
    function, so the hashes match.
    """
    action = dict(semantic_action(case, outcome))
    action["extra"] = {"tenant": str(case.tenant_id)}
    return action


def _required_text(field: str, value: Any) -> str:
    """A field the screen states as fact. A missing one is a refusal, never a default."""
    if not isinstance(value, str) or not value.strip():
        raise DecisionServiceError("decision_service_response_invalid", f"{field} is missing")
    return value


def _required_count(field: str, value: Any) -> int:
    """A positive whole number. ``approvalsRequired`` must never quietly become one approver."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DecisionServiceError("decision_service_response_invalid", f"{field} is missing")
    return value


def _approval(entry: Mapping[str, Any]) -> Approval:
    """One approval, as the screen shows it: who approved, how, in what position, for how long.

    Every one of those is an assertion about an authority event, so a payload that omits one is
    refused rather than shown with a blank or a default.
    """
    dwell = entry.get("dwellMs")
    if dwell is not None and (isinstance(dwell, bool) or not isinstance(dwell, int) or dwell < 0):
        raise DecisionServiceError("decision_service_response_invalid", "dwellMs is not a duration")
    return Approval(
        approver=_required_text("approval sub", entry.get("sub")),
        approver_auth=_required_text("approval approverAuth", entry.get("approverAuth")),
        dwell_ms=dwell,
        dwell_source=_required_text("approval dwellSource", entry.get("dwellSource")),
        position=_required_count("approval position", entry.get("position")),
        issued_at=_required_text("approval issuedAt", entry.get("issuedAt")),
        consumed_at=str(entry["consumedAt"]) if entry.get("consumedAt") else None,
    )


def _platform_owned_readiness(payload: Mapping[str, Any]) -> bool | None:
    """``decisionGrantsReady`` of a request this platform consumes by id, or ``None`` when absent.

    Consumption by request id is for a request that names no agent; the issuer refuses any other
    (``wrong_agent``), and a request that names one is that agent's decision, not this platform's.
    So a request naming an agent or a grant - which the issuer can answer when asked again for
    the same action while someone else's request is open - is refused here, before it is recorded
    on a case or offered to anyone as ready. An issuer from before the binding answers
    ``decisionGrantsReady`` not at all, which leaves readiness to the approvals; any value other
    than a boolean is refused rather than read as either.
    """
    for key in ("agentId", "grantId"):
        if payload.get(key) is not None:
            raise DecisionServiceError("decision_invalid", "wrong_agent", status=409)
    if "decisionGrantsReady" not in payload:
        return None
    ready = payload["decisionGrantsReady"]
    if not isinstance(ready, bool):
        raise DecisionServiceError("decision_service_response_invalid", "decisionGrantsReady is not a boolean")
    return ready


def _view(
    payload: Mapping[str, Any], *, require_approval_page: bool = False, platform_owned: bool = False
) -> DecisionRequestView:
    """The issuer's answer, parsed strictly.

    The console states what this carries - which action is being approved, how many approvals it
    needs, who has approved and how long they looked at it - so a field the issuer did not send is
    ``decision_service_response_invalid``, not a default. The field names are provisional
    (see the module docstring); a rename has to fail loudly rather than show one approver where
    four eyes were required.

    ``platform_owned`` is set when the request is consumed by its id
    (``AGENTICORG_CASE_DECISION_GRANT_RELEASE``); see :func:`_platform_owned_readiness`.
    """
    issuer_ready = _platform_owned_readiness(payload) if platform_owned else None
    approvals_entries = payload.get("approvals") or []
    if not isinstance(approvals_entries, list):
        raise DecisionServiceError("decision_service_response_invalid", "approvals is not a list")
    approvals = tuple(_approval(a) for a in approvals_entries if isinstance(a, Mapping))
    if len(approvals) != len(approvals_entries):
        raise DecisionServiceError("decision_service_response_invalid", "an approval is not an object")
    action = payload.get("action")
    if not isinstance(action, Mapping) or not action:
        raise DecisionServiceError("decision_service_response_invalid", "action is missing")
    return DecisionRequestView(
        request_id=_required_text("requestId", payload.get("requestId")),
        status=_required_text("status", payload.get("status")),
        approval_page=_required_text("approvalPage", payload.get("approvalPage")) if require_approval_page else "",
        action=dict(action),
        action_hash=_required_text("actionHash", payload.get("actionHash")),
        case_version=_required_text("caseVersion", payload.get("caseVersion")),
        approvals_required=_required_count("approvalsRequired", payload.get("approvalsRequired")),
        approvals=approvals,
        expires_at=_required_text("expiresAt", payload.get("expiresAt")),
        issuer_grants_ready=issuer_ready,
    )


#: What the issuer issues as a decision request id; anything else is not one of its requests.
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _consumed_request(payload: Mapping[str, Any], request_id: str) -> ConsumedDecision:
    """The issuer's receipt for a request consumed by its id, parsed strictly.

    The case records who decided and which grant each of them spent from this answer alone, so it
    has to be a confirmed consumption of exactly this request that names the grant on every
    approver, and the grants it names have to be exactly the ones it says it consumed. Anything
    else is refused (``decision_service_response_invalid``) rather than guessed: an approver
    recorded against the wrong credential is worse than a decision not recorded. The issuer has
    spent the grants by the time it answers, so a refusal here means a person approves again; that
    is the price of never recording a decision on an answer nobody can read. Unlike presented
    grants there is no second lookup for an answer without ``approvers[].jti``: every issuer that
    consumes by request id names it.
    """

    def invalid(detail: str) -> DecisionServiceError:
        return DecisionServiceError("decision_service_response_invalid", detail)

    if payload.get("consumed") is not True:
        raise invalid("the consumption was not confirmed")
    if payload.get("requestId") != request_id:
        raise invalid("the consumption is for another request")
    jtis = payload.get("jtis")
    if (
        not isinstance(jtis, list)
        or not jtis
        or any(not isinstance(grant_id, str) or not grant_id.strip() for grant_id in jtis)
        or len(set(jtis)) != len(jtis)
    ):
        raise invalid("decision grant ids are invalid")
    approvers = payload.get("approvers")
    if not isinstance(approvers, list) or not approvers or any(not isinstance(a, Mapping) for a in approvers):
        raise invalid("the approvers are invalid")
    pairs = tuple(
        (_required_text("consumption sub", a.get("sub")), _required_text("consumption jti", a.get("jti")))
        for a in approvers
    )
    spent = [grant_id for _, grant_id in pairs]
    if len(set(spent)) != len(spent) or set(spent) != set(jtis):
        raise invalid("the approvers' decision grants are not the consumed ones")
    return ConsumedDecision(
        request_id=request_id,
        approvers=pairs,
        action_hash=_required_text("actionHash", payload.get("actionHash")),
    )


@dataclass
class GrantexDecisionGrantService:
    """The Grantex auth service's decision-grant API, over HTTP.

    Provisional endpoint shapes (Grantex ``spec/decision-grant.md``):

    ``PUT  /v1/decisions/cases/{caseId}``          register the case's current version
    ``POST /v1/decisions/requests``                create a request, answering ``approvalPage``
    ``GET  /v1/decisions/requests/{id}``           status, approvals and, once approved,
                                                   ``decisionGrants`` (issuer binding off) or
                                                   ``decisionGrantsReady`` (binding on)
    ``POST /v1/decisions/consume``                 consume presented grants for one action, atomically
    ``POST /v1/decisions/requests/{id}/consume``   consume a request that names no agent by its id
                                                   (``consume_by_request_id``)

    Authenticated with the platform's developer API key. The approval page is the *only* place an
    approval happens; nothing here can approve. ``POST /v1/decisions/requests/{id}/grants``, which
    releases a request's grants to the agent it names, is not used: this platform's requests name
    no agent.
    """

    base_url: str
    api_key: str
    timeout_seconds: float = 10.0
    #: Consuming grants happens inside the case's row lock, so it gets a tighter deadline.
    consume_timeout_seconds: float = 5.0
    #: Injected in tests.
    client_factory: Any = None
    connector: str = "governed_cases"
    expires_in_seconds: int = 24 * 60 * 60
    #: ``AGENTICORG_CASE_DECISION_GRANT_RELEASE``: consume decisions by request id and never read
    #: their grants. Off, the grants are read from the request's status and presented, as before.
    consume_by_request_id: bool = False
    _headers: dict[str, str] = field(init=False, default_factory=dict)
    #: One client per running event loop; the console polls every few seconds per open case.
    _clients: dict[int, httpx.AsyncClient] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        if not self.base_url or not self.api_key:
            raise DecisionServiceError("decision_service_not_configured", status=503)
        self.base_url = self.base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _client(self) -> httpx.AsyncClient:
        """The client for this event loop, kept open: a case screen polls every few seconds.

        Keyed by loop so a client is never used from a loop other than the one that created its
        connections (tests and workers each have their own).
        """
        if self.client_factory is not None:
            return self.client_factory()
        loop_key = id(asyncio.get_running_loop())
        client = self._clients.get(loop_key)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout_seconds),
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            )
            self._clients[loop_key] = client
        return client

    async def _call(
        self, method: str, path: str, body: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> dict[str, Any]:
        client = self._client()
        owned = self.client_factory is not None
        try:
            try:
                response = await client.request(
                    method, path, headers=self._headers, json=body,
                    **({"timeout": httpx.Timeout(timeout)} if timeout is not None else {}),
                )  # fmt: skip
            finally:
                if owned:
                    await client.aclose()
        # enterprise-gate: broad-except-ok reason=decision-service-transport-failure-fails-closed
        except Exception as exc:
            logger.warning("case_decision_service_unreachable", path=path, error=type(exc).__name__)
            raise DecisionServiceError("decision_service_unavailable", type(exc).__name__) from exc
        return self._payload(response, path)

    def _payload(self, response: httpx.Response, path: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400 or not isinstance(payload, dict):
            reason, detail = self._refusal(response.status_code, payload if isinstance(payload, dict) else {})
            logger.warning(
                "case_decision_service_refused", path=path, status=response.status_code, reason=reason, detail=detail
            )
            raise DecisionServiceError(reason, detail, status=502 if response.status_code >= 500 else 409)
        return payload

    def _refusal(self, status: int, payload: Mapping[str, Any]) -> tuple[str, str]:
        code = str(payload.get("code") or "")
        sub_reason = str(payload.get("subReason") or "")
        if code == "DECISION_GRANTS_DISABLED":
            return "decision_service_disabled", code
        if status == 404:
            # The issuer answers 404 NOT_FOUND for a request it no longer holds - a request that
            # aged past its ceiling, for example. That is not the service being switched off.
            return "decision_request_not_found", code or "not found"
        if status == 403 and sub_reason and self.consume_by_request_id:
            # The issuer answers `wrong_agent` with 403: a refusal of this decision (the request
            # names an agent), not a failed sign-in, so the case says which. Still a refusal.
            return "decision_invalid", sub_reason
        if status == 401 or status == 403:
            return "decision_service_unauthorised", code
        if sub_reason:
            return "decision_invalid", sub_reason
        return "decision_service_refused", code or str(status)

    async def set_case_version(self, case_id: str, case_version: str) -> None:
        """Tell the issuer the case's current version so it supersedes and revokes what is stale."""
        await self._call("PUT", f"/v1/decisions/cases/{case_id}", {"caseVersion": case_version})

    async def create_request(
        self,
        *,
        action: Mapping[str, Any],
        case_version: str,
        memo: str,
        policy_score: Mapping[str, Any],
        four_eyes_on: Sequence[str],
        memo_ref: str = "",
        policy_score_ref: str = "",
    ) -> DecisionRequestView:
        # The case version is registered first: a later version supersedes this request, so a case
        # that changes after the request is made can never be decided on the old memo.
        await self.set_case_version(str(action["case_id"]), case_version)
        body: dict[str, Any] = {
            "action": dict(action),
            "connector": self.connector,
            "caseVersion": case_version,
            "memo": {"content": memo, **({"ref": memo_ref} if memo_ref else {})},
            "policyScore": {"content": dict(policy_score), **({"ref": policy_score_ref} if policy_score_ref else {})},
            "fourEyesOn": list(four_eyes_on),
            "expiresInSeconds": self.expires_in_seconds,
        }
        payload = await self._call("POST", "/v1/decisions/requests", body)
        # Only the create answer carries the approval page; the status answer does not.
        return _view(payload, require_approval_page=True, platform_owned=self.consume_by_request_id)

    async def get_request(self, request_id: str) -> DecisionRequestView:
        payload = await self._call("GET", f"/v1/decisions/requests/{request_id}")
        return _view(payload, platform_owned=self.consume_by_request_id)

    async def grants(self, request_id: str) -> list[str]:
        if self.consume_by_request_id:
            # The request is consumed by its id and its grants are never asked for. Refused rather
            # than answered with an empty list, which a caller would read as "not approved yet".
            raise DecisionServiceError("decision_grants_not_released", "consumed by request id", status=409)
        payload = await self._call("GET", f"/v1/decisions/requests/{request_id}")
        grants = payload.get("decisionGrants")
        return [str(g) for g in grants] if isinstance(grants, list) else []

    async def consume_request(
        self, *, request_id: str, action: Mapping[str, Any], case_version: str
    ) -> ConsumedDecision:
        """Consume the grants of this platform's own request for one action, by the request's id.

        ``POST /v1/decisions/requests/{id}/consume`` runs the same checks under the same locks as
        presenting the grants - the action, the case version, single use, four eyes - and answers
        the same receipt, but nothing here presents a grant: the issuer spends the ones it holds.
        A request that names an agent is refused (``wrong_agent``): only the grants its agent
        presents spend it. The receipt is parsed here; the verifier then holds it to the approvals
        the case recorded for the request (:func:`_hold_to_recorded_approvals`).
        """
        if not self.consume_by_request_id:
            # Off means exactly the behaviour from before the setting: the endpoint is never called.
            raise DecisionServiceError(
                "decision_service_not_configured", "consumption by request id is off", status=503
            )
        if not _REQUEST_ID.fullmatch(request_id):
            # Not an id the issuer could have issued, and it goes into the path.
            raise DecisionServiceError("decision_request_not_found", "not a decision request id", status=404)
        payload = await self._call(
            "POST",
            f"/v1/decisions/requests/{request_id}/consume",
            {"action": dict(action), "caseVersion": case_version},
            # The caller holds the case row locked while this runs.
            timeout=self.consume_timeout_seconds,
        )
        return _consumed_request(payload, request_id)

    async def _grant_ids_by_approver(
        self, request_id: str, subjects: Sequence[str], consumed: set[str]
    ) -> list[str]:
        """Which grant each approver spent, from the issuer's record of the request.

        Used only when the consumption answer did not name the grant on each approver. Every step
        fails closed: the request has to be readable, each approver has to match exactly one
        approval on it, and the grants that resolves to have to be exactly the ones the issuer said
        it consumed. Anything else is refused rather than guessed, because a decision recorded
        against the wrong approver's credential is worse than a decision not recorded.

        The caller holds the case row locked across the consumption, so this second call gets the
        same tighter deadline.
        """
        payload = await self._call(
            "GET", f"/v1/decisions/requests/{request_id}", timeout=self.consume_timeout_seconds
        )
        approvals = payload.get("approvals")
        if not isinstance(approvals, list):
            raise DecisionServiceError("decision_service_response_invalid", "the request has no approvals")
        resolved: list[str] = []
        for subject in subjects:
            matches: set[str] = set()
            for approval in approvals:
                if not isinstance(approval, Mapping) or approval.get("sub") != subject:
                    continue
                grant_id = approval.get("jti")
                if not isinstance(grant_id, str) or not grant_id.strip():
                    raise DecisionServiceError(
                        "decision_service_response_invalid", "the approver's decision grant is invalid"
                    )
                matches.add(grant_id)
            if len(matches) != 1:
                raise DecisionServiceError(
                    "decision_service_response_invalid", "the approver's decision grant is ambiguous"
                )
            resolved.append(matches.pop())
        if len(set(resolved)) != len(resolved) or set(resolved) != consumed:
            raise DecisionServiceError(
                "decision_service_response_invalid", "the resolved decision grants are not the consumed ones"
            )
        return resolved

    async def consume(
        self, *, grants: Sequence[str], action: Mapping[str, Any], case_version: str
    ) -> ConsumedDecision:
        payload = await self._call(
            "POST",
            "/v1/decisions/consume",
            {"decisionGrants": list(grants), "action": dict(action), "caseVersion": case_version},
            # The caller holds the case row locked while this runs.
            timeout=self.consume_timeout_seconds,
        )
        approvers = payload.get("approvers") or []
        raw_jtis = payload.get("jtis") or []
        if not isinstance(raw_jtis, list) or any(
            not isinstance(grant_id, str) or not grant_id.strip() for grant_id in raw_jtis
        ):
            raise DecisionServiceError("decision_service_response_invalid", "decision grant ids are invalid")
        jtis = set(raw_jtis)
        entries = [a for a in approvers if isinstance(a, Mapping)]
        if len(entries) != len(approvers):
            raise DecisionServiceError("decision_service_response_invalid", "an approver is not an object")
        if not entries:
            raise DecisionServiceError("decision_service_response_invalid", "no approver was returned")
        request_id = str(payload.get("requestId") or "")
        subjects = [_required_text("consumption sub", entry.get("sub")) for entry in entries]
        grant_ids: list[str] = []
        for entry in entries:
            grant_id = entry.get("jti")
            if grant_id is None or (isinstance(grant_id, str) and not grant_id.strip()):
                grant_ids.append("")
                continue
            if not isinstance(grant_id, str):
                raise DecisionServiceError(
                    "decision_service_response_invalid",
                    "an approver decision grant id is invalid",
                )
            grant_ids.append(grant_id)
        if not all(grant_ids):
            # An issuer that does not name the grant on each approver. The
            # answer also carries `jtis`, but pairing the two arrays by
            # position is not safe: `jtis` is in the order the grants were
            # presented and `approvers` is in approval order, so for a
            # four-eyes decision they can disagree and the case would record
            # one person's approval against the other's credential. Take the
            # pairing from the issuer's own record of the request instead,
            # which states the grant and the approver together.
            logger.warning("case_decision_consumption_without_grant_ids", request_id=request_id)
            grant_ids = await self._grant_ids_by_approver(request_id, subjects, jtis)
        pairs = tuple(zip(subjects, grant_ids, strict=True))
        return ConsumedDecision(
            request_id=str(payload.get("requestId") or ""),
            approvers=pairs,
            action_hash=str(payload.get("actionHash") or ""),
        )


def _recorded_approvals_required(case: GovernedCase, request_id: str) -> int:
    """How many approvals this request needs, from the case's own record of it.

    The case API records every request it makes with the ``approvalsRequired`` the issuer answered,
    which is what the console showed the person asking. A decision consumed by request id is held
    to it. A request the case has no record of, or whose record states no usable count, is refused
    before the issuer is asked (``decision_request_not_found``): there would be nothing to hold the
    receipt to, and a missing count must never read as one approver.
    """
    for record in case.decision_requests or []:
        if isinstance(record, Mapping) and record.get("request_id") == request_id:
            required = record.get("approvals_required")
            if isinstance(required, int) and not isinstance(required, bool) and required >= 1:
                return required
            raise DecisionServiceError(
                "decision_request_not_found", "the case's record of the request states no approval count", status=404
            )
    raise DecisionServiceError("decision_request_not_found", "not recorded on this case", status=404)


def _hold_to_recorded_approvals(consumed: ConsumedDecision, required: int) -> None:
    """A receipt names exactly the approvals the request needed, each from a different person.

    The issuer enforces four eyes when it consumes; this is the platform's own check of what it is
    about to record, so one approver where two were needed, more than were needed, or one person
    twice is refused (``decision_service_response_invalid``) rather than recorded. As with any
    receipt this platform cannot record on, the issuer has spent the grants by then and a person
    approves again. Presented grants carry no request the case recorded, so their count stays the
    issuer's alone.
    """
    subjects = [subject for subject, _ in consumed.approvers]
    if len(subjects) != required or len(set(subjects)) != len(subjects):
        raise DecisionServiceError(
            "decision_service_response_invalid",
            f"{len(subjects)} approvers, {len(set(subjects))} distinct; the request needed {required}",
        )


@dataclass
class ServiceDecisionVerifier:
    """``CaseRuntime.decision_verifier`` backed by a decision service.

    It consumes the grants at their issuer for the exact semantic action and the case's current
    version, so a case that changed since the approval is refused (``case_changed``) and a grant
    can be spent once. Anything other than a confirmed consumption is a refusal. Given a
    ``decision_request_id`` instead of grants, it consumes that request by its id
    (``AGENTICORG_CASE_DECISION_GRANT_RELEASE``) and holds the receipt to the approvals the case
    recorded for that request; given both, it refuses, because the caller has not said which
    decision it means.

    **This platform does not verify the decision grants themselves.** The issuer checks the
    signature and key, the audience and issuer, the action hash, the dwell source, the memo and
    policy hashes and the four-eyes structure under its own row locks when it consumes them, and
    refuses anything that does not match. Verifying them here as well (decision-grant profile
    §6 steps 2 and 3) needs the Grantex Python SDK's ``grantex.decisions`` verifier, which is not
    published yet; wiring it in is a prerequisite for turning
    ``AGENTICORG_CASE_DECISION_SERVICE`` on outside a development stack, and is recorded as such
    in ``docs/governance/decision-requests.md``.
    """

    service: DecisionGrantService

    async def verify(
        self,
        *,
        tenant_id: str,
        case: GovernedCase,
        outcome: str,
        grants: list[str],
        decision_request_id: str | None = None,
    ) -> DecisionCheck:
        if decision_request_id and grants:
            return DecisionCheck(allowed=False, reason="decision_invalid")
        if not grants and not decision_request_id:
            return DecisionCheck(allowed=False, reason="decision_required")
        action = case_action(case, outcome)
        try:
            if decision_request_id:
                required = _recorded_approvals_required(case, decision_request_id)
                consumed = await self.service.consume_request(
                    request_id=decision_request_id, action=action, case_version=str(case.version)
                )
                _hold_to_recorded_approvals(consumed, required)
            else:
                consumed = await self.service.consume(grants=grants, action=action, case_version=str(case.version))
        except DecisionServiceError as exc:
            decision_grants_consumed_total.labels(outcome=outcome, result="refused").inc()
            logger.warning("case_decision_grants_refused", case_ref=case.case_ref, reason=exc.reason, detail=exc.detail)
            return DecisionCheck(allowed=False, reason=exc.reason if exc.reason != "decision_invalid" else exc.detail)
        decision_grants_consumed_total.labels(outcome=outcome, result="consumed").inc()
        logger.info(
            "case_decision_grants_consumed",
            case_ref=case.case_ref,
            request_id=consumed.request_id,
            approvers=len(consumed.approvers),
            consumed_by="request_id" if decision_request_id else "grants",
        )
        return DecisionCheck(allowed=True, approvers=consumed.approvers)


#: The only dwell source an alert may read: the issuer measured it on its own approval page.
AUTHORITATIVE_DWELL_SOURCE = "server"


def _dwell_source(reported: str) -> str:
    """Bound the label: the issuer's value is free text, and a metric label is not.

    Anything that is not the authoritative source becomes ``other``. What matters on a dashboard
    is "the issuer measured this" against "it did not"; keeping every value the issuer might send
    would let a remote system decide this metric's cardinality.
    """
    return AUTHORITATIVE_DWELL_SOURCE if reported == AUTHORITATIVE_DWELL_SOURCE else "other"


def _approval_stage(position: int) -> str:
    """Low-cardinality position label: an approval is the first, the second, or a later one."""
    return {1: "first", 2: "second"}.get(position, "later")


def record_decision_dwell(view: DecisionRequestView, case_ref: str = "") -> None:
    """Record the issuer-measured dwell of every approval behind a decision that was just recorded.

    This is the authoritative dwell. The console's own render-to-submit figure
    (``agenticorg_case_console_dwell_seconds``) measures how fast a browser posted a form, which is
    not a constraint on anyone determined to rubber-stamp; this one is measured by the approval
    page, under the issuer's control, and is what the rubber-stamping alert reads.

    An approval whose dwell the issuer did not measure is recorded under ``dwell_source="other"``
    rather than dropped or counted as zero: a series that quietly stops arriving and a dwell that
    collapses to nothing must not look the same on a dashboard. The label is mapped rather than
    passed through, because the value comes from a remote system, and a label a remote system
    chooses is cardinality a remote system chooses.
    """
    for approval in view.approvals:
        if approval.dwell_ms is None:
            continue
        decision_dwell_seconds.labels(
            dwell_source=_dwell_source(approval.dwell_source),
            approval_stage=_approval_stage(approval.position),
        ).observe(approval.dwell_ms / 1000)
    logger.info(
        "case_decision_dwell_recorded",
        case_ref=case_ref,
        request_id=view.request_id,
        approvals=len(view.approvals),
        sources=sorted({a.dwell_source or "unreported" for a in view.approvals}),
    )


def four_eyes_on() -> tuple[str, ...]:
    """Outcomes that need two different approvers, from configuration."""
    from core.config import settings

    configured = str(getattr(settings, "case_decision_four_eyes_on", "") or "")
    return tuple(part.strip() for part in configured.split(",") if part.strip() in DECISION_OUTCOMES)


def decision_service() -> DecisionGrantService | None:
    """The configured decision service, or ``None`` when decision requests are switched off.

    Raises :class:`DecisionServiceError` (``decision_service_not_configured``) when a service is
    named but cannot be built, so a misconfiguration is refused rather than ignored.
    """
    import os

    from core.config import external_keys, settings

    kind = str(getattr(settings, "case_decision_service", "") or "").strip().lower()
    if kind in ("", "none", "off"):
        return None
    if kind != "grantex":
        logger.error("case_decision_service_unknown", kind=kind)
        raise DecisionServiceError("decision_service_not_configured", "unknown service", status=503)
    # The issuer has to be named explicitly. `grantex_base_url_for_env()` falls back to the
    # production origin when nothing is set, and a decision request must never be created against
    # an issuer nobody chose.
    base_url = (os.getenv("GRANTEX_BASE_URL", "").strip() or str(external_keys.grantex_base_url or "").strip()
                if "grantex_base_url" in external_keys.model_fields_set or os.getenv("GRANTEX_BASE_URL")
                else "")  # fmt: skip
    if not base_url:
        raise DecisionServiceError("decision_service_not_configured", "GRANTEX_BASE_URL is not set", status=503)
    return GrantexDecisionGrantService(
        base_url=base_url,
        api_key=os.getenv("GRANTEX_API_KEY", "") or external_keys.grantex_api_key,
        connector=str(getattr(settings, "case_decision_connector", "governed_cases")),
        consume_by_request_id=settings.case_decision_grant_release is True,
    )
