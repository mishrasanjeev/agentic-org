# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Pure URL-gate tests. No database or Redis connection is opened."""

from __future__ import annotations

import pytest

from tests.security.test_mfa_state_integration import _configuration

PG = "postgresql+asyncpg://synthetic:synthetic@postgres/mfa_state_tests"
REDIS = "redis://redis:6379/14"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("MFA_STATE_TEST_ENABLE", "1")
    monkeypatch.setenv("MFA_STATE_TEST_REQUIRED", "1")
    monkeypatch.setenv("MFA_STATE_TEST_PG_URL", PG)
    monkeypatch.setenv("MFA_STATE_TEST_REDIS_URL", REDIS)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    return monkeypatch


@pytest.mark.parametrize(
    "pg,redis",
    [
        (PG + "?host=external.example", REDIS),
        (PG + "?host=127.0.0.1", REDIS),
        (PG.replace("@postgres/", "@postgres:6543/"), REDIS),
        (PG, REDIS.replace(":6379/", ":6380/")),
        (PG.replace("/mfa_state_tests", "/shared_database"), REDIS),
        (PG, REDIS.replace("/14", "/0")),
        (PG, REDIS + "?host=external.example"),
        (PG, REDIS + "#external.example"),
        (PG.replace("postgresql+asyncpg", "postgresql"), REDIS),
        (PG, REDIS.replace("redis://", "rediss://")),
        (PG.replace("@postgres/", "@ao-mfa-public.example/"), REDIS),
        (PG, REDIS.replace("//redis:", "//ao-mfa-public.example:")),
    ],
)
def test_integration_gate_rejects_unsafe_endpoint_options(configured, pg, redis):
    configured.setenv("MFA_STATE_TEST_PG_URL", pg)
    configured.setenv("MFA_STATE_TEST_REDIS_URL", redis)
    with pytest.raises(pytest.fail.Exception):
        _configuration()


@pytest.mark.parametrize(
    "pg_host,redis_host",
    [
        ("localhost", "localhost"),
        ("127.0.0.1", "127.0.0.1"),
        ("postgres", "redis"),
        ("mfa-test-postgres", "mfa-test-redis"),
    ],
)
def test_integration_gate_accepts_only_explicit_local_aliases(configured, pg_host, redis_host):
    pg = PG.replace("@postgres/", f"@{pg_host}:5432/")
    redis = REDIS.replace("//redis:", f"//{redis_host}:")
    configured.setenv("MFA_STATE_TEST_PG_URL", pg)
    configured.setenv("MFA_STATE_TEST_REDIS_URL", redis)
    assert _configuration() == (pg, redis)


def test_integration_gate_requires_urls_in_required_mode(configured):
    configured.delenv("MFA_STATE_TEST_PG_URL")
    with pytest.raises(pytest.fail.Exception, match="dedicated PostgreSQL/Redis URLs required"):
        _configuration()


def test_integration_gate_skips_without_explicit_opt_in(configured):
    configured.delenv("MFA_STATE_TEST_ENABLE")
    configured.delenv("MFA_STATE_TEST_REQUIRED")
    with pytest.raises(pytest.skip.Exception, match="Explicit MFA_STATE_TEST_ENABLE"):
        _configuration()


def test_integration_gate_refuses_parallel_workers(configured):
    configured.setenv("PYTEST_XDIST_WORKER", "gw0")
    with pytest.raises(pytest.fail.Exception, match="Run serially"):
        _configuration()
