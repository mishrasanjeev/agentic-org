# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Keep the shared dependency and browser-runner contracts coherent."""

import json
from pathlib import Path

import yaml
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]


def test_browser_runner_images_match_the_locked_playwright_version() -> None:
    lock = json.loads((ROOT / "ui/package-lock.json").read_text(encoding="utf-8"))
    version = lock["packages"]["node_modules/@playwright/test"]["version"]
    compose = yaml.safe_load((ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8"))
    for service in ("e2e", "e2e-decisions"):
        image = compose["services"][service]["image"]
        assert image.startswith(f"mcr.microsoft.com/playwright:v{version}-noble@sha256:")
        assert len(image.rsplit("sha256:", 1)[1]) == 64


def test_exact_observability_pins_move_as_one_release_family() -> None:
    requirements = {}
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            requirement = Requirement(line)
            requirements[requirement.name] = requirement
    names = ("opentelemetry-api", "opentelemetry-sdk", "opentelemetry-exporter-otlp")
    pins = {str(requirements[name].specifier) for name in names}
    assert len(pins) == 1
    assert next(iter(pins)).startswith("==")
    core_pin = next(iter(requirements["langchain-core"].specifier))
    assert core_pin.operator == "=="
    assert Version(core_pin.version) >= Version("1.6.9")
