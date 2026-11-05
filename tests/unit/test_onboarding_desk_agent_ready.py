# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The agent-ready merchant offer (MO-70 to MO-75), the evidence sections (MO-63, MO-74) and the migration."""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from connectors.agent_identity import mock as mock_identity
from connectors.framework.agent_identity_provider import AgentIdentityUnavailable
from core.agent_identity import grantex, guard, scopes
from core.config import settings
from core.onboarding_desk import agent_ready, applications, cases, evidence, reports, store
from core.onboarding_desk.branding import branding_from
from core.onboarding_desk.store import DeskError
from tests.unit import test_onboarding_desk_applications as _application_tests
from tests.unit import test_onboarding_desk_cases as _case_tests
from tests.unit.test_onboarding_desk_agent_identity import FakeGrantex

TENANT = uuid.uuid4()
LEAD = uuid.uuid4()
APP = uuid.uuid4()
NOW = datetime(2026, 10, 27, 17, 0, tzinfo=UTC)
# The fakes the conversation and case tests use.
chat = _application_tests.chat
say = _application_tests.say
chat_app_row = _application_tests.app_row
db = _case_tests.db
pack_inputs = _case_tests.pack_inputs
governed = _case_tests.governed
SEED = _case_tests.SEED
BRANDING = branding_from(
    {"partner_name": "Northwind Payments", "onboarding_url": "https://apply.northwind.example/start",
     "sms_conversation": True}
)  # fmt: skip


def approved_case(*, verified=NOW - timedelta(days=3), outcome="approve", registry_match=True, findings=()):
    return SimpleNamespace(
        case_ref="case_x",
        provider="mock",
        state="decided",
        policy_id="business_onboarding_us",
        decision={"outcome": outcome, "decided_at": NOW.isoformat()} if outcome else None,
        memo={
            "created_at": verified.isoformat() if verified else None,
            "recommendation": {"proposed": "approve"},
            "sections": [{"section_id": "registry", "status": "complete", "findings": [{"code": c} for c in findings]}],
        },
        subject={
            "provider": "mock",
            "provider_ref": "mock-us-ca-0001",
            "jurisdiction": "US-CA",
            "identifiers": [{"scheme": "us_state_file_number", "value": "0001"}],
        },  # fmt: skip
        application={
            "legal_name": "Northwind Coffee LLC",
            "registered_address": {"lines": ["1 Market St"], "locality": "San Francisco", "region": "CA"},
            "declared_owners": [{"name": "Avery Example", "date_of_birth": "1980-04"}],
            "website": "https://northwind.example",
            "declared_activity": "food_service",
            "identifiers": [{"scheme": "us_ein", "value": "12-3456789"}],
        },
        agent_records=[
            {
                "agent": "business_underwriter",
                "status": "completed",
                "policy_evidence": {
                    "verification": {"status": "active", "registry_match": registry_match, "tax_id_match": True}
                },
            }  # fmt: skip
        ],
        policy_result={"tier": "low"},
        failure_reason=None,
        updated_at=NOW,
    )


def application(**overrides):
    base = {"id": APP, "lead_id": LEAD, "status": "submitted", "case_ref": "case_x", "locale": "en"}
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def offer(monkeypatch):
    """Agent identity on, the tenant's offer on, an approved case, a fake Grantex and the mock identity provider."""
    monkeypatch.setattr(settings, "onboarding_desk_agent_identity_enabled", True)
    provider = mock_identity.MockAgentIdentityProvider(clock=lambda: NOW)
    provider.enrolment_status = "pending_review"
    fake = FakeGrantex()
    state = {
        "settings": agent_ready.AgentReadySettings(enabled=True),
        "case": approved_case(),
        "consents": [],
        "enrolments": [],
        "provider": provider,
        "grantex": fake,
    }

    async def get_settings(tid):
        return state["settings"]

    async def get_case(tid, ref):
        return state["case"]

    async def get_branding(tid):
        return BRANDING

    async def get_lead(tid, lid):
        return SimpleNamespace(id=LEAD, business_name="Northwind Coffee LLC"), []

    async def encrypt(tid, text):
        return "enc:" + text

    async def add(tid, *rows):
        for row in rows:
            (state["enrolments"] if hasattr(row, "consent_id") else state["consents"]).append(row)

    async def list_enrolments(tid, aid):
        return list(state["enrolments"])

    async def consent(tid, cid):
        return next((c for c in state["consents"] if c.id == cid), None)

    async def update(tid, eid, **values):
        row = next(e for e in state["enrolments"] if e.id == eid)
        for key, value in values.items():
            setattr(row, key, value)
        return row

    async def withdraw_consent(tid, cid, moment):
        for row in state["consents"]:
            if row.id == cid and row.status == "granted":
                row.status, row.withdrawn_at, row.withdrawn_via = "withdrawn", moment, "applicant"

    async def grantex_agent(tid):
        return "ag_conversation"

    monkeypatch.setattr(agent_ready, "get_settings", get_settings)
    monkeypatch.setattr(cases, "get_case", get_case)
    monkeypatch.setattr(store, "get_branding", get_branding)
    monkeypatch.setattr(store, "get_lead", get_lead)
    monkeypatch.setattr(store, "_encrypt", encrypt)
    monkeypatch.setattr(agent_ready, "_add", add)
    monkeypatch.setattr(agent_ready, "list_enrolments", list_enrolments)
    monkeypatch.setattr(agent_ready, "_consent", consent)
    monkeypatch.setattr(agent_ready, "_update", update)
    monkeypatch.setattr(agent_ready, "_withdraw_consent", withdraw_consent)
    monkeypatch.setattr(agent_ready, "_grantex_agent", grantex_agent)
    monkeypatch.setattr(grantex, "grantex_identity_service", fake.service)
    monkeypatch.setattr(guard, "_configured_provider", lambda: provider)
    return state


