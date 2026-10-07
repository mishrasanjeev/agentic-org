# SPDX-License-Identifier: Apache-2.0
"""Model cards: one standard card per model a tenant uses.

A card is assembled live (``collect_card``) from what the platform already
knows and the part an administrator writes:

* **identity and facts**: provider, model, kind (``llm`` or ``embedding``)
  and the catalogue's figures (context window, output limit, tools, vision,
  dimensions);
* **use**: the roles the model plays for the tenant (default, fallback,
  embedding, agent), the agents that call it with their risk tier, and the
  highest tier among them (from the AI inventory);
* **governance**: the routing and access policies that name it, the limits
  that apply, and the residency decision for its provider;
* **economics and operations**: the list or negotiated price and the
  routing records' health over the quality window (calls, failure rate,
  latency, cost);
* **evaluation**: the newest stored evaluation run of the model;
* **written**: intended use, limitations, data handling, notes, the owner,
  and the approval (``draft`` until a second person approves; any edit
  returns it to draft).

``completeness`` names what is missing, so a reviewer sees which cards
are not yet fit to publish. Off (``AGENTICORG_GOVERNANCE_MODEL_CARDS_ENABLED``),
the endpoints are not found and nothing here reads or writes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from core.config import settings
from core.governance import inventory as inventory_module

logger = structlog.get_logger()

KINDS: tuple[str, ...] = ("llm", "embedding", "unknown")
STATUSES: tuple[str, ...] = ("draft", "approved")
WRITTEN_FIELDS: tuple[str, ...] = ("intended_use", "limitations", "data_handling", "notes")
REQUIRED_TEXT: tuple[str, ...] = ("intended_use", "limitations", "data_handling")
MAX_TEXT = 2000
MAX_EVAL_RUNS = 20


class ModelCardError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def enabled() -> bool:
    return bool(settings.governance_model_cards_enabled)


def normalise(provider: Any, model: Any) -> tuple[str, str]:
    p = str(provider or "").strip().lower()
    m = str(model or "").strip()
    if not p or not m or len(p) > 64 or len(m) > 128:
        raise ModelCardError(422, "model_name", "provider (at most 64 characters) and model (at most 128) are required")
    return p, m


def facts(provider: str, model: str) -> dict[str, Any]:
    """What the provider catalogue says about the model; ``in_catalogue`` is False for a model it does not list."""
    from core.ai_providers.catalog import find_embedding, find_llm

    llm = find_llm(provider, model)
    if llm is not None:
        return {
            "kind": "llm",
            "in_catalogue": True,
            "context_window": llm.context_window,
            "max_output_tokens": llm.max_output_tokens,
            "supports_tools": bool(llm.supports_tools),
            "supports_vision": bool(llm.supports_vision),
            "notes": llm.notes or "",
        }
    embedding = find_embedding(provider, model)
    if embedding is not None:
        return {
            "kind": "embedding",
            "in_catalogue": True,
            "dimensions": embedding.dimensions,
            "max_input_tokens": embedding.max_input_tokens,
            "notes": embedding.notes or "",
        }
    return {"kind": "unknown", "in_catalogue": False}


def usage(inv: inventory_module.Inventory, provider: str, model: str) -> dict[str, Any]:
    """How the tenant uses the model, from the inventory: roles, the agents that call it, the highest tier."""
    ref = inventory_module.model_ref(provider, model)
    asset = inv.assets.get(ref)
    agents = [
        {"id": a.ref.split(":", 1)[1], "name": a.name, "risk_tier": a.risk_tier, "status": a.status}
        for a in inv.select(kind="agent")
        if ref in a.depends_on
    ]
    return {
        "in_use": asset is not None,
        "roles": list((asset.detail.get("roles") if asset else None) or []),
        "settings_owner": asset.owner if asset else None,
        "risk_tier": inventory_module.highest_tier(
            [asset.risk_tier if asset else None, *(a["risk_tier"] for a in agents)]
        ),
        "agents": agents,
    }


def _names_model(policy: Any, provider: str, model: str) -> bool:
    own_model = str(getattr(policy, "model", None) or "").strip().lower()
    own_provider = str(getattr(policy, "provider", None) or "").strip().lower()
    if own_model == model.lower() and (not own_provider or own_provider == provider):
        return True
    for target in getattr(policy, "targets", None) or ():
        if str(target.get("model") or "").strip().lower() == model.lower():
            return True
    allowed = getattr(policy, "allowed_models", None) or ()
    return any(str(name).strip().lower() == model.lower() for name in allowed)


def governance(policy_set: Any, provider: str, model: str) -> dict[str, Any]:
    """The routing and access policies that name the model and the limits that apply to it."""
    routing = [
        {
            "id": p.id,
            "name": p.name,
            "priority": p.priority,
            "enabled": p.enabled,
            "tier": p.tier,
            "in_region_only": bool(p.in_region_only),
            "cost_aware": bool(getattr(p, "cost_aware", False)),
        }
        for p in getattr(policy_set, "routing", ())
        if _names_model(p, provider, model)
    ]
    access = [
        {"id": p.id, "name": p.name, "priority": p.priority, "enabled": p.enabled, "effect": p.effect}
        for p in getattr(policy_set, "access", ())
        if _names_model(p, provider, model)
    ]
    limits = [limit.to_dict() for limit in getattr(policy_set, "limits", ()) if limit.applies_to(provider, model)]
    return {"routing_policies": routing, "access_policies": access, "limits": limits}


def written_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {
            **dict.fromkeys(WRITTEN_FIELDS),
            "owner_user_id": None,
            "status": "draft",
            "approved_by": None,
            "reviewed_at": None,
            "updated_by": None,
            "updated_at": None,
        }
    return {
        **{field: getattr(row, field, None) for field in WRITTEN_FIELDS},
        "owner_user_id": str(row.owner_user_id) if getattr(row, "owner_user_id", None) else None,
        "status": getattr(row, "status", "draft") or "draft",
        "approved_by": str(row.approved_by) if getattr(row, "approved_by", None) else None,
        "reviewed_at": row.reviewed_at.isoformat() if getattr(row, "reviewed_at", None) else None,
        "updated_by": str(row.updated_by) if getattr(row, "updated_by", None) else None,
        "updated_at": row.updated_at.isoformat() if getattr(row, "updated_at", None) else None,
    }


def completeness(written: dict[str, Any], card_facts: dict[str, Any], use: dict[str, Any]) -> dict[str, Any]:
    """What the card still lacks: the written texts, an owner, a catalogue entry, and the approval."""
    missing = [field for field in REQUIRED_TEXT if not str(written.get(field) or "").strip()]
    if not written.get("owner_user_id") and not use.get("settings_owner"):
        missing.append("owner")
    if not card_facts.get("in_catalogue"):
        missing.append("catalogue")
    if written.get("status") != "approved":
        missing.append("approval")
    return {"complete": not missing, "missing": missing}


def build(
    provider: str,
    model: str,
    *,
    card_facts: dict[str, Any],
    use: dict[str, Any],
    policies: dict[str, Any],
    price: dict[str, Any] | None,
    health: dict[str, Any] | None,
    evaluation: dict[str, Any] | None,
    residency: dict[str, Any] | None,
    written: dict[str, Any],
) -> dict[str, Any]:
    """The standard card, section by section; pure, so it can be checked without a database."""
    return {
        "provider": provider,
        "model": model,
        "kind": card_facts.get("kind", "unknown"),
        "facts": card_facts,
        "use": use,
        "governance": {**policies, "residency": residency},
        "economics": {"price": price},
        "operations": {"health": health},
        "evaluation": evaluation,
        "written": written,
        "completeness": completeness(written, card_facts, use),
    }


async def _policy_set(tenant_id: uuid.UUID) -> Any:
    from core.governance import model_gateway

    try:
        return await model_gateway.active_policy_set(tenant_id)
    except (RuntimeError, TypeError, ValueError, OSError) as exc:
        logger.warning("model_card_policies_unavailable", error=type(exc).__name__)
        return model_gateway.PolicySet()


async def _health(tenant_id: uuid.UUID, provider: str, model: str) -> dict[str, Any] | None:
    from core.governance import model_gateway_records

    try:
        observed = await model_gateway_records.model_health(tenant_id)
    except (RuntimeError, TypeError, ValueError, OSError) as exc:
        logger.warning("model_card_health_unavailable", error=type(exc).__name__)
        return None
    item = observed.get((provider, model))
    return item.to_dict() if item is not None else None


async def _residency(tenant_id: uuid.UUID, provider: str, kind: str) -> dict[str, Any] | None:
    from core.governance import residency

    try:
        decision = await residency.check_provider(
            tenant_id, provider, kind="llm" if kind != "embedding" else "embedding"
        )
    except (RuntimeError, TypeError, ValueError, OSError) as exc:
        logger.warning("model_card_residency_unavailable", error=type(exc).__name__)
        return None
    return {
        "blocked": bool(decision.blocked),
        "reason": decision.reason or "",
        "data_region": decision.data_region or "",
        "local": bool(residency.is_local_provider(provider)),
    }


async def _evaluation(session: Any, tenant_id: uuid.UUID, model: str) -> dict[str, Any] | None:
    from sqlalchemy import select

    from core.evals import runs as eval_runs
    from core.models.eval_run import EvalRun

    rows = list(
        (
            await session.execute(
                select(EvalRun)
                .where(EvalRun.tenant_id == tenant_id, EvalRun.model == model)
                .order_by(EvalRun.created_at.desc())
                .limit(MAX_EVAL_RUNS)
            )
        )
        .scalars()
        .all()
    )
    ranked = eval_runs.rank_models(rows) if rows else []
    return ranked[0] if ranked else None


async def written_row(session: Any, tenant_id: uuid.UUID, provider: str, model: str) -> Any:
    from sqlalchemy import select

    from core.models.model_card import ModelCard

    return (
        await session.execute(
            select(ModelCard).where(
                ModelCard.tenant_id == tenant_id, ModelCard.provider == provider, ModelCard.model == model
            )
        )
    ).scalar_one_or_none()


def _price(provider: str, model: str) -> dict[str, Any] | None:
    from core.governance import model_pricing

    price = model_pricing.price_for(provider, model)
    return price.to_dict() if price is not None else None


async def collect_card(
    session: Any, tenant_id: uuid.UUID, provider: str, model: str, *, inv: inventory_module.Inventory | None = None
) -> dict[str, Any]:
    """Assemble the card: inventory, gateway, pricing, health, evaluation, residency and the written part."""
    provider, model = normalise(provider, model)
    inv = inv or await inventory_module.collect(session, tenant_id)
    card_facts = facts(provider, model)
    return build(
        provider,
        model,
        card_facts=card_facts,
        use=usage(inv, provider, model),
        policies=governance(await _policy_set(tenant_id), provider, model),
        price=_price(provider, model),
        health=await _health(tenant_id, provider, model),
        evaluation=await _evaluation(session, tenant_id, model),
        residency=await _residency(tenant_id, provider, card_facts["kind"]),
        written=written_dict(await written_row(session, tenant_id, provider, model)),
    )


async def list_cards(session: Any, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    """One summary per model the tenant uses: kind, roles, tier, callers, status and what is missing."""
    from sqlalchemy import select

    from core.models.model_card import ModelCard

    inv = await inventory_module.collect(session, tenant_id)
    rows = list((await session.execute(select(ModelCard).where(ModelCard.tenant_id == tenant_id))).scalars().all())
    written_by_model = {(str(r.provider), str(r.model)): r for r in rows}
    summaries = []
    for asset in inv.select(kind="model"):
        provider, model = asset.ref.split(":", 1)[1].split("/", 1) if "/" in asset.ref else ("unknown", asset.name)
        card_facts = facts(provider, model)
        use = usage(inv, provider, model)
        written = written_dict(written_by_model.get((provider, model)))
        done = completeness(written, card_facts, use)
        summaries.append(
            {
                "provider": provider,
                "model": model,
                "kind": card_facts["kind"],
                "roles": use["roles"],
                "risk_tier": use["risk_tier"],
                "agents": len(use["agents"]),
                "status": written["status"],
                **done,
            }
        )
    return summaries


async def write(
    session: Any, tenant_id: uuid.UUID, provider: str, model: str, fields: dict[str, Any], *, actor: uuid.UUID | None
) -> Any:
    """Write the administrator's part; any edit returns the card to draft."""
    from core.models.model_card import ModelCard

    provider, model = normalise(provider, model)
    if actor is None:
        raise ModelCardError(403, "no_actor", "A signed-in user writes a model card")
    unknown = sorted(set(fields) - set(WRITTEN_FIELDS) - {"owner_user_id"})
    if unknown:
        raise ModelCardError(422, "unknown_field", f"unknown card fields: {', '.join(unknown)}")
    for field in WRITTEN_FIELDS:
        value = fields.get(field)
        if value is not None and (not isinstance(value, str) or len(value) > MAX_TEXT):
            raise ModelCardError(422, "text_too_long", f"{field} is text of at most {MAX_TEXT} characters")
    row = await written_row(session, tenant_id, provider, model)
    if row is None:
        row = ModelCard(tenant_id=tenant_id, provider=provider, model=model)
        session.add(row)
    for field in WRITTEN_FIELDS:
        if field in fields:
            setattr(row, field, (fields[field] or "").strip() or None)
    if "owner_user_id" in fields:
        raw = fields.get("owner_user_id")
        try:
            row.owner_user_id = uuid.UUID(str(raw)) if raw else None
        except ValueError:
            raise ModelCardError(422, "owner_user_id", "owner_user_id is a user id") from None
    row.status = "draft"
    row.approved_by = None
    row.reviewed_at = None
    row.updated_by = actor
    row.updated_at = datetime.now(UTC)
    await session.flush()
    logger.info("model_card_written", provider=provider, model=model)
    return row


async def approve(
    session: Any,
    tenant_id: uuid.UUID,
    provider: str,
    model: str,
    *,
    actor: uuid.UUID | None,
    inv: inventory_module.Inventory | None = None,
) -> Any:
    """A second person approves a complete card; the last editor cannot."""
    provider, model = normalise(provider, model)
    if actor is None:
        raise ModelCardError(403, "no_actor", "A signed-in user approves a model card")
    row = await written_row(session, tenant_id, provider, model)
    if row is None:
        raise ModelCardError(404, "not_written", "The card has not been written yet")
    if row.updated_by is not None and row.updated_by == actor:
        raise ModelCardError(403, "second_person", "The person who last edited the card cannot approve it")
    inv = inv or await inventory_module.collect(session, tenant_id)
    done = completeness(written_dict(row), facts(provider, model), usage(inv, provider, model))
    missing = [m for m in done["missing"] if m != "approval"]
    if missing:
        raise ModelCardError(409, "incomplete", f"The card is missing: {', '.join(missing)}")
    row.status = "approved"
    row.approved_by = actor
    row.reviewed_at = datetime.now(UTC)
    await session.flush()
    logger.info("model_card_approved", provider=provider, model=model)
    return row
