# SPDX-License-Identifier: Apache-2.0
"""The spend routes: not found while off, the flow while on, bounded bodies, the scope family, admin writes,
commercial reads and the read view."""

from __future__ import annotations

import io
import typing
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import Column, MetaData, String, Table, create_engine, insert, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from starlette.datastructures import Headers, UploadFile

from api.deps import ActiveHumanAdmin, require_tenant_admin
from api.route_enforcement import SCOPE_FAMILIES, required_scopes_for
from api.route_metadata import ROUTE_METADATA_ATTR
from api.v1 import spend as api
from core.config import settings
from core.ownership import Caller
from core.spend import access, clock, imports, jobs, pricing, rates, vocab
from core.spend.errors import SpendError
from tests.unit.spend_fakes import FakeSession

TENANT = uuid.UUID("11111111-1111-4111-8111-111111111111")
ACTOR = uuid.UUID("33333333-3333-4333-8333-333333333333")
TID = str(TENANT)
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
ADMIN = ActiveHumanAdmin(user_id=ACTOR, tenant_id=TENANT, email="admin@example.com", role="admin")
ADMIN_CALLER = Caller(user_id=ACTOR, role="admin", domains=None, is_admin=True, is_machine=False)
AUDITOR = Caller(user_id=uuid.uuid4(), role="auditor", domains=None, is_admin=False, is_machine=False)
MACHINE = Caller(user_id=None, role="", domains=None, is_admin=False, is_machine=True)
DOMAIN_ROLE = Caller(user_id=uuid.uuid4(), role="cfo", domains=["finance"], is_admin=False, is_machine=False)
ALL_PATHS = {
    "/api/v1/spend/status",
    "/api/v1/spend/org-nodes",
    "/api/v1/spend/org-nodes/import",
    "/api/v1/spend/org-nodes/{node_id}",
    "/api/v1/spend/mappings",
    "/api/v1/spend/mappings/import",
    "/api/v1/spend/model-aliases",
    "/api/v1/spend/rate-cards",
    "/api/v1/spend/rate-cards/import",
    "/api/v1/spend/rate-cards/{card_id}",
    "/api/v1/spend/rate-cards/{card_id}/correct",
    "/api/v1/spend/commitments",
    "/api/v1/spend/commitments/{commitment_id}",
    "/api/v1/spend/fx-rates",
    "/api/v1/spend/fx-rates/import",
    "/api/v1/spend/price",
}


def api_job_paths() -> set[str]:
    """The routes that queue a maintenance job (rate class bulk-import, like the imports)."""
    return {
        "/spend/rollups/rebuild",
        "/spend/usage/backfill",
        "/spend/usage/restate",
        "/spend/usage/reattribute",
        "/spend/fx-rates/settle",
        "/spend/commitments/recompute",
    }


@pytest.fixture
def session(monkeypatch):
    import core.database

    store = FakeSession()
    monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: store)
    monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
    monkeypatch.setattr(settings, "spend_reporting_timezone", "Asia/Kolkata")
    monkeypatch.setattr(settings, "spend_provider_billing_timezones_json", "")
    monkeypatch.setattr(settings, "model_price_overrides_json", "")
    monkeypatch.setattr(clock, "now_utc", lambda: T0)  # handlers stamp the frozen clock
    monkeypatch.setattr(jobs, "_dispatch", lambda tenant_id, job_id: None)  # follow-up jobs stay queued
    pricing._ALIAS_CACHE.clear()
    return store


def upload(content: bytes, name: str = "rows.csv", content_type: str = "text/csv") -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=name, headers=Headers({"content-type": content_type}))


