# SPDX-License-Identifier: Apache-2.0
"""Who may act on a governed case: the four actions a person is accountable for need a human session.

Agent tokens carry tool scopes and are deliberately exempt from the RBAC scope families
(``api.route_enforcement._check_scope``), and an API key's scopes say nothing about who holds it,
so the route itself has to refuse them. These tests pin that refusal per route, and pin that an
actor recorded on a case always says what kind of credential acted.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from api.v1.governed_cases import (
    DecisionRequest,
    actor_for,
    approve_information_request,
    decide_governed_case,
    human_actor_for,
    review_screening_disposition,
    withdraw_case,
)
from core.agents.screening_disposition import DispositionReviewRequest
from core.cases.runtime import CaseRuntime
from core.cases.states import CaseError

TENANT = "0c9f2a5e-0000-4000-8000-000000000001"
CASE_REF = "case_0123456789abcdef01234567"


def _request(claims: dict[str, Any], auth_mode: str | None, scopes: list[str] | None = None) -> Any:
    return SimpleNamespace(
        state=SimpleNamespace(claims=claims, auth_mode=auth_mode, scopes=scopes or [], tenant_id=TENANT)
    )


def _human(sub: str = "analyst@tenant.invalid", **claims: Any) -> Any:
    return _request({"sub": sub, **claims}, "legacy", ["approvals:write"])


def _api_key() -> Any:
    return _request({"sub": "apikey:ao_key_prefix", "grantex:scopes": ["approvals:write"]}, "api_key",
                    ["approvals:write"])  # fmt: skip


def _agent_token() -> Any:
    return _request(
        {"sub": "agent:underwriter", "agenticorg:agent_id": "5f0a", "grantex:grant_id": "grant-1"},
        "grantex",
        ["tool:mock:read"],
    )


def _refusing_runtime() -> CaseRuntime:
    def unusable(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a refused caller must not reach the database")

    return CaseRuntime(session_factory=unusable, provider_factory=unusable)


MACHINE_CALLERS = [pytest.param(_api_key, id="api_key"), pytest.param(_agent_token, id="agent_token")]


def _body(response: Any) -> dict[str, Any]:
    return dict(json.loads(bytes(response.body).decode()))


async def _call_route(name: str, request: Any) -> Any:
    runtime = _refusing_runtime()
    if name == "withdraw":
        return await withdraw_case(CASE_REF, request, tenant_id=TENANT, runtime=runtime)
    if name == "decision":
        return await decide_governed_case(
            CASE_REF, DecisionRequest(outcome="approve"), request, tenant_id=TENANT, runtime=runtime
        )
    if name == "disposition_review":
        return await review_screening_disposition(
            CASE_REF,
            "hit-1",
            DispositionReviewRequest(action="accepted", final_outcome="true_match"),
            request,
            tenant_id=TENANT,
            runtime=runtime,
        )
    return await approve_information_request(CASE_REF, "a" * 64, request, tenant_id=TENANT, runtime=runtime)


HUMAN_ONLY_ROUTES = ["withdraw", "decision", "disposition_review", "information_request_approval"]


@pytest.mark.parametrize("route", HUMAN_ONLY_ROUTES)
@pytest.mark.parametrize("caller", MACHINE_CALLERS)
async def test_a_machine_caller_cannot_take_a_human_only_case_action(route: str, caller: Any) -> None:
    response = await _call_route(route, caller())
    assert response.status_code == 403
    assert _body(response)["error"]["reason"] == "human_session_required"


@pytest.mark.parametrize("route", HUMAN_ONLY_ROUTES)
async def test_a_human_session_passes_the_gate_on_every_human_only_route(route: str) -> None:
    # The gate is the first thing each route does, so a human gets past it and is refused later
    # (here: the tenant flag cannot be read, which counts as off).
    response = await _call_route(route, _human())
    assert response.status_code != 403 or _body(response)["error"]["reason"] != "human_session_required"


def test_an_actor_says_what_kind_of_credential_acted() -> None:
    assert actor_for(_human(**{"agenticorg:user_id": "u-1"})) == "user:u-1"
    assert actor_for(_human()) == "user:analyst@tenant.invalid"
    assert actor_for(_api_key()) == "api_key:ao_key_prefix"
    assert actor_for(_agent_token()) == "agent:5f0a"
    # An unknown mode is a machine until proven otherwise, never a person.
    assert actor_for(_request({"sub": "x"}, "mtls")).startswith("machine:")


def test_a_session_with_no_subject_is_refused() -> None:
    with pytest.raises(CaseError) as refused:
        actor_for(_request({"role": "analyst"}, "legacy"))
    assert refused.value.reason == "actor_unknown" and refused.value.status == 401


@pytest.mark.parametrize("caller", MACHINE_CALLERS)
def test_human_actor_refuses_every_machine_credential(caller: Any) -> None:
    with pytest.raises(CaseError) as refused:
        human_actor_for(caller())
    assert refused.value.reason == "human_session_required" and refused.value.status == 403


def test_a_legacy_session_whose_subject_looks_like_an_api_key_is_still_a_machine() -> None:
    """The subject prefix is checked as well as the mode, so a mislabelled session cannot pass."""
    with pytest.raises(CaseError):
        human_actor_for(_request({"sub": "apikey:ao_key_prefix"}, "legacy"))


def test_a_session_token_minted_for_an_agent_is_a_machine_even_in_legacy_mode() -> None:
    """A human session names the person it belongs to; a token with only an agent id does not."""
    agent_session = _request({"sub": "runner", "agenticorg:agent_id": "5f0a"}, "legacy", ["approvals:write"])
    assert actor_for(agent_session) == "agent:5f0a"
    with pytest.raises(CaseError):
        human_actor_for(agent_session)


def test_a_person_whose_token_also_carries_an_agent_id_is_still_a_person() -> None:
    """Session tokens carry an agent id for the tool gateway; that must not lock a person out."""
    person = _human(**{"agenticorg:user_id": "u-1", "agenticorg:agent_id": "5f0a"})
    assert human_actor_for(person) == "user:u-1"


def test_agent_tokens_skip_the_rbac_scope_family_so_the_route_gate_is_what_refuses_them() -> None:
    """Pins why the gate lives on the route: scope enforcement never sees an agent token."""
    from api.route_enforcement import _check_scope

    meta = {"auth_required": True, "scope": "approvals.governed_cases.write"}
    agent = _agent_token()
    agent.method, agent.url = "POST", SimpleNamespace(path="/governed-cases/x/decision")
    _check_scope(agent, meta)  # no exception: tool scopes are enforced by the tool gateway instead

    api_key = _api_key()
    api_key.method, api_key.url = "POST", SimpleNamespace(path="/governed-cases/x/decision")
    _check_scope(api_key, meta)  # an API key with approvals:write satisfies the family
