"""Regression gates for the 2026-06-13 security audit.

These tests pin the dependency-policy fixes that remove vulnerable
optional packages from the production install path.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _pyproject() -> dict:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))


def _dependency_names(values: list[str]) -> set[str]:
    names: set[str] = set()
    for value in values:
        name = value.split(";", 1)[0].split("[", 1)[0]
        for sep in ("==", ">=", "<=", "~=", "!=", ">", "<"):
            name = name.split(sep, 1)[0]
        names.add(name.strip().lower())
    return names


def test_production_dependencies_keep_patched_pillow_floor() -> None:
    deps = _pyproject()["project"]["dependencies"]
    assert "pillow>=12.2.0" in deps


def test_pytorch_owners_are_not_in_base_or_v4_dependencies() -> None:
    project = _pyproject()["project"]
    base_names = _dependency_names(project["dependencies"])
    v4_names = _dependency_names(project["optional-dependencies"]["v4"])

    forbidden = {"torch", "flagembedding", "routellm", "litellm", "composio-core"}
    assert not (base_names & forbidden)
    assert not (v4_names & forbidden)


def test_vulnerable_jwt_stack_is_not_in_production_dependencies() -> None:
    project = _pyproject()["project"]
    base_names = _dependency_names(project["dependencies"])
    assert "pyjwt" in base_names
    assert "python-jose" not in base_names
    assert "ecdsa" not in base_names

    requirements = (REPO / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "pyjwt[crypto]" in requirements
    assert "python-jose" not in requirements
    assert "\necdsa" not in requirements


def test_bge_m3_loader_is_explicit_extra_only() -> None:
    extras = _pyproject()["project"]["optional-dependencies"]
    assert "FlagEmbedding>=1.4.0" in extras["bge-m3"]
    assert "FlagEmbedding>=1.4.0" not in extras["v4"]


def test_requirements_v4_stays_installable_without_unsafe_optional_sdks() -> None:
    content = (REPO / "requirements-v4.txt").read_text(encoding="utf-8").lower()
    for package in ("composio-core", "routellm", "litellm", "torch", "flagembedding"):
        assert f"\n{package}" not in content
    assert 'presidio-analyzer==2.2.359; python_version >= "3.14"' in content
    assert "presidio-anonymizer==2.2.362" in content
    assert "presidio-anonymizer==2.2.363" not in content


def test_presidio_anonymizer_excludes_cryptography_upper_bound_release() -> None:
    extras = _pyproject()["project"]["optional-dependencies"]
    assert "presidio-anonymizer>=2.2.362,<2.2.363" in extras["v4"]
    assert "presidio-anonymizer>=2.2.0,<2.2.363" in extras["dev"]


def test_security_workflows_do_not_continue_on_error() -> None:
    for workflow in (
        REPO / ".github" / "workflows" / "security-scan.yml",
        REPO / ".github" / "workflows" / "deploy.yml",
    ):
        content = workflow.read_text(encoding="utf-8")
        assert "continue-on-error: true" not in content
        assert "pip-audit --desc || echo" not in content


def test_container_scan_fails_on_fixable_highs_without_blocking_unfixed_base_cves() -> None:
    workflow = (REPO / ".github" / "workflows" / "security-scan.yml").read_text(
        encoding="utf-8"
    )
    assert 'severity: "CRITICAL,HIGH"' in workflow
    assert "ignore-unfixed: true" in workflow
    assert 'exit-code: "1"' in workflow


def test_runtime_image_does_not_install_curl_for_healthcheck() -> None:
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    runtime_stage = dockerfile.split("FROM python:3.14-slim@sha256:", 2)[2]
    assert " curl " not in runtime_stage
    assert "urllib.request.urlopen" in runtime_stage
