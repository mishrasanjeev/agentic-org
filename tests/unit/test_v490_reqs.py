"""Tests for v4.9.0 P0 requirements: REQ-01, REQ-02, REQ-03, REQ-04, REQ-05, REQ-07."""

from __future__ import annotations

import time

import pytest

# ═══════════════════════════════════════════════════════════════════════════
# REQ-03: RLS session fix — verify get_tenant_session usage
# ═══════════════════════════════════════════════════════════════════════════


class TestREQ03RLSSessionFix:
    """Verify that tenant-scoped files use get_tenant_session, not async_session_factory."""

    @pytest.mark.parametrize("module_path", [
        "api/v1/invoices.py",
        "api/v1/workflow_variants.py",
    ])
    def test_no_async_session_factory_in_tenant_scoped_files(self, module_path):
        """These files must not use async_session_factory for tenant queries."""
        from pathlib import Path
        content = Path(module_path).read_text()
        # Should import get_tenant_session
        assert "get_tenant_session" in content, f"{module_path} must use get_tenant_session"
        # Should not import async_session_factory
        assert "async_session_factory" not in content, f"{module_path} must not use async_session_factory"

    def test_branding_uses_both_correctly(self):
        """branding.py uses async_session_factory for public route and get_tenant_session for admin."""
        from pathlib import Path
        content = Path("api/v1/branding.py").read_text()
        assert "get_tenant_session" in content
        # async_session_factory is allowed for the public /branding GET (no tenant context)
        assert "async_session_factory" in content

    def test_sso_admin_uses_tenant_session(self):
        """SSO admin CRUD endpoints use get_tenant_session."""
        from pathlib import Path
        content = Path("api/v1/sso.py").read_text()
        assert "get_tenant_session" in content


# ═══════════════════════════════════════════════════════════════════════════
# REQ-04: Auth state to Redis
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def no_auth_state_redis(monkeypatch):
    """Make `core.auth_state` take its in-memory path.

    Setting `auth_state._redis = None` does not do that: `None` means "not
    created yet", so the next call builds a client and reaches whatever Redis
    the machine runs (FINDINGS A-59). Patching the accessor is the only way to
    say "there is no Redis".
    """
    from unittest.mock import AsyncMock

    from core import auth_state

    monkeypatch.setattr(auth_state, "_redis", None)
    monkeypatch.setattr(auth_state, "_get_redis", AsyncMock(return_value=None))


