# SPDX-License-Identifier: Apache-2.0
"""A-2: the conformance suite is importable from an external package, and the mock passes it.

The wheel publishes ``testing/provider_conformance`` as ``agenticorg.testing.provider_conformance``
(``[tool.hatch.build.targets.wheel.force-include]`` in ``pyproject.toml``). This test lays the files
out exactly as that mapping does, then runs ``provider_example/conformance_example.py`` - which
imports only the published name - with pytest in a separate process, outside the repository's own
pytest configuration, against the mock in-process and over HTTP.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = Path(__file__).resolve().parent / "provider_example" / "conformance_example.py"


def published_layout(site: Path) -> None:
    """Copy the ``agenticorg`` package as the wheel would contain it."""
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = config["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert "sdk/agenticorg" in wheel["packages"]
    shutil.copytree(REPO_ROOT / "sdk" / "agenticorg", site / "agenticorg", ignore=shutil.ignore_patterns("__pycache__"))
    for source, destination in wheel["force-include"].items():
        origin, target = REPO_ROOT / source, site / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        if origin.is_dir():
            shutil.copytree(origin, target, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(origin, target)


def test_the_wheel_maps_the_suite_to_its_published_name() -> None:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    mapping = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert mapping == {
        "testing/__init__.py": "agenticorg/testing/__init__.py",
        "testing/provider_conformance": "agenticorg/testing/provider_conformance",
    }


def test_the_suite_uses_only_relative_imports_within_itself() -> None:
    for path in (REPO_ROOT / "testing" / "provider_conformance").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from testing" not in text and "import testing" not in text, path.name


@pytest.mark.timeout(300)
def test_an_external_package_runs_the_published_suite_and_the_mock_passes(tmp_path: Path) -> None:
    site = tmp_path / "site"
    project = tmp_path / "acme_kyb_package"
    published_layout(site)
    project.mkdir()
    shutil.copy2(EXAMPLE, project / "test_conformance.py")
    (project / "test_import_location.py").write_text(
        "import agenticorg.testing.provider_conformance as suite\n"
        "from pathlib import Path\n\n"
        "def test_the_suite_is_imported_from_the_published_layout():\n"
        f"    assert Path(suite.__file__).resolve().is_relative_to(Path({str(site)!r}).resolve())\n",
        encoding="utf-8",
    )
    (project / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "COV_CORE"))}
    env.update(
        PYTHONPATH=os.pathsep.join([str(site), str(REPO_ROOT)]),
        AGENTICORG_ENV="test",
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
    )
    completed = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-m", "pytest", "-q", "-rfEs", "-p", "no:cacheprovider", "-p", "no:randomly", "."],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=280,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output[-6000:]
    assert "23 passed" in output, output[-3000:]
    assert "skipped" not in output, output[-3000:]
