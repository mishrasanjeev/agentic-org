# SPDX-License-Identifier: Apache-2.0
"""Runtime stages must enforce the security-fixed package versions."""

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


@pytest.mark.parametrize(
    ("package", "minimum"),
    [
        ("libpcre2-8-0", "10.46-1~deb13u3"),
        ("libssl3t64", "3.5.7-1~deb13u3"),
        ("openssl", "3.5.7-1~deb13u3"),
        ("openssl-provider-legacy", "3.5.7-1~deb13u3"),
        ("libreoffice-core", "4:25.2.3-2+deb13u8"),
        ("fonts-opensymbol", "4:102.12+LibO25.2.3-2+deb13u8"),
    ],
)
def test_api_runtime_rejects_cached_vulnerable_packages(package: str, minimum: str) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime = (root / "Dockerfile").read_text(encoding="utf-8").rsplit("FROM ", 1)[1]
    install = runtime.split("RUN apt-get update", 1)[1].split("\nRUN ", 1)[0]
    assert "apt-get upgrade -y" in install
    check = f'dpkg --compare-versions "$(dpkg-query -W -f=\'${{Version}}\' {package})" ge \'{minimum}\''
    assert check in install, f"API runtime: missing security floor for {package}"