class TestREQ04AuthStateRedis:
    """Verify core.auth_state module works correctly."""

    @pytest.mark.asyncio
    async def test_record_auth_failure_returns_false_under_limit(self, no_auth_state_redis):
        # Reset in-memory state
        from core import auth_state
        from core.auth_state import record_auth_failure
        auth_state._mem_failures.clear()
        auth_state._mem_blocked.clear()

        result = await record_auth_failure("192.0.2.10")
        assert result is False

    @pytest.mark.asyncio
    async def test_the_limit_blocks_and_earlier_attempts_do_not(self, no_auth_state_redis):
        """The control fires: attempt AUTH_MAX_FAILURES blocks, the ones before it do not.

        Without this the suite only asserted the permissive answer, which a
        deleted control returns just as happily.
        """
        from core import auth_state
        from core.auth_state import AUTH_MAX_FAILURES, is_ip_blocked, record_auth_failure
        auth_state._mem_failures.clear()
        auth_state._mem_blocked.clear()
        ip = "192.0.2.13"

        for attempt in range(1, AUTH_MAX_FAILURES):
            assert await record_auth_failure(ip) is False, f"blocked early at attempt {attempt}"
            assert await is_ip_blocked(ip) is False

        assert await record_auth_failure(ip) is True, "the limit did not block"
        assert await is_ip_blocked(ip) is True

    @pytest.mark.asyncio
    async def test_a_block_is_bound_to_its_own_ip(self, no_auth_state_redis):
        from core import auth_state
        from core.auth_state import AUTH_MAX_FAILURES, is_ip_blocked, record_auth_failure
        auth_state._mem_failures.clear()
        auth_state._mem_blocked.clear()

        for _ in range(AUTH_MAX_FAILURES):
            await record_auth_failure("192.0.2.14")

        assert await is_ip_blocked("192.0.2.14") is True
        assert await is_ip_blocked("192.0.2.15") is False

    @pytest.mark.asyncio
    async def test_a_block_expires_and_the_ip_is_allowed_again(self, no_auth_state_redis):
        """The block is a lockout, not a ban: it lifts when its window passes."""
        from core import auth_state
        from core.auth_state import AUTH_MAX_FAILURES, is_ip_blocked, record_auth_failure
        auth_state._mem_failures.clear()
        auth_state._mem_blocked.clear()
        ip = "192.0.2.16"

        for _ in range(AUTH_MAX_FAILURES):
            await record_auth_failure(ip)
        assert await is_ip_blocked(ip) is True

        # Expire the block rather than waiting 15 minutes for it.
        auth_state._mem_blocked[ip] = time.time() - 1
        assert await is_ip_blocked(ip) is False

    @pytest.mark.asyncio
    async def test_ip_not_blocked_initially(self, no_auth_state_redis):
        from core import auth_state
        from core.auth_state import is_ip_blocked
        auth_state._mem_blocked.clear()

        result = await is_ip_blocked("192.0.2.11")
        assert result is False

    @pytest.mark.asyncio
    async def test_token_blacklist_roundtrip(self, no_auth_state_redis):
        from core import auth_state
        from core.auth_state import blacklist_token, is_token_blacklisted
        auth_state._mem_blacklist.clear()

        token = "test-jwt-token-12345"
        await blacklist_token(token)
        assert await is_token_blacklisted(token) is True
        assert await is_token_blacklisted("other-token") is False

    @pytest.mark.asyncio
    async def test_signup_rate_not_blocked_initially(self, no_auth_state_redis):
        from core import auth_state
        from core.auth_state import check_signup_rate
        auth_state._mem_signup.clear()

        result = await check_signup_rate("192.0.2.12")
        assert result is False

    @pytest.mark.asyncio
    async def test_signup_rate_blocks_past_the_hourly_limit(self, no_auth_state_redis):
        """The control fires: signup SIGNUP_MAX_PER_HOUR + 1 is refused."""
        from core import auth_state
        from core.auth_state import SIGNUP_MAX_PER_HOUR, check_signup_rate
        auth_state._mem_signup.clear()
        ip = "192.0.2.17"

        for attempt in range(1, SIGNUP_MAX_PER_HOUR + 1):
            assert await check_signup_rate(ip) is False, f"refused early at signup {attempt}"

        assert await check_signup_rate(ip) is True, "the hourly limit did not refuse"
        assert await check_signup_rate("192.0.2.18") is False, "another address was refused too"

    @pytest.mark.asyncio
    async def test_a_blacklisted_token_stays_blacklisted_and_others_do_not(
        self, no_auth_state_redis
    ):
        """The blacklist is per token, and repeated checks keep saying so."""
        from core import auth_state
        from core.auth_state import blacklist_token, is_token_blacklisted
        auth_state._mem_blacklist.clear()

        await blacklist_token("placeholder-revoked-token")  # noqa: S106 - not a credential
        for _ in range(3):
            assert await is_token_blacklisted("placeholder-revoked-token") is True
        assert await is_token_blacklisted("placeholder-other-token") is False

    def test_middleware_imports_auth_state(self):
        """auth/middleware.py imports from core.auth_state, not in-memory dicts."""
        from pathlib import Path
        content = Path("auth/middleware.py").read_text()
        assert "from core.auth_state import" in content
        assert "_failed_attempts" not in content
        assert "_blocked_ips" not in content

    def test_grantex_middleware_imports_auth_state(self):
        """auth/grantex_middleware.py imports from core.auth_state."""
        from pathlib import Path
        content = Path("auth/grantex_middleware.py").read_text()
        assert "from core.auth_state import" in content
        assert "def _record_failure" not in content


# ═══════════════════════════════════════════════════════════════════════════
# REQ-05: Async Redis
# ═══════════════════════════════════════════════════════════════════════════


class TestREQ05AsyncRedis:
    """Verify async Redis client and SSO/billing use it."""

    def test_async_redis_module_exists(self):
        from core.async_redis import get_async_redis
        assert callable(get_async_redis)

    def test_sso_uses_async_redis(self):
        """SSO replay marker uses async Redis, not sync _get_redis.

        Bug sheet 2026-09-14 row 7: login-flow state moved out of Redis
        (signed state + encrypted flow cookie); Redis only backs the
        best-effort one-shot replay marker, via the async client.
        """
        from pathlib import Path
        content = Path("api/v1/sso.py").read_text()
        assert "get_async_redis" in content
        assert "await r.set(" in content and "nx=True" in content
        assert "from core.billing.usage_tracker import _get_redis" not in content

    def test_billing_subscription_uses_async_redis(self):
        """Billing subscription endpoint uses async Redis."""
        from pathlib import Path
        # 2026-09-13: entitlement moved to the durable billing_subscriptions
        # row (core/billing/subscriptions.py); Redis is a cache warmed via the
        # async client. The route module must not touch a sync Redis client.
        content = Path("api/v1/billing.py").read_text()
        assert "get_subscription(" in content
        assert "redis.from_url(" not in content and "redis.Redis(" not in content
        subs = Path("core/billing/subscriptions.py").read_text()
        assert "core.async_redis" in subs


# ═══════════════════════════════════════════════════════════════════════════
# REQ-07: Connector secret encryption
# ═══════════════════════════════════════════════════════════════════════════


