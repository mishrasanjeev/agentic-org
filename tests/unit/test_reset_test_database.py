# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from scripts import reset_test_database as reset


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://agenticorg:agenticorg_dev@postgres:5432/agenticorg_test",
        "postgresql://u:p@127.0.0.1:58310/other_test",
    ],
)
def test_accepts_throwaway_test_databases(url: str) -> None:
    assert reset.safe_test_database_name(url).endswith("_test")


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("postgresql+asyncpg://agenticorg:agenticorg_dev@postgres:5432/agenticorg", "does not end in '_test'"),
        ("postgresql://u:p@host:5432/_test", "does not end in '_test'"),
        ("postgresql://u:p@host:5432/test_data", "does not end in '_test'"),
        ("postgresql://u:p@host:5432/", "does not name exactly one database"),
        ("postgresql://u:p@host:5432/a/b_test", "does not name exactly one database"),
        ('postgresql://u:p@host:5432/x"y_test', "characters other than"),
        # URL tricks: the drop must target exactly the database the name check saw.
        ("postgresql://u:p@host:5432/agenticorg_test?dbname=agenticorg", "query parameters"),
        ("postgresql://u:p@host:5432/agenticorg_test?options=-csearch_path%3Dpublic", "query parameters"),
        ("postgresql+asyncpg://u:p@host/agenticorg_test?database=agenticorg", "query parameters"),
        ("postgresql://u:p@host:5432/agenticorg_test#agenticorg", "query parameters"),
        ("postgresql://u:p@host:5432/agenticorg?x=_test", "query parameters"),
        ("postgresql://u:p@host:5432/agenticorg%2F_test", "percent-encoded"),
        ("postgresql://u:p@host:5432/agenticorg%5Ftest", "percent-encoded"),
        ("postgresql://u:p@host:5432/agenticorg_test%00", "percent-encoded"),
        ("postgresql://u:p@host:5432/agenticorg/../x_test", "does not name exactly one database"),
        ("postgresql://u:p@host1,host2:5432/agenticorg_test", "exactly one host"),
        ("postgresql://agenticorg_test@host:5432/agenticorg", "does not end in '_test'"),
        ("postgresql://u:p@host:5432/\uff41genticorg_test", "ASCII"),
        ("postgresql://u:p@host:99999/agenticorg_test", "cannot be parsed"),
        ("mysql://u:p@host:3306/agenticorg_test", "not a Postgres URL"),
        ("postgresql:///agenticorg_test", "URL has no host"),
    ],
)
def test_refuses_anything_else(url: str, reason: str) -> None:
    with pytest.raises(reset.UnsafeDatabaseURLError, match=reason):
        reset.safe_test_database_name(url)


def test_main_fails_closed_without_url(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("AGENTICORG_DB_URL", raising=False)
    assert reset.main() == 2
    assert "not set" in capsys.readouterr().err


def test_main_refuses_development_database_without_connecting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _no_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not connect to a database it refuses to reset")

    import psycopg2

    monkeypatch.setattr(psycopg2, "connect", _no_connect)
    monkeypatch.setenv("AGENTICORG_DB_URL", "postgresql+asyncpg://agenticorg:agenticorg_dev@postgres:5432/agenticorg")
    assert reset.main() == 2
    assert "refusing to drop it" in capsys.readouterr().err
