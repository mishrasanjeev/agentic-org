# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Withdrawing authority: pause and retire revoke, the middleware refuses revoked and inactive agents."""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from auth import revocation
from auth.token_pool import RevocationOutcome, TokenPool


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(revocation.settings, "grantex_revocation_check", "feed")  # the default is off
    revocation.reset_for_tests()
    yield
    revocation.reset_for_tests()


class _Feed:
    def __init__(self, *, revoked: set[str] | None = None, fresh: bool = True, error: Exception | None = None):
        self.revoked = revoked or set()
        self.fresh = fresh
        self.error = error
        self.stopped = False

    def is_fresh(self, now=None):
        return self.fresh

    def match(self, *, grant_id=None, token_id=None, parent_grant_id=None):
        if self.error is not None:
            raise self.error
        for candidate in (grant_id, token_id):
            if candidate and candidate in self.revoked:
                return SimpleNamespace(kind="grant", id=candidate, action="revoke")
        return None

    def stop(self):
        self.stopped = True


class TestCheck:
    def test_off_mode_never_consults_the_feed(self, monkeypatch):
        monkeypatch.setattr(revocation.settings, "grantex_revocation_check", "off")
        revocation.set_feed_for_tests(_Feed(revoked={"g1"}))
        assert revocation.check("g1", "t1") == revocation.RevocationState(False, "check_off")

    def test_no_api_key_means_no_feed_and_an_unknown_state(self, monkeypatch):
        monkeypatch.setattr(revocation.external_keys, "grantex_api_key", "")
        assert revocation.feed() is None
        assert revocation.check("g1", "t1") == revocation.RevocationState(None, "feed_unavailable")

    def test_an_unbuildable_feed_is_unknown_and_not_rebuilt_for_a_minute(self, monkeypatch):
        monkeypatch.setattr(revocation.external_keys, "grantex_api_key", "key")
        attempts = []

        def _boom():
            attempts.append(1)
            raise RuntimeError("no network")

        monkeypatch.setattr(revocation, "_build_feed", _boom)
        assert revocation.feed() is None and revocation.feed() is None
        assert len(attempts) == 1
        assert revocation.check("g1", None).detail == "feed_unavailable"

    def test_the_feed_decides_revoked_current_stale_and_error(self):
        revocation.set_feed_for_tests(_Feed(revoked={"g1", "tok-9"}))
        assert revocation.check("g1", "t1") == revocation.RevocationState(True, "revoked")
        assert revocation.check("g2", "tok-9").revoked is True
        assert revocation.check("g2", "t2") == revocation.RevocationState(False, "current")
        revocation.set_feed_for_tests(_Feed(fresh=False))
        assert revocation.check("g1", "t1") == revocation.RevocationState(None, "feed_stale")
        revocation.set_feed_for_tests(_Feed(error=ValueError("bad entry")))
        assert revocation.check("g1", "t1") == revocation.RevocationState(None, "feed_error:ValueError")


class TestRefusal:
    def test_a_retired_agent_is_refused_before_the_feed_is_asked(self):
        revocation.set_feed_for_tests(_Feed())
        assert revocation.refusal_reason(grant_id="g", token_id="t", agent_status="retired") == "agent_inactive"

    def test_a_paused_agent_is_refused_only_while_paused_agents_are_refused(self, monkeypatch):
        revocation.set_feed_for_tests(_Feed())
        monkeypatch.setattr(revocation.settings, "paused_agents_refused", False)
        assert revocation.refusal_reason(grant_id="g", token_id="t", agent_status="paused") is None
        monkeypatch.setattr(revocation.settings, "paused_agents_refused", True)
        assert revocation.refusal_reason(grant_id="g", token_id="t", agent_status="paused") == "agent_inactive"

    def test_a_revoked_grant_is_refused(self):
        revocation.set_feed_for_tests(_Feed(revoked={"g"}))
        assert revocation.refusal_reason(grant_id="g", token_id="t", agent_status="active") == "grant_revoked"
        assert revocation.refusal_reason(grant_id="other", token_id="t", agent_status="active") is None

    def test_an_unknown_state_refuses_in_a_strict_runtime_and_allows_elsewhere(self, monkeypatch):
        revocation.set_feed_for_tests(_Feed(fresh=False))
        monkeypatch.setattr(revocation.settings, "env", "production")
        assert revocation.refusal_reason(grant_id="g", token_id="t", agent_status="active") == "revocation_unknown"
        monkeypatch.setattr(revocation.settings, "env", "test")
        assert revocation.refusal_reason(grant_id="g", token_id="t", agent_status="active") is None