def run(coro):
    return asyncio.run(coro)


def accept(**overrides):
    values = {"name": "Avery Example", "role": "Owner", "contact": "avery@northwind.example", "now": NOW}
    values.update(overrides)
    return run(agent_ready.accept(TENANT, application(), **values))


def grant_approved(state, grant_id="grnt_enrol_1", *, issued=NOW + timedelta(minutes=1), purpose=None,
                   expires=NOW + timedelta(days=1), grant_scopes=None):  # fmt: skip
    """The person approved the consent grant at Grantex, as Grantex's grant view shows it."""
    state["grantex"].grants[grant_id] = {
        "grantId": grant_id,
        "status": "active",
        "scopes": grant_scopes or [scopes.ENROLMENT],
        "purpose": purpose or grantex.ENROLMENT_PURPOSE,
        "issuedAt": issued.isoformat(),
        "expiresAt": expires.isoformat(),
    }


# ── MO-70: settings ──────────────────────────────────────────────────────────


class TestSettings:
    def test_defaults_and_parsing(self):
        default = agent_ready.settings_from(None)
        assert default.enabled is False and default.max_verification_age_days == 90
        assert default.offer("en") == agent_ready.DEFAULT_OFFER["en"] and default.offer("fr").startswith("Votre")
        parsed = agent_ready.settings_from(
            {"enabled": True, "offer": {"en": "  Be   agent ready. ", "fr": ""}, "max_verification_age_days": 30}
        )
        assert parsed.offer_en == "Be agent ready." and parsed.offer_fr == agent_ready.DEFAULT_OFFER["fr"]
        assert agent_ready.settings_from(parsed.to_dict()) == parsed

    @pytest.mark.parametrize(
        "raw",
        [
            {"enabled": "yes"},
            {"other": 1},
            {"offer": {"de": "x"}},
            {"offer": "x"},
            {"offer": {"en": "x" * 321}},
            {"max_verification_age_days": 0},
            {"max_verification_age_days": 366},
            {"max_verification_age_days": True},
        ],
    )
    def test_refused(self, raw):
        with pytest.raises(DeskError) as caught:
            agent_ready.settings_from(raw)
        assert caught.value.code == "agent_ready_settings_invalid"

    def test_put_and_get_keep_other_settings(self, db):
        row = SimpleNamespace(settings={"cases": {"auto_open": True}}, updated_by="", updated_at=None)
        db([row])
        saved = run(agent_ready.put_settings(TENANT, {"enabled": True}, actor="admin"))
        assert saved.enabled and row.settings["cases"] == {"auto_open": True}
        assert row.settings["agent_ready"]["enabled"] is True
        db([row])
        assert run(agent_ready.get_settings(TENANT)).enabled is True
        db([])
        assert run(agent_ready.get_settings(TENANT)).enabled is False
        db([SimpleNamespace(settings={"agent_ready": {"enabled": "garbled"}})])
        assert run(agent_ready.get_settings(TENANT)).enabled is False, "unreadable settings count as off"
        session = db([])
        run(agent_ready.put_settings(TENANT, {}, actor="admin"))
        assert session.added[0].settings == {"agent_ready": agent_ready.AgentReadySettings().to_dict()}


# ── MO-71: when the offer shows, and that it never changes a decision ────────


