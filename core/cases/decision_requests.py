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

**The endpoint shapes below are provisional.** They follow the Grantex decision-grant API as
published (``/v1/decisions/...``, ``spec/decision-grant.md``), which is still changing; the Python
SDK's ``Grantex(...).decisions`` client covers the same endpoints from grantex 0.6, which is not
released yet. When it is, replace :class:`GrantexDecisionGrantService`'s request building with the
SDK client (called off the event loop with ``asyncio.to_thread``) and keep this interface.

Everything here is inert unless a tenant has ``governed_cases.enabled`` *and* the deployment sets
``AGENTICORG_CASE_DECISION_SERVICE=grantex``. Without it, decision requests are refused with
``decision_service_not_configured`` and the case decision route keeps refusing every decision with
``decision_required``: no decision grant, no decision.
"""

from __future__ import annotations

import json
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

    @property
    def approvals_received(self) -> int:
        return len(self.approvals)

    @property
    def grants_ready(self) -> bool:
        return self.status == "approved" and self.approvals_received >= self.approvals_required

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


def _approval(entry: Mapping[str, Any]) -> Approval:
    dwell = entry.get("dwellMs")
    return Approval(
        approver=str(entry.get("sub") or ""),
        approver_auth=str(entry.get("approverAuth") or ""),
        dwell_ms=int(dwell) if isinstance(dwell, (int, float)) else None,
        dwell_source=str(entry.get("dwellSource") or ""),
        position=int(entry.get("position") or 0),
        issued_at=str(entry.get("issuedAt") or ""),
        consumed_at=str(entry["consumedAt"]) if entry.get("consumedAt") else None,
    )


def _view(payload: Mapping[str, Any], *, approval_page: str = "") -> DecisionRequestView:
    request_id = str(payload.get("requestId") or "")
    if not request_id:
        raise DecisionServiceError("decision_service_response_invalid", "no request id")
    approvals = tuple(_approval(a) for a in payload.get("approvals") or [] if isinstance(a, Mapping))
    return DecisionRequestView(
        request_id=request_id,
        status=str(payload.get("status") or "unknown"),
        approval_page=str(payload.get("approvalPage") or approval_page),
        action=dict(payload.get("action") or {}),
        action_hash=str(payload.get("actionHash") or ""),
        case_version=str(payload.get("caseVersion") or ""),
        approvals_required=int(payload.get("approvalsRequired") or 1),
        approvals=approvals,
        expires_at=str(payload.get("expiresAt") or ""),
    )


@dataclass
class GrantexDecisionGrantService:
    """The Grantex auth service's decision-grant API, over HTTP.

    Provisional endpoint shapes (Grantex ``spec/decision-grant.md``):

    ``PUT  /v1/decisions/cases/{caseId}``     register the case's current version
    ``POST /v1/decisions/requests``           create a request, answering ``approvalPage``
    ``GET  /v1/decisions/requests/{id}``      status, approvals and, once approved, ``decisionGrants``
    ``POST /v1/decisions/consume``            consume the grants for one action, atomically

    Authenticated with the platform's developer API key. The approval page is the *only* place an
    approval happens; nothing here can approve.
    """

    base_url: str
    api_key: str
    timeout_seconds: float = 10.0
    #: Injected in tests.
    client_factory: Any = None
    connector: str = "governed_cases"
    expires_in_seconds: int = 24 * 60 * 60
    _headers: dict[str, str] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        if not self.base_url or not self.api_key:
            raise DecisionServiceError("decision_service_not_configured", status=503)
        self.base_url = self.base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _client(self) -> httpx.AsyncClient:
        if self.client_factory is not None:
            return self.client_factory()
        return httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(self.timeout_seconds))

    async def _call(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with self._client() as client:
                response = await client.request(method, path, headers=self._headers, json=body)
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

    @staticmethod
    def _refusal(status: int, payload: Mapping[str, Any]) -> tuple[str, str]:
        code = str(payload.get("code") or "")
        sub_reason = str(payload.get("subReason") or "")
        if code == "DECISION_GRANTS_DISABLED" or status == 404:
            return "decision_service_disabled", code or "not found"
        if status == 401 or status == 403:
            return "decision_service_unauthorised", code
        if sub_reason:
            return "decision_invalid", sub_reason
        return "decision_service_refused", code or str(status)

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
        await self._call("PUT", f"/v1/decisions/cases/{action['case_id']}", {"caseVersion": case_version})
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
        return _view(payload)

    async def get_request(self, request_id: str) -> DecisionRequestView:
        payload = await self._call("GET", f"/v1/decisions/requests/{request_id}")
        return _view(payload)

    async def grants(self, request_id: str) -> list[str]:
        payload = await self._call("GET", f"/v1/decisions/requests/{request_id}")
        grants = payload.get("decisionGrants")
        return [str(g) for g in grants] if isinstance(grants, list) else []

    async def consume(
        self, *, grants: Sequence[str], action: Mapping[str, Any], case_version: str
    ) -> ConsumedDecision:
        payload = await self._call(
            "POST",
            "/v1/decisions/consume",
            {"decisionGrants": list(grants), "action": dict(action), "caseVersion": case_version},
        )
        approvers = payload.get("approvers") or []
        jtis = payload.get("jtis") or []
        pairs = tuple(
            (str(a.get("sub") or a.get("approver") or ""), str(a.get("jti") or ""))
            for a in approvers
            if isinstance(a, Mapping)
        )
        if not pairs and jtis:
            pairs = tuple(("", str(jti)) for jti in jtis)
        if not pairs:
            raise DecisionServiceError("decision_service_response_invalid", "no approver was returned")
        return ConsumedDecision(
            request_id=str(payload.get("requestId") or ""),
            approvers=pairs,
            action_hash=str(payload.get("actionHash") or ""),
        )


@dataclass
class ServiceDecisionVerifier:
    """``CaseRuntime.decision_verifier`` backed by a decision service.

    It consumes the grants at their issuer for the exact semantic action and the case's current
    version, so a case that changed since the approval is refused (``case_changed``) and a grant
    can be spent once. Anything other than a confirmed consumption is a refusal.
    """

    service: DecisionGrantService

    async def verify(
        self, *, tenant_id: str, case: GovernedCase, outcome: str, grants: list[str]
    ) -> DecisionCheck:
        if not grants:
            return DecisionCheck(allowed=False, reason="decision_required")
        action = semantic_action(case, outcome)
        try:
            consumed = await self.service.consume(
                grants=grants, action=action, case_version=str(case.version)
            )
        except DecisionServiceError as exc:
            decision_grants_consumed_total.labels(outcome=outcome, result="refused").inc()
            logger.warning(
                "case_decision_grants_refused", case_ref=case.case_ref, reason=exc.reason, detail=exc.detail
            )
            return DecisionCheck(allowed=False, reason=exc.reason if exc.reason != "decision_invalid" else exc.detail)
        decision_grants_consumed_total.labels(outcome=outcome, result="consumed").inc()
        logger.info(
            "case_decision_grants_consumed",
            case_ref=case.case_ref,
            request_id=consumed.request_id,
            approvers=len(consumed.approvers),
        )
        return DecisionCheck(allowed=True, approvers=consumed.approvers)


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

    from core.config import external_keys, grantex_base_url_for_env, settings

    kind = str(getattr(settings, "case_decision_service", "") or "").strip().lower()
    if kind in ("", "none", "off"):
        return None
    if kind != "grantex":
        logger.error("case_decision_service_unknown", kind=kind)
        raise DecisionServiceError("decision_service_not_configured", "unknown service", status=503)
    return GrantexDecisionGrantService(
        base_url=grantex_base_url_for_env(),
        api_key=os.getenv("GRANTEX_API_KEY", "") or external_keys.grantex_api_key,
        connector=str(getattr(settings, "case_decision_connector", "governed_cases")),
    )
