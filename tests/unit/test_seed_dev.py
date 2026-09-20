# SPDX-License-Identifier: Apache-2.0
"""Development seed (scripts/seed_dev.py): guards and dataset, without a database."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import seed_dev


@pytest.mark.parametrize("runtime", ["", "production", "staging", "prod", "qa"])
async def test_seed_refuses_outside_development_before_connecting(
    runtime: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlalchemy.ext.asyncio as sa_async

    def no_engine(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not connect when the runtime is not development")

    monkeypatch.setattr(sa_async, "create_async_engine", no_engine)
    with pytest.raises(seed_dev.SeedError, match="refusing to seed"):
        await seed_dev.seed("postgresql+asyncpg://unused@192.0.2.1/none", {"AGENTICORG_ENV": runtime})


@pytest.mark.parametrize("runtime", ["development", "local", "test"])
def test_development_runtimes_are_accepted(runtime: str) -> None:
    seed_dev.assert_development_runtime({"AGENTICORG_ENV": runtime})


def test_seeded_users_are_the_oidc_stub_identities() -> None:
    users = {user.sub: user for user in seed_dev.load_users()}
    stub = json.loads(seed_dev.OIDC_STUB_CONFIG.read_text(encoding="utf-8"))
    stub_emails = {entry["sub"]: entry["email"] for entry in stub["users"]}
    assert set(users) == {"dev-approver-a", "dev-approver-b"}
    for sub, user in users.items():
        assert user.email == stub_emails[sub]
        assert user.email.endswith("@example.com")


def test_missing_stub_identity_fails_closed(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"users": [{"sub": "dev-approver-a", "email": "approver.a@example.com"}]}))
    with pytest.raises(seed_dev.SeedError, match="dev-approver-b"):
        seed_dev.load_users(config)


def test_non_reserved_email_domain_is_refused(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    users = [
        {"sub": "dev-approver-a", "email": "approver.a@example.com"},
        {"sub": "dev-approver-b", "email": "someone@mail.invalid.test"},
    ]
    config.write_text(json.dumps({"users": users}))
    with pytest.raises(seed_dev.SeedError, match="example.com"):
        seed_dev.load_users(config)


def test_seed_ids_are_stable_and_distinct() -> None:
    keys = ["tenant", "user:dev-approver-a", "user:dev-approver-b", "sso:dev-oidc", "approval-policy:two-step"]
    keys += [agent.key for agent in seed_dev.AGENTS]
    ids = [seed_dev.seed_id(key) for key in keys]
    assert ids == [seed_dev.seed_id(key) for key in keys]
    assert len(set(ids)) == len(ids)


def test_password_is_optional_and_must_be_long_enough() -> None:
    assert seed_dev._password_hash({}) is None
    with pytest.raises(seed_dev.SeedError, match="at least 12"):
        seed_dev._password_hash({"AGENTICORG_SEED_PASSWORD": "short"})


def test_main_fails_closed_without_database_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("AGENTICORG_DB_URL", raising=False)
    assert seed_dev.main() == 2
    assert "AGENTICORG_DB_URL is not set" in capsys.readouterr().err


@pytest.mark.parametrize(
    "environ",
    [
        {"AGENTICORG_ENV": "development", "APP_ENV": "production"},
        {"AGENTICORG_ENV": "development", "ENVIRONMENT": "staging"},
        {"AGENTICORG_ENV": "test", "NODE_ENV": "production"},
        {"AGENTICORG_ENV": "local", "ENV": "prod"},
    ],
)
def test_seed_refuses_production_markers_in_other_variables(environ: dict[str, str]) -> None:
    with pytest.raises(seed_dev.SeedError, match="production-like runtime"):
        seed_dev.assert_development_runtime(environ)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://agenticorg:agenticorg_dev@postgres:5432/agenticorg",
        "postgresql+asyncpg://agenticorg:agenticorg_dev@127.0.0.1:58310/agenticorg",
        "postgresql+asyncpg://agenticorg:agenticorg_dev@localhost/agenticorg_test",
        "postgresql+asyncpg://agenticorg:agenticorg_dev@[::1]:5432/agenticorg",
    ],
)
def test_local_databases_are_accepted(url: str) -> None:
    seed_dev.assert_local_database(url, {})


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("postgresql+asyncpg://u:p@db.example.com:5432/agenticorg", "is not local"),
        ("postgresql+asyncpg://u:p@192.0.2.10:5432/agenticorg", "is not local"),
        ("postgresql+asyncpg://u:p@postgres.example.com/agenticorg", "is not local"),
        ("postgresql+asyncpg:///agenticorg", "is not local"),
        ("postgresql+asyncpg://u:p@localhost/agenticorg?host=db.example.com", "query parameters"),
        ("postgresql+asyncpg://u:p@localhost:99999/agenticorg", "cannot be parsed"),
    ],
)
def test_non_local_databases_are_refused_before_connecting(
    url: str, reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlalchemy.ext.asyncio as sa_async

    def no_engine(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not connect to a database it refuses")

    monkeypatch.setattr(sa_async, "create_async_engine", no_engine)
    with pytest.raises(seed_dev.SeedError, match=reason):
        seed_dev.assert_local_database(url, {})
    with pytest.raises(seed_dev.SeedError, match=reason):
        import asyncio

        asyncio.run(seed_dev.seed(url, {"AGENTICORG_ENV": "development"}))


def test_remote_database_needs_the_explicit_override() -> None:
    url = "postgresql+asyncpg://u:p@db.example.com:5432/agenticorg"
    seed_dev.assert_local_database(url, {seed_dev.ALLOW_REMOTE_DB_ENV: "1"})
    for value in ("true", "yes", "0", ""):
        with pytest.raises(seed_dev.SeedError):
            seed_dev.assert_local_database(url, {seed_dev.ALLOW_REMOTE_DB_ENV: value})


def test_seeded_approval_steps_use_a_role_the_approval_flow_knows() -> None:
    from api.v1.approvals import _ROLE_HIERARCHY

    assert seed_dev.APPROVAL_STEP_ROLE in _ROLE_HIERARCHY
    assert set(seed_dev.USER_ROLES.values()) == {(seed_dev.APPROVAL_STEP_ROLE, "backoffice")}
