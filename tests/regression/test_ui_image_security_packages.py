# SPDX-License-Identifier: Apache-2.0
"""Both nginx runtime stages must install the security-fixed libraries."""

from pathlib import Path

import pytest


@pytest.mark.parametrize("dockerfile", ["Dockerfile.ui", "Dockerfile.ui.cloudrun"])
def test_ui_runtime_installs_fixed_security_packages(dockerfile: str) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime = (root / dockerfile).read_text(encoding="utf-8").rsplit("FROM ", 1)[1]
    upgrade = next(line for line in runtime.splitlines() if line.startswith("RUN apk "))
    assert "--no-cache --upgrade" in upgrade
    for package in ("libexpat>=2.8.5-r0", "pcre2>=10.49-r0", "tiff>=4.7.2-r0"):
        assert f"'{package}'" in upgrade, f"{dockerfile}: missing security floor for {package}"
