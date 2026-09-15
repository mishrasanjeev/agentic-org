# SPDX-License-Identifier: Apache-2.0
"""A provider shipped as its own package is discovered through its entry point and registered.

The package below is written to a temporary directory exactly as the provider guide describes it;
its ``pyproject.toml`` entry point is read back and loaded through ``connectors.plugins``.
"""

from __future__ import annotations

import importlib.metadata
import textwrap
import tomllib
from pathlib import Path

import pytest

from connectors import plugins
from connectors.framework.verification_provider import Capability
from connectors.providers.registry import ProviderRegistry

PYPROJECT = """\
# docs-snippet: start provider-pyproject
[project]
name = "acme-kyb-agenticorg"
version = "0.1.0"
dependencies = ["agenticorg"]

[project.entry-points."agenticorg.providers"]
acme_kyb = "acme_kyb_agenticorg.provider:AcmeKybProvider"
# docs-snippet: end provider-pyproject
"""

PROVIDER_MODULE = textwrap.dedent(
    """\
    from connectors.framework.verification_provider import Capability, VerificationProvider


    class AcmeKybProvider(VerificationProvider):
        name = "acme_kyb"
        capabilities = frozenset({Capability.RESOLVE, Capability.VERIFY})
    """
)


class _Dist:
    name = "acme-kyb-agenticorg"


class _EntryPoint(importlib.metadata.EntryPoint):
    dist = _Dist()  # type: ignore[assignment]


@pytest.fixture
def acme_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    package = tmp_path / "acme_kyb_agenticorg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "provider.py").write_text(PROVIDER_MODULE, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(ProviderRegistry, "_providers", dict(ProviderRegistry._providers))
    config = tomllib.loads((tmp_path / "pyproject.toml").read_text(encoding="utf-8"))
    return config["project"]["entry-points"]["agenticorg.providers"]


def test_the_documented_package_layout_registers_its_provider(acme_package: dict[str, str]) -> None:
    entry_points = [_EntryPoint(name, value, plugins.PROVIDERS_GROUP) for name, value in acme_package.items()]

    outcomes = plugins.load_plugins(
        enabled=True,
        allowlist={"acme-kyb-agenticorg"},
        entry_points=lambda *, group: entry_points if group == plugins.PROVIDERS_GROUP else [],
    )

    assert [(o.loaded, o.reason, o.detail) for o in outcomes] == [(True, "loaded", "acme_kyb")]
    provider = ProviderRegistry.create("acme_kyb")
    assert provider.capabilities == frozenset({Capability.RESOLVE, Capability.VERIFY})
    assert ProviderRegistry.get("mock") is not None and ProviderRegistry.get("mock").source == "native"  # type: ignore[union-attr]


def test_the_documented_package_is_not_loaded_without_the_allowlist(acme_package: dict[str, str]) -> None:
    entry_points = [_EntryPoint(name, value, plugins.PROVIDERS_GROUP) for name, value in acme_package.items()]
    outcomes = plugins.load_plugins(
        enabled=True,
        allowlist=set(),
        entry_points=lambda *, group: entry_points if group == plugins.PROVIDERS_GROUP else [],
    )
    assert [o.reason for o in outcomes] == ["not_allowlisted"]
    assert ProviderRegistry.get("acme_kyb") is None
