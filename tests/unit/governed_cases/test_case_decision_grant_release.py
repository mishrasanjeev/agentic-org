# SPDX-License-Identifier: Apache-2.0
"""Governed-case decisions consumed by request id (``AGENTICORG_CASE_DECISION_GRANT_RELEASE``).

With the Grantex auth service's ``DECISION_GRANT_AGENT_BINDING`` on, the developer API key never
receives a decision grant: ``GET /v1/decisions/requests/{id}`` reports ``decisionGrantsReady``
instead of ``decisionGrants``, grants are released only to the grant token of the agent a request
names, and a request that names no agent - the platform's own decision - is consumed by its id.
AgenticOrg's case decisions are its own: a person decides, no agent does, and the request names
none. So with the setting on, the platform consumes by request id and never asks for, presents,
stores or forwards a grant. Until the issuer's binding is on, its status answer still carries them;
they are parsed and dropped.

Everything here runs against :class:`FakeDecisionIssuer`, which answers over HTTP as the auth
service does in both states of its binding, so each test says what the platform sent, what it
never sent and what it made of the answer.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from core.cases import decision_requests as dr
from core.cases.decision_requests import (
    DecisionServiceError,
    GrantexDecisionGrantService,
    ServiceDecisionVerifier,
    case_action,
)
from core.cases.decisions import DecisionCheck, record_decision
from core.models.governed_case import GovernedCase
from core.test_doubles.fake_decision_grants import FakeDecisionGrantService
from core.test_doubles.fake_grantex_decision_issuer import ISSUER_ORIGIN, FakeDecisionIssuer

POLICY = {
    "schema_version": "1.0.0",
    "policy": {"policy_id": "business_onboarding_uk", "version": "1.2.0", "example": True, "reviewed_by": None},
    "inputs_digest": "sha256:" + "0" * 64,
    "score": 40,
    "tier": "medium",
    "reasons": [],
}
MEMO = {"memo_id": "memo_0001", "recommendation": {"proposed": "refer"}, "sections": [], "missing_items": []}
REQUEST_ID = "dreq_00000001"


def case_row(*, recorded: dict[str, Any] | None = None, **overrides: Any) -> GovernedCase:
    """A case awaiting its decision; ``recorded`` maps request ids to the approvals each needs."""
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "case_ref": "case_" + "b" * 24,
        "purpose": "aml.cdd.onboarding",
        "state": "awaiting_decision",
        "provider": "mock",
        "policy_id": "business_onboarding_uk",
        "application": {"legal_name": "Example Holdings Ltd", "jurisdiction": "GB"},
        "subject": {"provider": "mock", "provider_ref": "mock-gb-00000002", "jurisdiction": "GB"},
        "memo": MEMO,
        "policy_result": POLICY,
        "screening_dispositions": [],
        "version": 3,
        "decision_requests": [
            {"request_id": request_id, "approvals_required": required}
            for request_id, required in (recorded or {}).items()
        ],
    }
    values.update(overrides)
    return GovernedCase(**values)


def on_case(case: GovernedCase, view: Any) -> GovernedCase:
    """The case with its own record of a decision request, as the case API stores it."""
    case.decision_requests = [
        *(case.decision_requests or []),
        {"request_id": view.request_id, "approvals_required": view.approvals_required},
    ]
    return case


def _mode(release: bool) -> dict[str, Any]:
    # Off is the service as it was built before the setting existed, not an explicit False.
    return {"consume_by_request_id": True} if release else {}


def grantex(issuer: FakeDecisionIssuer, *, release: bool) -> GrantexDecisionGrantService:
    return GrantexDecisionGrantService(
        base_url=ISSUER_ORIGIN, api_key="test-only-key", client_factory=issuer.client_factory, **_mode(release)
    )


def answering(status: int, payload: Any, *, release: bool = True) -> GrantexDecisionGrantService:
    """A service whose issuer answers every call with one fixed response."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if isinstance(payload, bytes):
            return httpx.Response(status, content=payload)
        return httpx.Response(status, json=payload)

    transport = httpx.MockTransport(handler)
    service = GrantexDecisionGrantService(
        base_url=ISSUER_ORIGIN,
        api_key="test-only-key",
        client_factory=lambda: httpx.AsyncClient(transport=transport, base_url=ISSUER_ORIGIN),
        **_mode(release),
    )
    service.seen = seen  # type: ignore[attr-defined]
    return service


async def ask(service: GrantexDecisionGrantService, case: GovernedCase, outcome: str) -> Any:
    return await service.create_request(
        action=case_action(case, outcome),
        case_version=str(case.version),
        memo="memo text",
        policy_score=POLICY,
        four_eyes_on=("decline",),
    )


