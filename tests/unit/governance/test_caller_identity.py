# SPDX-License-Identifier: Apache-2.0
"""The caller's identity: derived from the auth middleware's request state and bound for the request."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from core.governance import caller_identity as ci


def _state(**over):
    base = {"claims": {}, "auth_mode": None}
    base.update(over)
    return SimpleNamespace(**base)


class TestIdentityFromState:
    def test_an_api_key_names_the_key_as_principal_and_its_name_as_application(self):
        state = _state(claims={"sub": "apikey:ak_12ab"}, auth_mode="api_key", api_key_name="Advisory App")
        identity = ci.identity_from_state(state)
        assert identity == ci.CallerIdentity(
            principal="api_key:ak_12ab", application="advisory app", auth_mode="api_key"
        )

    def test_an_api_key_without_a_name_is_its_own_application(self):
        identity = ci.identity_from_state(_state(claims={"sub": "apikey:ak_12ab"}, auth_mode="api_key"))
        assert identity.application == "api_key:ak_12ab" and identity.principal == "api_key:ak_12ab"

    def test_a_passport_names_the_subject_and_the_agent(self):
        state = _state(claims={"sub": "did:example:1", "agenticorg:agent_id": "a-1"}, auth_mode="grantex")
        identity = ci.identity_from_state(state)
        assert identity.principal == "grantex:did:example:1" and identity.application == "agent:a-1"

    def test_a_passport_falls_back_to_the_state_agent(self):
        state = _state(claims={"sub": "did:example:1"}, auth_mode="grantex", agent_id="a-2")
        assert ci.identity_from_state(state).application == "agent:a-2"

    def test_a_human_session_names_the_user_and_the_console(self):
        state = _state(
            claims={"sub": "u@example.test", "agenticorg:user_id": "11111111-1111-1111-1111-111111111111"},
            auth_mode="legacy",
        )
        identity = ci.identity_from_state(state)
        assert identity.principal == "user:11111111-1111-1111-1111-111111111111" and identity.application == "console"

    def test_a_human_session_without_a_user_id_uses_the_subject_and_an_authorised_party(self):
        identity = ci.identity_from_state(_state(claims={"sub": "u@example.test", "azp": "Portal"}, auth_mode="legacy"))
        assert identity.principal == "user:u@example.test" and identity.application == "portal"

    def test_a_buyer_credential_is_the_commerce_application(self):
        identity = ci.identity_from_state(_state(claims={"sub": "commerce-buyer:b1"}, auth_mode="commerce_buyer"))
        assert identity.principal == "commerce-buyer:b1" and identity.application == "commerce"

    def test_an_unknown_or_missing_mode_carries_nothing_a_policy_matches(self):
        assert ci.identity_from_state(_state()) == ci.CallerIdentity(auth_mode=None)
        assert ci.identity_from_state(_state(claims="not a dict", auth_mode="weird")) == ci.CallerIdentity(
            auth_mode="weird"
        )
        assert ci.identity_from_state(SimpleNamespace()) == ci.CallerIdentity()

    def test_to_dict_round_trips(self):
        identity = ci.CallerIdentity(principal="user:1", application="console", auth_mode="legacy")
        assert identity.to_dict() == {"principal": "user:1", "application": "console", "auth_mode": "legacy"}


class TestBinding:
    def test_bind_and_reset_scope_the_identity(self):
        assert ci.current_identity() is None
        token = ci.bind_identity(ci.CallerIdentity(principal="user:1", application="console"))
        try:
            assert ci.current_identity().principal == "user:1"
        finally:
            ci.reset_identity(token)
        assert ci.current_identity() is None

    def test_the_auth_middleware_binds_the_identity_for_the_rest_of_the_request_and_clears_it_after(self):
        from auth.grantex_middleware import GrantexAuthMiddleware

        seen: list[ci.CallerIdentity | None] = []

        async def call_next(_request):
            seen.append(ci.current_identity())
            return "response"

        request = SimpleNamespace(state=_state(claims={"sub": "apikey:ak_1"}, auth_mode="api_key", api_key_name="Ops"))
        result = asyncio.run(GrantexAuthMiddleware._continue(request, call_next))
        assert result == "response"
        assert seen == [ci.CallerIdentity(principal="api_key:ak_1", application="ops", auth_mode="api_key")]
        assert ci.current_identity() is None

    def test_the_middleware_clears_the_identity_when_the_request_fails(self):
        from auth.grantex_middleware import GrantexAuthMiddleware

        async def call_next(_request):
            raise RuntimeError("boom")

        request = SimpleNamespace(state=_state(claims={"sub": "u"}, auth_mode="legacy"))
        try:
            asyncio.run(GrantexAuthMiddleware._continue(request, call_next))
        except RuntimeError:
            pass
        assert ci.current_identity() is None

    def test_every_credential_path_of_the_middleware_hands_off_through_the_binding(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[3] / "auth" / "grantex_middleware.py").read_text(encoding="utf-8")
        # The exempt-path pass-throughs stay unbound; every verified credential binds.
        assert src.count("return await self._continue(request, call_next)") == 4
        assert "request.state.api_key_name = matched_key.name" in src
