# SPDX-License-Identifier: Apache-2.0
"""Development scripts against real Postgres: test-database reset and the seed entry point."""

from __future__ import annotations

import json
import os
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest

from scripts import reset_test_database, seed_dev

DB_URL = os.getenv("AGENTICORG_DB_URL", "")

pytestmark = pytest.mark.skipif(not DB_URL, reason="integration tests require AGENTICORG_DB_URL")


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _admin_connection() -> psycopg2.extensions.connection:
    parts = urlsplit(DB_URL)
    conn = psycopg2.connect(host=parts.hostname, port=parts.port or 5432, user=parts.username,
                            password=parts.password, dbname="postgres")
    conn.autocommit = True
    return conn


def test_reset_recreates_an_empty_throwaway_database() -> None:
    probe = "agenticorg_reset_probe_test"
    url = _with_database(DB_URL, probe)
    try:
        assert reset_test_database.reset(url) == probe
        parts = urlsplit(url)
        with psycopg2.connect(host=parts.hostname, port=parts.port or 5432, user=parts.username,
                              password=parts.password, dbname=probe) as conn, conn.cursor() as cur:
            cur.execute("CREATE TABLE leftover (id int)")
        assert reset_test_database.reset(url) == probe
        with psycopg2.connect(host=parts.hostname, port=parts.port or 5432, user=parts.username,
                              password=parts.password, dbname=probe) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.leftover')")
            assert cur.fetchone() == (None,)
    finally:
        conn = _admin_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{probe}" WITH (FORCE)')  # noqa: S608 - fixed name
        finally:
            conn.close()


def test_reset_main_refuses_a_non_test_database_on_a_real_server(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTICORG_DB_URL", _with_database(DB_URL, "postgres"))
    assert reset_test_database.main() == 2
    assert "refusing to drop it" in capsys.readouterr().err


def test_seed_main_prints_the_seeded_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], _setup_schema: None
) -> None:
    monkeypatch.setenv("AGENTICORG_ENV", "test")
    monkeypatch.delenv("AGENTICORG_SEED_PASSWORD", raising=False)
    assert seed_dev.main() == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["tenant"] == seed_dev.TENANT_SLUG
    assert [user["oidc_sub"] for user in summary["users"]] == ["dev-approver-a", "dev-approver-b"]

    monkeypatch.setenv("AGENTICORG_ENV", "production")
    assert seed_dev.main() == 2
    assert "refusing to seed" in capsys.readouterr().err