class _Redis:
    """Enough of a Redis client for the pool's revocation path."""

    def __init__(self, entries: dict[str, str]):
        self.entries = dict(entries)
        self.deleted: list[str] = []
        self.published: list[tuple[str, str]] = []

    async def scan_iter(self, match: str):
        prefix = match.rstrip("*")
        for key in list(self.entries):
            if key.startswith(prefix):
                yield key

    async def get(self, key):
        return self.entries.get(key)

    async def delete(self, key):
        self.entries.pop(key, None)
        self.deleted.append(key)

    async def publish(self, channel, payload):
        self.published.append((channel, payload))

    async def setex(self, key, ttl, value):
        self.entries[key] = value


def _grant(token: str, grant_id: str = "g"):
    from auth.token_pool import RunGrantToken

    return RunGrantToken(token=token, grant_id=grant_id, expires_at=4_000_000_000.0, source="minted", ttl_seconds=900)


class TestPool:
    def test_revoke_agent_grants_evicts_everywhere_and_counts_the_remote_result(self):
        pool = TokenPool()
        tenant, agent, other = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        pool._local_grants[f"grant:run:{tenant}:{agent}:aaa"] = _grant("tok-local")
        pool._local_grants[f"grant:run:{tenant}:{other}:bbb"] = _grant("tok-other")
        redis = _Redis(
            {
                f"grant:run:{tenant}:{agent}:aaa": json.dumps({"token": "tok-local", "grant_id": "g"}),
                f"grant:run:{tenant}:{agent}:ccc": json.dumps({"token": "tok-shared", "grant_id": "g2"}),
                f"grant:run:{tenant}:{other}:bbb": json.dumps({"token": "tok-other", "grant_id": "g3"}),
                f"agent:{agent}:token": json.dumps({"access_token": "legacy"}),
            }
        )
        pool.redis = redis
        task = MagicMock()
        pool._refresh_tasks[agent] = task
        revoked: list[str] = []

        async def revoker(token: str) -> bool:
            revoked.append(token)
            return token != "tok-shared"

        outcome = asyncio.run(pool.revoke_agent_grants(tenant, agent, revoker))
        assert outcome == RevocationOutcome(evicted=2, revoked=1, failed=1) and outcome.token_revoked is False
        assert sorted(revoked) == ["tok-local", "tok-shared"]
        assert (
            f"grant:run:{tenant}:{other}:bbb" in pool._local_grants
            and f"grant:run:{tenant}:{agent}:aaa" not in pool._local_grants
        )
        assert f"grant:run:{tenant}:{other}:bbb" in redis.entries and f"agent:{agent}:token" not in redis.entries
        assert redis.published == [("agenticorg:token:revoke", agent)]
        task.cancel.assert_called_once()
        assert agent not in pool._refresh_tasks

    def test_nothing_cached_is_an_honest_success_without_opening_a_client(self):
        pool = TokenPool()
        calls = []

        async def revoker(token: str) -> bool:
            calls.append(token)
            return True

        outcome = asyncio.run(pool.revoke_agent_grants("t", "a", revoker))
        assert outcome == RevocationOutcome(evicted=0, revoked=0, failed=0) and outcome.token_revoked is True
        assert calls == [] and pool._loop_clients == {}

    def test_a_broadcast_evicts_the_in_process_grants_of_that_agent(self):
        pool = TokenPool()
        tenant, agent, other = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        pool._local_grants[f"grant:run:{tenant}:{agent}:aaa"] = _grant("tok-a")
        pool._local_grants[f"grant:run:{tenant}:{other}:bbb"] = _grant("tok-b")
        assert pool._evict_local_agent_grants(agent) == ["tok-a"]
        assert list(pool._local_grants) == [f"grant:run:{tenant}:{other}:bbb"]

    def test_a_delegated_grant_is_kept_for_its_lifetime(self):
        pool = TokenPool()
        assert asyncio.run(pool.store_delegated_grant("child", {"token": "x"})) is False
        redis = _Redis({})
        pool.redis = redis
        assert (
            asyncio.run(pool.store_delegated_grant("child", {"token": "x", "expires_in": 120, "grant_id": "g"})) is True
        )
        assert json.loads(redis.entries["agent:child:delegated_grant"])["grant_id"] == "g"


