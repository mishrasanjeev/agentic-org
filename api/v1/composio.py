"""Composio Marketplace API — discover, search, and connect 1000+ apps.

Exposes the Composio SDK's app catalog and tool discovery to the frontend.
Apps are cached for 10 minutes to avoid hammering the Composio API.
"""

from __future__ import annotations

import os
import time
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query

logger = structlog.get_logger()

router = APIRouter(prefix="/composio", tags=["Composio Marketplace"])

# Guard Composio SDK import
try:
    from composio import ComposioToolSet as _ComposioToolSet

    _COMPOSIO_AVAILABLE = True
except ImportError:
    _ComposioToolSet = None  # type: ignore[assignment,misc]
    _COMPOSIO_AVAILABLE = False

_API_KEY = os.getenv("COMPOSIO_API_KEY", "")

# ── In-memory cache ─────────────────────────────────────────────────

_apps_cache: list[dict[str, Any]] = []
_apps_cache_ts: float = 0.0
_CACHE_TTL = 600  # 10 minutes


def _get_toolset():
    if not _COMPOSIO_AVAILABLE:
        raise HTTPException(503, "Composio SDK not installed (pip install composio-core)")
    if not _API_KEY:
        raise HTTPException(503, "COMPOSIO_API_KEY not configured")
    return _ComposioToolSet(api_key=_API_KEY)


def _refresh_apps_cache() -> list[dict[str, Any]]:
    global _apps_cache, _apps_cache_ts

    if _apps_cache and (time.monotonic() - _apps_cache_ts) < _CACHE_TTL:
        return _apps_cache

    ts = _get_toolset()
    raw_apps = ts.get_apps()

    apps: list[dict[str, Any]] = []
    for a in raw_apps:
        apps.append({
            "key": getattr(a, "key", ""),
            "name": getattr(a, "name", ""),
            "description": getattr(a, "description", "") or "",
            "logo": getattr(a, "logo", "") or "",
            "categories": getattr(a, "categories", []) or [],
            "enabled": getattr(a, "enabled", True),
            "no_auth": getattr(a, "no_auth", False),
        })

    _apps_cache = apps
    _apps_cache_ts = time.monotonic()
    logger.info("composio_apps_cached", count=len(apps))
    return apps


# ── Endpoints ───────────────────────────────────────────────────────


@router.get("/apps")
async def list_apps(
    search: str = "",
    category: str = "",
    limit: int = Query(default=100, le=1000),
    offset: int = 0,
) -> dict[str, Any]:
    """List all available Composio apps with search and category filtering."""
    try:
        apps = _refresh_apps_cache()
    except Exception as exc:
        logger.exception("composio_apps_fetch_failed")
        raise HTTPException(502, f"Failed to fetch Composio apps: {exc}") from exc

    # Filter
    filtered = apps
    if search:
        q = search.lower()
        filtered = [
            a for a in filtered
            if q in a["name"].lower() or q in a["description"].lower()
        ]
    if category:
        cat = category.lower()
        filtered = [
            a for a in filtered
            if any(cat == c.lower() for c in a["categories"])
        ]

    total = len(filtered)
    page = filtered[offset : offset + limit]

    return {
        "apps": page,
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/apps/{app_key}")
async def get_app_detail(app_key: str) -> dict[str, Any]:
    """Get details and available actions for a specific Composio app."""
    ts = _get_toolset()

    # Get app info
    apps = _refresh_apps_cache()
    app_info = next((a for a in apps if a["key"] == app_key), None)
    if not app_info:
        raise HTTPException(404, f"App '{app_key}' not found")

    # Get actions for this app
    try:
        actions = ts.find_actions_by_use_case(
            app=app_key,
            use_case="all",
        )
        action_list = []
        for act in actions[:50]:  # cap at 50 actions
            action_list.append({
                "name": getattr(act, "name", str(act)),
                "description": getattr(act, "description", "") or "",
            })
    except Exception:
        # find_actions_by_use_case may not work for all apps — fall back
        try:
            schemas = ts.get_action_schemas(apps=[app_key])
            action_list = [
                {
                    "name": getattr(s, "name", "") or s.get("name", ""),
                    "description": getattr(s, "description", "") or s.get("description", ""),
                }
                for s in schemas[:50]
            ]
        except Exception:
            action_list = []

    return {
        **app_info,
        "actions": action_list,
    }


@router.get("/categories")
async def list_categories() -> list[str]:
    """List all unique categories across Composio apps."""
    apps = _refresh_apps_cache()
    cats: set[str] = set()
    for a in apps:
        for c in a.get("categories", []):
            if c:
                cats.add(c)
    return sorted(cats)
