"""Industry pack installer - discover, install, and uninstall agent packs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text

from core.agents.packs.ca import CA_PACK
from core.database import get_tenant_session

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

# Legacy in-memory store kept for unit tests that call the sync helpers
# directly. The live API uses the async DB-backed helpers below.
_installed: dict[str, set[str]] = {}


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file, falling back to a minimal safe parser."""
    try:
        import yaml  # type: ignore[import-untyped]

        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except ImportError:
        import re

        text_value = path.read_text(encoding="utf-8")
        result: dict[str, Any] = {}
        for line in text_value.splitlines():
            match = re.match(r"^(\w[\w_]*)\s*:\s*(.+)$", line)
            if match:
                key, val = match.group(1), match.group(2).strip()
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

    for pack_dir in _discover_pack_dirs():
        if pack_dir.name in _DIR_TO_REGISTERED:
            continue
        cfg = _load_yaml(pack_dir / "config.yaml")
        packs.append(
            {
                "name": cfg.get("name", pack_dir.name),
                "display_name": cfg.get("display_name", pack_dir.name.title()),
                "description": cfg.get("description", ""),
                "agents": cfg.get("agents", []),
                "workflows": cfg.get("workflows", []),
                "compliance": cfg.get("compliance", []),
                "pricing": cfg.get("pricing", {}),
                "version": cfg.get("version", "0.0.0"),
            }
        )

    seen_names = {p["name"] for p in packs}
    for pack_id, pack_cfg in _REGISTERED_PACKS.items():
        name = pack_cfg.get("name", pack_id)
        if name in seen_names:
            continue
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

    for pack in list_packs():
        if pack["name"] == pack_name:
            return pack
    return None


def _build_install_summary(detail: dict[str, Any]) -> tuple[list[dict[str, Any]], list[Any]]:
    agents_created: list[dict[str, Any]] = []
    for agent_cfg in detail.get("agents", []):
        agents_created.append(
            {
                "type": agent_cfg.get("type", "unknown") if isinstance(agent_cfg, dict) else str(agent_cfg),
                "domain": agent_cfg.get("domain", "ops") if isinstance(agent_cfg, dict) else "ops",
                "mode": "shadow",
                "tools": agent_cfg.get("tools", []) if isinstance(agent_cfg, dict) else [],
            }
        )

    return agents_created, list(detail.get("workflows", []))


def install_pack(pack_name: str, tenant_id: str) -> dict[str, Any]:
    """Legacy sync install used by unit tests."""
    detail = get_pack_detail(pack_name)
    if detail is None:
        raise ValueError(f"Pack '{pack_name}' not found")

    tenant_packs = _installed.setdefault(tenant_id, set())
    if pack_name in tenant_packs:
        return {"status": "already_installed", "pack": pack_name, "tenant_id": tenant_id}

    created_agents, created_workflows = _build_install_summary(detail)
    tenant_packs.add(pack_name)

    return {
        "status": "installed",
        "pack": pack_name,
        "tenant_id": tenant_id,
        "agents_created": created_agents,
        "workflows_created": created_workflows,
    }


def uninstall_pack(pack_name: str, tenant_id: str) -> dict[str, Any]:
    """Legacy sync uninstall used by unit tests."""
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
    """Legacy sync list used by unit tests."""
    return sorted(_installed.get(tenant_id, set()))


async def get_installed_packs_async(tenant_id: str) -> list[str]:
    """Return persisted pack installs for a tenant."""
    tid = UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            text("""
                SELECT pack_name
                FROM industry_pack_installs
                WHERE tenant_id = :tenant_id
                ORDER BY pack_name
            """),
            {"tenant_id": tid},
        )
        return [str(row[0]) for row in result.fetchall()]


async def install_pack_async(pack_name: str, tenant_id: str) -> dict[str, Any]:
    """Install a pack using the persisted installs table."""
    detail = get_pack_detail(pack_name)
    if detail is None:
        raise ValueError(f"Pack '{pack_name}' not found")

    created_agents, created_workflows = _build_install_summary(detail)
    tid = UUID(tenant_id)

    async with get_tenant_session(tid) as session:
        existing = await session.execute(
            text("""
                SELECT 1
                FROM industry_pack_installs
                WHERE tenant_id = :tenant_id AND pack_name = :pack_name
                LIMIT 1
            """),
            {"tenant_id": tid, "pack_name": pack_name},
        )
        if existing.scalar_one_or_none():
            return {"status": "already_installed", "pack": pack_name, "tenant_id": tenant_id}

        await session.execute(
            text("""
                INSERT INTO industry_pack_installs (tenant_id, pack_name, agent_ids, workflow_ids)
                VALUES (
                    :tenant_id,
                    :pack_name,
                    CAST(:agent_ids AS jsonb),
                    CAST(:workflow_ids AS jsonb)
                )
            """),
            {
                "tenant_id": tid,
                "pack_name": pack_name,
                "agent_ids": json.dumps([agent["type"] for agent in created_agents]),
                "workflow_ids": json.dumps(created_workflows),
            },
        )

    return {
        "status": "installed",
        "pack": pack_name,
        "tenant_id": tenant_id,
        "agents_created": created_agents,
        "workflows_created": created_workflows,
    }


async def uninstall_pack_async(pack_name: str, tenant_id: str) -> dict[str, Any]:
    """Uninstall a pack using the persisted installs table."""
    tid = UUID(tenant_id)
    detail = get_pack_detail(pack_name)
    removed_agents = []
    removed_workflows = []
    if detail:
        for agent_cfg in detail.get("agents", []):
            removed_agents.append(
                agent_cfg.get("type", "unknown") if isinstance(agent_cfg, dict) else str(agent_cfg)
            )
        removed_workflows = list(detail.get("workflows", []))

    async with get_tenant_session(tid) as session:
        existing = await session.execute(
            text("""
                SELECT 1
                FROM industry_pack_installs
                WHERE tenant_id = :tenant_id AND pack_name = :pack_name
                LIMIT 1
            """),
            {"tenant_id": tid, "pack_name": pack_name},
        )
        if not existing.scalar_one_or_none():
            return {"status": "not_installed", "pack": pack_name, "tenant_id": tenant_id}

        await session.execute(
            text("""
                DELETE FROM industry_pack_installs
                WHERE tenant_id = :tenant_id AND pack_name = :pack_name
            """),
            {"tenant_id": tid, "pack_name": pack_name},
        )

    return {
        "status": "uninstalled",
        "pack": pack_name,
        "tenant_id": tenant_id,
        "agents_removed": removed_agents,
        "workflows_removed": removed_workflows,
    }