def _calls():
    """One direct call of every PR A route except the status."""
    node_id, card_id = uuid.uuid4(), uuid.uuid4()
    card = api.RateCardIn(
        provider="openai", usage_type="llm_tokens", unit="1m_input_tokens", unit_price=Decimal("1"),
        currency="USD", effective_from=date(2026, 1, 1), source="list",
    )  # fmt: skip
    commitment = api.CommitmentIn(
        provider="openai", kind="money", committed_amount=Decimal("1"), currency="USD",
        period_start=date(2026, 1, 1), period_end=date(2026, 2, 1),
    )  # fmt: skip
    return [
        api.list_org_nodes(tenant_id=TID),
        api.get_org_node(node_id, tenant_id=TID),
        api.create_org_node(api.OrgNodeIn(code="G", name="G", kind="group"), ADMIN, tenant_id=TID),
        api.update_org_node(node_id, api.OrgNodePatch(name="x"), ADMIN, tenant_id=TID),
        api.import_org_nodes(upload(b"code,name,kind\n"), False, ADMIN, tenant_id=TID),
        api.list_mappings(tenant_id=TID),
        api.put_mapping(
            api.MappingIn(source_type="application", source_ref="chat", use_case="x"), ADMIN, tenant_id=TID
        ),
        api.import_mappings(upload(b"source_type,source_ref\n"), False, ADMIN, tenant_id=TID),
        api.list_model_aliases(tenant_id=TID),
        api.put_model_alias(api.AliasIn(provider="openai", alias="a", model_sku="b"), ADMIN, tenant_id=TID),
        api.list_rate_cards(caller=ADMIN_CALLER, tenant_id=TID),
        api.create_rate_card(card, ADMIN, tenant_id=TID),
        api.update_rate_card(card_id, api.RateCardPatch(reference="x"), ADMIN, tenant_id=TID),
        api.correct_rate_card(card_id, api.CorrectIn(reason="a long enough reason"), ADMIN, tenant_id=TID),
        api.import_rate_cards(upload(b"provider\n"), False, ADMIN, tenant_id=TID),
        api.list_commitments(caller=ADMIN_CALLER, tenant_id=TID),
        api.create_commitment(commitment, ADMIN, tenant_id=TID),
        api.update_commitment(uuid.uuid4(), api.CommitmentPatch(status="closed"), ADMIN, tenant_id=TID),
        api.list_fx_rates(tenant_id=TID),
        api.put_fx_rate(
            api.FxRateIn(rate_date=date(2026, 1, 1), currency="USD", rate_to_inr=Decimal("83")), ADMIN, tenant_id=TID
        ),
        api.import_fx_rates(upload(b"rate_date,currency,rate_to_inr\n"), False, ADMIN, tenant_id=TID),
        api.price_quote(
            "openai", "llm_tokens", "input_token", "1", date(2026, 1, 1), caller=ADMIN_CALLER, tenant_id=TID
        ),
    ]


class TestFlag:
    @pytest.mark.asyncio
    async def test_routes_answer_not_found_while_off(self, monkeypatch):
        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
        calls = _calls()
        assert len(calls) == 22
        for call in calls:
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404 and info.value.detail["error"] == "spend_disabled"
        with pytest.raises(HTTPException) as info:
            await api.spend_admin(None)  # the flag is checked before any database read
        assert info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_status_answers_enabled_false_while_off(self, monkeypatch):
        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
        out = await api.spend_status(tenant_id=TID)
        assert out["enabled"] is False and out["reporting_currency"] == "INR"
        assert out["usage_types"] == list(vocab.USAGE_TYPES) and out["limits"] == {
            "import_rows": 5000,
            "import_bytes": 2097152,
            "usage_window_days": 31,
            "rebuild_days": 31,
            "restate_days": 92,
            "gpu_allocation_days": 31,
        }
        assert out["backfill_source"] in ("model_gateway_records", "none")
        assert out["partition_horizon"]["last_month"] == "2028-12" and out["writer"] == {"started": False}
        assert out["record_units"]["llm_tokens"][0] == "input_token" and "gb_month" in out["card_units"]["storage"]
        monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
        assert (await api.spend_status(tenant_id=TID))["enabled"] is True

    @pytest.mark.asyncio
    async def test_spend_admin_needs_an_active_human_administrator_once_on(self, monkeypatch):
        monkeypatch.setattr(settings, "spend_intelligence_enabled", True)

        async def human_admin(request):
            return ADMIN

        monkeypatch.setattr(api, "get_active_human_admin", human_admin)
        assert await api.spend_admin(object()) is ADMIN


