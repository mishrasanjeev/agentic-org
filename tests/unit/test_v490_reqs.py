"""Tests for v4.9.0 P0 requirements: REQ-01, REQ-02, REQ-03, REQ-04, REQ-05, REQ-07."""

from __future__ import annotations

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


class TestREQ04AuthStateRedis:
    """Verify core.auth_state module works correctly."""

    @pytest.mark.asyncio
    async def test_record_auth_failure_returns_false_under_limit(self):
        # Reset in-memory state
        from core import auth_state
        from core.auth_state import record_auth_failure
        auth_state._mem_failures.clear()
        auth_state._mem_blocked.clear()
        auth_state._redis = None  # force in-memory

        result = await record_auth_failure("192.168.1.1")
        assert result is False

    @pytest.mark.asyncio
    async def test_ip_not_blocked_initially(self):
        from core import auth_state
        from core.auth_state import is_ip_blocked
        auth_state._mem_blocked.clear()
        auth_state._redis = None

        result = await is_ip_blocked("10.0.0.1")
        assert result is False

    @pytest.mark.asyncio
    async def test_token_blacklist_roundtrip(self):
        from core import auth_state
        from core.auth_state import blacklist_token, is_token_blacklisted
        auth_state._mem_blacklist.clear()
        auth_state._redis = None

        token = "test-jwt-token-12345"
        await blacklist_token(token)
        assert await is_token_blacklisted(token) is True
        assert await is_token_blacklisted("other-token") is False

    @pytest.mark.asyncio
    async def test_signup_rate_not_blocked_initially(self):
        from core import auth_state
        from core.auth_state import check_signup_rate
        auth_state._mem_signup.clear()
        auth_state._redis = None

        result = await check_signup_rate("172.16.0.1")
        assert result is False

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
        """SSO state store uses async Redis, not sync _get_redis."""
        from pathlib import Path
        content = Path("api/v1/sso.py").read_text()
        assert "await r.setex" in content or "await r.get" in content
        assert "from core.billing.usage_tracker import _get_redis" not in content

    def test_billing_subscription_uses_async_redis(self):
        """Billing subscription endpoint uses async Redis."""
        from pathlib import Path
        content = Path("api/v1/billing.py").read_text()
        assert "await redis.get" in content
        # Cancel endpoint should also be async
        assert "await redis.set" in content or "await redis.delete" in content


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
    """Verify Composio SDK is installable and declared in runtime path."""

    def test_composio_core_in_v4_extras(self):
        """composio-core must be in [project.optional-dependencies].v4."""
        from pathlib import Path
        content = Path("pyproject.toml").read_text()
        assert "composio-core" in content, "composio-core must be a declared dep"

    def test_dockerfile_installs_v4_extras(self):
        """Dockerfile must install the v4 extras so composio-core is in the image."""
        from pathlib import Path
        content = Path("Dockerfile").read_text()
        assert '".[v4]"' in content, "Dockerfile must install .[v4] extras"

    def test_dockerfile_runtime_has_libjpeg(self):
        """Runtime stage must include libjpeg62-turbo for Pillow (composio dep)."""
        from pathlib import Path
        content = Path("Dockerfile").read_text()
        # libjpeg62-turbo must be in the runtime apt-get (after the second FROM)
        runtime = content.split("FROM python")[-1]
        assert "libjpeg62-turbo" in runtime, "runtime stage must include libjpeg62-turbo"

    def test_composio_sdk_importable(self):
        """composio SDK must import cleanly in the test env (matches runtime)."""
        try:
            import composio  # noqa: F401
        except ImportError:
            raise  # composio-core is a declared dependency; missing import is a packaging error

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

    def test_init_db_respects_alembic_flag(self):
        """init_db() must short-circuit when AGENTICORG_DDL_MANAGED_BY_ALEMBIC is truthy."""
        from pathlib import Path
        content = Path("core/database.py").read_text()
        assert "AGENTICORG_DDL_MANAGED_BY_ALEMBIC" in content

    @pytest.mark.skip(
        reason="Helm chart removed in Stage 4 of the Cloud Run cost-cut migration. "
        "AGENTICORG_DDL_MANAGED_BY_ALEMBIC is now set in the Cloud Run service env "
        "vars. Followup: rewrite to assert against Cloud Run config."
    )
    def test_helm_sets_alembic_flag(self):
        """Helm production values must enable alembic-managed DDL."""
        from pathlib import Path
        content = Path("helm/values.yaml").read_text()
        assert 'AGENTICORG_DDL_MANAGED_BY_ALEMBIC: "true"' in content