def paths(issuer: FakeDecisionIssuer) -> list[str]:
    return [f"{method} {path}" for method, path, _ in issuer.calls]


# --- the setting ----------------------------------------------------------------------------------


def test_the_setting_is_off_by_default_and_read_from_its_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config import Settings

    monkeypatch.delenv("AGENTICORG_CASE_DECISION_GRANT_RELEASE", raising=False)
    assert Settings().case_decision_grant_release is False
    monkeypatch.setenv("AGENTICORG_CASE_DECISION_GRANT_RELEASE", "true")
    assert Settings().case_decision_grant_release is True


@pytest.mark.parametrize("release", [False, True])
def test_the_configured_service_consumes_by_request_id_only_with_the_setting_on(
    monkeypatch: pytest.MonkeyPatch, release: bool
) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "case_decision_service", "grantex")
    monkeypatch.setattr(settings, "case_decision_grant_release", release)
    monkeypatch.setenv("GRANTEX_BASE_URL", ISSUER_ORIGIN)
    monkeypatch.setenv("GRANTEX_API_KEY", "test-only-key")
    service = dr.decision_service()
    assert isinstance(service, GrantexDecisionGrantService)
    assert service.consume_by_request_id is release


# --- setting off: exactly as before -----------------------------------------------------------------


async def test_with_the_setting_off_the_grants_are_read_from_the_status_and_presented() -> None:
    issuer = FakeDecisionIssuer(binding=False)
    service = grantex(issuer, release=False)
    case = case_row()
    view = await ask(service, case, "decline")
    issuer.approve(view.request_id, "user:9f:approver-a")
    issuer.approve(view.request_id, "user:9f:approver-b")

    grants = await service.grants(view.request_id)
    assert grants == issuer.tokens(view.request_id)
    consumed = await service.consume(grants=grants, action=case_action(case, "decline"), case_version="3")

    assert consumed.approvers == (("user:9f:approver-a", "dgnt_000000011"), ("user:9f:approver-b", "dgnt_000000012"))
    method, path, body = issuer.calls[-1]
    assert (method, path) == ("POST", "/v1/decisions/consume")
    assert set(body) == {"decisionGrants", "action", "caseVersion"}
    assert not any(p.endswith("/consume") and "/requests/" in p for p in paths(issuer))
    assert not any(p.endswith("/grants") for p in paths(issuer))


async def test_with_the_setting_off_an_issuer_binding_decisions_to_agents_releases_nothing() -> None:
    """Why the order matters: turn the issuer's binding on first and nothing can be recorded."""
    issuer = FakeDecisionIssuer(binding=True)
    service = grantex(issuer, release=False)
    view = await ask(service, case_row(), "approve")
    issuer.approve(view.request_id, "user:9f:approver-a")
    assert await service.grants(view.request_id) == []


async def test_with_the_setting_off_a_403_is_still_an_authentication_refusal() -> None:
    service = answering(403, {"reason": "decision_invalid", "subReason": "wrong_agent"}, release=False)
    with pytest.raises(DecisionServiceError) as refused:
        await service.consume(grants=["g1"], action={"case_id": "case_1"}, case_version="3")
    assert refused.value.reason == "decision_service_unauthorised"


async def test_with_the_setting_off_decision_grants_ready_is_not_read() -> None:
    payload = {
        "requestId": REQUEST_ID,
        "status": "approved",
        "action": {"case_id": "case_1"},
        "actionHash": "sha256:" + "1" * 64,
        "caseVersion": "3",
        "approvalsRequired": 1,
        "approvals": [
            {"sub": "user:a", "approverAuth": "sso", "dwellSource": "server", "position": 1, "issuedAt": "x"}
        ],
        "expiresAt": "2026-09-21T10:00:00Z",
        "decisionGrantsReady": "not-a-boolean",
        "agentId": "ag_other",
    }
    view = await answering(200, payload, release=False).get_request(REQUEST_ID)
    assert view.grants_ready is True


async def test_with_the_setting_off_consumption_by_request_id_is_never_attempted() -> None:
    issuer = FakeDecisionIssuer(binding=False)
    with pytest.raises(DecisionServiceError) as refused:
        await grantex(issuer, release=False).consume_request(
            request_id=REQUEST_ID, action={"case_id": "case_1"}, case_version="3"
        )
    assert refused.value.reason == "decision_service_not_configured"
    assert issuer.calls == []


# --- setting on: consumed by request id ---------------------------------------------------------------


