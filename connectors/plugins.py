# SPDX-License-Identifier: Apache-2.0
"""Load connectors and agents shipped as separate packages, via entry points.

A package extends AgenticOrg without changing this repository by declaring
entry points in one of these groups::

    [project.entry-points."agenticorg.connectors"]
    acme_kyb = "acme_kyb_agenticorg.connector:AcmeKybConnector"

- ``agenticorg.connectors`` - a ``BaseConnector`` subclass
- ``agenticorg.agents`` - a ``BaseAgent`` subclass
- ``agenticorg.providers`` and ``agenticorg.workflows`` - discovered, but
  rejected until those registries exist

Loading is off unless ``AGENTICORG_PLUGIN_LOADING`` is true, and only
distributions named in ``AGENTICORG_PLUGIN_ALLOWLIST`` are imported: loading
an entry point runs that package's code. Plugins load after native
registration and never replace a native connector or agent of the same name.
A plugin that is not allowlisted, fails to import, has the wrong type or
collides with a native name is rejected with a logged reason; the rest still
load and the application still starts. See
``docs/providers/plugin-packages.md``.
"""

from __future__ import annotations

import importlib.metadata
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import structlog
from prometheus_client import Counter

logger = structlog.get_logger()

CONNECTORS_GROUP = "agenticorg.connectors"
PROVIDERS_GROUP = "agenticorg.providers"
AGENTS_GROUP = "agenticorg.agents"
WORKFLOWS_GROUP = "agenticorg.workflows"
PLUGIN_GROUPS = (CONNECTORS_GROUP, PROVIDERS_GROUP, AGENTS_GROUP, WORKFLOWS_GROUP)
_UNSUPPORTED_GROUPS = frozenset({PROVIDERS_GROUP, WORKFLOWS_GROUP})

_plugin_load_total = Counter(
    "agenticorg_plugin_load_total",
    "Entry-point plugins considered at startup, by group and outcome",
    ["group", "outcome"],
)


@dataclass(frozen=True)
class PluginOutcome:
    group: str
    name: str
    distribution: str | None
    loaded: bool
    reason: str
    detail: str = ""


class _PluginRejectedError(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _normalise(distribution: str) -> str:
    """PEP 503 normalisation, so ``Acme_KYB`` and ``acme-kyb`` are the same package."""
    return re.sub(r"[-_.]+", "-", distribution).strip().lower()


def parse_allowlist(value: str) -> frozenset[str]:
    return frozenset(_normalise(item) for item in value.split(",") if item.strip())


def _register_connector(obj: Any) -> str:
    from connectors.framework.base_connector import BaseConnector  # noqa: PLC0415
    from connectors.registry import ConnectorRegistry  # noqa: PLC0415

    if not (isinstance(obj, type) and issubclass(obj, BaseConnector) and obj.name):
        raise _PluginRejectedError("invalid_type", f"expected a named BaseConnector subclass, got {obj!r}")
    if ConnectorRegistry.get(obj.name) is not None:
        raise _PluginRejectedError("name_conflict", f"connector {obj.name!r} is already registered")
    ConnectorRegistry.register(obj)
    return str(obj.name)


def _register_agent(obj: Any) -> str:
    from core.agents.base import BaseAgent  # noqa: PLC0415
    from core.agents.registry import AgentRegistry  # noqa: PLC0415

    if not (isinstance(obj, type) and issubclass(obj, BaseAgent) and obj.agent_type):
        raise _PluginRejectedError("invalid_type", f"expected a BaseAgent subclass with an agent_type, got {obj!r}")
    if AgentRegistry.has_type(obj.agent_type):
        raise _PluginRejectedError("name_conflict", f"agent type {obj.agent_type!r} is already registered")
    AgentRegistry.register(obj)
    return str(obj.agent_type)


_REGISTRARS: dict[str, Callable[[Any], str]] = {
    CONNECTORS_GROUP: _register_connector,
    AGENTS_GROUP: _register_agent,
}


def _load_one(group: str, entry_point: Any, allowlist: frozenset[str]) -> PluginOutcome:
    dist = getattr(getattr(entry_point, "dist", None), "name", None)
    base = {"group": group, "name": str(entry_point.name), "distribution": dist}
    try:
        if group in _UNSUPPORTED_GROUPS:
            raise _PluginRejectedError("unsupported_group", f"no registry for {group} in this release")
        if not dist:
            raise _PluginRejectedError(
                "unknown_distribution", "entry point is not attached to an installed distribution"
            )
        if _normalise(dist) not in allowlist:
            raise _PluginRejectedError(
                "not_allowlisted", f"distribution {dist!r} is not in AGENTICORG_PLUGIN_ALLOWLIST"
            )
        try:
            obj = entry_point.load()
        # enterprise-gate: broad-except-ok reason=third-party-plugin-import-failure-rejects-that-plugin
        except Exception as exc:  # noqa: BLE001
            raise _PluginRejectedError("load_failed", f"{type(exc).__name__}: {exc}") from exc
        registered = _REGISTRARS[group](obj)
    except _PluginRejectedError as rejected:
        return PluginOutcome(**base, loaded=False, reason=rejected.reason, detail=rejected.detail)
    return PluginOutcome(**base, loaded=True, reason="loaded", detail=registered)


def load_plugins(
    *,
    enabled: bool,
    allowlist: Iterable[str],
    entry_points: Callable[..., Iterable[Any]] = importlib.metadata.entry_points,
) -> list[PluginOutcome]:
    """Discover and register allowlisted plugins; returns one outcome per entry point."""
    if not enabled:
        logger.info("plugin_loading_disabled")
        return []

    allowed = frozenset(_normalise(name) for name in allowlist)
    outcomes: list[PluginOutcome] = []
    for group in PLUGIN_GROUPS:
        for entry_point in entry_points(group=group):
            outcome = _load_one(group, entry_point, allowed)
            outcomes.append(outcome)
            _plugin_load_total.labels(group=group, outcome=outcome.reason).inc()
            log = logger.info if outcome.loaded else logger.error
            log(
                "plugin_loaded" if outcome.loaded else "plugin_rejected",
                group=group,
                plugin=outcome.name,
                distribution=outcome.distribution,
                reason=outcome.reason,
                detail=outcome.detail,
            )
    return outcomes


def load_configured_plugins() -> list[PluginOutcome]:
    """Load plugins according to ``AGENTICORG_PLUGIN_LOADING`` and ``AGENTICORG_PLUGIN_ALLOWLIST``."""
    from core.config import settings  # noqa: PLC0415

    return load_plugins(enabled=settings.plugin_loading, allowlist=parse_allowlist(settings.plugin_allowlist))