class TestFlow:
    @pytest.mark.asyncio
    async def test_route_flow_while_on(self, session):
        created = await api.create_org_node(api.OrgNodeIn(code="g-1", name="Group", kind="group"), ADMIN, tenant_id=TID)
        assert created["code"] == "G-1"
        await api.create_org_node(
            api.OrgNodeIn(code="BU-1", name="Retail", kind="business_unit", parent_code="G-1"), ADMIN, tenant_id=TID
        )
        listed = await api.list_org_nodes(tenant_id=TID)
        assert listed["total"] == 2
        detail = await api.get_org_node(uuid.UUID(created["id"]), tenant_id=TID)
        assert detail["ancestors"] == []
        patched = await api.update_org_node(
            uuid.UUID(created["id"]), api.OrgNodePatch(name="Renamed"), ADMIN, tenant_id=TID
        )
        assert patched["name"] == "Renamed" and session.of("audit_log")[-1].actor_id == str(ACTOR)
        with pytest.raises(HTTPException) as info:
            await api.create_org_node(api.OrgNodeIn(code="G-1", name="Again", kind="group"), ADMIN, tenant_id=TID)
        assert info.value.status_code == 409 and info.value.detail["error"] == "code_taken"
        with pytest.raises(HTTPException) as info:
            await api.get_org_node(uuid.uuid4(), tenant_id=TID)
        assert info.value.status_code == 404 and info.value.detail["error"] == "not_found"
        with pytest.raises(HTTPException) as info:
            await api.list_org_nodes(kind="division", tenant_id=TID)
        assert info.value.status_code == 422

        report = await api.import_org_nodes(
            upload(b"code,name,kind,parent_code\nD-1,Cards,department,BU-1\nX,Bad,division,\n"),
            True,
            ADMIN,
            tenant_id=TID,
        )
        assert report["dry_run"] and report["created"] == 1 and report["rejected"][0]["reason"] == "invalid_kind"
        with pytest.raises(HTTPException) as info:
            await api.import_org_nodes(upload(b"code,name\nA,B\n"), False, ADMIN, tenant_id=TID)
        assert info.value.status_code == 400 and info.value.detail["error"] == "missing_columns"

        mapping = await api.put_mapping(
            api.MappingIn(source_type="application", source_ref="chat", org_node_code="BU-1"), ADMIN, tenant_id=TID
        )
        assert mapping["org_node_code"] == "BU-1"
        assert (await api.list_mappings(tenant_id=TID))["total"] == 1
        with pytest.raises(HTTPException):
            await api.put_mapping(
                api.MappingIn(source_type="agent", source_ref="nope", use_case="x"), ADMIN, tenant_id=TID
            )
        report = await api.import_mappings(
            upload(
                b'[{"source_type": "application", "source_ref": "voice", "use_case": "ivr"}]',
                "m.json",
                "application/json",
            ),
            False,
            ADMIN,
            tenant_id=TID,
        )
        assert report["created"] == 1
        alias = await api.put_model_alias(
            api.AliasIn(provider="openai", alias="gpt-4o-2024-08-06", model_sku="gpt-4o"), ADMIN, tenant_id=TID
        )
        assert alias["model_sku"] == "gpt-4o"
        assert (await api.list_model_aliases(provider="openai", tenant_id=TID))["total"] == 1
        with pytest.raises(HTTPException):
            await api.put_model_alias(api.AliasIn(provider="openai", alias="x", model_sku="x"), ADMIN, tenant_id=TID)

        card = await api.create_rate_card(
            api.RateCardIn(
                provider="openai", usage_type="llm_tokens", model_sku="gpt-4o", unit="1m_input_tokens",
                unit_price=Decimal("2.5"), currency="USD", effective_from=date(2026, 1, 1), source="contract",
                volume_tiers=[api.TierIn(from_quantity=Decimal("0"), unit_price=Decimal("2.5"))],
            ),
            ADMIN,
            tenant_id=TID,
        )  # fmt: skip
        assert card["source"] == "contract" and card["volume_tiers"] == [{"from_quantity": "0", "unit_price": "2.5"}]
        updated = await api.update_rate_card(
            uuid.UUID(card["id"]), api.RateCardPatch(reference="MSA-2"), ADMIN, tenant_id=TID
        )
        assert updated["reference"] == "MSA-2"
        with pytest.raises(HTTPException) as info:
            await api.update_rate_card(
                uuid.UUID(card["id"]), api.RateCardPatch(unit_price=Decimal("3")), ADMIN, tenant_id=TID
            )
        assert info.value.status_code == 409 and info.value.detail["error"] == "card_in_use"
        corrected = await api.correct_rate_card(
            uuid.UUID(card["id"]),
            api.CorrectIn(unit_price=Decimal("2.4"), reason="Keyed the wrong rate"),
            ADMIN,
            tenant_id=TID,
        )
        assert corrected["retired_id"] == card["id"] and corrected["card"]["unit_price"] == "2.4"
        with pytest.raises(HTTPException) as info:
            await api.correct_rate_card(
                uuid.UUID(card["id"]), api.CorrectIn(reason="Already retired one"), ADMIN, tenant_id=TID
            )
        assert info.value.status_code == 409
        cards = await api.list_rate_cards(status="active", caller=AUDITOR, tenant_id=TID)
        assert cards["total"] == 1 and cards["items"][0]["replaces_id"] == card["id"]
        day = date.fromisoformat(corrected["card"]["effective_from"])
        in_force = await api.list_rate_cards(as_of=day, caller=AUDITOR, tenant_id=TID)
        assert in_force["total"] == 1 and in_force["items"][0]["id"] == corrected["card"]["id"]
        retired = await api.list_rate_cards(as_of=day, status="retired", caller=AUDITOR, tenant_id=TID)
        assert [c["id"] for c in retired["items"]] == [card["id"]]
        report = await api.import_rate_cards(
            upload(
                b"provider,usage_type,unit,unit_price,currency,effective_from,source,model_sku\n"
                b"openai,llm_tokens,1m_output_tokens,10,USD,2026-01-01,list,gpt-4o\n"
            ),
            False,
            ADMIN,
            tenant_id=TID,
        )
        assert report["created"] == 1

        commitment = await api.create_commitment(
            api.CommitmentIn(
                provider="openai", kind="quantity", usage_type="llm_tokens", unit="1m_input_tokens",
                committed_quantity=Decimal("500"), period_start=date(2026, 10, 1), period_end=date(2026, 11, 1),
            ),
            ADMIN,
            tenant_id=TID,
        )  # fmt: skip
        assert commitment["remaining"] == "500"
        closed = await api.update_commitment(
            uuid.UUID(commitment["id"]), api.CommitmentPatch(status="closed"), ADMIN, tenant_id=TID
        )
        assert closed["status"] == "closed"
        assert (await api.list_commitments(caller=ADMIN_CALLER, tenant_id=TID))["total"] == 1
        with pytest.raises(HTTPException):
            await api.create_commitment(
                api.CommitmentIn(
                    provider="openai", kind="money", period_start=date(2026, 1, 1), period_end=date(2026, 2, 1)
                ),
                ADMIN,
                tenant_id=TID,
            )

        rate = await api.put_fx_rate(
            api.FxRateIn(rate_date=date(2026, 10, 1), currency="USD", rate_to_inr=Decimal("83")), ADMIN, tenant_id=TID
        )
        assert rate["previous_rate"] is None
        report = await api.import_fx_rates(
            upload(b"rate_date,currency,rate_to_inr\n2026-09-30,USD,82.9\n"), False, ADMIN, tenant_id=TID
        )
        assert report["created"] == 1
        assert (await api.list_fx_rates(currency="USD", tenant_id=TID))["total"] == 2
        with pytest.raises(HTTPException):
            await api.put_fx_rate(
                api.FxRateIn(rate_date=date(2026, 10, 1), currency="INR", rate_to_inr=Decimal("1")),
                ADMIN,
                tenant_id=TID,
            )

        quote = await api.price_quote(
            "openai", "llm_tokens", "input_token", "1000000", date(2026, 10, 1), model="GPT-4o-2024-08-06",
            caller=ADMIN_CALLER, tenant_id=TID,
        )  # fmt: skip
        assert quote["amount"] == "2.4000000000" and quote["amount_inr"] == "199.2000000000"
        assert quote["price_source"] == "contract" and quote["flags"] == []
        with pytest.raises(HTTPException) as info:
            await api.price_quote(
                "openai", "llm_tokens", "input_token", "1e100000", date(2026, 10, 1), caller=ADMIN_CALLER, tenant_id=TID
            )
        assert info.value.status_code == 422 and info.value.detail["error"] == "invalid_number"

    @pytest.mark.asyncio
    async def test_oversize_upload_is_refused_as_import_too_large(self, session, monkeypatch):
        monkeypatch.setattr(imports, "MAX_IMPORT_BYTES", 16)
        with pytest.raises(HTTPException) as info:
            await api.import_fx_rates(upload(b"rate_date,currency,rate_to_inr\n" * 4), False, ADMIN, tenant_id=TID)
        assert info.value.status_code == 413 and info.value.detail["error"] == "import_too_large"

    @pytest.mark.asyncio
    async def test_too_many_rows_and_bad_tenant(self, session, monkeypatch):
        monkeypatch.setattr(imports, "MAX_IMPORT_ROWS", 1)
        with pytest.raises(HTTPException) as info:
            await api.import_fx_rates(
                upload(b"rate_date,currency,rate_to_inr\n2026-01-01,USD,1\n2026-01-02,USD,1\n"),
                False,
                ADMIN,
                tenant_id=TID,
            )
        assert info.value.status_code == 413 and info.value.detail["error"] == "too_many_rows"
        with pytest.raises(HTTPException) as info:
            await api.list_fx_rates(tenant_id="not-a-tenant")
        assert info.value.status_code == 401