@pytest.mark.parametrize("binding", [False, True], ids=["issuer_binding_off", "issuer_binding_on"])
async def test_with_the_setting_on_a_four_eyes_decision_is_consumed_by_request_id(binding: bool) -> None:
    """The console's own decision: the request names no agent and no grant is asked for or presented."""
    issuer = FakeDecisionIssuer(binding=binding)
    service = grantex(issuer, release=True)
    case = case_row()
    view = await ask(service, case, "decline")
    on_case(case, view)
    _, _, created = issuer.calls[1]
    assert "agentId" not in created and "grantId" not in created
    assert view.approvals_required == 2 and view.grants_ready is False

    issuer.approve(view.request_id, "user:9f:approver-a")
    assert (await service.get_request(view.request_id)).grants_ready is False
    issuer.approve(view.request_id, "user:9f:approver-b")
    assert (await service.get_request(view.request_id)).grants_ready is True

    check = await ServiceDecisionVerifier(service).verify(
        tenant_id="t", case=case, outcome="decline", grants=[], decision_request_id=view.request_id
    )
    assert check.allowed is True
    assert check.approvers == (("user:9f:approver-a", "dgnt_000000011"), ("user:9f:approver-b", "dgnt_000000012"))

    method, path, body = issuer.calls[-1]
    assert (method, path) == ("POST", f"/v1/decisions/requests/{view.request_id}/consume")
    assert body == {"action": case_action(case, "decline"), "caseVersion": "3"}
    # Nothing ever asked for a grant, and nothing the platform sent carries one.
    assert "POST /v1/decisions/consume" not in paths(issuer)
    assert not any(p.endswith("/grants") for p in paths(issuer))
    sent = repr([b for _, _, b in issuer.calls])
    assert all(token not in sent for token in issuer.tokens(view.request_id))


async def test_with_the_setting_on_the_platform_never_asks_for_a_grant() -> None:
    issuer = FakeDecisionIssuer(binding=False)
    with pytest.raises(DecisionServiceError) as refused:
        await grantex(issuer, release=True).grants(REQUEST_ID)
    assert refused.value.reason == "decision_grants_not_released"
    assert issuer.calls == []


async def test_the_release_endpoint_is_not_this_platforms_path() -> None:
    """The issuer releases grants only to the agent a request names; the console's names none."""
    issuer = FakeDecisionIssuer(binding=True, agent_grant_tokens={"agent-grant-token": ("ag_underwriter", "grnt_1")})
    view = await ask(grantex(issuer, release=True), case_row(), "approve")
    issuer.approve(view.request_id, "user:9f:approver-a")
    released = issuer.handle(
        httpx.Request(
            "POST",
            f"{ISSUER_ORIGIN}/v1/decisions/requests/{view.request_id}/grants",
            headers={"authorization": "Bearer test-only-key"},
            json={"grantToken": "agent-grant-token"},
        )
    )
    assert released.status_code == 403 and released.json()["subReason"] == "wrong_agent"


@pytest.mark.parametrize(
    ("status", "approvals", "issuer_ready", "ready"),
    [
        pytest.param("approved", 2, True, True, id="ready"),
        # Approved, but a grant was consumed, revoked or has expired: only the issuer knows.
        pytest.param("approved", 2, False, False, id="issuer_says_not_ready"),
        # The two disagree: the answer that withholds wins.
        pytest.param("pending", 1, True, False, id="contradiction_fails_closed"),
        # An issuer with its binding off sends no decisionGrantsReady at all.
        pytest.param("approved", 2, None, True, id="binding_off_no_field"),
        pytest.param("pending", 1, None, False, id="binding_off_pending"),
    ],
)
async def test_grants_ready_follows_the_issuers_decision_grants_ready(
    status: str, approvals: int, issuer_ready: bool | None, ready: bool
) -> None:
    payload = _status(status=status, approvals=approvals)
    if issuer_ready is not None:
        payload["decisionGrantsReady"] = issuer_ready
    view = await answering(200, payload).get_request(REQUEST_ID)
    assert view.grants_ready is ready
    assert view.as_dict()["grants_ready"] is ready


@pytest.mark.parametrize("value", ["true", 1, None, {}, []])
async def test_a_decision_grants_ready_that_is_not_a_boolean_is_refused(value: Any) -> None:
    payload = {**_status(status="approved", approvals=2), "decisionGrantsReady": value}
    with pytest.raises(DecisionServiceError) as refused:
        await answering(200, payload).get_request(REQUEST_ID)
    assert refused.value.reason == "decision_service_response_invalid"


@pytest.mark.parametrize(
    ("agent_id", "grant_id"),
    [("ag_other", None), (None, "grnt_other"), ("ag_other", "grnt_other"), ("", None), (17, None)],
)
async def test_a_request_that_names_an_agent_is_not_this_platforms_decision(agent_id: Any, grant_id: Any) -> None:
    payload = {**_status(status="approved", approvals=2), "agentId": agent_id, "grantId": grant_id}
    with pytest.raises(DecisionServiceError) as refused:
        await answering(200, payload).get_request(REQUEST_ID)
    assert (refused.value.reason, refused.value.detail) == ("decision_invalid", "wrong_agent")


