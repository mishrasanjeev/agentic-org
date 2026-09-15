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
    keys = ["tenant", "user:dev-approver-a", "user:dev-approver-b", "sso:dev-oidc", "approval-policy:four-eyes"]
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
