# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Runtime stages must enforce the security-fixed package versions."""

from pathlib import Path

import pytest


@pytest.mark.parametrize("dockerfile", ["Dockerfile.ui", "Dockerfile.ui.cloudrun"])
def test_ui_runtime_installs_fixed_security_packages(dockerfile: str) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime = (root / dockerfile).read_text(encoding="utf-8").rsplit("FROM ", 1)[1]
    upgrade = next(line for line in runtime.splitlines() if line.startswith("RUN apk "))
    assert "--no-cache --upgrade" in upgrade
    for package in (
        "libexpat>=2.8.5-r0",
        "pcre2>=10.49-r0",
        "tiff>=4.7.2-r0",
        "libx11>=1.8.14-r0",
        "brotli-libs>=1.2.1-r0",
        "curl>=8.23.0-r0",
        "libcurl>=8.23.0-r0",
    ):
        assert f"'{package}'" in upgrade, f"{dockerfile}: missing security floor for {package}"


@pytest.mark.parametrize(
    ("package", "minimum"),
    [
        ("apt", "3.0.4"),
        ("libapt-pkg7.0", "3.0.4"),
        ("zlib1g", "1:1.3.dfsg+really1.3.1-1+deb13u1"),
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
    assert "apt-get update -o Acquire::Retries=0" in runtime
    assert "apt-get upgrade -y -o Acquire::Retries=0" in install
    check = f'dpkg --compare-versions "$(dpkg-query -W -f=\'${{Version}}\' {package})" ge \'{minimum}\''
    assert check in install, f"API runtime: missing security floor for {package}"


@pytest.mark.parametrize(
    ("package", "minimum"),
    [
        ("apt", "3.0.4"),
        ("libapt-pkg7.0", "3.0.4"),
        ("zlib1g", "1:1.3.dfsg+really1.3.1-1+deb13u1"),
    ],
)
def test_mcp_runtime_requires_fixed_security_packages(package: str, minimum: str) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime = (root / "mcp-server/Dockerfile.hosted").read_text(encoding="utf-8").rsplit("FROM ", 1)[1]
    assert "apt-get -o Acquire::Retries=0 update" in runtime
    upgrade = next(line for line in runtime.splitlines() if "--only-upgrade" in line)
    assert package in upgrade.split("--only-upgrade", 1)[1].split()
    check = f'dpkg --compare-versions "$(dpkg-query -W -f=\'${{Version}}\' {package})" ge \'{minimum}\''
    assert check in runtime
    assert runtime.index(check) < runtime.index("USER node")


@pytest.mark.parametrize("filename", ["Dockerfile", "Dockerfile.tools"])
def test_debian_builders_bootstrap_without_vulnerable_apt_retries(filename: str) -> None:
    root = Path(__file__).resolve().parents[2]
    builder = (root / filename).read_text(encoding="utf-8").split("\nFROM ", 2)[1]
    install = builder.split("RUN apt-get update", 1)[1].split("\nCOPY ", 1)[0]
    update, packages = install.split("apt-get install", 1)
    assert "Acquire::Retries=0" in update
    assert "Acquire::Retries=0" in packages
    assert "dpkg-query -W -f='${Version}' zlib1g" in packages
    assert "ge '1:1.3.dfsg+really1.3.1-1+deb13u1'" in packages