class TestOffer:
    def test_only_after_approval_with_identity_and_the_tenant_on(self, offer, monkeypatch):
        assert run(agent_ready.offer_view(TENANT, application(), now=NOW))["status"] == "offered"
        assert run(agent_ready.offer_view(TENANT, application(status="in_progress"), now=NOW)) is None
        assert run(agent_ready.offer_view(TENANT, application(case_ref=None), now=NOW)) is None
        offer["case"] = approved_case(outcome="decline")
        assert run(agent_ready.offer_view(TENANT, application(), now=NOW)) is None
        offer["case"] = approved_case(outcome=None)
        assert run(agent_ready.offer_view(TENANT, application(), now=NOW)) is None
        offer["case"] = approved_case()
        offer["settings"] = agent_ready.AgentReadySettings(enabled=False)
        assert run(agent_ready.offer_view(TENANT, application(), now=NOW)) is None
        offer["settings"] = agent_ready.AgentReadySettings(enabled=True, offer_fr="Offre.")
        assert run(agent_ready.offer_view(TENANT, application(locale="fr"), now=NOW))["offer"] == "Offre."
        monkeypatch.setattr(settings, "onboarding_desk_agent_identity_enabled", False)
        assert run(agent_ready.offer_view(TENANT, application(), now=NOW)) is None

    def test_decisions_are_identical_with_the_offer_on_or_off(self, offer, monkeypatch):
        before = copy.deepcopy(vars(offer["case"]))
        view_before = cases.case_view(offer["case"])
        run(agent_ready.offer_view(TENANT, application(), now=NOW))
        accept()
        grant_approved(offer)
        run(agent_ready.continue_enrolment(TENANT, application(), now=NOW))
        run(agent_ready.withdraw(TENANT, application(), now=NOW))
        assert vars(offer["case"]) == before and cases.case_view(offer["case"]) == view_before
        offer["settings"] = agent_ready.AgentReadySettings(enabled=False)
        assert run(agent_ready.offer_view(TENANT, application(), now=NOW)) is None
        assert cases.case_view(offer["case"]) == view_before

    def test_the_sms_offer_after_submission(self, chat, offer, monkeypatch):
        async def leads_by_phone(tid, phone, *, statuses=("new", "contacted", "engaged", "applying")):
            return [SimpleNamespace(id=chat["leads"][0].id)] if statuses == ("submitted",) else []

        async def allowed(tid, role, scope=None):
            return guard.OFF

        async def get_lead(tid, lid):
            return SimpleNamespace(id=lid), chat["consents"]

        monkeypatch.setattr(store, "get_lead", get_lead)
        monkeypatch.setattr(store, "open_leads_by_phone", leads_by_phone)
        monkeypatch.setattr(guard, "check_action", allowed)
        chat["app"] = chat_app_row(status="submitted", case_ref="case_x")
        reply = say("hello")
        assert "ready for AI agents" in reply and "https://apply.northwind.example/start?t=" in reply
        offer["settings"] = agent_ready.AgentReadySettings(enabled=True, offer_en="Be agent ready.")
        assert "Be agent ready. Reply PERSON for help, or accept on the secure page:" in say("hello")
        chat["app"] = chat_app_row(status="submitted", case_ref="case_x", locale="fr")
        assert "agents IA" in say("bonjour")
        chat["app"] = chat_app_row(status="submitted", case_ref="case_x")
        offer["case"] = approved_case(verified=NOW - timedelta(days=400))
        assert "verify your business again" in say("hello")
        offer["settings"] = agent_ready.AgentReadySettings(enabled=False)
        assert say("hello") is None, "a submitted lead gets no reply without the offer, as before"


# ── MO-72, MO-73, MO-75: accepting, sharing and withdrawing ──────────────────


