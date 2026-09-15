# SPDX-License-Identifier: Apache-2.0
"""Entry-point plugin loading (connectors/plugins.py)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from connectors import plugins
from connectors.framework.base_connector import BaseConnector
from connectors.registry import ConnectorRegistry
from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry


@dataclass
class _Dist:
    name: str


class _EntryPoint:
    def __init__(self, name: str, dist: str | None, target: Callable[[], Any]) -> None:
        self.name = name
        self.dist = _Dist(dist) if dist else None
        self._target = target
        self.loaded = False

    def load(self) -> Any:
        self.loaded = True
        return self._target()


def _discovery(groups: dict[str, list[_EntryPoint]]) -> Callable[..., list[_EntryPoint]]:
    def entry_points(*, group: str) -> list[_EntryPoint]:
        return groups.get(group, [])

    return entry_points


class AcmeKybConnector(BaseConnector):
    name = "acme_kyb_plugin_test"
    category = "compliance"

    def _register_tools(self) -> None:  # pragma: no cover - never executed
        return None

    async def _authenticate(self) -> None:  # pragma: no cover - never executed
        return None

    async def health_check(self) -> dict[str, Any]:  # pragma: no cover - never executed
        return {"status": "ok"}


class AcmeReviewerAgent(BaseAgent):
    agent_type = "acme_reviewer_plugin_test"
    domain = "compliance"


@pytest.fixture(autouse=True)
def _isolated_registries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ConnectorRegistry, "_connectors", dict(ConnectorRegistry._connectors))
    monkeypatch.setattr(AgentRegistry, "_registry", dict(AgentRegistry._registry))


def _outcome(results: list[plugins.PluginOutcome], name: str) -> plugins.PluginOutcome:
    return next(r for r in results if r.name == name)


def test_disabled_loading_imports_nothing() -> None:
    ep = _EntryPoint("acme_kyb", "acme-kyb", lambda: AcmeKybConnector)
    results = plugins.load_plugins(
        enabled=False,
        allowlist={"acme-kyb"},
        entry_points=_discovery({"agenticorg.connectors": [ep]}),
    )
    assert results == []
    assert ep.loaded is False
    assert ConnectorRegistry.get("acme_kyb_plugin_test") is None


def test_allowlisted_connector_and_agent_are_registered() -> None:
    connector = _EntryPoint("acme_kyb", "acme-kyb", lambda: AcmeKybConnector)
    agent = _EntryPoint("acme_reviewer", "acme-kyb", lambda: AcmeReviewerAgent)
    results = plugins.load_plugins(
        enabled=True,
        allowlist={"acme-kyb"},
        entry_points=_discovery({"agenticorg.connectors": [connector], "agenticorg.agents": [agent]}),
    )
    assert _outcome(results, "acme_kyb").loaded
    assert _outcome(results, "acme_reviewer").loaded
    assert ConnectorRegistry.get("acme_kyb_plugin_test") is AcmeKybConnector
    assert AgentRegistry.get_by_type("acme_reviewer_plugin_test") is AcmeReviewerAgent


def test_distribution_names_are_compared_in_normalised_form() -> None:
    ep = _EntryPoint("acme_kyb", "Acme_KYB", lambda: AcmeKybConnector)
    results = plugins.load_plugins(
        enabled=True, allowlist={"acme-kyb"}, entry_points=_discovery({"agenticorg.connectors": [ep]})
    )
    assert _outcome(results, "acme_kyb").loaded


def test_plugin_outside_the_allowlist_is_rejected_without_being_imported() -> None:
    ep = _EntryPoint("acme_kyb", "acme-kyb", lambda: AcmeKybConnector)
    results = plugins.load_plugins(
        enabled=True, allowlist={"other-dist"}, entry_points=_discovery({"agenticorg.connectors": [ep]})
    )
    outcome = _outcome(results, "acme_kyb")
    assert (outcome.loaded, outcome.reason) == (False, "not_allowlisted")
    assert ep.loaded is False
    assert ConnectorRegistry.get("acme_kyb_plugin_test") is None


def test_entry_point_without_a_distribution_is_rejected() -> None:
    ep = _EntryPoint("acme_kyb", None, lambda: AcmeKybConnector)
    results = plugins.load_plugins(
        enabled=True, allowlist={"acme-kyb"}, entry_points=_discovery({"agenticorg.connectors": [ep]})
    )
    assert _outcome(results, "acme_kyb").reason == "unknown_distribution"
    assert ep.loaded is False


def test_import_failure_rejects_the_plugin_and_loading_continues() -> None:
    def broken() -> Any:
        raise ImportError("missing optional dependency")

    bad = _EntryPoint("broken", "acme-kyb", broken)
    good = _EntryPoint("acme_kyb", "acme-kyb", lambda: AcmeKybConnector)
    results = plugins.load_plugins(
        enabled=True, allowlist={"acme-kyb"}, entry_points=_discovery({"agenticorg.connectors": [bad, good]})
    )
    assert _outcome(results, "broken").reason == "load_failed"
    assert "ImportError" in _outcome(results, "broken").detail
    assert _outcome(results, "acme_kyb").loaded


def test_wrong_object_type_is_rejected() -> None:
    ep = _EntryPoint("not_a_connector", "acme-kyb", lambda: object)
    results = plugins.load_plugins(
        enabled=True, allowlist={"acme-kyb"}, entry_points=_discovery({"agenticorg.connectors": [ep]})
    )
    assert _outcome(results, "not_a_connector").reason == "invalid_type"


def test_native_connector_keeps_priority_over_a_plugin_with_the_same_name() -> None:
    native_name = next(iter(ConnectorRegistry._connectors))
    native = ConnectorRegistry.get(native_name)
    impostor = type("Impostor", (AcmeKybConnector,), {"name": native_name})
    ep = _EntryPoint("impostor", "acme-kyb", lambda: impostor)
    results = plugins.load_plugins(
        enabled=True, allowlist={"acme-kyb"}, entry_points=_discovery({"agenticorg.connectors": [ep]})
    )
    assert _outcome(results, "impostor").reason == "name_conflict"
    assert ConnectorRegistry.get(native_name) is native


def test_native_agent_keeps_priority_over_a_plugin_with_the_same_type() -> None:
    native_type = next(iter(AgentRegistry._registry))
    native = AgentRegistry.get_by_type(native_type)
    impostor = type("Impostor", (AcmeReviewerAgent,), {"agent_type": native_type})
    ep = _EntryPoint("impostor", "acme-kyb", lambda: impostor)
    results = plugins.load_plugins(
        enabled=True, allowlist={"acme-kyb"}, entry_points=_discovery({"agenticorg.agents": [ep]})
    )
    assert _outcome(results, "impostor").reason == "name_conflict"
    assert AgentRegistry.get_by_type(native_type) is native


@pytest.mark.parametrize("group", ["agenticorg.workflows"])
def test_groups_without_a_registry_are_rejected_explicitly(group: str) -> None:
    ep = _EntryPoint("thing", "acme-kyb", lambda: object)
    results = plugins.load_plugins(enabled=True, allowlist={"acme-kyb"}, entry_points=_discovery({group: [ep]}))
    assert _outcome(results, "thing").reason == "unsupported_group"
    assert ep.loaded is False


def test_every_documented_group_is_discovered() -> None:
    seen: list[str] = []

    def entry_points(*, group: str) -> list[_EntryPoint]:
        seen.append(group)
        return []

    plugins.load_plugins(enabled=True, allowlist=set(), entry_points=entry_points)
    assert seen == [
        "agenticorg.connectors",
        "agenticorg.providers",
        "agenticorg.agents",
        "agenticorg.workflows",
    ]


def test_parse_allowlist() -> None:
    assert plugins.parse_allowlist(" acme-kyb, Other_Dist ,,") == frozenset({"acme-kyb", "other-dist"})
    assert plugins.parse_allowlist("") == frozenset()


def test_outcomes_are_counted_with_bounded_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[dict[str, str]] = []

    class _Counter:
        def labels(self, **labels: str) -> _Counter:
            recorded.append(labels)
            return self

        def inc(self) -> None:
            return None

    monkeypatch.setattr(plugins, "_plugin_load_total", _Counter())
    ep = _EntryPoint("acme_kyb", "acme-kyb", lambda: AcmeKybConnector)
    plugins.load_plugins(enabled=True, allowlist=set(), entry_points=_discovery({"agenticorg.connectors": [ep]}))
    assert recorded == [{"group": "agenticorg.connectors", "outcome": "not_allowlisted"}]


def test_configured_loading_reads_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import settings

    captured: dict[str, Any] = {}

    def fake_load(*, enabled: bool, allowlist: frozenset[str]) -> list[plugins.PluginOutcome]:
        captured.update(enabled=enabled, allowlist=allowlist)
        return []

    monkeypatch.setattr(settings, "plugin_loading", True)
    monkeypatch.setattr(settings, "plugin_allowlist", "acme-kyb, Beta_Pkg")
    monkeypatch.setattr(plugins, "load_plugins", fake_load)

    plugins.load_configured_plugins()

    assert captured == {"enabled": True, "allowlist": frozenset({"acme-kyb", "beta-pkg"})}


def test_plugin_loading_is_off_by_default() -> None:
    from core.config import Settings

    fresh = Settings(_env_file=None)
    assert fresh.plugin_loading is False
    assert fresh.plugin_allowlist == ""


def test_api_startup_and_celery_workers_load_plugins() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    main = (root / "api" / "main.py").read_text(encoding="utf-8")
    assert main.index("await init_db()") < main.index("load_configured_plugins()")
    celery = (root / "core" / "tasks" / "celery_app.py").read_text(encoding="utf-8")
    assert "@worker_process_init.connect" in celery
    assert "load_configured_plugins()" in celery
