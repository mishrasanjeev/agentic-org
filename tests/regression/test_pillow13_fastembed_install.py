# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Fastembed 0.9.1 resolves normally with patched Pillow, with no conflict waiver."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _installed(name: str) -> Version:
    try:
        return Version(importlib.metadata.version(name))
    except importlib.metadata.PackageNotFoundError:
        pytest.skip(f"{name} is not installed in this environment")


def test_fastembed_is_resolved_from_project_with_patched_pillow() -> None:
    requirements = {r.name.lower(): r for r in map(Requirement, _project()["dependencies"])}
    assert str(requirements["fastembed"].specifier) == "==0.9.1"
    assert str(requirements["pillow"].specifier) == ">=13.0.0"
    assert _installed("fastembed") == Version("0.9.1")
    assert _installed("pillow") >= Version("13.0.0")


def test_no_dependency_bypass_or_pillow_waiver_remains() -> None:
    assert not (ROOT / "requirements-fastembed.txt").exists()
    assert not (ROOT / "scripts" / "pip_check.py").exists()
    for name in (
        "Dockerfile", "Dockerfile.tools", ".github/workflows/deploy.yml",
        ".github/workflows/mfa-security.yml", ".github/workflows/rag-eval.yml",
    ):
        source = (ROOT / name).read_text(encoding="utf-8")
        assert "requirements-fastembed.txt" not in source, name
        assert "scripts/pip_check.py" not in source, name
        assert "--no-deps" not in source, name
        assert "pip check" in source, name


def test_all_fastembed_runtime_requirements_are_satisfied_including_pillow() -> None:
    _installed("fastembed")
    environment = {**default_environment(), "extra": ""}
    checked = set()
    for raw in importlib.metadata.requires("fastembed") or []:
        requirement = Requirement(raw)
        if requirement.marker is not None and not requirement.marker.evaluate(environment):
            continue
        installed = _installed(requirement.name)
        assert requirement.specifier.contains(installed, prereleases=True), f"{raw}, installed {installed}"
        checked.add(requirement.name.lower())
    assert checked == {
        "numpy", "onnxruntime", "tqdm", "requests", "tokenizers",
        "huggingface-hub", "loguru", "pillow", "mmh3", "py-rust-stemmers",
    }


def test_fastembed_text_embedding_imports_with_patched_pillow() -> None:
    _installed("fastembed")
    from fastembed import TextEmbedding
    from PIL import Image

    assert Version(Image.__version__) >= Version("13.0.0")
    assert callable(TextEmbedding)


def test_installed_environment_passes_strict_pip_check() -> None:
    _installed("fastembed")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "check"], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
