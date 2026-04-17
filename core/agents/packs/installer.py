"""Industry pack installer - discover, install, and uninstall agent packs."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, text

from core.agents.packs.ca import CA_PACK
from core.database import get_tenant_session
from core.models.agent import (
    Agent,
    AgentCostLedger,
    AgentLifecycleEvent,
    AgentTeamMember,
    AgentVersion,
    ShadowComparison,
)
from core.models.audit import AuditLog
from core.models.ca_subscription import CASubscription
from core.models.company import Company
from core.models.hitl import HITLQueue
from core.models.prompt_template import PromptEditHistory
from core.models.tool_call import ToolCall
from core.models.workflow import StepExecution, WorkflowDefinition, WorkflowRun

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

_DEFAULT_LLM_MODEL = "gpt-4o-mini"
_DEFAULT_FALLBACK_MODEL = "gpt-4o-mini"
_DEFAULT_TIMEOUT_HOURS = 4


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _title_case(value: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[_\-\s]+", value) if part)


def _normalize_tool_names(tools: list[str]) -> list[str]:
    normalized: list[str] = []
    for tool_name in tools:
        if ":" in tool_name:
            normalized.append(tool_name.rsplit(":", 1)[-1])
        else:
            normalized.append(tool_name)
    return normalized


def _resolve_pack_dir(pack_name: str) -> Path | None:
    # Resolve by string-matching against directories we discover ourselves.
    # `pack_name` never flows into a path expression — the set of discovered
    # pack directories is the allowlist, and we only return Paths we built.
    if not pack_name or not isinstance(pack_name, str):
        return None

    discovered = _discover_pack_dirs()

    for pack_dir in discovered:
        if pack_dir.name == pack_name:
            return pack_dir

    for dir_name, reg_id in _DIR_TO_REGISTERED.items():
        if pack_name == reg_id:
            for pack_dir in discovered:
                if pack_dir.name == dir_name:
                    return pack_dir

    for pack_dir in discovered:
        cfg = _load_yaml(pack_dir / "config.yaml")
        if cfg.get("name") == pack_name:
            return pack_dir
    return None


def _pack_label(detail: dict[str, Any]) -> str:
    return str(detail.get("display_name") or detail.get("name") or "Industry Pack")


def _workflow_title(workflow_name: str) -> str:
    return _title_case(workflow_name)


def _agent_base_name(agent_cfg: dict[str, Any], index: int) -> str:
    explicit_name = str(agent_cfg.get("name") or "").strip()
    if explicit_name:
        return explicit_name
    agent_type = str(agent_cfg.get("type") or f"pack_agent_{index + 1}")
    return _title_case(agent_type)


def _company_scope_name(company_name: str | None) -> str | None:
    value = (company_name or "").strip()
    return value or None


def _agent_display_name(
    pack_label: str,
    agent_cfg: dict[str, Any],
    index: int,
    company_name: str | None = None,
) -> str:
    scope_name = _company_scope_name(company_name)
    if scope_name:
        return f"{scope_name} - {_agent_base_name(agent_cfg, index)}"
    return f"{pack_label} - {_agent_base_name(agent_cfg, index)}"


def _agent_type(agent_cfg: dict[str, Any], index: int) -> str:
    explicit_type = str(agent_cfg.get("type") or "").strip()
    if explicit_type:
        return explicit_type
    return _slugify(_agent_base_name(agent_cfg, index)) or f"pack_agent_{index + 1}"


def _read_pack_prompt(pack_name: str, agent_cfg: dict[str, Any]) -> str:
    prompt_file = str(agent_cfg.get("prompt_file") or "").strip()
    if not prompt_file:
        return ""

    pack_dir = _resolve_pack_dir(pack_name)
    if not pack_dir:
        return ""

    # Enumerate the real files under the pack directory and match by
    # relative posix path — pure string lookup, so `prompt_file` never
    # flows into a path expression.
    pack_root = pack_dir.resolve()
    prompt_file_posix = prompt_file.replace("\\", "/").lstrip("/")
    known_files = {
        p.relative_to(pack_root).as_posix(): p
        for p in pack_root.rglob("*")
        if p.is_file()
    }
    match = known_files.get(prompt_file_posix)
    if match is None:
        return ""
    return match.read_text(encoding="utf-8").strip()


def _build_system_prompt(pack_name: str, detail: dict[str, Any], agent_cfg: dict[str, Any], index: int) -> str:
    prompt_sections: list[str] = [
        f"You are {_agent_base_name(agent_cfg, index)} deployed from {_pack_label(detail)}.",
    ]

    pack_description = str(detail.get("description") or "").strip()
    if pack_description:
        prompt_sections.append(pack_description)

    agent_description = str(agent_cfg.get("description") or "").strip()
    if agent_description:
        prompt_sections.append(agent_description)

    file_prompt = _read_pack_prompt(pack_name, agent_cfg)
    if file_prompt:
        prompt_sections.append(file_prompt)

    prompt_suffix = str(agent_cfg.get("system_prompt_suffix") or "").strip()
    if prompt_suffix:
        prompt_sections.append(prompt_suffix)

    tools = agent_cfg.get("tools") if isinstance(agent_cfg.get("tools"), list) else []
    if tools:
        prompt_sections.append(
            "Authorized tools: " + ", ".join(_normalize_tool_names([str(tool) for tool in tools]))
        )

    return "\n\n".join(section for section in prompt_sections if section).strip()


def _select_workflow_agent_specs(
    workflow_name: str,
    agent_specs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    workflow_tokens = set(_slugify(workflow_name).split("_"))
    scored: list[tuple[int, int, dict[str, Any]]] = []

    for index, spec in enumerate(agent_specs):
        haystacks = [
            _slugify(str(spec.get("agent_type") or "")),
            _slugify(str(spec.get("display_name") or "")),
            _slugify(str(spec.get("description") or "")),
        ]
        score = 0
        for token in workflow_tokens:
            if not token:
                continue
            if any(token in haystack for haystack in haystacks):
                score += 1
        scored.append((score, index, spec))

    relevant = [spec for score, _, spec in sorted(scored, key=lambda item: (-item[0], item[1])) if score > 0]
    if relevant:
        return relevant[:3]
    return agent_specs[: min(3, len(agent_specs))]


def _build_workflow_definition(
    pack_name: str,
    detail: dict[str, Any],
    workflow_name: str,
    agent_specs: list[dict[str, Any]],
    company_id: UUID | None = None,
    company_name: str | None = None,
) -> dict[str, Any]:
    selected_agents = _select_workflow_agent_specs(workflow_name, agent_specs)
    steps: list[dict[str, Any]] = []
    previous_step_id: str | None = None

    for index, spec in enumerate(selected_agents, start=1):
        step_id = f"agent_{index}"
        step: dict[str, Any] = {
            "id": step_id,
            "type": "agent",
            "agent_id": str(spec["id"]),
            "agent_type": spec["agent_type"],
            "authorized_tools": spec["authorized_tools"],
            "system_prompt_text": spec["system_prompt_text"],
            "llm_model": spec["llm_model"],
            "action": workflow_name,
        }
        if previous_step_id:
            step["depends_on"] = [previous_step_id]
        steps.append(step)
        previous_step_id = step_id

    if selected_agents:
        review_step = {
            "id": "human_review",
            "type": "human_in_loop",
            "assignee_role": "admin",
            "timeout_hours": _DEFAULT_TIMEOUT_HOURS,
            "decision_options": {"options": ["approve", "reject"]},
            "depends_on": [previous_step_id],
        }
        steps.append(review_step)

    workflow_title = _workflow_title(workflow_name)
    pack_label = _pack_label(detail)
    scope_name = _company_scope_name(company_name)
    workflow_display_name = (
        f"{scope_name} - {workflow_title}" if scope_name else f"{pack_label} - {workflow_title}"
    )
    workflow_description = (
        f"{workflow_title} workflow for {scope_name} provisioned by {pack_label}."
        if scope_name
        else f"{workflow_title} workflow provisioned by {pack_label}."
    )
    return {
        "name": workflow_display_name,
        "version": "1.0",
        "description": workflow_description,
        "domain": selected_agents[0]["domain"] if selected_agents else "ops",
        "trigger_type": "manual",
        "trigger_config": {
            "source_pack": pack_name,
            **({"company_id": str(company_id)} if company_id else {}),
        },
        "timeout_hours": _DEFAULT_TIMEOUT_HOURS,
        "metadata": {
            "source": "industry_pack",
            "pack_name": pack_name,
            "workflow_key": workflow_name,
            **({"company_id": str(company_id)} if company_id else {}),
            **({"company_name": scope_name} if scope_name else {}),
        },
        "steps": steps or [{"id": "start", "type": "transform"}],
    }


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


async def _get_or_create_pack_agents(
    session,
    tid: UUID,
    pack_name: str,
    detail: dict[str, Any],
    company_id: UUID | None = None,
    company_name: str | None = None,
) -> list[dict[str, Any]]:
    agent_specs: list[dict[str, Any]] = []
    pack_label = _pack_label(detail)

    for index, raw_agent_cfg in enumerate(detail.get("agents", [])):
        agent_cfg = raw_agent_cfg if isinstance(raw_agent_cfg, dict) else {"type": str(raw_agent_cfg)}
        display_name = _agent_display_name(pack_label, agent_cfg, index, company_name)
        agent_type = _agent_type(agent_cfg, index)
        normalized_tools = _normalize_tool_names(
            [str(tool) for tool in agent_cfg.get("tools", []) if tool]
        )
        llm_model = str(agent_cfg.get("llm_model") or _DEFAULT_LLM_MODEL)
        llm_config = {
            "model": llm_model,
            "fallback_model": _DEFAULT_FALLBACK_MODEL,
            "temperature": 0.1,
            "context_strategy": "sliding_16k",
        }
        confidence_floor = Decimal(str(agent_cfg.get("confidence_floor", "0.88")))
        system_prompt_text = _build_system_prompt(pack_name, detail, agent_cfg, index)
        existing = await session.execute(
            select(Agent).where(
                Agent.tenant_id == tid,
                Agent.company_id == company_id,
                Agent.agent_type == agent_type,
                Agent.employee_name == display_name,
                Agent.version == "1.0.0",
            )
        )
        agent = existing.scalar_one_or_none()

        if agent is None:
            agent = Agent(
                tenant_id=tid,
                company_id=company_id,
                name=display_name,
                agent_type=agent_type,
                domain=str(agent_cfg.get("domain") or "ops"),
                description=str(agent_cfg.get("description") or ""),
                system_prompt_ref=f"industry-pack://{pack_name}/{agent_type}",
                system_prompt_text=system_prompt_text,
                llm_model=llm_model,
                llm_fallback=_DEFAULT_FALLBACK_MODEL,
                llm_config=llm_config,
                confidence_floor=confidence_floor,
                hitl_condition=str(
                    agent_cfg.get("hitl_condition")
                    or f"confidence < {confidence_floor}"
                ),
                max_retries=3,
                authorized_tools=normalized_tools,
                status="shadow",
                version="1.0.0",
                employee_name=display_name,
                designation=_agent_base_name(agent_cfg, index),
                specialization=f"{pack_label} automation",
                is_builtin=False,
                config={
                    "pack_install": {
                        "pack_name": pack_name,
                        "display_name": pack_label,
                        "agent_index": index,
                        "source": "industry_pack",
                        **({"company_id": str(company_id)} if company_id else {}),
                        **({"company_name": company_name} if company_name else {}),
                    }
                },
            )
            session.add(agent)
            await session.flush()

            session.add(
                AgentVersion(
                    tenant_id=tid,
                    agent_id=agent.id,
                    version=agent.version,
                    system_prompt=system_prompt_text or agent.system_prompt_ref,
                    authorized_tools=normalized_tools,
                    hitl_policy={
                        "condition": agent.hitl_condition,
                        "assignee_role": "admin",
                        "timeout_hours": _DEFAULT_TIMEOUT_HOURS,
                        "on_timeout": "escalate",
                        "escalation_chain": [],
                    },
                    llm_config=llm_config,
                    confidence_floor=confidence_floor,
                    deployed_at=datetime.now(UTC),
                )
            )

        agent_specs.append(
            {
                "id": agent.id,
                "company_id": agent.company_id,
                "display_name": display_name,
                "agent_type": agent.agent_type,
                "domain": agent.domain,
                "authorized_tools": list(agent.authorized_tools or []),
                "system_prompt_text": agent.system_prompt_text or system_prompt_text,
                "llm_model": agent.llm_model,
                "description": agent.description or "",
                "mode": agent.status,
            }
        )

    return agent_specs


async def _get_or_create_pack_workflows(
    session,
    tid: UUID,
    pack_name: str,
    detail: dict[str, Any],
    agent_specs: list[dict[str, Any]],
    company_id: UUID | None = None,
    company_name: str | None = None,
) -> list[dict[str, Any]]:
    workflows_created: list[dict[str, Any]] = []

    for raw_workflow in detail.get("workflows", []):
        workflow_key = str(raw_workflow)
        definition = _build_workflow_definition(
            pack_name,
            detail,
            workflow_key,
            agent_specs,
            company_id=company_id,
            company_name=company_name,
        )
        workflow_name = str(definition["name"])

        existing = await session.execute(
            select(WorkflowDefinition).where(
                WorkflowDefinition.tenant_id == tid,
                WorkflowDefinition.company_id == company_id,
                WorkflowDefinition.name == workflow_name,
                WorkflowDefinition.version == "1.0",
            )
        )
        workflow = existing.scalar_one_or_none()
        if workflow is None:
            workflow = WorkflowDefinition(
                tenant_id=tid,
                company_id=company_id,
                name=workflow_name,
                version="1.0",
                description=str(definition.get("description") or ""),
                domain=str(definition.get("domain") or "ops"),
                definition=definition,
                trigger_type=str(definition.get("trigger_type") or "manual"),
                trigger_config=definition.get("trigger_config") or {},
                is_active=True,
            )
            session.add(workflow)
            await session.flush()

        workflows_created.append(
            {
                "id": workflow.id,
                "company_id": workflow.company_id,
                "name": workflow.name,
                "description": workflow.description or "",
            }
        )

    return workflows_created


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


def _parse_install_ids(raw_values: Any) -> list[str]:
    if not raw_values:
        return []
    if isinstance(raw_values, str):
        raw_values = json.loads(raw_values)
    return [str(value) for value in raw_values if value]


async def is_pack_installed_for_session(session, tid: UUID, pack_name: str) -> bool:
    existing = await session.execute(
        text("""
            SELECT 1
            FROM industry_pack_installs
            WHERE tenant_id = :tenant_id AND pack_name = :pack_name
            LIMIT 1
        """),
        {"tenant_id": tid, "pack_name": pack_name},
    )
    return existing.scalar_one_or_none() is not None


async def ensure_ca_pack_subscription_sync_async(tenant_id: str) -> bool:
    """Install the CA pack when an active or trial CA subscription exists.

    Returns ``True`` when a missing ``ca-firm`` install was repaired, otherwise
    ``False``. This heals tenants that were provisioned with CA subscription
    state before the pack install lifecycle became durable.
    """
    tid = UUID(tenant_id)

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(CASubscription).where(CASubscription.tenant_id == tid)
        )
        subscription = result.scalar_one_or_none()
        if not subscription or subscription.status not in {"trial", "active"}:
            return False

        if await is_pack_installed_for_session(session, tid, _ca_id):
            return False

    await install_pack_async(_ca_id, tenant_id)
    return True


async def _merge_install_assets(
    session,
    tid: UUID,
    pack_name: str,
    agent_ids: list[UUID],
    workflow_ids: list[UUID],
) -> None:
    existing = await session.execute(
        text("""
            SELECT agent_ids, workflow_ids
            FROM industry_pack_installs
            WHERE tenant_id = :tenant_id AND pack_name = :pack_name
            ORDER BY installed_at DESC
            LIMIT 1
        """),
        {"tenant_id": tid, "pack_name": pack_name},
    )
    row = existing.mappings().first()
    existing_agent_ids = _parse_install_ids(row.get("agent_ids") if row else [])
    existing_workflow_ids = _parse_install_ids(row.get("workflow_ids") if row else [])
    merged_agent_ids = list(dict.fromkeys(existing_agent_ids + [str(value) for value in agent_ids]))
    merged_workflow_ids = list(
        dict.fromkeys(existing_workflow_ids + [str(value) for value in workflow_ids])
    )

    if row:
        await session.execute(
            text("""
                UPDATE industry_pack_installs
                SET agent_ids = CAST(:agent_ids AS jsonb),
                    workflow_ids = CAST(:workflow_ids AS jsonb)
                WHERE tenant_id = :tenant_id AND pack_name = :pack_name
            """),
            {
                "tenant_id": tid,
                "pack_name": pack_name,
                "agent_ids": json.dumps(merged_agent_ids),
                "workflow_ids": json.dumps(merged_workflow_ids),
            },
        )
        return

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
            "agent_ids": json.dumps(merged_agent_ids),
            "workflow_ids": json.dumps(merged_workflow_ids),
        },
    )


async def sync_company_pack_assets_for_session(
    session,
    tid: UUID,
    pack_name: str,
    company_id: UUID,
    company_name: str,
) -> dict[str, Any]:
    detail = get_pack_detail(pack_name)
    if detail is None:
        raise ValueError(f"Pack '{pack_name}' not found")

    agent_specs = await _get_or_create_pack_agents(
        session,
        tid,
        pack_name,
        detail,
        company_id=company_id,
        company_name=company_name,
    )
    workflows_created = await _get_or_create_pack_workflows(
        session,
        tid,
        pack_name,
        detail,
        agent_specs,
        company_id=company_id,
        company_name=company_name,
    )
    await _merge_install_assets(
        session,
        tid,
        pack_name,
        [UUID(str(spec["id"])) for spec in agent_specs],
        [UUID(str(workflow["id"])) for workflow in workflows_created],
    )
    session.add(
        AuditLog(
            tenant_id=tid,
            company_id=company_id,
            event_type="pack.company_sync",
            actor_type="system",
            actor_id="industry_pack_installer",
            resource_type="company",
            resource_id=str(company_id),
            action=f"Synchronized {_pack_label(detail)} assets for {company_name}",
            outcome="success",
            details={
                "pack_name": pack_name,
                "company_id": str(company_id),
                "company_name": company_name,
                "agents_synced": len(agent_specs),
                "workflows_synced": len(workflows_created),
            },
        )
    )
    return {
        "company_id": str(company_id),
        "company_name": company_name,
        "agents_created": [
            {
                "id": str(spec["id"]),
                "company_id": str(spec["company_id"]) if spec.get("company_id") else None,
                "name": spec["display_name"],
                "type": spec["agent_type"],
                "domain": spec["domain"],
                "mode": spec["mode"],
                "tools": spec["authorized_tools"],
            }
            for spec in agent_specs
        ],
        "workflows_created": [
            {
                "id": str(workflow["id"]),
                "company_id": str(workflow["company_id"]) if workflow.get("company_id") else None,
                "name": workflow["name"],
                "description": workflow["description"],
            }
            for workflow in workflows_created
        ],
    }


async def install_pack_async(pack_name: str, tenant_id: str) -> dict[str, Any]:
    """Install a pack and provision tenant-scoped agents/workflows."""
    detail = get_pack_detail(pack_name)
    if detail is None:
        raise ValueError(f"Pack '{pack_name}' not found")

    tid = UUID(tenant_id)

    async with get_tenant_session(tid) as session:
        already_installed = await is_pack_installed_for_session(session, tid, pack_name)

        if pack_name == _ca_id:
            company_rows = await session.execute(
                select(Company).where(Company.tenant_id == tid).order_by(Company.name.asc())
            )
            companies = company_rows.scalars().all()
            per_company_results = [
                await sync_company_pack_assets_for_session(
                    session,
                    tid,
                    pack_name,
                    company.id,
                    company.name,
                )
                for company in companies
            ]
            created_agents = [
                agent
                for result in per_company_results
                for agent in result["agents_created"]
            ]
            workflows_created = [
                workflow
                for result in per_company_results
                for workflow in result["workflows_created"]
            ]
            if not companies:
                await _merge_install_assets(session, tid, pack_name, [], [])
        else:
            agent_specs = await _get_or_create_pack_agents(session, tid, pack_name, detail)
            workflows_created = await _get_or_create_pack_workflows(
                session,
                tid,
                pack_name,
                detail,
                agent_specs,
            )
            created_agents = [
                {
                    "id": str(spec["id"]),
                    "company_id": str(spec["company_id"]) if spec.get("company_id") else None,
                    "name": spec["display_name"],
                    "type": spec["agent_type"],
                    "domain": spec["domain"],
                    "mode": spec["mode"],
                    "tools": spec["authorized_tools"],
                }
                for spec in agent_specs
            ]
            await _merge_install_assets(
                session,
                tid,
                pack_name,
                [UUID(str(spec["id"])) for spec in agent_specs],
                [UUID(str(workflow["id"])) for workflow in workflows_created],
            )

        session.add(
            AuditLog(
                tenant_id=tid,
                event_type="pack.install",
                actor_type="system",
                actor_id="industry_pack_installer",
                resource_type="industry_pack",
                resource_id=pack_name,
                action=f"Installed {_pack_label(detail)}",
                outcome="success",
                details={
                    "pack_name": pack_name,
                    "agents_created": len(created_agents),
                    "workflows_created": len(workflows_created),
                    "status": "already_installed" if already_installed else "installed",
                },
            )
        )

    return {
        "status": "already_installed" if already_installed else "installed",
        "pack": pack_name,
        "tenant_id": tenant_id,
        "agents_created": created_agents,
        "workflows_created": workflows_created,
    }


async def uninstall_pack_async(pack_name: str, tenant_id: str) -> dict[str, Any]:
    """Uninstall a pack and remove its owned tenant-scoped assets."""
    tid = UUID(tenant_id)

    async with get_tenant_session(tid) as session:
        existing = await session.execute(
            text("""
                SELECT agent_ids, workflow_ids
                FROM industry_pack_installs
                WHERE tenant_id = :tenant_id AND pack_name = :pack_name
                LIMIT 1
            """),
            {"tenant_id": tid, "pack_name": pack_name},
        )
        row = existing.mappings().first()
        if not row:
            return {"status": "not_installed", "pack": pack_name, "tenant_id": tenant_id}

        raw_agent_ids = row.get("agent_ids") or []
        raw_workflow_ids = row.get("workflow_ids") or []
        if isinstance(raw_agent_ids, str):
            raw_agent_ids = json.loads(raw_agent_ids)
        if isinstance(raw_workflow_ids, str):
            raw_workflow_ids = json.loads(raw_workflow_ids)

        agent_ids = [uuid.UUID(str(value)) for value in raw_agent_ids if value]
        workflow_ids = [uuid.UUID(str(value)) for value in raw_workflow_ids if value]

        removed_agents: list[str] = []
        removed_workflows: list[str] = []

        if workflow_ids:
            workflow_rows = await session.execute(
                select(WorkflowDefinition).where(
                    WorkflowDefinition.tenant_id == tid,
                    WorkflowDefinition.id.in_(workflow_ids),
                )
            )
            workflows = workflow_rows.scalars().all()
            removed_workflows = [workflow.name for workflow in workflows]

            run_ids_result = await session.execute(
                select(WorkflowRun.id).where(
                    WorkflowRun.tenant_id == tid,
                    WorkflowRun.workflow_def_id.in_(workflow_ids),
                )
            )
            run_ids = list(run_ids_result.scalars().all())
            if run_ids:
                await session.execute(
                    StepExecution.__table__.delete().where(
                        StepExecution.tenant_id == tid,
                        StepExecution.workflow_run_id.in_(run_ids),
                    )
                )
                await session.execute(
                    WorkflowRun.__table__.delete().where(
                        WorkflowRun.tenant_id == tid,
                        WorkflowRun.id.in_(run_ids),
                    )
                )

            await session.execute(
                WorkflowDefinition.__table__.delete().where(
                    WorkflowDefinition.tenant_id == tid,
                    WorkflowDefinition.id.in_(workflow_ids),
                )
            )

        if agent_ids:
            agent_rows = await session.execute(
                select(Agent).where(
                    Agent.tenant_id == tid,
                    Agent.id.in_(agent_ids),
                )
            )
            agents = agent_rows.scalars().all()
            removed_agents = [agent.employee_name or agent.name for agent in agents]

            await session.execute(
                HITLQueue.__table__.delete().where(
                    HITLQueue.tenant_id == tid,
                    HITLQueue.agent_id.in_(agent_ids),
                )
            )
            await session.execute(
                PromptEditHistory.__table__.delete().where(
                    PromptEditHistory.tenant_id == tid,
                    PromptEditHistory.agent_id.in_(agent_ids),
                )
            )
            await session.execute(
                AgentLifecycleEvent.__table__.delete().where(
                    AgentLifecycleEvent.tenant_id == tid,
                    AgentLifecycleEvent.agent_id.in_(agent_ids),
                )
            )
            await session.execute(
                AgentCostLedger.__table__.delete().where(
                    AgentCostLedger.tenant_id == tid,
                    AgentCostLedger.agent_id.in_(agent_ids),
                )
            )
            await session.execute(
                AgentVersion.__table__.delete().where(
                    AgentVersion.tenant_id == tid,
                    AgentVersion.agent_id.in_(agent_ids),
                )
            )
            await session.execute(
                ToolCall.__table__.delete().where(
                    ToolCall.tenant_id == tid,
                    ToolCall.agent_id.in_(agent_ids),
                )
            )
            await session.execute(
                AgentTeamMember.__table__.delete().where(
                    AgentTeamMember.agent_id.in_(agent_ids),
                )
            )
            await session.execute(
                ShadowComparison.__table__.delete().where(
                    ShadowComparison.tenant_id == tid,
                    ShadowComparison.shadow_agent_id.in_(agent_ids),
                )
            )
            await session.execute(
                ShadowComparison.__table__.delete().where(
                    ShadowComparison.tenant_id == tid,
                    ShadowComparison.reference_agent_id.in_(agent_ids),
                )
            )
            await session.execute(
                Agent.__table__.delete().where(
                    Agent.tenant_id == tid,
                    Agent.id.in_(agent_ids),
                )
            )

        await session.execute(
            text("""
                DELETE FROM industry_pack_installs
                WHERE tenant_id = :tenant_id AND pack_name = :pack_name
            """),
            {"tenant_id": tid, "pack_name": pack_name},
        )
        session.add(
            AuditLog(
                tenant_id=tid,
                event_type="pack.uninstall",
                actor_type="system",
                actor_id="industry_pack_installer",
                resource_type="industry_pack",
                resource_id=pack_name,
                action=f"Uninstalled {pack_name}",
                outcome="success",
                details={
                    "pack_name": pack_name,
                    "agents_removed": removed_agents,
                    "workflows_removed": removed_workflows,
                },
            )
        )

    return {
        "status": "uninstalled",
        "pack": pack_name,
        "tenant_id": tenant_id,
        "agents_removed": removed_agents,
        "workflows_removed": removed_workflows,
    }