class TestEnrolment:
    def test_accept_records_the_consent_and_requests_the_grant(self, offer):
        result = accept()
        assert result["consent_url"] == "https://grantex.example/c/1"
        consent = offer["consents"][0]
        assert (consent.channel, consent.purpose, consent.written, consent.status) == (
            "web",
            "agent_enrolment",
            True,
            "granted",
        )
        assert "Avery Example (Owner)" in consent.wording and "Northwind Coffee LLC" in consent.wording
        assert json.loads(consent.subject_ciphertext.removeprefix("enc:")) == {
            "name": "Avery Example",
            "role": "Owner",
            "contact": "avery@northwind.example",
        }
        enrolment = result["enrolment"]
        assert enrolment["status"] == "consent_pending" and enrolment["consent_request_id"] == "areq_1"
        method, path, body = offer["grantex"].requests[-1]
        assert (method, path) == ("POST", "/v1/authorize")
        assert body["scopes"] == [scopes.ENROLMENT] and body["purpose"] == "x-agenticorg.agent_enrolment"
        assert body["principalId"].startswith("obd_") and "Avery" not in body["principalId"]
        assert accept()["enrolment"]["id"] == enrolment["id"], "one live enrolment per application"
        assert len(offer["consents"]) == 1

    def test_accept_refusals(self, offer, monkeypatch):
        for bad in ({"contact": "not a contact"}, {"name": " "}, {"role": "x" * 81}):
            with pytest.raises(DeskError) as caught:
                accept(**bad)
            assert caught.value.code == "representative_invalid"
        monkeypatch.setattr(grantex, "grantex_identity_service", lambda: None)
        with pytest.raises(DeskError) as caught:
            accept()
        assert caught.value.code == "consent_service_not_configured" and offer["consents"] == []
        offer["grantex"].down = True
        monkeypatch.setattr(grantex, "grantex_identity_service", offer["grantex"].service)
        with pytest.raises(DeskError) as caught:
            accept()
        assert caught.value.code == "consent_service_unavailable" and offer["consents"] == []
        offer["case"] = approved_case(outcome="decline")
        with pytest.raises(DeskError) as caught:
            accept()
        assert caught.value.code == "agent_ready_not_offered"

    def test_hand_off_shares_only_verified_fields_and_records_the_answer(self, offer):
        accept()
        pending = run(agent_ready.continue_enrolment(TENANT, application(), now=NOW))["enrolment"]
        assert pending["status"] == "consent_pending" and offer["provider"].enrolments == {}
        grant_approved(offer)
        shared = run(agent_ready.continue_enrolment(TENANT, application(), now=NOW))["enrolment"]
        assert shared["status"] == "shared" and shared["consent_grant_id"] == "grnt_enrol_1"
        assert shared["provider"] == "mock" and shared["provider_status"] == "pending_review", "as received"
        assert shared["provider_ref"].startswith("mock-enrol-")
        assert shared["shared_fields"] == ["jurisdiction", "legal_name", "registry_identifiers", "registry_status"]
        ((request, _),) = offer["provider"].enrolments.values()
        sent = json.dumps({"ref": request.verification_ref, "fields": request.verified_fields})
        for unverified in ("1 Market St", "Avery", "food_service", "northwind.example", "12-3456789", "1980-04"):
            assert unverified not in sent, unverified
        assert request.verification_ref == {"case_ref": "case_x", "provider": "mock", "provider_ref": "mock-us-ca-0001",
                                            "verified_at": (NOW - timedelta(days=3)).isoformat()}  # fmt: skip
        assert request.consent_grant_id == "grnt_enrol_1"
        again = run(agent_ready.continue_enrolment(TENANT, application(), now=NOW))["enrolment"]
        assert again == shared and len(offer["provider"].enrolments) == 1, "never sent again"

    @pytest.mark.parametrize(
        ("case", "excluded"),
        [
            ({"registry_match": False}, "legal_name"),
            ({"findings": ("name_mismatch",)}, "legal_name"),
            ({"findings": ("name_check_inconclusive",)}, "legal_name"),
        ],
    )
    def test_the_legal_name_is_shared_only_when_verified(self, case, excluded):
        _, fields = agent_ready.verified_payload(approved_case(**case))
        assert excluded not in fields and fields["jurisdiction"] == "US-CA"

    def test_nothing_verified_means_nothing_shared(self):
        bare = approved_case()
        bare.subject, bare.agent_records, bare.memo = None, [], {"created_at": NOW.isoformat()}
        reference, fields = agent_ready.verified_payload(bare)
        assert fields == {} and reference["provider"] == "mock" and reference["provider_ref"] is None

    def test_an_old_verification_is_never_handed_off(self, offer):
        offer["case"] = approved_case(verified=NOW - timedelta(days=91))
        view = run(agent_ready.offer_view(TENANT, application(), now=NOW))
        assert view == {"status": "reverify_required", "message": agent_ready.TEXT["en"]["reverify"].format(
            partner="Northwind Payments")}  # fmt: skip
        with pytest.raises(DeskError) as caught:
            accept()
        assert caught.value.code == "verification_stale" and "verify your business again" in caught.value.message
        offer["case"] = approved_case()
        accept()
        grant_approved(offer)
        offer["case"] = approved_case(verified=NOW - timedelta(days=200))
        with pytest.raises(DeskError) as caught:
            run(agent_ready.continue_enrolment(TENANT, application(), now=NOW))
        assert caught.value.code == "verification_stale" and offer["provider"].enrolments == {}
        offer["case"] = approved_case(verified=None)
        assert run(agent_ready.offer_view(TENANT, application(), now=NOW))["status"] == "reverify_required"
        offer["settings"] = agent_ready.AgentReadySettings(enabled=True, max_verification_age_days=365)
        offer["case"] = approved_case(verified=NOW - timedelta(days=200))
        assert run(agent_ready.continue_enrolment(TENANT, application(), now=NOW))["enrolment"]["status"] == "shared"

    def test_withdrawal_revokes_the_grant_and_blocks_sharing(self, offer):
        accept()
        grant_approved(offer)
        run(agent_ready.continue_enrolment(TENANT, application(), now=NOW))
        result = run(agent_ready.withdraw(TENANT, application(), now=NOW))
        assert result["grant_revoked"] is True and result["enrolment"]["status"] == "revoked"
        assert offer["consents"][0].status == "withdrawn"
        assert offer["grantex"].grants["grnt_enrol_1"]["status"] == "revoked"
        with pytest.raises(DeskError) as caught:
            run(agent_ready.continue_enrolment(TENANT, application(), now=NOW))
        assert caught.value.code == "enrolment_not_found" and len(offer["provider"].enrolments) == 1
        with pytest.raises(DeskError):
            run(agent_ready.withdraw(TENANT, application(), now=NOW))

    def test_a_grant_revoked_at_grantex_or_a_withdrawn_consent_blocks_sharing(self, offer):
        accept()
        grant_approved(offer)
        offer["enrolments"][0].consent_grant_id = "grnt_enrol_1"
        offer["grantex"].grants["grnt_enrol_1"]["status"] = "revoked"
        blocked = run(agent_ready.continue_enrolment(TENANT, application(), now=NOW))["enrolment"]
        assert blocked["status"] == "revoked" and blocked["refusal_reason"] == "consent_grant_revoked"
        assert offer["provider"].enrolments == {}
        offer["enrolments"].clear()
        offer["consents"].clear()
        accept()
        offer["consents"][0].status = "withdrawn"
        grant_approved(offer, "grnt_enrol_2")
        blocked = run(agent_ready.continue_enrolment(TENANT, application(), now=NOW))["enrolment"]
        assert blocked["refusal_reason"] == "consent_withdrawn" and offer["provider"].enrolments == {}

    def test_provider_refusal_and_outage_are_recorded_not_invented(self, offer, monkeypatch):
        accept()
        grant_approved(offer)

        offer["provider"].default_fixture = "unreachable"
        with pytest.raises(DeskError) as caught:
            run(agent_ready.continue_enrolment(TENANT, application(), now=NOW))
        assert caught.value.status == 503 and caught.value.code == AgentIdentityUnavailable.reason
        enrolment = offer["enrolments"][0]
        assert enrolment.status == "consent_pending" and enrolment.provider_ref is None
        offer["provider"].default_fixture = "active"

        class Refusing(mock_identity.MockAgentIdentityProvider):
            async def enrol_business(self, request, *, deadline):
                from connectors.framework.agent_identity_provider import AgentIdentityRefused

                raise AgentIdentityRefused(self.name, "no")

        monkeypatch.setattr(guard, "_configured_provider", lambda: Refusing())
        refused = run(agent_ready.continue_enrolment(TENANT, application(), now=NOW))["enrolment"]
        assert refused["status"] == "refused" and refused["provider_ref"] is None

    def test_only_a_grant_issued_for_this_enrolment_is_accepted(self, offer):
        accept()
        # An approval from before this enrolment began (an earlier, withdrawn one) is never reused.
        grant_approved(offer, "grnt_old", issued=NOW - timedelta(days=2))
        grant_approved(offer, "grnt_wrong_purpose", purpose="aml.cdd.onboarding")
        grant_approved(offer, "grnt_wrong_scope", grant_scopes=["agenticorg:other:read:x"])
        grant_approved(offer, "grnt_expired", expires=NOW - timedelta(minutes=1))
        pending = run(agent_ready.continue_enrolment(TENANT, application(), now=NOW))
        assert pending["enrolment"]["status"] == "consent_pending" and offer["provider"].enrolments == {}
        assert pending["consent_url"] == "https://grantex.example/c/1"
        grant_approved(offer, "grnt_new")
        shared = run(agent_ready.continue_enrolment(TENANT, application(), now=NOW))["enrolment"]
        assert shared["status"] == "shared" and shared["consent_grant_id"] == "grnt_new"

    def test_an_expired_grant_blocks_sharing(self, offer):
        accept()
        grant_approved(offer, "grnt_enrol_1", expires=NOW + timedelta(minutes=5))
        offer["enrolments"][0].consent_grant_id = "grnt_enrol_1"
        blocked = run(agent_ready.continue_enrolment(TENANT, application(), now=NOW + timedelta(minutes=10)))
        assert blocked["enrolment"]["refusal_reason"] == "consent_grant_expired"
        assert offer["provider"].enrolments == {}
        record = SimpleNamespace(consent_id=offer["consents"][0].id, consent_grant_id="grnt_enrol_1")
        offer["grantex"].grants["grnt_enrol_1"].pop("expiresAt")
        assert run(agent_ready.sharing_allowed(TENANT, record, offer["grantex"].service(), now=NOW)) == (
            "consent_grant_expired"
        )

    def test_withdrawal_revokes_every_enrolment_grant_the_person_holds(self, offer):
        accept()
        # Approved at Grantex but never picked up by continue: withdrawal must still revoke it.
        grant_approved(offer, "grnt_unclaimed")
        grant_approved(offer, "grnt_older", issued=NOW - timedelta(days=3))
        grant_approved(offer, "grnt_other", grant_scopes=["agenticorg:other:read:x"])
        result = run(agent_ready.withdraw(TENANT, application(), now=NOW))
        assert result["grant_revoked"] is True and result["revoke_failed"] is False
        grants = offer["grantex"].grants
        assert grants["grnt_unclaimed"]["status"] == grants["grnt_older"]["status"] == "revoked"
        assert grants["grnt_other"]["status"] == "active", "only enrolment grants"
        assert result["enrolment"]["refusal_reason"] == "consent_withdrawn"

    def test_a_failed_revocation_is_recorded_and_still_blocks_sharing(self, offer, monkeypatch):
        accept()
        grant_approved(offer)
        offer["grantex"].down = True
        result = run(agent_ready.withdraw(TENANT, application(), now=NOW))
        assert result["revoke_failed"] is True and result["grant_revoked"] is False
        assert result["enrolment"]["status"] == "revoked"
        assert result["enrolment"]["refusal_reason"] == "consent_withdrawn_grant_revoke_failed"
        offer["grantex"].down = False
        with pytest.raises(DeskError):
            run(agent_ready.continue_enrolment(TENANT, application(), now=NOW))
        assert offer["provider"].enrolments == {}
        offer["enrolments"].clear()
        offer["consents"].clear()
        accept()
        monkeypatch.setattr(grantex, "grantex_identity_service", lambda: None)
        result = run(agent_ready.withdraw(TENANT, application(), now=NOW))
        assert result["revoke_failed"] is True and result["enrolment"]["status"] == "revoked"

    def test_the_consent_page_is_kept_while_approval_is_pending(self, offer):
        first = accept()
        assert first["consent_url"] == "https://grantex.example/c/1"
        assert offer["enrolments"][0].consent_url == "https://grantex.example/c/1"
        again = accept()
        assert again["consent_url"] == "https://grantex.example/c/1", "a repeat accept shows it again"
        view = run(agent_ready.offer_view(TENANT, application(), now=NOW))
        assert view["status"] == "consent_pending" and view["consent_url"] == "https://grantex.example/c/1"
        assert "consent_url" not in view["enrolment"], "the evidence view never carries it"
        grant_approved(offer)
        run(agent_ready.continue_enrolment(TENANT, application(), now=NOW))
        assert run(agent_ready.offer_view(TENANT, application(), now=NOW))["consent_url"] is None
        assert agent_ready._safe_url("http://grantex.example/c/1") is None
        assert agent_ready._safe_url("javascript:alert(1)") is None

    def test_a_concurrent_second_accept_returns_the_live_enrolment(self, offer, monkeypatch):
        from sqlalchemy.exc import IntegrityError

        winner = SimpleNamespace(id=uuid.uuid4(), status="consent_pending", consent_id=uuid.uuid4(),
                                 consent_request_id="areq_0", consent_grant_id=None, shared_fields=[],
                                 verification_ref={}, verified_at=NOW, provider=None, provider_ref=None,
                                 provider_status=None, refusal_reason=None, created_at=NOW, shared_at=None,
                                 revoked_at=None, consent_url="https://grantex.example/c/0")  # fmt: skip
        race = {"added": False, "after": [winner]}

        async def lists(tid, aid):
            return race["after"] if race["added"] else []

        async def add(tid, *rows):
            race["added"] = True
            raise IntegrityError("INSERT", {}, Exception("ux_onboarding_enrolments_live"))

        monkeypatch.setattr(agent_ready, "list_enrolments", lists)
        monkeypatch.setattr(agent_ready, "_add", add)
        result = accept()
        assert result["enrolment"]["id"] == str(winner.id) and result["consent_url"] == "https://grantex.example/c/0"
        race.update(added=False, after=[])
        with pytest.raises(DeskError) as caught:
            accept()
        assert caught.value.code == "enrolment_in_progress" and caught.value.status == 409

    def test_continue_and_withdraw_read_nothing_while_unavailable(self, offer, monkeypatch):
        async def boom(tid, aid):
            raise AssertionError("no enrolment is read while the offer is unavailable")

        monkeypatch.setattr(agent_ready, "list_enrolments", boom)
        monkeypatch.setattr(settings, "onboarding_desk_agent_identity_enabled", False)
        for call in (agent_ready.continue_enrolment, agent_ready.withdraw):
            with pytest.raises(DeskError) as caught:
                run(call(TENANT, application(), now=NOW))
            assert (caught.value.status, caught.value.code) == (404, "agent_ready_not_offered")

    def test_enrolment_view(self):
        row = SimpleNamespace(id=uuid.uuid4(), status="shared", consent_id=uuid.uuid4(), consent_request_id="a",
                              consent_grant_id="g", shared_fields=["legal_name"], verification_ref={"case_ref": "c"},
                              verified_at=NOW, provider="mock", provider_ref="r", provider_status="received",
                              refusal_reason=None, created_at=NOW, shared_at=NOW, revoked_at=None)  # fmt: skip
        view = agent_ready.enrolment_view(row)
        assert view["shared_at"] == NOW.isoformat() and view["revoked_at"] is None