class TestAccess:
    @pytest.mark.asyncio
    async def test_commercial_reads_refuse_machine_callers_and_domain_roles(self, session):
        for caller in (
            MACHINE,
            DOMAIN_ROLE,
            Caller(user_id=None, role="admin", domains=None, is_admin=True, is_machine=True),
        ):
            for call in (
                api.list_rate_cards(caller=caller, tenant_id=TID),
                api.list_commitments(caller=caller, tenant_id=TID),
                api.price_quote(
                    "openai", "llm_tokens", "input_token", "1", date(2026, 1, 1), caller=caller, tenant_id=TID
                ),
            ):
                with pytest.raises(HTTPException) as info:
                    await call
                assert info.value.status_code == 403 and info.value.detail["error"] == "commercial_read_refused"

    @pytest.mark.asyncio
    async def test_admin_and_auditor_read_rate_cards(self, session):
        for caller in (ADMIN_CALLER, AUDITOR):
            assert (await api.list_rate_cards(caller=caller, tenant_id=TID))["total"] == 0
            assert (await api.list_commitments(caller=caller, tenant_id=TID))["total"] == 0

    def test_read_view_filters_agents_for_everyone_but_administrators_and_auditors(self):
        from core.models.agent import Agent

        records = Table("usage_records", MetaData(), Column("tenant_id", PG_UUID), Column("agent_id", PG_UUID))
        admin = access.read_view(ADMIN_CALLER)
        assert admin.agent_clause is None and admin.show_user_ids and admin.commercial
        everything = access.usage_filter(admin, records, Agent.__table__, tenant_id=TENANT)
        assert str(everything.compile(dialect=postgresql.dialect())) == "true"
        domain = access.read_view(DOMAIN_ROLE)
        assert not domain.show_user_ids and not domain.commercial
        sql = str(access.usage_filter(domain, records, Agent.__table__, tenant_id=TENANT).compile(
            dialect=postgresql.dialect()))  # fmt: skip
        assert "usage_records.agent_id IS NULL" in sql and "agents.tenant_id = %(tenant_id_1)s" in sql
        assert "usage_records.tenant_id" not in sql  # the tenant is bound: the subquery is not correlated
        assert "agents.domain IN" in sql and "agents.owner_user_id" in sql
        machine = access.read_view(MACHINE)
        assert not machine.show_user_ids and not machine.commercial and machine.agent_clause is not None
        auditor = access.read_view(AUDITOR)
        assert auditor.show_user_ids and auditor.agent_clause is None  # every record, as on GET /audit
        user = uuid.uuid4()
        assert access.redact_user(auditor, user) == str(user)
        assert access.redact_user(domain, user) is None and access.redact_user(auditor, None) is None
        access.require_commercial(AUDITOR)
        with pytest.raises(SpendError):
            access.require_commercial(MACHINE)

    def test_commercial_audit_clause_hides_exactly_the_commercial_spend_rows(self):
        rows = Table("audit_rows", MetaData(), Column("event_type", String(100)))
        engine = create_engine("sqlite://")
        rows.metadata.create_all(engine)
        kept = ["spend.org_nodes.create", "spend.fx_rates.import", "spend.rateXcards.create", "agent.run", "auth.login"]
        hidden = ["spend.rate_cards.create", "spend.rate_cards.correct", "spend.commitments.update"]
        with engine.begin() as connection:
            connection.execute(insert(rows), [{"event_type": name} for name in kept + hidden])
        for caller in (DOMAIN_ROLE, MACHINE, Caller(user_id=None, role="admin", domains=None, is_admin=True,
                                                    is_machine=True)):  # fmt: skip
            clause = access.commercial_audit_clause(caller, rows.c.event_type)
            with engine.begin() as connection:
                seen = [r[0] for r in connection.execute(select(rows.c.event_type).where(clause))]
            assert sorted(seen) == sorted(kept)  # "_" is matched literally, not as a wildcard
        assert access.commercial_audit_clause(ADMIN_CALLER, rows.c.event_type) is None
        assert access.commercial_audit_clause(AUDITOR, rows.c.event_type) is None

    @pytest.mark.asyncio
    async def test_general_audit_read_hides_rate_card_and_commitment_rows_from_refused_callers(self, monkeypatch):
        """A caller refused GET /spend/rate-cards cannot read the same prices from GET /audit."""
        from api.v1 import audit as audit_api

        class Capture:
            def __init__(self, kept=True, entries=()):
                self.statements = []
                self.kept = kept
                self.entries = list(entries)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def execute(self, statement):
                self.statements.append(statement)
                if "spend_rate_cards" in sql_of(statement):
                    return SimpleNamespace(scalar=lambda: self.kept)
                return SimpleNamespace(scalar=lambda: 0, scalars=lambda: SimpleNamespace(all=lambda: self.entries))

        def sql_of(statement):
            return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

        def request(role, *, user_id=True, auth_mode="legacy", scopes=None, domains=None):
            claims = {"sub": f"{role}@example.com", "role": role}
            if user_id:
                claims["agenticorg:user_id"] = str(uuid.uuid4())
            if domains is not None:
                claims["agenticorg:domains"] = domains
            scopes = scopes or (["agenticorg:admin"] if role == "admin" else ["agents:read", "audit:read"])
            return SimpleNamespace(state=SimpleNamespace(claims=claims, scopes=scopes, auth_mode=auth_mode))

        cases = [
            (request("admin"), "admin", False),
            (request("auditor", scopes=["audit:read"]), "auditor", False),
            (request("cfo", domains=["finance"]), "cfo", True),
            (request("domain_lead"), "domain_lead", True),
            (request("admin", user_id=False, auth_mode="api_key"), "admin", True),
            (request("", user_id=False, auth_mode="grantex", scopes=["audit:read"]), "", True),
        ]
        assert settings.spend_intelligence_enabled is False  # the filter does not depend on the flag
        for req, role, filtered in cases:
            capture = Capture()
            monkeypatch.setattr(audit_api, "get_tenant_session", lambda tenant_id, c=capture: c)
            out = await audit_api.query_audit(request=req, event_type="spend.rate_cards", tenant_id=TID, user_role=role)
            # A refused caller first asks whether the tenant keeps rate cards or commitments.
            assert out.total == 0 and len(capture.statements) == (3 if filtered else 2), role
            if filtered:
                kept_sql = sql_of(capture.statements[0])
                assert "FROM spend_rate_cards" in kept_sql and "FROM spend_commitments" in kept_sql
                assert "FROM spend_invoices" in kept_sql and "FROM spend_reconciliations" in kept_sql
                assert f"tenant_id = '{TID}'" in kept_sql and "audit_log" not in kept_sql
            for statement in capture.statements[-2:]:  # the count and the page
                sql = sql_of(statement)
                assert ("NOT LIKE 'spend.rate/_cards.'" in sql and "NOT LIKE 'spend.commitments.'" in sql) is filtered
                assert ("NOT LIKE 'spend.invoices.'" in sql and "NOT LIKE 'spend.reconciliations.'" in sql) is filtered
                assert ("spend.rate/_cards." in sql) is filtered, role

        # A tenant that never kept a rate card or commitment runs the audit query it ran before.
        machine = request("admin", user_id=False, auth_mode="api_key")
        before = Capture(kept=False)
        monkeypatch.setattr(audit_api, "get_tenant_session", lambda tenant_id, c=before: c)
        await audit_api.query_audit(request=machine, tenant_id=TID, user_role="admin")
        admin = Capture(kept=False)
        monkeypatch.setattr(audit_api, "get_tenant_session", lambda tenant_id, c=admin: c)
        await audit_api.query_audit(request=request("admin"), tenant_id=TID, user_role="admin")
        assert [sql_of(s) for s in before.statements[1:]] == [sql_of(s) for s in admin.statements]
        assert all("NOT LIKE" not in sql_of(s) for s in before.statements[1:])

        # A first rate card committed between the check and the page query is still not shown.
        raced = SimpleNamespace(event_type="spend.rate_cards.create")
        other = SimpleNamespace(event_type="agent.run.resumed")
        monkeypatch.setattr(audit_api, "_audit_to_dict", lambda entry: {"event_type": entry.event_type})
        race = Capture(kept=False, entries=[raced, other])
        monkeypatch.setattr(audit_api, "get_tenant_session", lambda tenant_id, c=race: c)
        out = await audit_api.query_audit(request=machine, tenant_id=TID, user_role="admin")
        assert out.items == [{"event_type": "agent.run.resumed"}]
        shown = Capture(kept=False, entries=[raced, other])
        monkeypatch.setattr(audit_api, "get_tenant_session", lambda tenant_id, c=shown: c)
        out = await audit_api.query_audit(
            request=request("auditor", scopes=["audit:read"]), tenant_id=TID, user_role="auditor"
        )
        assert len(out.items) == 2  # an auditor reads them

    def test_commercial_audit_event_matches_the_clause_prefixes(self):
        assert access.is_commercial_audit_event("spend.rate_cards.correct")
        assert access.is_commercial_audit_event("spend.commitments.update")
        assert access.is_commercial_audit_event("spend.invoices.import")
        assert access.is_commercial_audit_event("spend.reconciliations.accept_item")
        for other in ("spend.rateXcards.create", "spend.org_node.create", "Spend.rate_cards.create", None, 7):
            assert not access.is_commercial_audit_event(other)
        assert access.COMMERCIAL_AUDIT_PREFIXES == tuple(prefix for prefix, _ in access.COMMERCIAL_AUDIT_SOURCES)


