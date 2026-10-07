# SPDX-License-Identifier: Apache-2.0
"""The AI asset inventory: what a tenant runs, who owns it, which version, at which risk tier.

An AI bill of materials assembled live from the tenant's configuration,
never from a run and never from prompt text:

* **agents**, with their owner, version, runtime status and the registry's
  risk tier and state;
* **models**: the models the tenant's settings name (default, fallback,
  embedding) and the models its agents call, each at the highest risk tier
  of the agents that depend on it;
* **prompts**: the tenant's prompt templates and the agents' own prompts
  (as a content hash and a version, never the text);
* **knowledge bases**: one per document domain, with document and chunk
  counts and the embedding models in use;
* **tools** and the **connectors** behind them, each at the highest risk
  tier of the agents that call them.

Every asset carries a reference (``kind:key``), a name, an owner, a version,
a risk tier and a status, and the assets it depends on; ``export`` writes the
inventory as a bill-of-materials document with components and dependencies.
An owner is a user id, ``platform`` for a built-in, or ``None`` when nobody
has claimed the asset, which the summary counts so a reviewer sees the gap.

Off (``AGENTICORG_GOVERNANCE_INVENTORY_ENABLED``), the endpoints are not
found and nothing here reads the database.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from core.agent_registry.dependencies import KNOWLEDGE_TOOLS, _connector_of
from core.config import settings

logger = structlog.get_logger()

KINDS: tuple[str, ...] = ("agent", "model", "prompt", "knowledge_base", "tool", "connector")
TIERS: tuple[str, ...] = ("low", "medium", "high", "critical")
BOM_FORMAT = "AgenticOrg-AIBOM"
SPEC_VERSION = "1.0"
MAX_ASSETS = 5000
PLATFORM = "platform"


def enabled() -> bool:
    return bool(settings.governance_inventory_enabled)


def highest_tier(tiers: list[str | None]) -> str | None:
    """The highest of the known risk tiers, or None when none is set."""
    known = [t for t in tiers if t in TIERS]
    if not known:
        return None
    return max(known, key=TIERS.index)


@dataclass
class Asset:
    ref: str
    kind: str
    name: str
    owner: str | None = None
    version: str | None = None
    risk_tier: str | None = None
    status: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "kind": self.kind,
            "name": self.name,
            "owner": self.owner,
            "version": self.version,
            "risk_tier": self.risk_tier,
            "status": self.status,
            "detail": dict(self.detail),
            "depends_on": list(self.depends_on),
        }


class Inventory:
    """The assets by reference; adding a known reference merges dependencies and raises the tier."""

    def __init__(self) -> None:
        self.assets: dict[str, Asset] = {}

    def add(self, asset: Asset) -> Asset:
        existing = self.assets.get(asset.ref)
        if existing is None:
            if len(self.assets) >= MAX_ASSETS:
                raise ValueError(f"the inventory holds at most {MAX_ASSETS} assets")
            self.assets[asset.ref] = asset
            return asset
        for dep in asset.depends_on:
            if dep not in existing.depends_on:
                existing.depends_on.append(dep)
        existing.risk_tier = highest_tier([existing.risk_tier, asset.risk_tier])
        if existing.owner is None and asset.owner is not None:
            existing.owner = asset.owner
        for key, value in asset.detail.items():
            current = existing.detail.get(key)
            if isinstance(current, list) and isinstance(value, list):
                current.extend(v for v in value if v not in current)
            else:
                existing.detail.setdefault(key, value)
        return existing

    def raise_tier(self, ref: str, tier: str | None) -> None:
        asset = self.assets.get(ref)
        if asset is not None:
            asset.risk_tier = highest_tier([asset.risk_tier, tier])

    def summary(self) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        by_tier: dict[str, int] = {}
        unowned = untiered = 0
        for asset in self.assets.values():
            by_kind[asset.kind] = by_kind.get(asset.kind, 0) + 1
            by_tier[asset.risk_tier or "unset"] = by_tier.get(asset.risk_tier or "unset", 0) + 1
            unowned += asset.owner is None
            untiered += asset.kind == "agent" and asset.risk_tier is None
        return {
            "assets": len(self.assets),
            "by_kind": dict(sorted(by_kind.items())),
            "by_risk_tier": dict(sorted(by_tier.items())),
            "unowned": unowned,
            "untiered_agents": untiered,
        }

    def select(self, *, kind: str | None = None, risk_tier: str | None = None, q: str | None = None) -> list[Asset]:
        needle = (q or "").strip().lower()
        chosen = []
        for asset in self.assets.values():
            if kind and asset.kind != kind:
                continue
            if risk_tier and (asset.risk_tier or "unset") != risk_tier:
                continue
            if needle and needle not in asset.name.lower() and needle not in asset.ref.lower():
                continue
            chosen.append(asset)
        return sorted(chosen, key=lambda a: (KINDS.index(a.kind) if a.kind in KINDS else 99, a.name.lower(), a.ref))

    def as_dict(self, **filters: Any) -> dict[str, Any]:
        return {"assets": [a.as_dict() for a in self.select(**filters)], "summary": self.summary()}


def model_ref(provider: Any, model: Any) -> str:
    return f"model:{str(provider or 'unknown').strip().lower()}/{str(model).strip()}"


def _owner_of(user_id: Any, *, builtin: bool = False) -> str | None:
    if user_id:
        return str(user_id)
    return PLATFORM if builtin else None


def _date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)[:10] if value else None


def build(
    *,
    agents: list[Any],
    entries: list[Any],
    prompts: list[Any],
    ai_settings: Any = None,
    knowledge_rows: list[tuple[Any, ...]],
) -> Inventory:
    """The inventory from the rows the loaders read; pure, so it can be checked without a database."""
    inv = Inventory()
    entries_by_agent = {getattr(e, "agent_id", None): e for e in entries}

    if ai_settings is not None:
        owner = _owner_of(getattr(ai_settings, "updated_by", None))
        for role, provider, model in (
            ("default", getattr(ai_settings, "llm_provider", None), getattr(ai_settings, "llm_model", None)),
            ("fallback", getattr(ai_settings, "llm_provider", None), getattr(ai_settings, "llm_fallback_model", None)),
            (
                "embedding",
                getattr(ai_settings, "embedding_provider", None),
                getattr(ai_settings, "embedding_model", None),
            ),
        ):
            if model:
                inv.add(
                    Asset(
                        ref=model_ref(provider, model),
                        kind="model",
                        name=str(model),
                        owner=owner,
                        version=str(model),
                        status="configured",
                        detail={"provider": str(provider or "unknown"), "roles": [role]},
                    )
                )
                roles = inv.assets[model_ref(provider, model)].detail.setdefault("roles", [])
                if role not in roles:
                    roles.append(role)

    template_by_name: dict[str, str] = {}
    for template in prompts:
        ref = f"prompt:{template.id}"
        builtin = bool(getattr(template, "is_builtin", False))
        inv.add(
            Asset(
                ref=ref,
                kind="prompt",
                name=str(template.name),
                owner=_owner_of(getattr(template, "created_by", None), builtin=builtin),
                version=_date(getattr(template, "updated_at", None)),
                status="active" if getattr(template, "is_active", True) else "inactive",
                detail={
                    "agent_type": getattr(template, "agent_type", None),
                    "domain": getattr(template, "domain", None),
                    "builtin": builtin,
                },
            )
        )
        template_by_name.setdefault(str(template.name), ref)

    for row in knowledge_rows:
        domain = str(row[0] or "shared")
        models = [m for m in (row[4] or []) if m] if len(row) > 4 else []
        inv.add(
            Asset(
                ref=f"knowledge_base:{domain}",
                kind="knowledge_base",
                name=f"Knowledge base ({domain})",
                version=_date(row[3] if len(row) > 3 else None),
                status="ready",
                detail={
                    "domain": domain,
                    "documents": int(row[1] or 0),
                    "chunks": int(row[2] or 0) if len(row) > 2 else 0,
                    "embedding_models": sorted(str(m) for m in models),
                },
            )
        )

    for agent in agents:
        entry = entries_by_agent.get(agent.id)
        tier = getattr(entry, "risk_tier", None) if entry is not None else None
        deps: list[str] = []
        for role, model in (
            ("agent", getattr(agent, "llm_model", None)),
            ("fallback", getattr(agent, "llm_fallback", None)),
        ):
            if model:
                ref = model_ref(getattr(agent, "llm_provider", None), model)
                inv.add(
                    Asset(
                        ref=ref,
                        kind="model",
                        name=str(model),
                        version=str(model),
                        risk_tier=tier,
                        status="in_use",
                        detail={"provider": str(getattr(agent, "llm_provider", None) or "unknown"), "roles": [role]},
                    )
                )
                inv.raise_tier(ref, tier)
                deps.append(ref)
        builtin = bool(getattr(agent, "is_builtin", False))
        owner = _owner_of(getattr(agent, "owner_user_id", None), builtin=builtin)
        text = str(getattr(agent, "system_prompt_text", None) or "")
        prompt_ref = str(getattr(agent, "system_prompt_ref", None) or "")
        if text:
            ref = f"prompt:agent:{agent.id}"
            inv.add(
                Asset(
                    ref=ref,
                    kind="prompt",
                    name=f"{agent.name} (own prompt)",
                    owner=owner,
                    version=hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
                    risk_tier=tier,
                    status="in_use",
                    detail={"characters": len(text), "agent_id": str(agent.id)},
                )
            )
            deps.append(ref)
        elif prompt_ref:
            ref = template_by_name.get(prompt_ref) or f"prompt:ref:{prompt_ref}"
            if ref not in inv.assets:
                inv.add(
                    Asset(
                        ref=ref,
                        kind="prompt",
                        name=prompt_ref,
                        owner=PLATFORM,
                        status="reference",
                        detail={"reference": prompt_ref},
                    )
                )
            inv.raise_tier(ref, tier)
            deps.append(ref)
        for tool in list(getattr(agent, "authorized_tools", None) or []):
            name = str(tool)
            connector = _connector_of(name)
            tool_ref = f"tool:{name}"
            inv.add(
                Asset(
                    ref=tool_ref,
                    kind="tool",
                    name=name,
                    owner=PLATFORM if connector is None else None,
                    risk_tier=tier,
                    status="in_use",
                    detail={"connector": connector},
                    depends_on=[f"connector:{connector}"] if connector else [],
                )
            )
            inv.raise_tier(tool_ref, tier)
            deps.append(tool_ref)
            if connector:
                inv.add(
                    Asset(
                        ref=f"connector:{connector}",
                        kind="connector",
                        name=connector,
                        risk_tier=tier,
                        status="in_use",
                    )
                )
                inv.raise_tier(f"connector:{connector}", tier)
            if name in KNOWLEDGE_TOOLS:
                domain = str(getattr(agent, "domain", None) or "shared")
                kb_ref = f"knowledge_base:{domain}"
                if kb_ref not in inv.assets:
                    inv.add(
                        Asset(
                            ref=kb_ref,
                            kind="knowledge_base",
                            name=f"Knowledge base ({domain})",
                            status="empty",
                            detail={"domain": domain, "documents": 0, "chunks": 0, "embedding_models": []},
                        )
                    )
                inv.raise_tier(kb_ref, tier)
                deps.append(kb_ref)
        inv.add(
            Asset(
                ref=f"agent:{agent.id}",
                kind="agent",
                name=str(agent.name),
                owner=owner,
                version=str(getattr(agent, "version", None) or "") or None,
                risk_tier=tier,
                status=str(getattr(agent, "status", None) or "unknown"),
                detail={
                    "agent_type": getattr(agent, "agent_type", None),
                    "domain": getattr(agent, "domain", None),
                    "registry_state": getattr(entry, "state", None) if entry is not None else "draft",
                    "maturity": getattr(agent, "maturity", None),
                    "visibility": getattr(agent, "visibility", None),
                    "builtin": builtin,
                },
                depends_on=deps,
            )
        )
    return inv


async def collect(session: Any, tenant_id: uuid.UUID) -> Inventory:
    """Read the tenant's rows and build the inventory."""
    from sqlalchemy import select
    from sqlalchemy import text as sqltext

    from core.models.agent import Agent
    from core.models.agent_registry import AgentRegistryEntry
    from core.models.prompt_template import PromptTemplate
    from core.models.tenant_ai_setting import TenantAISetting

    agents = list((await session.execute(select(Agent).where(Agent.tenant_id == tenant_id))).scalars().all())
    entries = list(
        (await session.execute(select(AgentRegistryEntry).where(AgentRegistryEntry.tenant_id == tenant_id)))
        .scalars()
        .all()
    )
    prompts = list(
        (await session.execute(select(PromptTemplate).where(PromptTemplate.tenant_id == tenant_id))).scalars().all()
    )
    ai_settings = (
        await session.execute(select(TenantAISetting).where(TenantAISetting.tenant_id == tenant_id))
    ).scalar_one_or_none()
    knowledge_rows = list(
        (
            await session.execute(
                sqltext(
                    "SELECT COALESCE(domain, 'shared'), "
                    "COUNT(DISTINCT COALESCE(source_object_id::text, split_part(source, '#chunk', 1))), "
                    "COUNT(*), MAX(created_at), array_agg(DISTINCT embedding_model) "
                    "FROM knowledge_documents WHERE tenant_id = :tid AND status = 'ready' "
                    "GROUP BY 1 ORDER BY 1"
                ),
                {"tid": str(tenant_id)},
            )
        ).fetchall()
    )
    inv = build(agents=agents, entries=entries, prompts=prompts, ai_settings=ai_settings, knowledge_rows=knowledge_rows)
    logger.info("governance_inventory_collected", **{k: v for k, v in inv.summary().items() if k != "by_risk_tier"})
    return inv


def export(inv: Inventory, *, tenant_id: uuid.UUID, generated_at: datetime | None = None) -> dict[str, Any]:
    """The inventory as a bill-of-materials document: components, their dependencies and a summary."""
    now = generated_at or datetime.now(UTC)
    serial = uuid.uuid5(uuid.NAMESPACE_URL, f"agenticorg:aibom:{tenant_id}:{now.isoformat()}")
    assets = inv.select()
    return {
        "bomFormat": BOM_FORMAT,
        "specVersion": SPEC_VERSION,
        "serialNumber": f"urn:uuid:{serial}",
        "metadata": {"generated_at": now.isoformat(), "tenant_id": str(tenant_id), "summary": inv.summary()},
        "components": [
            {
                "bom-ref": a.ref,
                "type": a.kind,
                "name": a.name,
                "version": a.version,
                "owner": a.owner,
                "risk_tier": a.risk_tier,
                "status": a.status,
                "properties": dict(a.detail),
            }
            for a in assets
        ],
        "dependencies": [{"ref": a.ref, "dependsOn": list(a.depends_on)} for a in assets if a.depends_on],
    }