async def test_asking_again_while_another_agents_request_is_open_is_refused_wrong_agent() -> None:
    """Issuer binding off: a repeat is answered with the open request, whichever agent it names."""
    issuer = FakeDecisionIssuer(binding=False)
    case = case_row()
    issuer.open_request(case_action(case, "approve"), "3", agent_id="ag_other", grant_id="grnt_other")
    with pytest.raises(DecisionServiceError) as refused:
        await ask(grantex(issuer, release=True), case, "approve")
    assert (refused.value.reason, refused.value.detail) == ("decision_invalid", "wrong_agent")

    # Issuer binding on: the issuer refuses it itself, with 409.
    bound = FakeDecisionIssuer(binding=True)
    bound.open_request(case_action(case, "approve"), "3", agent_id="ag_other", grant_id="grnt_other")
    with pytest.raises(DecisionServiceError) as refused:
        await ask(grantex(bound, release=True), case, "approve")
    assert (refused.value.reason, refused.value.detail) == ("decision_invalid", "wrong_agent")


@pytest.mark.parametrize("binding", [False, True], ids=["issuer_binding_off", "issuer_binding_on"])
async def test_consuming_another_agents_request_by_id_is_refused_wrong_agent(binding: bool) -> None:
    """The issuer answers 403 ``wrong_agent``: a refusal of this decision, not a failed sign-in."""
    issuer = FakeDecisionIssuer(binding=binding)
    case = case_row()
    request_id = issuer.open_request(case_action(case, "approve"), "3", agent_id="ag_other", grant_id="grnt_other")
    issuer.approve(request_id, "user:9f:approver-a")
    # Recorded on the case, as it has to be for the issuer to be asked at all.
    case.decision_requests = [{"request_id": request_id, "approvals_required": 1}]

    check = await ServiceDecisionVerifier(grantex(issuer, release=True)).verify(
        tenant_id="t", case=case, outcome="approve", grants=[], decision_request_id=request_id
    )
    assert (check.allowed, check.reason) == (False, "wrong_agent")
    assert issuer.requests[request_id].grants[0].consumed_at is None


async def test_with_the_setting_on_a_403_without_a_sub_reason_is_still_an_authentication_refusal() -> None:
    service = answering(403, {"message": "Forbidden", "code": "FORBIDDEN"})
    with pytest.raises(DecisionServiceError) as refused:
        await service.consume_request(request_id=REQUEST_ID, action={"case_id": "case_1"}, case_version="3")
    assert refused.value.reason == "decision_service_unauthorised"


async def test_a_wrong_api_key_is_still_an_authentication_refusal() -> None:
    issuer = FakeDecisionIssuer(binding=True, api_key="another-test-only-key")
    with pytest.raises(DecisionServiceError) as refused:
        await grantex(issuer, release=True).consume_request(
            request_id=REQUEST_ID, action={"case_id": "case_1"}, case_version="3"
        )
    assert refused.value.reason == "decision_service_unauthorised"


@pytest.mark.parametrize(
    ("approvers", "sub_reason"),
    [
        pytest.param([], "unknown_grant", id="nobody_approved_yet"),
        pytest.param(["user:9f:approver-a"], "four_eyes_incomplete", id="one_of_two"),
    ],
)
async def test_consuming_a_request_that_is_not_fully_approved_is_refused(approvers: list[str], sub_reason: str) -> None:
    issuer = FakeDecisionIssuer(binding=True)
    service = grantex(issuer, release=True)
    case = case_row()
    view = await ask(service, case, "decline")
    on_case(case, view)
    for approver in approvers:
        issuer.approve(view.request_id, approver)
    check = await ServiceDecisionVerifier(service).verify(
        tenant_id="t", case=case, outcome="decline", grants=[], decision_request_id=view.request_id
    )
    assert (check.allowed, check.reason) == (False, sub_reason)


