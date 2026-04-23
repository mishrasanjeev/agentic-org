"""Generic RPA script registry.

Discovers every Python module under ``rpa/scripts/`` that exposes a
``SCRIPT_META`` dict and an ``async def run(...)`` entrypoint, and makes
them available to the API + scheduler without a hardcoded list.

SCRIPT_META contract (each script module must define this):

    SCRIPT_META = {
        "name": "display name",                    # human-readable
        "description": "what this RPA does",
        "category": "compliance|research|general|…",
        "params_schema": {                         # UI hint — optional
            "param_key": {"type": "string", "label": "...", "required": True},
            ...
        },
        "required_params": ["list", "of", "keys"], # back-compat alias
        "estimated_duration_s": 60,                # optional — default 60
        "produces_chunks": True,                   # optional — if the
            # script returns {chunks: [...]} to be vector-embedded
        "admin_only": False,                       # optional — forces
            # tenant-admin scope to run (SSRF / secret risk)
        "http_only": False,                        # optional — run via
            # httpx instead of Playwright (static sites, JSON APIs)
        "target_quality": 4.8,                     # optional — default
            # 4.8 per the 2026-04-23 RPA spec
    }

The existing scripts (``epfo_ecr_download``, ``mca_company_search``,
``generic_portal``) each define some of these fields. Fields not
defined are filled with sensible defaults so older scripts keep
working while new scripts can opt into richer metadata.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent


def _load_module(script_file: Path) -> Any | None:
    """Import a script module by file path, return None on failure."""
    mod_name = f"rpa.scripts.{script_file.stem}"
    try:
        return importlib.import_module(mod_name)
    except Exception:
        return None


def discover_scripts() -> dict[str, dict[str, Any]]:
    """Return a dict of ``{script_key: metadata}`` for every script in
    ``rpa/scripts/`` that defines SCRIPT_META + ``run``.

    Script key is the filename stem (``rbi_org_scraper.py`` →
    ``rbi_org_scraper``). Scripts without SCRIPT_META are skipped;
    scripts without ``run`` are skipped.

    The returned metadata is normalised:
    - missing ``params_schema`` is derived from ``required_params``.
    - missing ``estimated_duration_s`` defaults to 60.
    - missing ``target_quality`` defaults to 4.8.
    - missing ``http_only`` / ``admin_only`` / ``produces_chunks``
      default to False.
    """
    scripts: dict[str, dict[str, Any]] = {}
    for script_file in sorted(_SCRIPTS_DIR.glob("*.py")):
        if script_file.stem.startswith("_"):
            continue
        mod = _load_module(script_file)
        if mod is None:
            continue
        meta = getattr(mod, "SCRIPT_META", None)
        if not isinstance(meta, dict):
            continue
        if not callable(getattr(mod, "run", None)):
            continue

        normalised = dict(meta)
        normalised.setdefault("script_key", script_file.stem)
        normalised.setdefault(
            "name",
            meta.get("name") or script_file.stem.replace("_", " ").title(),
        )
        normalised.setdefault("description", "")
        normalised.setdefault("category", "general")
        normalised.setdefault("estimated_duration_s", 60)
        normalised.setdefault("target_quality", 4.8)
        normalised.setdefault("admin_only", False)
        normalised.setdefault("http_only", False)
        normalised.setdefault("produces_chunks", False)
        if "params_schema" not in normalised:
            required = normalised.get("required_params") or []
            normalised["params_schema"] = {
                key: {"type": "string", "label": key, "required": True}
                for key in required
            }
        scripts[script_file.stem] = normalised
    return scripts


def get_script_meta(script_key: str) -> dict[str, Any] | None:
    """Return metadata for a single script, or None if not registered."""
    return discover_scripts().get(script_key)
