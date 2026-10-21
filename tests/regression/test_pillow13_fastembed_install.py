# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Pillow 13 is installed even though fastembed 0.9.0 declares ``pillow<13``.

The 2026-10 Pillow advisories (JPEG, TIFF and GIF decoders, reachable through
document and image OCR) are fixed only in Pillow 13.0.0. fastembed 0.9.0, the
default local embedding runtime and its latest release, caps Pillow below 13,
so resolving the project with fastembed in ``pyproject.toml`` kept Pillow at
12.3.0 and the dependency audit failed on every pull request. fastembed is now
installed from ``requirements-fastembed.txt`` with ``--no-deps``; its other
runtime requirements are declared in ``pyproject.toml``, and
``scripts/pip_check.py`` accepts exactly that one broken requirement.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import re
import tomllib
from pathlib import Path

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.version import Version

from scripts import pip_check

ROOT = Path(__file__).resolve().parents[2]

FASTEMBED_INSTALL = "--no-deps --require-hashes -r requirements-fastembed.txt"
PATCHED_PILLOW = Version("13.0.0")


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _requirements(values: list[str]) -> list[Requirement]:
    return [Requirement(value) for value in values]


def _installed(name: str) -> Version | None:
    try:
        return Version(importlib.metadata.version(name))
    except importlib.metadata.PackageNotFoundError:
        return None


# ── Declared dependencies ───────────────────────────────────────────────────


def test_pyproject_requires_patched_pillow_and_no_lower_pillow_floor() -> None:
    pillow = [r for r in _requirements(_project()["dependencies"]) if r.name.lower() == "pillow"]
    assert [str(r.specifier) for r in pillow] == [">=13.0.0"]


def test_fastembed_is_not_resolved_from_pyproject() -> None:
    project = _project()
    groups = [project["dependencies"], *project["optional-dependencies"].values()]
    names = {r.name.lower() for group in groups for r in _requirements(group)}
    assert "fastembed" not in names


def test_fastembed_pin_is_exact_and_hashed() -> None:
    text = (ROOT / "requirements-fastembed.txt").read_text(encoding="utf-8")
    body = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    joined = " ".join(line.rstrip("\\").strip() for line in body)
    match = re.fullmatch(r"fastembed==(\d+\.\d+\.\d+)( --hash=sha256:[0-9a-f]{64})+", joined)
    assert match, joined


def test_every_python_install_also_installs_fastembed_without_dependencies() -> None:
    for name in ("Dockerfile", "Dockerfile.tools"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert f"pip install --no-cache-dir {FASTEMBED_INSTALL}" in text, name
        assert "-m pip check" not in text and "&& pip check" not in text, f"{name} must use scripts/pip_check.py"
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    installs = [line for line in workflow.splitlines() if "uv pip install --system -e" in line]
    assert installs
    for line in installs:
        assert f"uv pip install --system {FASTEMBED_INSTALL}" in line, line


# ── Installed environment (CI installs fastembed; skipped where it is absent) ──


def test_installed_pillow_is_patched() -> None:
    installed = _installed("pillow")
    if installed is None:
        pytest.skip("Pillow is not installed in this environment")
    assert installed >= PATCHED_PILLOW


def test_fastembed_runtime_requirements_are_declared_and_satisfied() -> None:
    if _installed("fastembed") is None:
        pytest.skip("fastembed is not installed in this environment")
    declared = {r.name.lower() for r in _requirements(_project()["dependencies"])}
    environment = default_environment()
    checked = 0
    for raw in importlib.metadata.requires("fastembed") or []:
        requirement = Requirement(raw)
        if requirement.marker is not None and not requirement.marker.evaluate({**environment, "extra": ""}):
            continue
        name = requirement.name.lower()
        if name == "pillow":
            continue
        assert name in declared, f"fastembed requires {raw}; declare it in pyproject.toml"
        installed = _installed(requirement.name)
        assert installed is not None, raw
        assert requirement.specifier.contains(installed, prereleases=True), f"{raw}, installed {installed}"
        checked += 1
    assert checked >= 8


def test_fastembed_text_embedding_imports_with_patched_pillow() -> None:
    if importlib.util.find_spec("fastembed") is None:
        pytest.skip("fastembed is not installed in this environment")
    from fastembed import TextEmbedding
    from PIL import Image

    assert Version(Image.__version__) >= PATCHED_PILLOW
    assert callable(TextEmbedding)


# ── scripts/pip_check.py ────────────────────────────────────────────────────

ACCEPTED_312 = (
    'fastembed 0.9.0 has requirement pillow<13.0,>=10.3.0; python_version >= "3.10" and '
    'python_version < "3.13", but you have pillow 13.0.0.'
)
ACCEPTED_314 = (
    'fastembed 0.9.0 has requirement pillow<13.0,>=12.0.0; python_version >= "3.14", but you have pillow 13.0.0.'
)


@pytest.mark.parametrize("line", [ACCEPTED_312, ACCEPTED_314])
def test_pip_check_accepts_fastembed_pillow_ceiling(line: str) -> None:
    assert pip_check.is_accepted(line)


@pytest.mark.parametrize(
    "line",
    [
        # Pillow older than fastembed's floor is a real conflict, not the ceiling.
        'fastembed 0.9.0 has requirement pillow<13.0,>=12.0.0; python_version >= "3.14", but you have pillow 11.0.0.',
        "fastembed 0.9.0 has requirement numpy>=2.3.0, but you have numpy 2.2.0.",
        "fastembed 0.9.0 requires onnxruntime, which is not installed.",
        "reportlab 5.0.1 has requirement pillow<13, but you have pillow 13.0.0.",
        "fastembed 0.9.0 has requirement pillow<13.0, but you have numpy 13.0.0.",
        "fastembed 0.9.0 has requirement not a requirement!!, but you have pillow 13.0.0.",
        "",
    ],
)
def test_pip_check_rejects_every_other_problem(line: str) -> None:
    assert not pip_check.is_accepted(line)


def test_pip_check_reports_only_unaccepted_lines() -> None:
    other = "langgraph 1.2.16 requires langgraph-sdk, which is not installed."

    def runner(_argv: object) -> tuple[int, str]:
        return 1, f"{ACCEPTED_314}\n{other}\n"

    assert pip_check.check(runner) == [other]


def test_pip_check_passes_a_clean_environment_and_the_accepted_conflict() -> None:
    assert pip_check.check(lambda _argv: (0, "No broken requirements found.\n")) == []
    assert pip_check.check(lambda _argv: (1, f"{ACCEPTED_312}\n")) == []


def test_pip_check_fails_closed_without_output() -> None:
    assert pip_check.check(lambda _argv: (1, "")) == ["pip check failed (exit 1) without output"]
