"""Single source of truth for product-wide numeric and version claims.

Every externally visible count (connectors, agents, tools) and the product
version must be derived from this endpoint. This replaces the README /
Landing / Pricing / Dashboard hardcoded numbers that had drifted apart and
were contradicting each other (e.g. README claimed 57 connectors while the
runtime registry has 53).

Rule: if a public surface cites a number, that number is fetched from
`/api/v1/product-facts` at render time (or pinned at build time from the
same endpoint). No other source is authoritative.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from connectors.registry import ConnectorRegistry
from core.agents.registry import AgentRegistry

router = APIRouter()


class ProductFacts(BaseModel):
    version: str
    connector_count: int
    agent_count: int
    tool_count: int


@lru_cache(maxsize=1)
def _version_from_pyproject() -> str:
    """Read the version from pyproject.toml so no file drifts out of sync.

    pyproject.toml is the canonical source shipped by packaging. api/main.py
    and api/v1/health.py previously hardcoded a mirror; this function is the
    one place that translates.
    """
    try:
        import tomllib
    except ImportError:  # Python <3.11 fallback
        import tomli as tomllib  # type: ignore[no-redef]
    root = Path(__file__).resolve().parents[2]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


@lru_cache(maxsize=1)
def _tool_count() -> int:
    """Count distinct tools exposed across all registered connectors.

    Uses the same tool index the /connectors/tools endpoint uses so the
    number on the landing page always matches the number the UI shows
    inside the product.
    """
    try:
        from core.langgraph.tool_adapter import _build_tool_index

        return len(_build_tool_index())
    except Exception:
        # Fallback path: same source /connectors/tools falls back to.
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

        unique: set[str] = set()
        for tools in _AGENT_TYPE_DEFAULT_TOOLS.values():
            unique.update(tools)
        return len(unique)


@router.get("/product-facts", response_model=ProductFacts)
async def product_facts() -> ProductFacts:
    """Canonical counts + version for every externally visible surface."""
    cr = ConnectorRegistry()
    ar = AgentRegistry()
    return ProductFacts(
        version=_version_from_pyproject(),
        connector_count=len(cr.all_names()),
        agent_count=len(ar.all_types()),
        tool_count=_tool_count(),
    )