class TestRouteShape:
    def test_bodies_are_bounded(self):
        base = {"code": "G", "name": "G", "kind": "group"}
        api.OrgNodeIn(**base)
        for over in ({"extra": 1}, {"code": "C" * 65}, {"kind": "division"}, {"name": ""}, {"name": "n" * 201}):
            with pytest.raises(ValidationError):
                api.OrgNodeIn(**{**base, **over})
        card = {
            "provider": "openai", "usage_type": "llm_tokens", "unit": "1m_input_tokens", "unit_price": "2.5",
            "currency": "USD", "effective_from": "2026-01-01", "source": "list",
        }  # fmt: skip
        api.RateCardIn(**card)
        tier = {"from_quantity": "0", "unit_price": "1"}
        for over in (
            {"unit_price": "1e10"},
            {"unit_price": "0.00000000001"},
            {"unit_price": "-1"},
            {"currency": "usd"},
            {"source": "rumour"},
            {"batch_discount_pct": "100.5"},
            {"volume_tiers": [tier] * 21},
            {"volume_tiers": [{"from_quantity": "1e16", "unit_price": "1"}]},
            {"model_sku": "m" * 129},
            {"reference": "r" * 201},
            {"unknown": True},
        ):
            with pytest.raises(ValidationError):
                api.RateCardIn(**{**card, **over})
        for model, body in (
            (api.FxRateIn, {"rate_date": "2026-01-01", "currency": "USD", "rate_to_inr": "0"}),
            (api.FxRateIn, {"rate_date": "2026-01-01", "currency": "USD", "rate_to_inr": "1.123456789"}),
            (api.FxRateIn, {"rate_date": "2026-01-01", "currency": "USD", "rate_to_inr": "1e7"}),
            (api.CommitmentIn, {"provider": "p", "kind": "money", "committed_amount": "1e13", "currency": "USD",
                                "period_start": "2026-01-01", "period_end": "2026-02-01"}),
            (api.CorrectIn, {"reason": "short"}),
            (api.CommitmentPatch, {"status": "active"}),
            (api.RateCardPatch, {"status": "active"}),
            (api.MappingIn, {"source_type": "printer", "source_ref": "x"}),
            (api.AliasIn, {"provider": "", "alias": "a", "model_sku": "b"}),
        ):  # fmt: skip
            with pytest.raises(ValidationError):
                model(**body)
        assert typing.get_args(api.NodeKind) == vocab.NODE_KINDS
        assert typing.get_args(api.SourceType) == vocab.SOURCE_TYPES
        assert typing.get_args(api.CardSource) == vocab.CARD_SOURCES
        assert typing.get_args(api.TierMode) == vocab.TIER_MODES
        assert typing.get_args(api.FxSource) == vocab.FX_SOURCES
        assert typing.get_args(api.CommitmentKind) == vocab.COMMITMENT_KINDS
        assert rates.REASON_MIN == 10 and rates.REASON_MAX == 500

    def test_router_registered_under_spend_family(self):
        from api.main import app

        paths = set(app.openapi()["paths"])
        assert ALL_PATHS <= paths
        assert SCOPE_FAMILIES["spend"] == ("audit:read", "approvals:write")
        assert required_scopes_for("spend.org.read", "GET") == ("audit:read",)
        assert required_scopes_for("spend.rate_cards.sensitive.write", "POST") == ("approvals:write",)
        operations = 0
        for route in api.router.routes:
            meta = getattr(route.endpoint, ROUTE_METADATA_ATTR)
            operations += len(route.methods)
            assert meta["scope"].startswith("spend.") and meta["audit_event"].startswith("spend.")
            assert meta["auth_required"] and meta["tenant_required"]
            jobs_route = route.path in api_job_paths()
            imports_route = route.path.endswith("/import")
            preview_route = route.path == "/spend/storage/sample"  # measures every store, as heavy as a job
            # A reconciliation run reads a provider's month of rollups and writes every item: as heavy as a job.
            run_route = route.path == "/spend/reconciliations" and "POST" in route.methods
            heavy = imports_route or jobs_route or preview_route or run_route
            assert meta["rate_limit"] == ("bulk-import" if heavy else "standard")
            if "GET" in route.methods:
                assert meta["idempotency"] == "read-only"
        assert operations == 47  # 23 reference-data, 13 usage, 2 metering and 9 reconciliation operations

    def test_write_routes_carry_tenant_admin_dependency(self):
        writes = [r for r in api.router.routes if not r.methods <= {"GET", "HEAD"}]
        assert len(writes) == 25  # 14 reference-data writes, 6 job routes, the storage preview, 4 reconciliation
        for route in writes:
            assert require_tenant_admin in route.dependencies, route.path
            assert api.spend_admin in [d.call for d in route.dependant.dependencies], route.path
            assert ".sensitive.write" in getattr(route.endpoint, ROUTE_METADATA_ATTR)["scope"]
        for route in api.router.routes:
            if route.methods <= {"GET", "HEAD"}:
                assert require_tenant_admin not in route.dependencies
        commercial = {
            "/spend/rate-cards",
            "/spend/commitments",
            "/spend/price",
            "/spend/invoices",
            "/spend/invoices/{invoice_id}",
            "/spend/reconciliations",
            "/spend/reconciliations/{reconciliation_id}",
            "/spend/gate",
        }
        for route in api.router.routes:
            if route.methods <= {"GET"} and route.path in commercial:
                assert "caller" in route.dependant.call.__code__.co_varnames
