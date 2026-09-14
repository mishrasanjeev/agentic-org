# SPDX-License-Identifier: Apache-2.0
"""Console image healthchecks must probe the address nginx actually listens on.

Both console images ran ``wget --spider http://localhost/...``. In the
nginx:alpine base ``localhost`` resolves to ``::1`` first, while
``ui/nginx.conf`` (``listen 80``) and ``ui/nginx.cloudrun.conf.template``
(``listen ${PORT}``) listen on IPv4 only, so the probe was refused and the
container reported unhealthy while serving traffic. Anything that waits on
container health (``docker compose up --wait``, ``depends_on:
service_healthy``) never proceeded.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_IMAGES = ("Dockerfile.ui", "Dockerfile.ui.cloudrun")


def _healthcheck(dockerfile: str) -> str:
    text = (REPO_ROOT / dockerfile).read_text(encoding="utf-8").replace("\\\n", " ")
    matches = [line for line in text.splitlines() if line.startswith("HEALTHCHECK")]
    assert len(matches) == 1, f"{dockerfile} should declare exactly one HEALTHCHECK"
    return matches[0]


@pytest.mark.parametrize("dockerfile", UI_IMAGES)
def test_ui_healthcheck_probes_ipv4_loopback_not_localhost(dockerfile: str) -> None:
    check = _healthcheck(dockerfile)
    assert "localhost" not in check, f"{dockerfile}: 'localhost' resolves to ::1 but nginx listens on IPv4"
    assert "http://127.0.0.1" in check


@pytest.mark.parametrize("dockerfile", UI_IMAGES)
def test_ui_healthcheck_uses_the_nginx_health_location(dockerfile: str) -> None:
    check = _healthcheck(dockerfile)
    assert re.search(r"http://127\.0\.0\.1(:\$\{PORT\})?/health\b", check), check


def test_ui_nginx_configs_listen_on_ipv4_only() -> None:
    # If either config starts listening on [::], localhost would work again and
    # this pin can be relaxed; until then the probe must use 127.0.0.1.
    for config in ("ui/nginx.conf", "ui/nginx.cloudrun.conf.template"):
        text = (REPO_ROOT / config).read_text(encoding="utf-8")
        assert "listen [::]" not in text
        assert re.search(r"^\s*location = /health\b", text, re.MULTILINE), config
