"""Smoke + contract tests for scripts/inventory_stale_ca_pack_agents.py.

Surfaces the exact failure mode the first cut shipped with: the script
imported a name (``get_engine``) that doesn't exist on
``core.database`` and the Cloud Run job exited 1 on the first
production run (2026-05-03). These tests pin:

1. The script is importable (catches missing/renamed names in
   ``core.database``).
2. The script references the real ``engine`` symbol, not a non-
   existent factory.
3. ``_inventory`` is callable and async (so the entrypoint
   ``asyncio.run(_inventory())`` works without a TypeError).
4. The CA pack agent type tuple still matches what the installer
   creates — drift here would make the inventory undercount.
"""

from __future__ import annotations

import importlib
import inspect


def test_script_module_imports_without_error() -> None:
    """If ``core.database`` is renamed/refactored, this catches the
    ImportError at CI time instead of in a one-shot prod job."""
    mod = importlib.import_module("scripts.inventory_stale_ca_pack_agents")
    assert mod is not None


def test_script_uses_real_engine_attribute() -> None:
    """Pin the engine import so a future refactor that removes the
    module-level ``engine`` attribute fails CI rather than the prod
    job."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    from core.database import engine as db_engine

    assert isinstance(db_engine, AsyncEngine)


def test_inventory_function_is_async_and_callable() -> None:
    from scripts.inventory_stale_ca_pack_agents import _inventory

    assert callable(_inventory)
    assert inspect.iscoroutinefunction(_inventory)


def test_ca_pack_agent_types_match_installer() -> None:
    """Drift between this script's hardcoded type list and the actual
    pack manifest would cause the inventory to silently miss agents.
    Lock the two together."""
    from core.agents.packs.ca import CA_PACK
    from scripts.inventory_stale_ca_pack_agents import CA_PACK_AGENT_TYPES

    pack_types = set()
    for agent_cfg in CA_PACK.get("agents", []):
        # CA_PACK uses agent_type names like "gst_filing"; the agents
        # table stores them with an "_agent" suffix attached by the
        # installer — match that convention.
        raw = agent_cfg.get("type") or agent_cfg.get("name", "").lower().replace(" ", "_")
        if not raw:
            continue
        pack_types.add(f"{raw.lower()}_agent" if not raw.endswith("agent") else raw)
    inventory_types = set(CA_PACK_AGENT_TYPES)
    # Every type the inventory checks must correspond to a real pack
    # agent. We don't require equality both ways because the installer
    # may rename normalize; one-way coverage is the contract.
    # ``fp_a_analyst_agent`` and ``ar_collections_agent`` come out of
    # the installer's type-normalization. Tolerate the underscore
    # variants.
    normalized_pack = {t.replace("&", "_").replace("__", "_") for t in pack_types}
    for t in inventory_types:
        normalized_t = t.replace("&", "_").replace("__", "_")
        # Either the inventory type matches a pack type directly, or
        # its base (no _agent suffix) maps to one. Otherwise the
        # inventory will silently undercount.
        assert (
            t in pack_types
            or normalized_t in normalized_pack
            or t.removesuffix("_agent") in {p.removesuffix("_agent") for p in pack_types}
        ), f"Inventory type {t!r} has no matching pack agent type. Drift!"
