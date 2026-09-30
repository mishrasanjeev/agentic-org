# SPDX-License-Identifier: Apache-2.0
"""Keep the synthetic external-buyer runner off remote and production targets."""

from __future__ import annotations

import pytest

from examples.a2a_commerce_demo.run_demo import _require_local_demo


def test_synthetic_demo_accepts_local_development_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTICORG_ENV", "development")
    monkeypatch.setenv(
        "AGENTICORG_DB_URL",
        "postgresql+asyncpg://agenticorg:local@127.0.0.1:55480/agenticorg",
    )
    monkeypatch.delenv("K_SERVICE", raising=False)
    _require_local_demo("http://127.0.0.1:18081")


@pytest.mark.parametrize(
    ("environment", "database", "api", "cloud_service"),
    [
        ("production", "127.0.0.1", "http://127.0.0.1:18081", ""),
        ("development", "prod.example.com", "http://127.0.0.1:18081", ""),
        ("development", "127.0.0.1", "https://api.example.com", ""),
        ("development", "127.0.0.1", "http://127.0.0.1:18081", "api-service"),
    ],
)
def test_synthetic_demo_refuses_unsafe_targets(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    database: str,
    api: str,
    cloud_service: str,
) -> None:
    monkeypatch.setenv("AGENTICORG_ENV", environment)
    monkeypatch.setenv(
        "AGENTICORG_DB_URL",
        f"postgresql+asyncpg://agenticorg:local@{database}:5432/agenticorg",
    )
    if cloud_service:
        monkeypatch.setenv("K_SERVICE", cloud_service)
    else:
        monkeypatch.delenv("K_SERVICE", raising=False)
    with pytest.raises(RuntimeError, match="local development database and loopback"):
        _require_local_demo(api)