class TestREQ07ConnectorSecrets:
    """Verify connector secret encryption is end-to-end."""

    def test_gateway_no_plaintext_fallback(self):
        """Gateway must NOT fall back to Connector.auth_config."""
        from pathlib import Path
        content = Path("core/tool_gateway/gateway.py").read_text()
        assert "db_connector.auth_config" not in content

    def test_backfill_script_exists(self):
        """Backfill script exists and is importable."""
        from core.crypto.backfill_connector_secrets import backfill
        assert callable(backfill)

    def test_connector_test_reads_encrypted(self):
        """Connector test endpoint reads from ConnectorConfig."""
        from pathlib import Path
        content = Path("api/v1/connectors.py").read_text()
        # The test endpoint should reference ConnectorConfig
        assert "ConnectorConfig" in content
        assert "credentials_encrypted" in content


# ═══════════════════════════════════════════════════════════════════════════
# REQ-01: Composio marketplace runtime deps
# ═══════════════════════════════════════════════════════════════════════════


class TestREQ01ComposioRuntime:
    """Verify Composio is guarded while its SDK has unsafe Pillow pins."""

    def test_composio_core_not_in_production_extras(self):
        """composio-core must stay out of production extras until patched."""
        from pathlib import Path
        content = Path("pyproject.toml").read_text()
        v4_block = content.split("v4 = [", 1)[1].split("]", 1)[0]
        assert '"composio-core' not in v4_block

    def test_dockerfile_installs_v4_extras(self):
        """Dockerfile must still install production-safe v4 extras."""
        from pathlib import Path
        content = Path("Dockerfile").read_text()
        assert '".[v4]"' in content, "Dockerfile must install .[v4] extras"

    def test_dockerfile_runtime_has_libjpeg(self):
        """Runtime stage must include libjpeg62-turbo for patched Pillow."""
        from pathlib import Path
        content = Path("Dockerfile").read_text()
        # libjpeg62-turbo must be in the runtime apt-get (after the second FROM)
        runtime = content.split("FROM python")[-1]
        assert "libjpeg62-turbo" in runtime, "runtime stage must include libjpeg62-turbo"

    def test_composio_api_is_guarded_when_sdk_missing(self):
        """Marketplace API must fail closed when the SDK is not installed."""
        from pathlib import Path
        content = Path("api/v1/composio.py").read_text()
        assert "_COMPOSIO_AVAILABLE = False" in content
        assert 'HTTPException(503, "Composio SDK not installed' in content

    def test_health_diagnostics_exposes_composio(self):
        """Admin diagnostics endpoint must surface composio sdk/api_key state."""
        from pathlib import Path
        content = Path("api/v1/health.py").read_text()
        assert "composio" in content.lower()
        assert "sdk_loaded" in content
        assert "api_key_configured" in content


# ═══════════════════════════════════════════════════════════════════════════
# REQ-02: Alembic as sole DDL delivery path
# ═══════════════════════════════════════════════════════════════════════════


class TestREQ02AlembicDDL:
    """Verify Alembic is wired as the schema authority."""

    def test_alembic_ini_exists(self):
        """alembic.ini must exist at repo root."""
        from pathlib import Path
        assert Path("alembic.ini").exists(), "alembic.ini must exist"

    def test_alembic_env_exists(self):
        """migrations/env.py must exist and reference BaseModel.metadata."""
        from pathlib import Path
        env = Path("migrations/env.py")
        assert env.exists(), "migrations/env.py must exist"
        content = env.read_text()
        assert "target_metadata" in content
        assert "BaseModel" in content or "Base.metadata" in content

    def test_ci_migration_guard_exists(self):
        """CI must enforce that model changes ship with a migration."""
        from pathlib import Path
        guard = Path("scripts/check_migration_required.py")
        assert guard.exists(), "migration guard script must exist"

    def test_alembic_migrate_wrapper_exists(self):
        """Idempotent migrate wrapper used by deploy pipeline."""
        from pathlib import Path
        wrapper = Path("scripts/alembic_migrate.py")
        assert wrapper.exists(), "scripts/alembic_migrate.py must exist"
        content = wrapper.read_text()
        assert "BASELINE_REVISION" in content
        assert "stamp" in content
        assert "upgrade" in content

    def test_deploy_uses_migrate_wrapper(self):
        """deploy.yml must invoke the wrapper, not raw 'alembic upgrade'."""
        from pathlib import Path
        content = Path(".github/workflows/deploy.yml").read_text()
        assert "scripts/alembic_migrate.py" in content

    def test_init_db_verifies_alembic_in_strict_runtime(self):
        """init_db() must verify Alembic state instead of relying on startup DDL."""
        from pathlib import Path
        content = Path("core/database.py").read_text()
        assert "verify_runtime_schema_current" in content
        assert "is_strict_runtime_env(settings.env)" in content
        assert "AGENTICORG_ENABLE_LEGACY_STARTUP_DDL" in content

    @pytest.mark.skip(
        reason="Helm chart removed in Stage 4 of the Cloud Run cost-cut migration. "
        "Runtime schema safety is now enforced by strict init_db() verification "
        "and the Cloud Run migration job."
    )
    def test_helm_sets_alembic_flag(self):
        """Helm production values must enable alembic-managed DDL."""
        from pathlib import Path
        content = Path("helm/values.yaml").read_text()
        assert 'AGENTICORG_ENABLE_LEGACY_STARTUP_DDL: "0"' in content
