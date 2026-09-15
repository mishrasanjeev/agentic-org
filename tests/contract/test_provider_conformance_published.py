# SPDX-License-Identifier: Apache-2.0
"""A-2: the conformance suite is importable from an external package, and the mock passes it.

The wheel publishes ``testing/provider_conformance`` as ``agenticorg.testing.provider_conformance``
(``[tool.hatch.build.targets.wheel.force-include]`` in ``pyproject.toml``). This test lays out every
package the wheel contains exactly as the wheel does, then runs
``provider_example/conformance_example.py`` - which imports only the published name - with pytest in
a separate process, from a directory outside the repository, with only that layout on
``PYTHONPATH`` and never the repository root. It runs the mock in-process and over HTTP in strict
mode, so a skipped check fails.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from testing.provider_conformance import CHECKS

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = Path(__file__).resolve().parent / "provider_example" / "conformance_example.py"
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def _wheel_config() -> dict:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["hatch"]["build"]["targets"]["wheel"]


def published_layout(site: Path) -> None:
    """Copy every package and forced include to where the wheel installs it."""
    wheel = _wheel_config()
    for package in wheel["packages"]:
        origin = REPO_ROOT / package
        if origin.is_dir():
            shutil.copytree(origin, site / Path(package).name, ignore=_IGNORE)
    for source, destination in wheel["force-include"].items():
        origin, target = REPO_ROOT / source, site / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        if origin.is_dir():
            shutil.copytree(origin, target, ignore=_IGNORE, dirs_exist_ok=True)
        else:
            shutil.copy2(origin, target)


def test_the_wheel_maps_the_suite_to_its_published_name() -> None:
    wheel = _wheel_config()
    assert "sdk/agenticorg" in wheel["packages"] and "connectors" in wheel["packages"]
    assert wheel["force-include"] == {
        "testing/__init__.py": "agenticorg/testing/__init__.py",
        "testing/provider_conformance": "agenticorg/testing/provider_conformance",
    }


def test_the_suite_uses_only_relative_imports_within_itself() -> None:
    for path in (REPO_ROOT / "testing" / "provider_conformance").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from testing" not in text and "import testing" not in text, path.name


def test_the_image_build_copies_every_forced_include() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    builder = dockerfile[: dockerfile.index('pip install --no-cache-dir ".[v4]"')]
    for source in _wheel_config()["force-include"]:
        top = source.split("/")[0]
        assert f"COPY {top}/ {top}/" in builder, f"Dockerfile builder stage does not copy {top}/"


LOCATION_TEST = """\
import sys
from pathlib import Path

import agenticorg.testing.provider_conformance as suite
import connectors.framework.verification_provider as interface

SITE = Path({site!r}).resolve()
REPO = Path({repo!r}).resolve()


def test_everything_is_imported_from_the_published_layout():
    assert Path(suite.__file__).resolve().is_relative_to(SITE)
    assert Path(interface.__file__).resolve().is_relative_to(SITE)
    assert all(Path(entry or ".").resolve() != REPO for entry in sys.path)
"""


@pytest.mark.timeout(300)
def test_an_external_package_runs_the_published_suite_strictly_and_the_mock_passes(tmp_path: Path) -> None:
    site = tmp_path / "site"
    project = tmp_path / "acme_kyb_package"
    published_layout(site)
    project.mkdir()
    shutil.copy2(EXAMPLE, project / "test_conformance.py")
    (project / "test_import_location.py").write_text(
        LOCATION_TEST.format(site=str(site), repo=str(REPO_ROOT)), encoding="utf-8"
    )
    (project / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "COV_CORE", "PYTHONPATH"))}
    env.update(
        PYTHONPATH=str(site),
        AGENTICORG_ENV="test",
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
    )
    completed = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-m", "pytest", "-q", "-rfEs", "-p", "no:cacheprovider", "."],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=280,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output[-6000:]
    expected = 2 * len(CHECKS) + 1
    assert f"{expected} passed" in output, output[-3000:]
    assert "skipped" not in output, output[-3000:]
