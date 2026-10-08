# SPDX-License-Identifier: Apache-2.0
"""The AI asset inventory: every asset with owner, version and risk tier, dependencies, and the export."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.v1 import governance_inventory as api
from core.config import settings
from core.governance import inventory

OWNER = uuid.uuid4()
AGENT_A = uuid.uuid4()
AGENT_B = uuid.uuid4()
TEMPLATE = uuid.uuid4()


def _agent(agent_id, name, **over):
    base = {
        "id": agent_id,
        "name": name,
        "agent_type": "support",
        "domain": "finance",
        "status": "active",
        "version": "1.2.0",
        "owner_user_id": OWNER,
        "is_builtin": False,
        "llm_provider": "openai",
        "llm_model": "gpt-test",
        "llm_fallback": None,
        "system_prompt_text": "",
        "system_prompt_ref": "collections_v2",
        "authorized_tools": ["knowledge_base_search", "composio:salesforce:get_account"],
        "maturity": "pilot",
        "visibility": "tenant",
    }
    base.update(over)
    return SimpleNamespace(**base)


def _rows():
    agents = [
        _agent(AGENT_A, "Collections"),
        _agent(
            AGENT_B,
            "Drafter",
            owner_user_id=None,
            is_builtin=True,
            llm_model="gpt-mini",
            llm_fallback="gpt-test",
            system_prompt_text="You draft letters.",
            system_prompt_ref=None,
            authorized_tools=["send_email"],
            domain="ops",
        ),
    ]
    entries = [SimpleNamespace(agent_id=AGENT_A, risk_tier="high", state="published")]
    prompts = [
        SimpleNamespace(
            id=TEMPLATE,
            name="collections_v2",
            agent_type="support",
            domain="finance",
            is_builtin=False,
            is_active=True,
            created_by=OWNER,
            updated_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
    ]
    ai_settings = SimpleNamespace(
        llm_provider="openai",
        llm_model="gpt-test",
        llm_fallback_model="gpt-mini",
        embedding_provider="local",
        embedding_model="BAAI/bge-small-en-v1.5",
        updated_by=OWNER,
    )
    knowledge = [("finance", 3, 40, datetime(2026, 10, 5, tzinfo=UTC), ["local/BAAI/bge-small-en-v1.5"])]
    return agents, entries, prompts, ai_settings, knowledge


class TestBuild:
    def test_every_kind_is_listed_with_owner_version_tier_and_dependencies(self):
        agents, entries, prompts, ai_settings, knowledge = _rows()
        inv = inventory.build(
            agents=agents, entries=entries, prompts=prompts, ai_settings=ai_settings, knowledge_rows=knowledge
        )
        a = inv.assets[f"agent:{AGENT_A}"]
        assert (a.owner, a.version, a.risk_tier, a.status) == (str(OWNER), "1.2.0", "high", "active")
        assert a.detail["registry_state"] == "published"
        assert a.depends_on == [
            "model:openai/gpt-test",
            f"prompt:{TEMPLATE}",
            "tool:knowledge_base_search",
            "knowledge_base:finance",
            "tool:composio:salesforce:get_account",
        ]
        # The model the settings name is also the one the high-risk agent calls: it carries that tier.
        model = inv.assets["model:openai/gpt-test"]
        assert model.risk_tier == "high" and model.owner == str(OWNER)
        assert model.detail["roles"] == ["default", "agent", "fallback"]
        assert inv.assets["model:openai/gpt-mini"].detail["roles"] == ["fallback", "agent"]
        # The template is owned by its author and dated by its last change; the built-in agent's own prompt is a hash.
        template = inv.assets[f"prompt:{TEMPLATE}"]
        assert (template.owner, template.version, template.risk_tier) == (str(OWNER), "2026-10-01", "high")
        own = inv.assets[f"prompt:agent:{AGENT_B}"]
        assert own.owner == "platform" and len(own.version) == 12 and "draft" not in own.detail
        # Tools and connectors carry the highest tier of their callers; the knowledge base its counts.
        assert inv.assets["tool:composio:salesforce:get_account"].depends_on == ["connector:salesforce"]
        assert inv.assets["connector:salesforce"].risk_tier == "high"
        assert inv.assets["tool:send_email"].owner == "platform" and inv.assets["tool:send_email"].risk_tier is None
        kb = inv.assets["knowledge_base:finance"]
        assert kb.detail == {
            "domain": "finance",
            "documents": 3,
            "chunks": 40,
            "embedding_models": ["local/BAAI/bge-small-en-v1.5"],
        }
        assert kb.version == "2026-10-05" and kb.risk_tier == "high"
        assert inv.assets["model:local/BAAI/bge-small-en-v1.5"].detail["roles"] == ["embedding"]

    def test_the_summary_counts_kinds_tiers_and_gaps(self):
        agents, entries, prompts, ai_settings, knowledge = _rows()
        inv = inventory.build(
            agents=agents, entries=entries, prompts=prompts, ai_settings=ai_settings, knowledge_rows=knowledge
        )
        summary = inv.summary()
        assert summary["by_kind"] == {
            "agent": 2,
            "connector": 1,
            "knowledge_base": 1,
            "model": 3,
            "prompt": 2,
            "tool": 3,
        }
        assert summary["by_risk_tier"]["high"] >= 6 and summary["untiered_agents"] == 1
        assert summary["unowned"] >= 1 and summary["assets"] == 12

    def test_filters_narrow_and_the_order_is_by_kind_then_name(self):
        agents, entries, prompts, ai_settings, knowledge = _rows()
        inv = inventory.build(
            agents=agents, entries=entries, prompts=prompts, ai_settings=ai_settings, knowledge_rows=knowledge
        )
        assert [a.name for a in inv.select(kind="agent")] == ["Collections", "Drafter"]
        assert [a.ref for a in inv.select(risk_tier="unset", kind="tool")] == ["tool:send_email"]
        assert [a.kind for a in inv.select(q="gpt-test")] == ["model"]
        listed = inv.as_dict(kind="model")
        assert {a["kind"] for a in listed["assets"]} == {"model"} and listed["summary"]["assets"] == 12

    def test_an_agent_with_an_unknown_prompt_reference_or_no_knowledge_base_gets_placeholders(self):
        agent = _agent(
            AGENT_A, "Lone", system_prompt_ref="missing_template", authorized_tools=["knowledge_base_search"]
        )
        inv = inventory.build(agents=[agent], entries=[], prompts=[], ai_settings=None, knowledge_rows=[])
        assert inv.assets["prompt:ref:missing_template"].status == "reference"
        assert (
            inv.assets["knowledge_base:finance"].status == "empty"
            and inv.assets["knowledge_base:finance"].detail["documents"] == 0
        )
        assert (
            inv.assets[f"agent:{AGENT_A}"].risk_tier is None
            and inv.assets[f"agent:{AGENT_A}"].detail["registry_state"] == "draft"
        )

    def test_the_highest_tier_wins_and_the_inventory_is_bounded(self):
        assert inventory.highest_tier(["low", None, "critical", "odd"]) == "critical"
        assert inventory.highest_tier([None, "odd"]) is None
        inv = inventory.Inventory()
        inv.add(inventory.Asset(ref="tool:x", kind="tool", name="x", risk_tier="low", depends_on=["connector:a"]))
        merged = inv.add(
            inventory.Asset(
                ref="tool:x", kind="tool", name="x", owner="platform", risk_tier="high", depends_on=["connector:b"]
            )
        )
        assert (
            merged.risk_tier == "high"
            and merged.owner == "platform"
            and merged.depends_on == ["connector:a", "connector:b"]
        )
        import pytest as _pytest

        full = inventory.Inventory()
        full.assets = {
            f"tool:{i}": inventory.Asset(ref=f"tool:{i}", kind="tool", name=str(i)) for i in range(inventory.MAX_ASSETS)
        }
        with _pytest.raises(ValueError):
            full.add(inventory.Asset(ref="tool:more", kind="tool", name="more"))

    def test_off_by_default(self):
        assert settings.governance_inventory_enabled is False and inventory.enabled() is False


def test_export_is_a_bill_of_materials_with_components_and_dependencies():
    agents, entries, prompts, ai_settings, knowledge = _rows()
    inv = inventory.build(
        agents=agents, entries=entries, prompts=prompts, ai_settings=ai_settings, knowledge_rows=knowledge
    )
    tid = uuid.uuid4()
    when = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    bom = inventory.export(inv, tenant_id=tid, generated_at=when)
    assert bom["bomFormat"] == "AgenticOrg-AIBOM" and bom["specVersion"] == "1.0"
    assert bom["serialNumber"].startswith("urn:uuid:") and bom == inventory.export(
        inv, tenant_id=tid, generated_at=when
    )
    assert bom["metadata"]["tenant_id"] == str(tid) and bom["metadata"]["summary"]["assets"] == 12
    component = next(c for c in bom["components"] if c["bom-ref"] == f"agent:{AGENT_A}")
    assert component == {
        "bom-ref": f"agent:{AGENT_A}",
        "type": "agent",
        "name": "Collections",
        "version": "1.2.0",
        "owner": str(OWNER),
        "risk_tier": "high",
        "status": "active",
        "properties": inv.assets[f"agent:{AGENT_A}"].detail,
    }
    deps = {d["ref"]: d["dependsOn"] for d in bom["dependencies"]}
    assert deps[f"agent:{AGENT_A}"] == inv.assets[f"agent:{AGENT_A}"].depends_on
    assert deps["tool:composio:salesforce:get_account"] == ["connector:salesforce"]
    # No prompt text anywhere in the export.
    assert "You draft letters." not in str(bom)


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _Session:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), params))
        return _Result(self.answers.pop(0) if self.answers else [])


@pytest.mark.asyncio
async def test_collect_reads_the_tenant_rows_in_order():
    agents, entries, prompts, ai_settings, knowledge = _rows()
    session = _Session(agents, entries, prompts, [ai_settings], knowledge)
    tid = uuid.uuid4()
    inv = await inventory.collect(session, tid)
    assert inv.summary()["assets"] == 12
    assert len(session.statements) == 5
    sql, params = session.statements[4]
    assert "FROM knowledge_documents WHERE tenant_id = :tid AND status = 'ready'" in sql and params == {"tid": str(tid)}
    assert all("tenant_id" in sql for sql, _ in session.statements[:4])


class TestEndpoints:
    @pytest.mark.asyncio
    async def test_off_the_endpoints_are_not_found(self):
        with pytest.raises(HTTPException) as refused:
            await api.list_inventory(kind=None, risk_tier=None, q=None, tenant_id=str(uuid.uuid4()))
        assert refused.value.status_code == 404 and refused.value.detail["error"] == "governance_inventory_disabled"
        with pytest.raises(HTTPException) as refused:
            await api.export_inventory(tenant_id=str(uuid.uuid4()))
        assert refused.value.status_code == 404

    @pytest.mark.asyncio
    async def test_on_the_list_filters_and_the_export_is_the_bom(self, monkeypatch):
        monkeypatch.setattr(settings, "governance_inventory_enabled", True)
        agents, entries, prompts, ai_settings, knowledge = _rows()
        built = inventory.build(
            agents=agents, entries=entries, prompts=prompts, ai_settings=ai_settings, knowledge_rows=knowledge
        )

        async def _collect(_tenant):
            return built

        monkeypatch.setattr(api, "_collect", _collect)
        tid = str(uuid.uuid4())
        listed = await api.list_inventory(kind="agent", risk_tier=None, q=None, tenant_id=tid)
        assert [a["name"] for a in listed["assets"]] == ["Collections", "Drafter"]
        with pytest.raises(HTTPException) as refused:
            await api.list_inventory(kind="widget", risk_tier=None, q=None, tenant_id=tid)
        assert refused.value.status_code == 422
        with pytest.raises(HTTPException) as refused:
            await api.list_inventory(kind=None, risk_tier="extreme", q=None, tenant_id=tid)
        assert refused.value.status_code == 422
        bom = await api.export_inventory(tenant_id=tid)
        assert bom["bomFormat"] == "AgenticOrg-AIBOM" and len(bom["components"]) == 12

    def test_the_router_is_registered_behind_admin_and_a_sensitive_scope(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        main = (root / "api" / "main.py").read_text(encoding="utf-8")
        assert "governance_inventory," in main and "app.include_router(governance_inventory.router" in main
        src = (root / "api" / "v1" / "governance_inventory.py").read_text(encoding="utf-8")
        assert (
            src.count("dependencies=[require_tenant_admin]") == 2
            and src.count('scope="governance.inventory.sensitive.read"') == 2
        )