# ── MO-63 and MO-74: evidence and the report ─────────────────────────────────


class TestEvidenceAndReport:
    def test_identity_sections_verify_and_list_every_action(self, pack_inputs, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "onboarding_desk_evidence_signing_key", SEED)
        monkeypatch.setattr(settings, "onboarding_desk_agent_identity_enabled", True)
        stamp = {"agent_identity_id": "did:x", "attestation_sha256": "sha256:" + "a" * 64, "trust_tier": "t2"}
        pack_inputs["case"] = governed(
            agent_records=[{"agent": "business_underwriter", "run_id": "run1", "tool_calls": [
                {"sequence": 1, "tool": "verify_business", "outcome": "ok", "reason": "", "grant_id": "g",
                 "identity_presented": True, "agent_identity": stamp},
                {"sequence": 2, "tool": "ownership", "outcome": "ok"},
            ]}]
        )  # fmt: skip

        async def attempts(tid, lid):
            return [SimpleNamespace(id=uuid.uuid4(), channel="sms", kind="first_contact", status="sent",
                                    refusal_reason=None, provider_ref="SM1", attempt_no=1, created_at=NOW,
                                    sent_at=NOW, agent_identity_id="did:x", agent_attestation_sha256="sha256:b",
                                    agent_trust_tier="t2")]  # fmt: skip

        async def actions(tid, lid):
            return [SimpleNamespace(role="onboarding_outreach", action="outreach_send", channel="sms", outcome="held",
                                    reason="agent_identity_unknown", grant_id=None, created_at=NOW,
                                    agent_identity_id="did:x", agent_attestation_sha256="sha256:b",
                                    agent_trust_tier="t2")]  # fmt: skip

        async def enrolments(tid, aid):
            return [SimpleNamespace(id=uuid.uuid4(), status="shared", consent_id=uuid.uuid4(), consent_request_id="a",
                                    consent_grant_id="grnt_e", shared_fields=["legal_name"],
                                    verification_ref={"case_ref": "case_x"}, verified_at=NOW, provider="mock",
                                    provider_ref="mock-enrol-1", provider_status="received", refusal_reason=None,
                                    created_at=NOW, shared_at=NOW, revoked_at=None)]  # fmt: skip

        monkeypatch.setattr(store, "list_attempts", attempts)
        monkeypatch.setattr(store, "list_agent_actions", actions)
        monkeypatch.setattr(agent_ready, "list_enrolments", enrolments)
        pack_inputs["row"].transcript_ciphertext = json.dumps(
            [{"from": "desk", "text": "ok", "at": NOW.isoformat(), "channel": "sms", "agent_identity": stamp}]
        )
        pack = run(evidence.build_pack(TENANT, APP, now=NOW))
        assert set(pack["sections"]) == {*evidence.SECTIONS, *evidence.IDENTITY_SECTIONS}
        section = pack["sections"]["agent_identity"]
        assert (
            section["actions"][0]["outcome"] == "held" and section["actions"][0]["agent_identity"]["trust_tier"] == "t2"
        )
        assert section["contact_attempts"][0]["agent_identity_id"] == "did:x"
        assert section["replies"][0]["trust_tier"] == "t2"
        assert [c["tool"] for c in section["case_tool_calls"]] == ["verify_business"]
        assert section["case_tool_calls"][0]["identity_presented"] is True
        assert pack["sections"]["enrolment"][0]["provider_ref"] == "mock-enrol-1"
        key = evidence.public_key()
        assert evidence.verify(pack, key["public_key"]) == []
        tampered = json.loads(json.dumps(pack, default=str))
        tampered["sections"]["enrolment"][0]["shared_fields"] = ["legal_name", "owners"]
        assert evidence.verify(tampered, key["public_key"]) == ["section enrolment does not match its digest"]
        tampered = json.loads(json.dumps(pack, default=str))
        del tampered["sections"]["agent_identity"]
        assert "sections differ from the manifest" in evidence.verify(tampered, key["public_key"])

        from scripts.verify_onboarding_evidence import main

        path = tmp_path / "pack.json"
        path.write_text(json.dumps(pack, default=str), encoding="utf-8")
        assert main([str(path), "--public-key", key["public_key"]]) == 0
        path.write_text(json.dumps(tampered), encoding="utf-8")
        assert main([str(path), "--public-key", key["public_key"]]) == 1
        monkeypatch.setattr(settings, "onboarding_desk_agent_identity_enabled", False)
        off = run(evidence.build_pack(TENANT, APP, now=NOW))
        assert set(off["sections"]) == set(evidence.SECTIONS), "off: the pack is Release 1's"

    def test_the_report_counts_agent_ready_merchants(self):
        from tests.unit.test_onboarding_desk_cases import PERIOD, app_row, lead

        first, second = lead(), lead()
        apps = [app_row(id=uuid.uuid4(), lead_id=first.id), app_row(id=uuid.uuid4(), lead_id=second.id)]
        enrolments = [
            SimpleNamespace(application_id=apps[0].id, status="shared"),
            SimpleNamespace(application_id=apps[0].id, status="revoked"),
            SimpleNamespace(application_id=apps[1].id, status="consent_pending"),
            SimpleNamespace(application_id=uuid.uuid4(), status="shared"),
        ]
        report = reports.summarise(PERIOD, [first, second], apps, [], [], enrolments)
        assert report["funnel"]["agent_ready"] == 1
        assert "agent_ready" not in reports.summarise(PERIOD, [first, second], apps, [], [])["funnel"]

    def test_summary_reads_enrolments_only_with_identity_on(self, db, monkeypatch):
        from tests.unit.test_onboarding_desk_cases import PERIOD, app_row, lead

        first = lead()
        app = app_row(id=uuid.uuid4(), lead_id=first.id)
        monkeypatch.setattr(settings, "onboarding_desk_agent_identity_enabled", True)
        db([first], [app], [], [SimpleNamespace(application_id=app.id, status="shared")])
        assert run(reports.summary(TENANT, PERIOD))["funnel"]["agent_ready"] == 1


