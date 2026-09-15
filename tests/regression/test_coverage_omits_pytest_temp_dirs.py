# SPDX-License-Identifier: Apache-2.0
"""Coverage must not measure files that tests write under pytest's temp dirs.

``--basetemp=codex-pytest-basetemp`` puts ``tmp_path`` inside the checkout,
so a test that writes and imports a module there (for example the provider
plugin packaging test) was recorded by ``--cov=.``. The directory is removed
afterwards, ``coverage report`` then stops with "No source for code" and the
release-acceptance coverage gate fails with no TOTAL.
"""

from __future__ import annotations

import fnmatch
import shutil
import subprocess
import sys
from pathlib import Path

import coverage

ROOT = Path(__file__).resolve().parents[2]
TEMP_DIRS = ("codex-pytest-basetemp", "codex-pytest-temp", "codex-pytest-cache", "codex-pytest-artifacts")

# The driver measures one throwaway module under basetemp and one real,
# dependency-free repository file, so the report still has data once the
# throwaway module is gone (and needs no installed project dependencies).
DRIVER = """\
import runpy
import sys
sys.path.insert(0, sys.argv[1])
import probe_module
runpy.run_path(sys.argv[2], run_name="coverage_probe")
print(probe_module.VALUE)
"""


def test_coverage_config_omits_every_pytest_temp_dir() -> None:
    cov = coverage.Coverage(config_file=str(ROOT / "pyproject.toml"))
    omit = cov.get_option("run:omit") or []
    for name in TEMP_DIRS:
        probe = f"{name}/test_x0/pkg/module.py"
        assert any(fnmatch.fnmatch(probe, pattern) for pattern in omit), (name, omit)


def test_report_survives_a_deleted_module_written_under_basetemp(tmp_path: Path) -> None:
    work = ROOT / "codex-pytest-basetemp" / f"coverage-omit-probe-{tmp_path.name}"
    work.mkdir(parents=True, exist_ok=True)
    data_file = tmp_path / ".coverage"
    rcfile = f"--rcfile={ROOT / 'pyproject.toml'}"
    try:
        (work / "probe_module.py").write_text("VALUE = 1\n", encoding="utf-8")
        driver = work / "driver.py"
        driver.write_text(DRIVER, encoding="utf-8")
        run = subprocess.run(  # noqa: S603 - literal argv
            [
                sys.executable, "-m", "coverage", "run", rcfile,
                f"--data-file={data_file}", "--source=.", str(driver), str(work),
                str(ROOT / "scripts" / "check_license_headers.py"),
            ],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=120,
        )
        assert run.returncode == 0, run.stderr
    finally:
        shutil.rmtree(work, ignore_errors=True)
    report = subprocess.run(  # noqa: S603 - literal argv
        [sys.executable, "-m", "coverage", "report", rcfile, f"--data-file={data_file}"],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=120,
    )
    assert report.returncode == 0, report.stdout + report.stderr
    assert any(line.startswith("TOTAL") for line in report.stdout.splitlines()), report.stdout
