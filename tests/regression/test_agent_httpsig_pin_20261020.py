# SPDX-License-Identifier: Apache-2.0
"""grantex-agent-httpsig is pinned to a full commit sha of the Grantex repository, never a branch.

The library is not published to PyPI (its package is marked ``Private :: Do Not
Upload``), so AgenticOrg installs it as a direct git reference. A branch or tag
reference would change what a rebuild installs; only a 40-character commit sha is
reproducible. The two declarations must agree, and every image that installs the
project's Python dependencies needs ``git``.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIN = re.compile(
    r"^grantex-agent-httpsig @ git\+https://github\.com/mishrasanjeev/grantex@(?P<sha>[0-9a-f]{40})"
    r"#subdirectory=packages/agent-httpsig-py$"
)


def _requirements_pin() -> str:
    lines = [
        line.strip()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("grantex-agent-httpsig")
    ]
    assert len(lines) == 1, lines
    return lines[0]


def _pyproject_pin() -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    pins = [dep for dep in project["dependencies"] if dep.startswith("grantex-agent-httpsig")]
    assert len(pins) == 1, pins
    return pins[0]


def test_both_declarations_pin_the_same_full_commit_sha() -> None:
    requirement, project = _requirements_pin(), _pyproject_pin()
    assert PIN.fullmatch(requirement), requirement
    assert PIN.fullmatch(project), project
    assert requirement == project


def test_the_pin_regex_refuses_branch_tag_and_short_sha_references() -> None:
    base = (
        "grantex-agent-httpsig @ git+https://github.com/mishrasanjeev/grantex@{}#subdirectory=packages/agent-httpsig-py"
    )
    for ref in (
        "main",
        "feat/agent-network-identity",
        "v0.2.0",
        "f006cc82",
        "F006CC82BF2261E3B2E9601C93587395956FB685",
    ):
        assert not PIN.fullmatch(base.format(ref)), ref
    assert PIN.fullmatch(base.format("f006cc82bf2261e3b2e9601c93587395956fb685"))


def test_hatch_allows_the_direct_reference() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["hatch"]["metadata"]["allow-direct-references"] is True


def test_images_that_install_python_dependencies_have_git() -> None:
    for name in ("Dockerfile", "Dockerfile.tools"):
        text = (ROOT / name).read_text(encoding="utf-8")
        install = text.index("pip install")
        apt = text.rfind("apt-get install", 0, install)
        assert apt != -1, name
        assert re.search(r"\bgit\b", text[apt:install].split("&& rm -rf")[0]), name