class TestRevokeAgentAuthority:
    def test_revokes_through_the_authority_service_and_counts_failures(self, monkeypatch):
        from auth import grantex as grantex_module
        from auth import token_pool as pool_module

        pool = TokenPool()
        tenant, agent = str(uuid.uuid4()), str(uuid.uuid4())
        pool._local_grants[f"grant:run:{tenant}:{agent}:aaa"] = _grant("tok-ok")
        pool._local_grants[f"grant:run:{tenant}:{agent}:bbb"] = _grant("tok-bad", "g2")
        monkeypatch.setattr(pool_module, "token_pool", pool)

        async def _revoke(token: str) -> None:
            if token == "tok-bad":
                raise RuntimeError("authority service unavailable")

        monkeypatch.setattr(grantex_module.grantex_client, "revoke_token", _revoke)
        outcome = asyncio.run(revocation.revoke_agent_authority(tenant, agent))
        assert outcome == RevocationOutcome(evicted=2, revoked=1, failed=1)

    def test_pause_and_retire_report_the_real_result(self, monkeypatch):
        from api.v1 import agents as api

        agent_id = uuid.uuid4()
        tenant = str(uuid.uuid4())
        agent = SimpleNamespace(id=agent_id, status="active", owner_user_id=None, visibility="shared")
        session = MagicMock()
        session.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: agent))
        session.add = MagicMock()

        class _Ctx:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: _Ctx())
        monkeypatch.setattr(api, "require_agent_mutable", lambda *_a, **_k: None)
        monkeypatch.setattr(
            api,
            "revoke_agent_authority",
            AsyncMock(return_value=RevocationOutcome(evicted=2, revoked=1, failed=1)),
        )
        result = asyncio.run(api.pause_agent(agent_id=agent_id, tenant_id=tenant))
        assert result["status"] == "paused" and result["token_revoked"] is False
        assert result["grants_revoked"] == 1 and result["grants_failed"] == 1
        api.revoke_agent_authority.assert_awaited_once_with(tenant, str(agent_id))


class TestMiddleware:
    def test_an_inactive_or_revoked_agent_token_is_refused_after_verification(self, monkeypatch):
        from auth import grantex_middleware as gm

        verified = gm.VerifiedGrantexToken(
            principal_id="user-1",
            agent_did="did:grantex:agent-abc",
            developer_id="dev-1",
            scopes=("agents.read",),
            grant_id="g-1",
            delegation_depth=0,
            issued_at=1,
            token_id="tok-1",
        )
        monkeypatch.setattr(gm, "grantex_expected_issuer", lambda: "https://issuer.example")
        monkeypatch.setattr(gm, "grantex_expected_audience", lambda: "agenticorg")
        monkeypatch.setattr(gm, "_is_grantex_token", lambda _token: True)
        monkeypatch.setattr(gm, "_verify_grantex_token_sync", lambda *_a, **_k: verified)
        tenant, agent = str(uuid.uuid4()), str(uuid.uuid4())
        status = {"value": "active"}
        monkeypatch.setattr(
            gm, "_resolve_agent_tenant", AsyncMock(side_effect=lambda _did: (tenant, agent, status["value"]))
        )
        revocation.set_feed_for_tests(_Feed())
        claims = asyncio.run(gm.resolve_grantex_claims("header.payload.signature"))
        assert claims["agenticorg:agent_id"] == agent and claims["grantex:grant_id"] == "g-1"
        status["value"] = "retired"
        with pytest.raises(gm.GrantexAuthError, match="agent_inactive"):
            asyncio.run(gm.resolve_grantex_claims("header.payload.signature"))
        status["value"] = "active"
        revocation.set_feed_for_tests(_Feed(revoked={"g-1"}))
        with pytest.raises(gm.GrantexAuthError, match="grant_revoked"):
            asyncio.run(gm.resolve_grantex_claims("header.payload.signature"))
        revocation.set_feed_for_tests(_Feed(fresh=False))
        monkeypatch.setattr(revocation.settings, "env", "production")
        with pytest.raises(gm.GrantexAuthError, match="revocation_unknown"):
            asyncio.run(gm.resolve_grantex_claims("header.payload.signature"))