# ── Routes ───────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api import deps
    from api.v1 import onboarding_desk as api

    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[deps.get_current_tenant] = lambda: str(TENANT)
    app.dependency_overrides[deps.get_current_user] = lambda: {"sub": str(uuid.uuid4())}
    app.dependency_overrides[deps.require_tenant_admin.dependency] = lambda: None
    monkeypatch.setattr(settings, "onboarding_desk_enabled", True)

    async def applicant(request):
        return TENANT, application()

    monkeypatch.setattr(api, "_applicant", applicant)
    return TestClient(app)


class TestRoutes:
    def test_settings_routes(self, client, monkeypatch):
        saved = {}

        async def get_settings(tid):
            return saved.get("value", agent_ready.AgentReadySettings())

        async def put_settings(tid, raw, *, actor):
            saved["value"] = agent_ready.settings_from(raw)
            return saved["value"]

        monkeypatch.setattr(agent_ready, "get_settings", get_settings)
        monkeypatch.setattr(agent_ready, "put_settings", put_settings)
        base = "/api/v1/onboarding-desk"
        first = client.get(f"{base}/agent-ready-settings").json()
        assert first["enabled"] is False and first["available"] is False
        assert client.put(f"{base}/agent-ready-settings", json={"enabled": True}).json()["enabled"] is True
        assert client.put(f"{base}/agent-ready-settings", json={"enabled": 1}).status_code == 422
        monkeypatch.setattr(settings, "onboarding_desk_enabled", False)
        assert client.get(f"{base}/agent-ready-settings").status_code == 404

    def test_applicant_routes(self, client, offer):
        base = "/api/v1/onboarding-desk/apply/agent-ready"
        body = {"name": "Avery Example", "role": "Owner", "contact": "avery@northwind.example", "accept": False}
        assert client.post(f"{base}/accept", json=body).status_code == 422
        accepted = client.post(f"{base}/accept", json={**body, "accept": True}).json()
        assert accepted["enrolment"]["status"] == "consent_pending"
        # The routes run on the real clock.
        real_now = datetime.now(UTC)
        grant_approved(offer, issued=real_now + timedelta(minutes=1), expires=real_now + timedelta(days=1))
        assert client.post(f"{base}/continue").json()["enrolment"]["status"] == "shared"
        assert client.post(f"{base}/withdraw").json()["enrolment"]["status"] == "revoked"
        assert client.post(f"{base}/withdraw").status_code == 404

    def test_the_applicant_state_carries_the_offer_only_when_it_applies(self, client, monkeypatch):
        views = {"value": None}

        async def state(tid, row, *, operator=False):
            return {"status": "submitted"}

        async def offer_view(tid, row, *, now=None):
            return views["value"]

        monkeypatch.setattr(applications, "state", state)
        monkeypatch.setattr(agent_ready, "offer_view", offer_view)
        url = "/api/v1/onboarding-desk/apply/application"
        assert client.get(url).json() == {"status": "submitted"}
        views["value"] = {"status": "offered", "offer": "x", "enrolment": None}
        assert client.get(url).json()["agent_ready"]["status"] == "offered"