async def test_consuming_by_request_id_is_bound_to_the_action_and_the_case_version() -> None:
    issuer = FakeDecisionIssuer(binding=True)
    service = grantex(issuer, release=True)
    case = case_row()
    view = await ask(service, case, "approve")
    on_case(case, view)
    issuer.approve(view.request_id, "user:9f:approver-a")
    verifier = ServiceDecisionVerifier(service)

    other_outcome = await verifier.verify(
        tenant_id="t", case=case, outcome="decline", grants=[], decision_request_id=view.request_id
    )
    assert (other_outcome.allowed, other_outcome.reason) == (False, "action_mismatch")

    other_tenant = on_case(case_row(case_ref=case.case_ref, tenant_id=uuid.uuid4()), view)
    cross_tenant = await verifier.verify(
        tenant_id="t", case=other_tenant, outcome="approve", grants=[], decision_request_id=view.request_id
    )
    assert (cross_tenant.allowed, cross_tenant.reason) == (False, "action_mismatch")

    await service.set_case_version(case.case_ref, "4")
    changed = await verifier.verify(
        tenant_id="t", case=on_case(case_row(case_ref=case.case_ref, tenant_id=case.tenant_id, version=4), view),
        outcome="approve", grants=[], decision_request_id=view.request_id,
    )  # fmt: skip
    assert (changed.allowed, changed.reason) == (False, "case_changed")


async def test_a_request_consumed_by_its_id_is_spent() -> None:
    issuer = FakeDecisionIssuer(binding=True)
    service = grantex(issuer, release=True)
    case = case_row()
    view = await ask(service, case, "approve")
    on_case(case, view)
    issuer.approve(view.request_id, "user:9f:approver-a")
    verifier = ServiceDecisionVerifier(service)
    by_id = {"tenant_id": "t", "case": case, "outcome": "approve", "grants": [], "decision_request_id": view.request_id}
    first = await verifier.verify(**by_id)
    assert first.allowed is True
    again = await verifier.verify(**by_id)
    assert (again.allowed, again.reason) == (False, "consumed")
    assert (await service.get_request(view.request_id)).grants_ready is False


def _receipt(**overrides: Any) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "consumed": True,
        "requestId": REQUEST_ID,
        "jtis": ["dgnt_1", "dgnt_2"],
        "approvers": [{"sub": "user:a", "jti": "dgnt_1"}, {"sub": "user:b", "jti": "dgnt_2"}],
        "actionHash": "sha256:" + "1" * 64,
    }
    receipt.update(overrides)
    return {k: v for k, v in receipt.items() if v is not _ABSENT}


_ABSENT = object()


def _status(*, status: str, approvals: int) -> dict[str, Any]:
    return {
        "requestId": REQUEST_ID,
        "status": status,
        "action": {"case_id": "case_1", "action": "case_decision", "decision": "decline", "subject": "mock:x"},
        "actionHash": "sha256:" + "1" * 64,
        "caseVersion": "3",
        "approvalsRequired": 2,
        "agentId": None,
        "grantId": None,
        "approvals": [
            {
                "jti": f"dgnt_{n}",
                "sub": f"user:{n}",
                "approverAuth": "sso",
                "dwellMs": 61250,
                "dwellSource": "server",
                "position": n,
                "issuedAt": "2026-09-20T10:00:00Z",
                "consumedAt": None,
            }
            for n in range(1, approvals + 1)
        ],  # fmt: skip
        "expiresAt": "2026-09-21T10:00:00Z",
    }