class _ParentAwareFeed(_Feed):
    def match(self, *, grant_id=None, token_id=None, parent_grant_id=None):
        if parent_grant_id and parent_grant_id in self.revoked:
            return SimpleNamespace(kind="grant", id=parent_grant_id, action="revoke")
        return super().match(grant_id=grant_id, token_id=token_id, parent_grant_id=parent_grant_id)


class TestReviewFollowUps:
    def test_the_feed_check_is_off_by_default(self):
        from core.config import Settings

        assert Settings.model_fields["grantex_revocation_check"].default == "off"

    def test_a_revoked_parent_grant_refuses_its_delegated_children(self):
        revocation.set_feed_for_tests(_ParentAwareFeed(revoked={"parent"}))
        assert revocation.check("child", "t1", "parent").revoked is True
        assert revocation.check("child", "t1", None).revoked is False
        assert (
            revocation.refusal_reason(grant_id="child", token_id="t1", agent_status="active", parent_grant_id="parent")
            == revocation.REASON_REVOKED
        )

    def test_the_verified_token_carries_its_parent_grant(self):
        from auth.grantex_middleware import VerifiedGrantexToken

        token = VerifiedGrantexToken(
            principal_id="p",
            agent_did="did:a",
            developer_id="d",
            scopes=(),
            grant_id="g",
            delegation_depth=1,
            issued_at=1,
            token_id="t",
            parent_grant_id="parent",
        )
        assert token.parent_grant_id == "parent"
        assert VerifiedGrantexToken("p", "did:a", "d", (), "g", 0, 1).parent_grant_id == ""

    def test_the_delegated_grant_is_revoked_and_deleted_with_the_rest(self):
        import asyncio

        from auth.token_pool import TokenPool

        pool = TokenPool()
        pool.redis = _Redis({"agent:a1:delegated_grant": '{"grantToken": "tok-delegated", "grantId": "gd"}'})
        revoked: list[str] = []

        async def revoker(token: str) -> bool:
            revoked.append(token)
            return True

        outcome = asyncio.run(pool.revoke_agent_grants("t", "a1", revoker))
        assert revoked == ["tok-delegated"] and outcome.evicted == 1 and outcome.token_revoked
        assert "agent:a1:delegated_grant" in pool.redis.deleted

    def test_delegated_ttl_reads_the_sdk_fields(self):
        import time
        from datetime import UTC, datetime

        from auth.token_pool import _delegated_ttl_seconds, _grant_token_of

        assert _delegated_ttl_seconds({"expiresIn": 120}) == 120
        assert _delegated_ttl_seconds({"expires_in": "90"}) == 90
        later = datetime.fromtimestamp(time.time() + 600, tz=UTC).isoformat().replace("+00:00", "Z")
        assert 590 <= _delegated_ttl_seconds({"expiresIn": "10m", "expiresAt": later}) <= 600
        assert _delegated_ttl_seconds({}) == 8 * 3600
        assert _grant_token_of('{"grantToken": "x"}') == "x" and _grant_token_of("not json") == ""

    def test_the_middleware_forwards_the_parent_grant_to_the_feed(self, monkeypatch):
        from auth import grantex_middleware as gm

        verified = gm.VerifiedGrantexToken(
            principal_id="user-1",
            agent_did="did:grantex:agent-abc",
            developer_id="dev-1",
            scopes=("agents.read",),
            grant_id="child",
            delegation_depth=1,
            issued_at=1,
            token_id="tok-1",
            parent_grant_id="parent",
        )
        monkeypatch.setattr(gm, "grantex_expected_issuer", lambda: "https://issuer.example")
        monkeypatch.setattr(gm, "grantex_expected_audience", lambda: "agenticorg")
        monkeypatch.setattr(gm, "_is_grantex_token", lambda _token: True)
        monkeypatch.setattr(gm, "_verify_grantex_token_sync", lambda *_a, **_k: verified)
        tenant, agent = str(uuid.uuid4()), str(uuid.uuid4())
        monkeypatch.setattr(gm, "_resolve_agent_tenant", AsyncMock(return_value=(tenant, agent, "active")))
        revocation.set_feed_for_tests(_ParentAwareFeed())
        assert asyncio.run(gm.resolve_grantex_claims("header.payload.signature"))["grantex:grant_id"] == "child"
        revocation.set_feed_for_tests(_ParentAwareFeed(revoked={"parent"}))
        with pytest.raises(gm.GrantexAuthError, match="grant_revoked"):
            asyncio.run(gm.resolve_grantex_claims("header.payload.signature"))