# ── Migration ────────────────────────────────────────────────────────────────


class TestMigration:
    def test_v6z121(self, monkeypatch):
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z121_desk_agent_identity.py"
        spec = importlib.util.spec_from_file_location("v6_z121_desk_agent_identity", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        assert module.revision == "v6z121_desk_agent_identity"
        assert module.down_revision == "v6z117_insights_benchmarks"
        statements = []
        monkeypatch.setattr(module.op, "execute", lambda sql: statements.append(" ".join(str(sql).split())))
        module.upgrade()
        joined = "\n".join(statements)
        for column in ("agent_identity_id", "agent_attestation_sha256", "agent_trust_tier", "subject_ciphertext"):
            assert f"ADD COLUMN IF NOT EXISTS {column}" in joined
        for table in ("onboarding_desk_agent_actions", "onboarding_desk_enrolments"):
            assert f"CREATE TABLE IF NOT EXISTS {table}" in joined
            assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in joined
            assert f"CREATE POLICY {table}_tenant_isolation" in joined
        assert "'voice','web'" in joined and "WHERE status <> 'revoked'" in joined
        assert "consent_url VARCHAR(2048) NULL" in joined
        statements.clear()
        module.downgrade()
        # The web consents are removed with the forced row-level security lifted, then it is forced again.
        delete = statements.index("DELETE FROM onboarding_desk_consents WHERE channel = 'web';")
        assert statements[delete - 1] == "ALTER TABLE onboarding_desk_consents NO FORCE ROW LEVEL SECURITY;"
        assert statements[delete + 1] == "ALTER TABLE onboarding_desk_consents FORCE ROW LEVEL SECURITY;"
        assert statements.index("ALTER TABLE onboarding_desk_consents ADD CONSTRAINT ck_onboarding_consents_channel "
                                "CHECK (channel IN ('sms','email','whatsapp','voice'));") > delete  # fmt: skip
        assert any("DROP TABLE IF EXISTS onboarding_desk_enrolments" in s for s in statements)
        assert any("DROP COLUMN IF EXISTS agent_identity_id" in s for s in statements)

    def test_the_models_match(self):
        from core.models.onboarding_desk import (
            OnboardingAgentAction,
            OnboardingConsent,
            OnboardingContactAttempt,
            OnboardingEnrolment,
        )

        assert {"agent_identity_id", "agent_attestation_sha256", "agent_trust_tier"} <= set(
            OnboardingContactAttempt.__table__.columns.keys()
        )
        assert "subject_ciphertext" in OnboardingConsent.__table__.columns.keys()
        assert OnboardingAgentAction.__tablename__ == "onboarding_desk_agent_actions"
        assert OnboardingEnrolment.__tablename__ == "onboarding_desk_enrolments"
