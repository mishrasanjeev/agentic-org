"""Industry pack installer — discover, install, and uninstall agent packs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.agents.packs.ca import CA_PACK

_PACKS_DIR = Path(__file__).resolve().parent

# Registry of programmatically-defined packs (supplement YAML-based discovery).
_ca_id: str = str(CA_PACK["id"])
_REGISTERED_PACKS: dict[str, dict[str, Any]] = {
    _ca_id: CA_PACK,
}

# Map pack directory names to registered pack IDs so YAML discovery defers to
# the richer programmatic definition when both exist.
_DIR_TO_REGISTERED: dict[str, str] = {
    "ca": _ca_id,
}

# In-memory store keyed by tenant_id → set of installed pack names.
# Replace with DB-backed store once migration exists.
_installed: dict[str, set[str]] = {}


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file, falling back to a minimal safe parser."""
    try:
        import yaml  # type: ignore[import-untyped]

        with open(path) as fh:
            return yaml.safe_load(fh) or {}
    except ImportError:
        # Minimal parser for CI environments without PyYAML
        import re

        text = path.read_text(encoding="utf-8")
        # Very basic: convert simple YAML key: value lines to JSON-ish dict
        result: dict[str, Any] = {}
        for line in text.splitlines():
            m = re.match(r"^(\w[\w_]*)\s*:\s*(.+)$", line)
            if m:
                key, val = m.group(1), m.group(2).strip()
                if val.startswith("[") and val.endswith("]"):
                    result[key] = [v.strip().strip("'\"") for v in val[1:-1].split(",")]
                else:
                    result[key] = val
        return result


def _discover_pack_dirs() -> list[Path]:
    """Return directories under packs/ that contain a config.yaml."""
    dirs: list[Path] = []
    if not _PACKS_DIR.is_dir():
        return dirs
    for entry in sorted(_PACKS_DIR.iterdir()):
        if entry.is_dir() and (entry / "config.yaml").exists():
            dirs.append(entry)
    return dirs


def list_packs() -> list[dict[str, Any]]:
    """Return metadata for every available industry pack."""
    packs: list[dict[str, Any]] = []

    # YAML-based packs discovered from subdirectories.
    # Skip directories that have a richer programmatic registration.
    for pack_dir in _discover_pack_dirs():
        if pack_dir.name in _DIR_TO_REGISTERED:
            continue  # handled by _REGISTERED_PACKS below
        cfg = _load_yaml(pack_dir / "config.yaml")
        packs.append(
            {
                "name": cfg.get("name", pack_dir.name),
                "display_name": cfg.get("display_name", pack_dir.name.title()),
                "description": cfg.get("description", ""),
                "agents": cfg.get("agents", []),
                "workflows": cfg.get("workflows", []),
                "compliance": cfg.get("compliance", []),
            }
        )

    # Programmatically-registered packs (e.g. CA pack with pricing metadata).
    seen_names = {p["name"] for p in packs}
    for pack_id, pack_cfg in _REGISTERED_PACKS.items():
        name = pack_cfg.get("name", pack_id)
        if name in seen_names:
            continue  # YAML config already captured this pack
        packs.append(
            {
                "name": pack_id,
                "display_name": pack_cfg.get("name", pack_id),
                "description": pack_cfg.get("description", ""),
                "agents": pack_cfg.get("agents", []),
                "workflows": pack_cfg.get("workflows", []),
                "compliance": pack_cfg.get("compliance", []),
                "pricing": pack_cfg.get("pricing", {}),
                "version": pack_cfg.get("version", "0.0.0"),
            }
        )

    return packs


def get_pack_detail(pack_name: str) -> dict[str, Any] | None:
    """Return full config for a single pack, or None if not found."""
    # Check registered packs first for exact ID match.
    if pack_name in _REGISTERED_PACKS:
        cfg = _REGISTERED_PACKS[pack_name]
        return {
            "name": pack_name,
            "display_name": cfg.get("name", pack_name),
            "description": cfg.get("description", ""),
            "agents": cfg.get("agents", []),
            "workflows": cfg.get("workflows", []),
            "compliance": cfg.get("compliance", []),
            "pricing": cfg.get("pricing", {}),
            "version": cfg.get("version", "0.0.0"),
        }
    # Fall back to YAML-based discovery.
    for pack in list_packs():
        if pack["name"] == pack_name:
            return pack
    return None


def install_pack(pack_name: str, tenant_id: str) -> dict[str, Any]:
    """Install a pack for a tenant — creates agents in shadow mode and registers workflows.

    Returns a summary dict with created agents and workflows.
    """
    detail = get_pack_detail(pack_name)
    if detail is None:
        raise ValueError(f"Pack '{pack_name}' not found")

    tenant_packs = _installed.setdefault(tenant_id, set())
    if pack_name in tenant_packs:
        return {"status": "already_installed", "pack": pack_name, "tenant_id": tenant_id}

    created_agents: list[dict[str, Any]] = []
    for agent_cfg in detail.get("agents", []):
        created_agents.append(
            {
                "type": agent_cfg.get("type", "unknown") if isinstance(agent_cfg, dict) else str(agent_cfg),
                "domain": agent_cfg.get("domain", "ops") if isinstance(agent_cfg, dict) else "ops",
                "mode": "shadow",
                "tools": agent_cfg.get("tools", []) if isinstance(agent_cfg, dict) else [],
            }
        )

    created_workflows = list(detail.get("workflows", []))

    tenant_packs.add(pack_name)

    return {
        "status": "installed",
        "pack": pack_name,
        "tenant_id": tenant_id,
        "agents_created": created_agents,
        "workflows_created": created_workflows,
    }


def uninstall_pack(pack_name: str, tenant_id: str) -> dict[str, Any]:
    """Remove a pack's agents and workflows for a tenant."""
    tenant_packs = _installed.get(tenant_id, set())
    if pack_name not in tenant_packs:
        return {"status": "not_installed", "pack": pack_name, "tenant_id": tenant_id}

    detail = get_pack_detail(pack_name)
    removed_agents = []
    removed_workflows = []
    if detail:
        for agent_cfg in detail.get("agents", []):
            removed_agents.append(
                agent_cfg.get("type", "unknown") if isinstance(agent_cfg, dict) else str(agent_cfg)
            )
        removed_workflows = list(detail.get("workflows", []))

    tenant_packs.discard(pack_name)

    return {
        "status": "uninstalled",
        "pack": pack_name,
        "tenant_id": tenant_id,
        "agents_removed": removed_agents,
        "workflows_removed": removed_workflows,
    }


def get_installed_packs(tenant_id: str) -> list[str]:
    """Return names of packs currently installed for a tenant."""
    return sorted(_installed.get(tenant_id, set()))