async def test_a_well_formed_receipt_records_each_approver_with_the_grant_they_spent() -> None:
    consumed = await answering(200, _receipt()).consume_request(
        request_id=REQUEST_ID, action={"case_id": "case_1"}, case_version="3"
    )
    assert consumed.request_id == REQUEST_ID
    assert consumed.approvers == (("user:a", "dgnt_1"), ("user:b", "dgnt_2"))


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"consumed": _ABSENT}, id="consumed_missing"),
        pytest.param({"consumed": False}, id="consumed_false"),
        pytest.param({"consumed": "true"}, id="consumed_not_a_boolean"),
        pytest.param({"requestId": _ABSENT}, id="request_id_missing"),
        pytest.param({"requestId": "dreq_00000002"}, id="another_request"),
        pytest.param({"jtis": _ABSENT}, id="jtis_missing"),
        pytest.param({"jtis": []}, id="jtis_empty"),
        pytest.param({"jtis": "dgnt_1"}, id="jtis_not_a_list"),
        pytest.param({"jtis": ["dgnt_1", 2]}, id="jti_not_a_string"),
        pytest.param({"jtis": ["dgnt_1", "dgnt_1"]}, id="jtis_repeated"),
        pytest.param({"approvers": _ABSENT}, id="approvers_missing"),
        pytest.param({"approvers": []}, id="approvers_empty"),
        pytest.param({"approvers": ["user:a", "user:b"]}, id="approver_not_an_object"),
        pytest.param({"approvers": [{"jti": "dgnt_1"}, {"sub": "user:b", "jti": "dgnt_2"}]}, id="approver_without_sub"),
        pytest.param({"approvers": [{"sub": "user:a"}, {"sub": "user:b", "jti": "dgnt_2"}]}, id="approver_without_jti"),
        pytest.param(
            {"approvers": [{"sub": "user:a", "jti": {"id": "dgnt_1"}}, {"sub": "user:b", "jti": "dgnt_2"}]},
            id="approver_jti_not_a_string",
        ),
        pytest.param(
            {"approvers": [{"sub": "user:a", "jti": "dgnt_9"}, {"sub": "user:b", "jti": "dgnt_2"}]},
            id="approver_spent_a_grant_not_consumed",
        ),
        pytest.param({"approvers": [{"sub": "user:a", "jti": "dgnt_1"}]}, id="a_consumed_grant_without_approver"),
        pytest.param(
            {"approvers": [{"sub": "user:a", "jti": "dgnt_1"}, {"sub": "user:b", "jti": "dgnt_1"}]},
            id="two_approvers_one_grant",
        ),
        pytest.param({"actionHash": _ABSENT}, id="action_hash_missing"),
    ],
)
async def test_a_receipt_that_is_not_a_confirmed_consumption_of_this_request_is_refused(
    overrides: dict[str, Any],
) -> None:
    """Who decided is recorded from this answer, so nothing in it is guessed or defaulted."""
    service = answering(200, _receipt(**overrides))
    with pytest.raises(DecisionServiceError) as refused:
        await service.consume_request(request_id=REQUEST_ID, action={"case_id": "case_1"}, case_version="3")
    assert refused.value.reason == "decision_service_response_invalid"
    # And the verifier turns it into a refusal of the decision.
    check = await ServiceDecisionVerifier(answering(200, _receipt(**overrides))).verify(
        tenant_id="t",
        case=case_row(recorded={REQUEST_ID: 2}),
        outcome="approve",
        grants=[],
        decision_request_id=REQUEST_ID,
    )
    assert (check.allowed, check.reason) == (False, "decision_service_response_invalid")


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        # An unreadable body reads as an empty answer, which confirms nothing.
        pytest.param(b"not json", "decision_service_response_invalid", id="not_json"),
        pytest.param([_receipt()], "decision_service_refused", id="a_list"),
        pytest.param("ok", "decision_service_refused", id="a_string"),
    ],
)
async def test_a_receipt_that_is_not_an_object_is_refused(body: Any, reason: str) -> None:
    check = await ServiceDecisionVerifier(answering(200, body)).verify(
        tenant_id="t",
        case=case_row(recorded={REQUEST_ID: 2}),
        outcome="approve",
        grants=[],
        decision_request_id=REQUEST_ID,
    )
    assert (check.allowed, check.reason) == (False, reason)


@pytest.mark.parametrize(
    ("required", "approvers"),
    [
        # Four eyes recorded on the case, one approver in the receipt: never one where two were needed.
        pytest.param(2, [("user:a", "dgnt_1")], id="one_approver_for_four_eyes"),
        pytest.param(1, [("user:a", "dgnt_1"), ("user:b", "dgnt_2")], id="more_approvers_than_required"),
        pytest.param(2, [("user:a", "dgnt_1"), ("user:a", "dgnt_2")], id="the_same_approver_twice"),
    ],
)
async def test_a_receipt_that_does_not_satisfy_the_approvals_the_case_recorded_is_refused(
    required: int, approvers: list[tuple[str, str]]
) -> None:
    """The issuer enforces four eyes; the platform still refuses a receipt that says it did not."""
    receipt = _receipt(
        jtis=[jti for _, jti in approvers], approvers=[{"sub": sub, "jti": jti} for sub, jti in approvers]
    )
    service = answering(200, receipt)
    check = await ServiceDecisionVerifier(service).verify(
        tenant_id="t", case=case_row(recorded={REQUEST_ID: required}), outcome="decline", grants=[],
        decision_request_id=REQUEST_ID,
    )  # fmt: skip
    assert (check.allowed, check.reason, check.approvers) == (False, "decision_service_response_invalid", ())
    # The issuer was asked: the answer, not the question, is what is refused.
    assert [request.url.path for request in service.seen] == [f"/v1/decisions/requests/{REQUEST_ID}/consume"]  # type: ignore[attr-defined]


async def test_a_request_not_recorded_on_the_case_is_refused_before_the_issuer_is_asked() -> None:
    service = answering(200, _receipt())
    check = await ServiceDecisionVerifier(service).verify(
        tenant_id="t", case=case_row(recorded={"dreq_00000002": 2}), outcome="approve", grants=[],
        decision_request_id=REQUEST_ID,
    )  # fmt: skip
    assert (check.allowed, check.reason) == (False, "decision_request_not_found")
    assert service.seen == []  # type: ignore[attr-defined]


