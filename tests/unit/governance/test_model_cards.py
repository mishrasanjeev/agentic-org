# SPDX-License-Identifier: Apache-2.0
"""Model cards: assembled from what the platform knows, completed by an administrator, approved by a second person."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.v1 import governance_model_cards as api
from core.config import settings
from core.governance import inventory, model_cards
from core.governance.model_gateway import AccessPolicy, Policy, PolicySet
from core.governance.model_gateway_limits import Limit

ROOT = Path(__file__).resolve().parents[3]
OWNER = uuid.uuid4()
EDITOR = uuid.uuid4()
APPROVER = uuid.uuid4()
AGENT = uuid.uuid4()


def _inventory():
    agent = SimpleNamespace(
        id=AGENT,
        name="Collections",
        agent_type="support",
        domain="finance",
        status="active",
        version="1.0.0",
        owner_user_id=OWNER,
        is_builtin=False,
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        llm_fallback=None,
        system_prompt_text="",
        system_prompt_ref=None,
        authorized_tools=[],
        maturity="pilot",
        visibility="tenant",
    )
    entries = [SimpleNamespace(agent_id=AGENT, risk_tier="high", state="published")]
    ai_settings = SimpleNamespace(
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        llm_fallback_model=None,
        embedding_provider="local",
        embedding_model="BAAI/bge-m3",
        updated_by=OWNER,
    )
    return inventory.build(agents=[agent], entries=entries, prompts=[], ai_settings=ai_settings, knowledge_rows=[])


class TestSections:
    def test_facts_come_from_the_catalogue(self):
        llm = model_cards.facts("openai", "gpt-4o-mini")
        assert (
            llm["kind"] == "llm" and llm["in_catalogue"] and llm["context_window"] == 128_000 and llm["supports_vision"]
        )
        embedding = model_cards.facts("local", "BAAI/bge-m3")
        assert embedding["kind"] == "embedding" and embedding["dimensions"] == 1024
        assert model_cards.facts("openai", "made-up") == {"kind": "unknown", "in_catalogue": False}

    def test_use_names_roles_callers_and_the_highest_tier(self):
        use = model_cards.usage(_inventory(), "openai", "gpt-4o-mini")
        assert use["in_use"] and use["roles"] == ["default", "agent"] and use["settings_owner"] == str(OWNER)
        assert use["risk_tier"] == "high"
        assert use["agents"] == [{"id": str(AGENT), "name": "Collections", "risk_tier": "high", "status": "active"}]
        assert model_cards.usage(_inventory(), "openai", "gpt-4o")["in_use"] is False

    def test_governance_lists_the_policies_naming_the_model_and_the_limits_applying(self):
        policy_set = PolicySet(
            routing=(
                Policy(id="r1", name="finance to mini", priority=1, model="gpt-4o-mini", provider="openai"),
                Policy(id="r2", name="targets", priority=2, targets=({"provider": "openai", "model": "GPT-4o-mini"},)),
                Policy(id="r3", name="other", priority=3, model="gpt-4o"),
            ),
            access=(
                AccessPolicy(id="a1", name="allow mini", priority=1, allowed_models=("gpt-4o-mini",), effect="allow"),
                AccessPolicy(id="a2", name="deny other", priority=2, model="gpt-4o", effect="deny"),
            ),
            limits=(
                Limit(id="l1", provider="openai", model=None, max_concurrency=4),
                Limit(id="l2", provider="gemini"),
            ),
        )
        gov = model_cards.governance(policy_set, "openai", "gpt-4o-mini")
        assert [p["id"] for p in gov["routing_policies"]] == ["r1", "r2"]
        assert [p["id"] for p in gov["access_policies"]] == ["a1"] and gov["access_policies"][0]["effect"] == "allow"
        assert [limit["id"] for limit in gov["limits"]] == ["l1"]

    def test_completeness_names_what_is_missing(self):
        written = model_cards.written_dict(None)
        done = model_cards.completeness(written, {"in_catalogue": False}, {"settings_owner": None})
        assert done == {
            "complete": False,
            "missing": ["intended_use", "limitations", "data_handling", "owner", "catalogue", "approval"],
        }
        full = {**written, "intended_use": "x", "limitations": "y", "data_handling": "z", "status": "approved"}
        assert model_cards.completeness(full, {"in_catalogue": True}, {"settings_owner": str(OWNER)}) == {
            "complete": True,
            "missing": [],
        }

    def test_the_card_is_built_section_by_section_and_names_are_bounded(self):
        card = model_cards.build(
            "openai",
            "gpt-4o-mini",
            card_facts=model_cards.facts("openai", "gpt-4o-mini"),
            use=model_cards.usage(_inventory(), "openai", "gpt-4o-mini"),
            policies={"routing_policies": [], "access_policies": [], "limits": []},
            price={"input_per_million": 0.15, "output_per_million": 0.6, "source": "list"},
            health=None,
            evaluation=None,
            residency={"blocked": False, "reason": "", "data_region": "", "local": False},
            written=model_cards.written_dict(None),
        )
        assert set(card) == {
            "provider",
            "model",
            "kind",
            "facts",
            "use",
            "governance",
            "economics",
            "operations",
            "evaluation",
            "written",
            "completeness",
        }
        assert card["governance"]["residency"]["blocked"] is False and card["economics"]["price"]["source"] == "list"
        assert card["completeness"]["missing"] == ["intended_use", "limitations", "data_handling", "approval"]
        with pytest.raises(model_cards.ModelCardError) as refused:
            model_cards.normalise("", "gpt")
        assert refused.value.status == 422
        assert model_cards.normalise(" OpenAI ", " gpt-4o-mini ") == ("openai", "gpt-4o-mini")

    def test_off_by_default(self):
        assert settings.governance_model_cards_enabled is False and model_cards.enabled() is False


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
        self.added = []
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        return _Result(self.answers.pop(0) if self.answers else [])

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None


def _row(**over):
    base = {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "intended_use": "Drafting collection letters.",
        "limitations": "No legal advice.",
        "data_handling": "No customer identifiers in prompts.",
        "notes": None,
        "owner_user_id": OWNER,
        "status": "draft",
        "approved_by": None,
        "reviewed_at": None,
        "updated_by": EDITOR,
        "updated_at": datetime(2026, 10, 7, tzinfo=UTC),
    }
    base.update(over)
    return SimpleNamespace(**base)


class TestWriteAndApprove:
    @pytest.mark.asyncio
    async def test_writing_creates_or_updates_the_row_and_returns_it_to_draft(self):
        session = _Session([])
        row = await model_cards.write(
            session,
            uuid.uuid4(),
            "OpenAI",
            "gpt-4o-mini",
            {"intended_use": " Letters. ", "owner_user_id": str(OWNER)},
            actor=EDITOR,
        )
        assert session.added == [row] and row.provider == "openai" and row.intended_use == "Letters."
        assert row.owner_user_id == OWNER and row.status == "draft" and row.updated_by == EDITOR
        existing = _row(status="approved", approved_by=APPROVER, reviewed_at=datetime(2026, 10, 1, tzinfo=UTC))
        again = await model_cards.write(
            _Session([existing]), uuid.uuid4(), "openai", "gpt-4o-mini", {"notes": "x"}, actor=EDITOR
        )
        assert again is existing and again.status == "draft" and again.approved_by is None and again.reviewed_at is None

    @pytest.mark.asyncio
    async def test_writing_is_refused_without_an_actor_for_unknown_fields_and_long_text(self):
        tid = uuid.uuid4()
        with pytest.raises(model_cards.ModelCardError) as refused:
            await model_cards.write(_Session([]), tid, "openai", "gpt-4o-mini", {}, actor=None)
        assert refused.value.code == "no_actor"
        with pytest.raises(model_cards.ModelCardError) as refused:
            await model_cards.write(_Session([]), tid, "openai", "gpt-4o-mini", {"colour": "blue"}, actor=EDITOR)
        assert refused.value.code == "unknown_field"
        with pytest.raises(model_cards.ModelCardError) as refused:
            await model_cards.write(_Session([]), tid, "openai", "gpt-4o-mini", {"notes": "x" * 2001}, actor=EDITOR)
        assert refused.value.code == "text_too_long"
        with pytest.raises(model_cards.ModelCardError) as refused:
            await model_cards.write(_Session([]), tid, "openai", "gpt-4o-mini", {"owner_user_id": "nope"}, actor=EDITOR)
        assert refused.value.code == "owner_user_id"

    @pytest.mark.asyncio
    async def test_a_second_person_approves_a_complete_card_and_the_editor_cannot(self):
        tid = uuid.uuid4()
        inv = _inventory()
        with pytest.raises(model_cards.ModelCardError) as refused:
            await model_cards.approve(_Session([_row()]), tid, "openai", "gpt-4o-mini", actor=EDITOR, inv=inv)
        assert refused.value.code == "second_person" and refused.value.status == 403
        with pytest.raises(model_cards.ModelCardError) as refused:
            await model_cards.approve(_Session([]), tid, "openai", "gpt-4o-mini", actor=APPROVER, inv=inv)
        assert refused.value.code == "not_written"
        with pytest.raises(model_cards.ModelCardError) as refused:
            await model_cards.approve(
                _Session([_row(limitations=None)]), tid, "openai", "gpt-4o-mini", actor=APPROVER, inv=inv
            )
        assert refused.value.code == "incomplete" and "limitations" in refused.value.message
        with pytest.raises(model_cards.ModelCardError) as refused:
            await model_cards.approve(_Session([_row()]), tid, "openai", "gpt-4o-mini", actor=None, inv=inv)
        assert refused.value.code == "no_actor"
        row = _row()
        approved = await model_cards.approve(_Session([row]), tid, "openai", "gpt-4o-mini", actor=APPROVER, inv=inv)
        assert approved.status == "approved" and approved.approved_by == APPROVER and approved.reviewed_at is not None


class TestCollect:
    @pytest.mark.asyncio
    async def test_collect_card_assembles_every_section_from_the_seams(self, monkeypatch):
        tid = uuid.uuid4()
        inv = _inventory()

        async def _policies(_tid):
            return PolicySet(routing=(Policy(id="r1", name="mini", priority=1, model="gpt-4o-mini"),))

        async def _health(_tid, provider, model):
            return {"calls": 10, "failures": 1, "failure_rate": 0.1}

        async def _residency(_tid, provider, kind):
            return {"blocked": False, "reason": "", "data_region": "in", "local": False}

        monkeypatch.setattr(model_cards, "_policy_set", _policies)
        monkeypatch.setattr(model_cards, "_health", _health)
        monkeypatch.setattr(model_cards, "_residency", _residency)
        run = SimpleNamespace(
            id=uuid.uuid4(),
            model="gpt-4o-mini",
            prompt_label="v2",
            prompt_hash="abc",
            cases_run=10,
            cases_total=10,
            offset=0,
            pass_rate=0.9,
            metrics={"classification": {"accuracy": 0.9}},
            avg_latency_ms=120,
            tokens=1000,
            cost_usd=0.01,
            scores={"faithfulness": {"mean": 0.8}},
            created_at=datetime(2026, 10, 6, tzinfo=UTC),
        )
        session = _Session([run], [_row()])
        card = await model_cards.collect_card(session, tid, "openai", "gpt-4o-mini", inv=inv)
        assert card["facts"]["in_catalogue"] and card["use"]["risk_tier"] == "high"
        assert [p["id"] for p in card["governance"]["routing_policies"]] == ["r1"]
        assert card["governance"]["residency"]["data_region"] == "in" and card["operations"]["health"]["calls"] == 10
        assert card["economics"]["price"] is None or "input_per_million" in card["economics"]["price"]
        assert card["evaluation"]["pass_rate"] == 0.9 and card["evaluation"]["scores"] == {"faithfulness": 0.8}
        assert card["written"]["intended_use"] == "Drafting collection letters." and card["written"][
            "updated_by"
        ] == str(EDITOR)
        assert card["completeness"] == {"complete": False, "missing": ["approval"]}
        assert "EvalRun" in session.statements[0] or "eval_runs" in session.statements[0]

    @pytest.mark.asyncio
    async def test_list_cards_gives_one_summary_per_model_in_use(self, monkeypatch):
        inv = _inventory()

        async def _collect(_session, _tid):
            return inv

        monkeypatch.setattr(inventory, "collect", _collect)
        session = _Session([_row(status="approved", approved_by=APPROVER)])
        cards = await model_cards.list_cards(session, uuid.uuid4())
        assert [(c["provider"], c["model"], c["kind"]) for c in cards] == [
            ("local", "BAAI/bge-m3", "embedding"),
            ("openai", "gpt-4o-mini", "llm"),
        ]
        mini = next(c for c in cards if c["model"] == "gpt-4o-mini")
        assert mini["status"] == "approved" and mini["complete"] and mini["agents"] == 1 and mini["risk_tier"] == "high"
        bge = next(c for c in cards if c["model"] == "BAAI/bge-m3")
        assert bge["status"] == "draft" and not bge["complete"] and "intended_use" in bge["missing"]


class TestEndpoints:
    @pytest.mark.asyncio
    async def test_off_the_endpoints_are_not_found(self):
        tid = str(uuid.uuid4())
        with pytest.raises(HTTPException) as refused:
            await api.list_model_cards(tenant_id=tid)
        assert refused.value.status_code == 404 and refused.value.detail["error"] == "governance_model_cards_disabled"
        with pytest.raises(HTTPException) as refused:
            await api.get_model_card(provider="openai", model="gpt-4o-mini", tenant_id=tid)
        assert refused.value.status_code == 404
        with pytest.raises(HTTPException) as refused:
            await api.write_model_card(
                api.ModelCardIn(notes="x"), provider="openai", model="gpt-4o-mini", tenant_id=tid, user={}
            )
        assert refused.value.status_code == 404
        with pytest.raises(HTTPException) as refused:
            await api.approve_model_card(provider="openai", model="gpt-4o-mini", tenant_id=tid, user={})
        assert refused.value.status_code == 404

    @pytest.mark.asyncio
    async def test_on_a_refusal_is_mapped_and_a_write_answers_with_the_written_part(self, monkeypatch):
        monkeypatch.setattr(settings, "governance_model_cards_enabled", True)
        import core.database

        session = _Session([])
        monkeypatch.setattr(core.database, "get_tenant_session", lambda _tid: session)
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: session)
        tid = str(uuid.uuid4())
        with pytest.raises(HTTPException) as refused:
            await api.write_model_card(
                api.ModelCardIn(notes="x"), provider="openai", model="gpt-4o-mini", tenant_id=tid, user={}
            )
        assert refused.value.status_code == 403 and refused.value.detail["error"] == "no_actor"
        answer = await api.write_model_card(
            api.ModelCardIn(intended_use="Letters."),
            provider="openai",
            model="gpt-4o-mini",
            tenant_id=tid,
            user={"agenticorg:user_id": str(EDITOR)},
        )
        assert answer["provider"] == "openai" and answer["written"]["intended_use"] == "Letters."
        assert answer["written"]["status"] == "draft" and answer["written"]["updated_by"] == str(EDITOR)

    def test_the_router_is_registered_behind_admin_and_the_governance_scopes(self):
        main = (ROOT / "api" / "main.py").read_text(encoding="utf-8")
        assert "governance_model_cards," in main and "app.include_router(governance_model_cards.router" in main
        src = (ROOT / "api" / "v1" / "governance_model_cards.py").read_text(encoding="utf-8")
        assert src.count("dependencies=[require_tenant_admin]") == 4
        assert src.count('scope="governance.inventory.sensitive.read"') == 2
        assert src.count('scope="governance.inventory.sensitive.write"') == 2


def test_the_migration_and_the_model_are_shaped():
    migration = (ROOT / "migrations" / "versions" / "v6_z54_model_cards.py").read_text(encoding="utf-8")
    assert 'down_revision = "v6z53_retrieval_metrics"' in migration
    assert "ux_model_cards_tenant_model" in migration and "model_cards_tenant_isolation" in migration
    assert "CHECK (status IN ('draft', 'approved'))" in migration
    from core.models.model_card import ModelCard

    assert ModelCard.__tablename__ == "model_cards"