@pytest.mark.parametrize("required", [None, 0, -1, "2", 2.0, True], ids=repr)
async def test_a_recorded_request_without_a_usable_approval_count_is_refused_before_the_issuer_is_asked(
    required: Any,
) -> None:
    """Nothing to hold the receipt to, so nothing is consumed: it is never read as one approver."""
    service = answering(200, _receipt())
    case = case_row()
    case.decision_requests = [{"request_id": REQUEST_ID, "approvals_required": required}]
    if required is None:
        case.decision_requests = [{"request_id": REQUEST_ID}]
    check = await ServiceDecisionVerifier(service).verify(
        tenant_id="t", case=case, outcome="approve", grants=[], decision_request_id=REQUEST_ID
    )
    assert (check.allowed, check.reason) == (False, "decision_request_not_found")
    assert service.seen == []  # type: ignore[attr-defined]


async def test_before_the_issuer_binding_the_status_carries_grants_the_platform_drops() -> None:
    """Setting on, issuer binding off: the status answer still carries the tokens.

    They reach this server in that answer and nowhere else: the parsed view keeps none of them,
    the status the console is served carries none, and nothing the platform sends carries one.
    """
    issuer = FakeDecisionIssuer(binding=False)
    service = grantex(issuer, release=True)
    case = case_row()
    view = await ask(service, case, "approve")
    on_case(case, view)
    issuer.approve(view.request_id, "user:9f:approver-a")
    tokens = issuer.tokens(view.request_id)

    answered = issuer.handle(
        httpx.Request(
            "GET",
            f"{ISSUER_ORIGIN}/v1/decisions/requests/{view.request_id}",
            headers={"authorization": "Bearer test-only-key"},
        )
    )
    assert answered.json()["decisionGrants"] == tokens

    status = await service.get_request(view.request_id)
    assert status.grants_ready is True
    assert all(token not in repr(status) and token not in repr(status.as_dict()) for token in tokens)
    check = await ServiceDecisionVerifier(service).verify(
        tenant_id="t", case=case, outcome="approve", grants=[], decision_request_id=view.request_id
    )
    assert check.allowed is True
    sent = repr([body for _, _, body in issuer.calls])
    assert all(token not in sent for token in tokens)


@pytest.mark.parametrize(
    ("status", "payload", "reason"),
    [
        (
            404,
            {"code": "NOT_FOUND", "reason": "decision_invalid", "subReason": "unknown_grant"},
            "decision_request_not_found",
        ),
        (404, {"code": "DECISION_GRANTS_DISABLED"}, "decision_service_disabled"),
        (409, {"code": "DECISION_INVALID", "reason": "decision_invalid", "subReason": "revoked"}, "revoked"),
        (503, {"code": "DECISION_AUDIT_UNAVAILABLE"}, "decision_service_refused"),
    ],
)
async def test_every_refusal_of_a_consumption_by_request_id_denies_the_decision(
    status: int, payload: dict[str, Any], reason: str
) -> None:
    check = await ServiceDecisionVerifier(answering(status, payload)).verify(
        tenant_id="t",
        case=case_row(recorded={REQUEST_ID: 2}),
        outcome="approve",
        grants=[],
        decision_request_id=REQUEST_ID,
    )
    assert (check.allowed, check.reason) == (False, reason)


async def test_an_unreachable_issuer_fails_consumption_by_request_id_closed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    transport = httpx.MockTransport(handler)
    service = GrantexDecisionGrantService(
        base_url=ISSUER_ORIGIN,
        api_key="test-only-key",
        client_factory=lambda: httpx.AsyncClient(transport=transport, base_url=ISSUER_ORIGIN),
        **_mode(True),
    )
    check = await ServiceDecisionVerifier(service).verify(
        tenant_id="t",
        case=case_row(recorded={REQUEST_ID: 2}),
        outcome="approve",
        grants=[],
        decision_request_id=REQUEST_ID,
    )
    assert (check.allowed, check.reason) == (False, "decision_service_unavailable")


async def test_consumption_by_request_id_runs_under_the_tighter_deadline() -> None:
    """The case row is locked while it runs, as it is for presented grants."""
    service = answering(200, _receipt())
    service.consume_timeout_seconds = 2.5
    await service.consume_request(request_id=REQUEST_ID, action={"case_id": "case_1"}, case_version="3")
    (request,) = service.seen  # type: ignore[attr-defined]
    assert request.extensions["timeout"]["read"] == 2.5


@pytest.mark.parametrize("request_id", ["", "../consume", "dreq 1", "x" * 129])
async def test_a_request_id_the_issuer_could_not_have_issued_is_refused_before_any_call(request_id: str) -> None:
    service = answering(200, _receipt())
    with pytest.raises(DecisionServiceError) as refused:
        await service.consume_request(request_id=request_id, action={"case_id": "case_1"}, case_version="3")
    assert refused.value.reason == "decision_request_not_found"
    assert service.seen == []  # type: ignore[attr-defined]


# --- the verifier and the recorded decision ---------------------------------------------------------


async def test_the_verifier_refuses_a_request_id_together_with_grants() -> None:
    """One or the other: a caller holding grants and naming a request has not said which it means."""
    service = answering(200, _receipt())
    check = await ServiceDecisionVerifier(service).verify(
        tenant_id="t", case=case_row(), outcome="approve", grants=["g1"], decision_request_id=REQUEST_ID
    )
    assert (check.allowed, check.reason) == (False, "decision_invalid")
    assert service.seen == []  # type: ignore[attr-defined]


async def test_the_verifier_with_neither_grants_nor_a_request_id_still_requires_a_decision() -> None:
    service = answering(200, _receipt())
    check = await ServiceDecisionVerifier(service).verify(tenant_id="t", case=case_row(), outcome="approve", grants=[])
    assert (check.allowed, check.reason) == (False, "decision_required")
    assert service.seen == []  # type: ignore[attr-defined]


async def test_recording_a_decision_passes_the_request_id_only_when_there_is_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verifier written before consumption by request id existed is called exactly as before."""
    from core.cases import decisions as case_decisions

    async def no_transition(session: Any, case: Any, target: Any, **kwargs: Any) -> Any:
        case.state = target.value
        return case

    monkeypatch.setattr(case_decisions, "transition", no_transition)
    monkeypatch.setattr(case_decisions, "validate", lambda *args: None)
    monkeypatch.setattr(case_decisions, "business_case_document", lambda case: {})
    received: list[dict[str, Any]] = []

    class Spy:
        async def verify(self, **kwargs: Any) -> DecisionCheck:
            received.append(kwargs)
            return DecisionCheck(allowed=True, approvers=(("user:a", "dgnt_1"),))

    await record_decision(None, case_row(), outcome="approve", grants=["g1"], verifier=Spy(), actor="user:x")  # type: ignore[arg-type]
    await record_decision(
        None, case_row(), outcome="approve", grants=[], verifier=Spy(), actor="user:x",  # type: ignore[arg-type]
        decision_request_id=REQUEST_ID,
    )  # fmt: skip
    assert "decision_request_id" not in received[0]
    assert received[1]["decision_request_id"] == REQUEST_ID and received[1]["grants"] == []


# --- the in-memory service, in the same mode ------------------------------------------------------------


async def test_the_fake_service_consumes_by_request_id_as_the_issuer_does() -> None:
    fake = FakeDecisionGrantService(consume_by_request_id=True)
    case = case_row()
    action = case_action(case, "decline")
    view = await fake.create_request(
        action=action, case_version="3", memo="memo", policy_score=POLICY, four_eyes_on=("decline",)
    )
    on_case(case, view)
    with pytest.raises(DecisionServiceError) as refused:
        await fake.grants(view.request_id)
    assert refused.value.reason == "decision_grants_not_released"

    with pytest.raises(DecisionServiceError) as refused:
        await fake.consume_request(request_id=view.request_id, action=action, case_version="3")
    assert refused.value.detail == "unknown_grant"
    fake.approve(view.request_id, "user:9f:approver-a")
    with pytest.raises(DecisionServiceError) as refused:
        await fake.consume_request(request_id=view.request_id, action=action, case_version="3")
    assert refused.value.detail == "four_eyes_incomplete"
    fake.approve(view.request_id, "user:9f:approver-b")
    assert (await fake.get_request(view.request_id)).grants_ready is True

    check = await ServiceDecisionVerifier(fake).verify(
        tenant_id="t", case=case, outcome="decline", grants=[], decision_request_id=view.request_id
    )
    assert check.allowed is True
    assert check.approvers == (
        ("user:9f:approver-a", f"jti-{view.request_id}-1"),
        ("user:9f:approver-b", f"jti-{view.request_id}-2"),
    )
    assert (await fake.get_request(view.request_id)).grants_ready is False
    with pytest.raises(DecisionServiceError) as refused:
        await fake.consume_request(request_id=view.request_id, action=action, case_version="3")
    assert refused.value.detail == "consumed"


async def test_the_fake_service_does_not_consume_by_request_id_with_the_setting_off() -> None:
    fake = FakeDecisionGrantService()
    with pytest.raises(DecisionServiceError) as refused:
        await fake.consume_request(request_id=REQUEST_ID, action={"case_id": "case_1"}, case_version="3")
    assert refused.value.reason == "decision_service_not_configured"
