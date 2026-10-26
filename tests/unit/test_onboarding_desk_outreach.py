# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Onboarding desk outreach: branding, signed links, messages, senders, the engine, the store and the routes."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.config import settings
from core.onboarding_desk import branding as branding_mod
from core.onboarding_desk import messages, outreach, rules, senders, store, tokens
from core.onboarding_desk.branding import Branding, Sender
from core.onboarding_desk.store import DeskError

TENANT = uuid.uuid4()
LEAD = uuid.uuid4()
NOW = datetime(2026, 10, 27, 17, 0, tzinfo=UTC)

BRANDING_RAW = {
    "partner_name": "Northwind Payments",
    "onboarding_url": "https://apply.northwind.example/start",
    "postal_address": "100 Example Street, Toronto ON M5V 0A1",
    "auto_contact": True,
    "first_contact_delay_minutes": 5,
    "senders": {
        "sms": {"connector": "registry-twilio", "sender": "+1 416 555 0100"},
        "email": {"connector": "registry-sendgrid", "sender": "Hello@Northwind.example"},
        "whatsapp": {"connector": "registry-whatsapp", "template": "onboarding_invite", "language": "en_US"},
    },
}


def full_branding() -> Branding:
    return branding_mod.branding_from(BRANDING_RAW)


# ── Branding ─────────────────────────────────────────────────────────────────


class TestBranding:
    def test_valid_branding_normalises_senders(self):
        branding = full_branding()
        assert branding.senders["sms"].sender == "+14165550100"
        assert branding.senders["email"].sender == "hello@northwind.example"
        assert branding.to_dict()["senders"]["whatsapp"] == {
            "connector": "registry-whatsapp", "template": "onboarding_invite", "language": "en_US",
        }
        assert branding_mod.branding_from(branding.to_dict()) == branding
        assert all(branding.missing_for(c) is None for c in ("sms", "email", "whatsapp"))

    def test_missing_pieces(self):
        assert Branding().missing_for("sms") == "branding_partner_name_missing"
        assert Branding(partner_name="P").missing_for("sms") == "branding_onboarding_url_missing"
        base = Branding(partner_name="P", onboarding_url="https://a.example")
        assert base.missing_for("sms") == "sender_not_configured"
        with_email = Branding(partner_name="P", onboarding_url="https://a.example",
                              senders={"email": Sender("registry-sendgrid", "a@a.example")})
        assert with_email.missing_for("email") == "branding_postal_address_missing"
        assert full_branding().missing_for("voice") == "sender_not_configured"

    @pytest.mark.parametrize(
        ("overrides", "field"),
        [
            ({"partner_name": "x" * 61}, "partner_name"),
            ({"postal_address": "x" * 201}, "postal_address"),
            ({"auto_contact": "yes"}, "auto_contact"),
            ({"first_contact_delay_minutes": 5000}, "first_contact_delay_minutes"),
            ({"onboarding_url": "http://apply.example/start"}, "onboarding_url"),
            ({"onboarding_url": "https://apply.example/start?x=1"}, "onboarding_url"),
            ({"onboarding_url": "https://" + "a" * 300 + ".example"}, "onboarding_url"),
            ({"senders": {"voice": {}}}, "senders"),
            ({"senders": {"sms": "registry-twilio"}}, "senders.sms"),
            ({"senders": {"sms": {"connector": "twilio", "sender": "+14165550100"}}}, "senders.sms.connector"),
            ({"senders": {"sms": {"connector": "registry-twilio", "sender": "12"}}}, "senders.sms.sender"),
            ({"senders": {"sms": {"connector": "registry-twilio"}}}, "senders.sms.sender"),
            ({"senders": {"email": {"connector": "registry-sendgrid", "sender": "bad"}}}, "senders.email.sender"),
            ({"senders": {"email": {"connector": "registry-sendgrid"}}}, "senders.email.sender"),
            ({"senders": {"whatsapp": {"connector": "registry-whatsapp", "template": "Bad Name", "language": "en"}}},
             "senders.whatsapp.template"),
            ({"senders": {"whatsapp": {"connector": "registry-whatsapp", "template": "ok", "language": "english"}}},
             "senders.whatsapp.language"),
        ],
    )
    def test_invalid_branding(self, overrides, field):
        with pytest.raises(branding_mod.BrandingError) as caught:
            branding_mod.branding_from({**BRANDING_RAW, **overrides})
        assert caught.value.field == field

    def test_localhost_http_only_in_local_environments(self, monkeypatch):
        monkeypatch.setattr(settings, "env", "local")
        assert branding_mod.branding_from({"onboarding_url": "http://localhost:3000/apply/"}).onboarding_url == (
            "http://localhost:3000/apply"
        )
        monkeypatch.setattr(settings, "env", "production")
        with pytest.raises(branding_mod.BrandingError):
            branding_mod.branding_from({"onboarding_url": "http://localhost:3000/apply"})


# ── Signed links ─────────────────────────────────────────────────────────────


class TestTokens:
    def test_round_trip(self):
        token = tokens.sign("apply", TENANT, LEAD, now=NOW)
        claims = tokens.verify(token, "apply", now=NOW + timedelta(days=1))
        assert claims.tenant_id == TENANT and claims.lead_id == LEAD and claims.purpose == "apply"

    def test_refusals(self):
        token = tokens.sign("apply", TENANT, LEAD, now=NOW)
        with pytest.raises(tokens.TokenError, match="signature"):
            tokens.verify(token, "unsubscribe", now=NOW)
        with pytest.raises(tokens.TokenError, match="expired"):
            tokens.verify(token, "apply", now=NOW + timedelta(days=15))
        payload, mac = token.split(".")
        with pytest.raises(tokens.TokenError, match="signature"):
            tokens.verify(payload + "x." + mac, "apply", now=NOW)
        for bad in ("", "nodot", "a.b.c", "x" * 600):
            with pytest.raises(tokens.TokenError, match="malformed"):
                tokens.verify(bad, "apply", now=NOW)
        with pytest.raises(ValueError):
            tokens.sign("login", TENANT, LEAD)

    def test_a_correctly_signed_payload_naming_another_purpose_is_refused(self):
        body = json.dumps({"p": "unsubscribe", "t": str(TENANT), "l": str(LEAD), "e": int(NOW.timestamp()) + 999})
        payload = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
        forged = f"{payload}.{tokens._mac(payload, 'apply')}"
        with pytest.raises(tokens.TokenError, match="purpose"):
            tokens.verify(forged, "apply", now=NOW)
        garbage = base64.urlsafe_b64encode(b"not json").decode().rstrip("=")
        with pytest.raises(tokens.TokenError, match="malformed"):
            tokens.verify(f"{garbage}.{tokens._mac(garbage, 'apply')}", "apply", now=NOW)


# ── Messages ─────────────────────────────────────────────────────────────────


class TestMessages:
    def test_sms_in_english_and_french(self):
        branding = full_branding()
        en = messages.render("first_contact", "sms", "en", branding, contact_name="Avery Example", link="https://l.example/x")
        assert en.text.startswith("Northwind Payments: Hi Avery,") and "AI assistant" in en.text
        assert en.text.endswith("Reply STOP to opt out.") and "https://l.example/x" in en.text
        fr = messages.render("reminder", "sms", "fr", branding, contact_name="", link="https://l.example/x")
        assert "assistant IA" in fr.text and "ARRET" in fr.text and fr.text.startswith("Northwind Payments :")

    def test_email_carries_unsubscribe_address_and_escaped_html(self):
        branding = Branding(partner_name="A&B <Pay>", onboarding_url="https://a.example", postal_address="1 Road")
        message = messages.render(
            "first_contact", "email", "en", branding, contact_name="Avery", link="https://a.example?t=1&x=2",
            unsubscribe_link="https://api.example/u?t=9",
        )
        assert message.subject == "Finish your A&B <Pay> application"
        assert "Unsubscribe: https://api.example/u?t=9" in message.text and "1 Road" in message.text
        assert "<Pay>" not in message.html and "A&amp;B &lt;Pay&gt;" in message.html
        assert '<a href="https://a.example?t=1&amp;x=2">' in message.html
        assert '<a href="https://api.example/u?t=9">' in message.html
        fr = messages.render("reminder", "email", "fr", branding, contact_name="", link="https://a.example",
                             unsubscribe_link="https://api.example/u")
        assert fr.subject.startswith("Votre demande") and "Se désabonner" in fr.text

    def test_whatsapp_uses_template_parameters(self):
        message = messages.render("first_contact", "whatsapp", "en", full_branding(), contact_name="x", link="L")
        assert message.template_params == ("Northwind Payments", "L") and message.text == ""

    def test_refusals(self):
        branding = full_branding()
        with pytest.raises(ValueError, match="kind"):
            messages.render("nag", "sms", "en", branding, contact_name="", link="L")
        with pytest.raises(ValueError, match="no message"):
            messages.render("first_contact", "voice", "en", branding, contact_name="", link="L")
        with pytest.raises(ValueError, match="too long"):
            long_link = "https://x.example/" + "a" * 500
            messages.render("first_contact", "sms", "en", branding, contact_name="", link=long_link)


# ── Senders ──────────────────────────────────────────────────────────────────


class FakeConnector:
    def __init__(self, result=None, error=None, disconnect_error=False):
        self.result = result if result is not None else {"sid": "SM123"}
        self.error = error
        self.calls = []
        self.disconnect_error = disconnect_error

    async def _done(self, name, **params):
        self.calls.append((name, params))
        if self.error:
            raise self.error
        return self.result

    async def send_sms(self, **params):
        return await self._done("send_sms", **params)

    async def send_email(self, **params):
        return await self._done("send_email", **params)

    async def send_template_message(self, **params):
        return await self._done("send_template_message", **params)

    async def disconnect(self):
        if self.disconnect_error:
            raise RuntimeError("socket")


class TestSenders:
    def run(self, monkeypatch, connector, channel, message):
        async def fake(tenant_id, sender):
            return connector

        monkeypatch.setattr(senders, "_connector", fake)
        return asyncio.run(senders.send(TENANT, channel, full_branding(), "+14165550123", message))

    def test_each_channel_calls_its_tool(self, monkeypatch):
        sms = FakeConnector()
        assert self.run(monkeypatch, sms, "sms", messages.Message(text="hi")) == senders.SendResult(True, "SM123")
        assert sms.calls[0] == ("send_sms", {"to": "+14165550123", "body": "hi", "from_number": "+14165550100"})
        email = FakeConnector(result={"status": "accepted"})
        result = self.run(monkeypatch, email, "email", messages.Message(text="t", subject="s", html="<p>t</p>"))
        assert result.ok and result.provider_ref is None
        assert email.calls[0][1]["from_email"] == "hello@northwind.example" and email.calls[0][1]["from_name"]
        whatsapp = FakeConnector(result={"messages": [{"id": "wamid.1"}]})
        result = self.run(monkeypatch, whatsapp, "whatsapp", messages.Message(text="", template_params=("P", "L")))
        assert result.provider_ref == "wamid.1"
        call = whatsapp.calls[0][1]
        assert call["to"] == "14165550123" and call["template_name"] == "onboarding_invite"
        assert call["components"][0]["parameters"] == [{"type": "text", "text": "P"}, {"type": "text", "text": "L"}]

    def test_failures_are_returned_not_raised(self, monkeypatch):
        assert self.run(monkeypatch, FakeConnector(result={"error": "21610 unsubscribed"}), "sms",
                        messages.Message(text="x")).error.startswith("provider_error:")
        assert self.run(monkeypatch, FakeConnector(error=TimeoutError()), "sms",
                        messages.Message(text="x")).error == "send_failed:TimeoutError"
        assert self.run(monkeypatch, FakeConnector(disconnect_error=True), "sms", messages.Message(text="x")).ok
        assert self.run(monkeypatch, None, "sms", messages.Message(text="x")).error == "sender_connector_not_configured"
        no_sms = Branding(partner_name="P", onboarding_url="https://a.example")
        assert asyncio.run(senders.send(TENANT, "sms", no_sms, "+1", messages.Message(text="x"))).error == (
            "sender_not_configured"
        )
        assert senders._ref({"id": "x"}) == "x" and senders._ref({}) is None

    def test_connector_resolution(self, monkeypatch):
        import api.v1.agents as agents_api
        from connectors.registry import ConnectorRegistry

        connected = {}

        class Fake:
            def __init__(self, config):
                connected["config"] = config

            async def connect(self):
                connected["connected"] = True

        async def none_resolved(tenant_id, ids, a, b):
            return {}, []

        async def resolved(tenant_id, ids, a, b):
            return {"account_sid": "AC1"}, ["twilio"]

        monkeypatch.setattr(agents_api, "_resolve_connector_configs", none_resolved)
        assert asyncio.run(senders._connector(TENANT, Sender("registry-twilio", "+1"))) is None
        monkeypatch.setattr(agents_api, "_resolve_connector_configs", resolved)
        monkeypatch.setattr(ConnectorRegistry, "get", classmethod(lambda cls, name: Fake))
        instance = asyncio.run(senders._connector(TENANT, Sender("registry-twilio", "+1")))
        assert isinstance(instance, Fake) and connected == {"config": {"account_sid": "AC1"}, "connected": True}
        monkeypatch.setattr(ConnectorRegistry, "get", classmethod(lambda cls, name: None))
        assert asyncio.run(senders._connector(TENANT, Sender("registry-twilio", "+1"))) is None


# ── The engine ───────────────────────────────────────────────────────────────


def lead_row(**overrides):
    base = {"id": LEAD, "status": "new", "locale": "en", "contact_ciphertext": "enc"}
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def engine(monkeypatch):
    state = {
        "lead": lead_row(),
        "policy": rules.Policy(channels=("sms", "email")),
        "branding": full_branding(),
        "decisions": {},
        "send": senders.SendResult(True, "SM1"),
        "finished": [],
        "refusals": [],
        "next": [],
        "sent": [],
    }

    async def get_lead(tid, lid):
        return state["lead"], []

    async def get_policy(tid):
        return state["policy"]

    async def get_branding(tid):
        return state["branding"]

    async def decision(tid, lid, channel, *, now, is_reminder=False):
        return state["decisions"].get(channel, rules.Decision(True, "allowed"))

    async def claim(tid, lid, channel, kind, *, now):
        return SimpleNamespace(id=uuid.uuid4())

    async def finish(tid, attempt_id, **kwargs):
        state["finished"].append(kwargs)

    async def decrypt(lead):
        return json.dumps({"contact_name": "Avery", "email": "a@a.example", "phone": "+14165550123"})

    async def refusal(tid, lid, channel, kind, reason):
        state["refusals"].append((channel, reason))

    async def set_next(tid, lid, at):
        state["next"].append(at)

    async def send(tid, channel, branding, to, message):
        state["sent"].append((channel, to, message))
        return state["send"]

    for name, fn in (("get_lead", get_lead), ("get_policy", get_policy), ("get_branding", get_branding),
                     ("contact_decision", decision), ("claim_attempt", claim), ("finish_attempt", finish),
                     ("decrypt_contact", decrypt), ("record_refusal", refusal), ("set_next_contact", set_next)):
        monkeypatch.setattr(store, name, fn)
    monkeypatch.setattr(senders, "send", send)
    return state


class TestEngine:
    def test_sends_on_the_first_allowed_channel_with_links(self, engine):
        outcome = asyncio.run(outreach.contact_lead(TENANT, LEAD, now=NOW))
        assert outcome.sent and outcome.channel == "sms" and outcome.reason == "sent"
        channel, to, message = engine["sent"][0]
        assert to == "+14165550123" and "?t=" in message.text and "Reply STOP" in message.text
        claims = tokens.verify(message.text.split("?t=")[1].split(" ")[0], "apply", now=NOW)
        assert claims.lead_id == LEAD
        assert engine["finished"][0]["ok"] and engine["finished"][0]["next_contact_at"] == NOW + timedelta(hours=24)

    def test_email_gets_an_unsubscribe_link(self, engine):
        engine["policy"] = rules.Policy(channels=("email",))
        outcome = asyncio.run(outreach.contact_lead(TENANT, LEAD, now=NOW))
        message = engine["sent"][0][2]
        assert outcome.channel == "email" and "/api/v1/onboarding-desk/unsubscribe?t=" in message.text

    def test_closed_leads_and_missing_senders(self, engine):
        engine["lead"] = lead_row(status="opted_out")
        assert asyncio.run(outreach.contact_lead(TENANT, LEAD, now=NOW)).reason == "lead_opted_out"
        engine["lead"] = lead_row()
        engine["branding"] = Branding(partner_name="P", onboarding_url="https://a.example")
        assert asyncio.run(outreach.contact_lead(TENANT, LEAD, now=NOW)).reason == "sender_not_configured"
        with pytest.raises(DeskError):
            asyncio.run(outreach.contact_lead(TENANT, LEAD, kind="nag", now=NOW))

    def test_refusals_schedule_the_next_try(self, engine):
        later = NOW + timedelta(hours=3)
        sooner = NOW + timedelta(hours=1)
        engine["decisions"] = {"sms": rules.Decision(False, "outside_hours", later),
                               "email": rules.Decision(False, "outside_hours", sooner)}
        outcome = asyncio.run(outreach.contact_lead(TENANT, LEAD, now=NOW))
        assert not outcome.sent and outcome.next_allowed_at == sooner and engine["next"] == [sooner]
        assert engine["refusals"] == []
        engine["decisions"] = {"sms": rules.Decision(False, "no_consent")}
        outcome = asyncio.run(outreach.contact_lead(TENANT, LEAD, channel="sms", now=NOW))
        assert outcome.reason == "no_consent" and engine["refusals"] == [("sms", "no_consent")]
        assert engine["next"][-1] == NOW + timedelta(hours=24)
        engine["lead"] = lead_row(status="contacted")
        asyncio.run(outreach.contact_lead(TENANT, LEAD, channel="sms", kind="reminder", now=NOW))
        assert engine["next"][-1] is None

    def test_branding_gaps_are_refusals(self, engine):
        engine["branding"] = Branding(partner_name="P", onboarding_url="https://a.example",
                                      senders={"email": Sender("registry-sendgrid", "a@a.example")})
        engine["policy"] = rules.Policy(channels=("email",))
        assert asyncio.run(outreach.contact_lead(TENANT, LEAD, now=NOW)).reason == "branding_postal_address_missing"

    def test_send_failure_and_invalid_message(self, engine):
        engine["send"] = senders.SendResult(False, error="provider_error:x")
        outcome = asyncio.run(outreach.contact_lead(TENANT, LEAD, now=NOW))
        assert not outcome.sent and outcome.reason == "provider_error:x" and engine["finished"][-1]["ok"] is False
        engine["branding"] = branding_mod.branding_from(
            {**BRANDING_RAW, "onboarding_url": "https://apply.example/" + "a" * 270}
        )
        engine["send"] = senders.SendResult(True, "x")
        outcome = asyncio.run(outreach.contact_lead(TENANT, LEAD, now=NOW))
        assert outcome.reason == "message_invalid" and engine["finished"][-1]["error"] == "message_invalid"

    def test_run_due_and_sweep(self, monkeypatch, engine):
        due = [lead_row(id=uuid.uuid4()), lead_row(id=uuid.uuid4(), status="contacted"), lead_row(id=uuid.uuid4())]
        kinds = []

        async def due_leads(tid, *, now, first_contact_delay, limit):
            assert first_contact_delay == timedelta(minutes=5) and limit == 50
            return due

        results = iter([outreach.Outcome(True, "sent"), outreach.Outcome(False, "outside_hours", next_allowed_at=NOW),
                        DeskError(409, "contact_in_progress", "busy")])

        async def contact(tid, lid, *, kind, now):
            kinds.append(kind)
            item = next(results)
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr(store, "due_leads", due_leads)
        monkeypatch.setattr(outreach, "contact_lead", contact)
        counts = asyncio.run(outreach.run_due(TENANT, now=NOW))
        assert counts == {"due": 3, "sent": 1, "deferred": 1, "refused": 1}
        assert kinds == ["first_contact", "reminder", "first_contact"]
        other = uuid.uuid4()

        async def tenants():
            return [TENANT, other]

        async def branding_for(tid):
            return full_branding() if tid == TENANT else Branding()

        async def run(tid, *, now=None):
            return {"due": 1, "sent": 1, "deferred": 0, "refused": 0}

        monkeypatch.setattr(store, "tenant_ids_with_desk", tenants)
        monkeypatch.setattr(store, "get_branding", branding_for)
        monkeypatch.setattr(outreach, "run_due", run)
        assert asyncio.run(outreach.sweep(now=NOW)) == {"tenants": 1, "sent": 1, "deferred": 0, "refused": 0}


# ── Store additions ──────────────────────────────────────────────────────────


class FakeResult:
    def __init__(self, rows):
        self.rows = list(rows)

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        return list(self.rows)

    def scalar(self):
        return self.rows[0] if self.rows else None


class FakeSession:
    def __init__(self, results):
        self.results = [list(r) for r in results]
        self.added = []

    async def execute(self, statement):
        return FakeResult(self.results.pop(0) if self.results else [])

    def add(self, row):
        self.added.append(row)


@pytest.fixture
def db(monkeypatch):
    def install(*results):
        session = FakeSession(results)

        @asynccontextmanager
        async def fake(tenant_id):
            yield session

        monkeypatch.setattr(store, "_session", fake)
        return session

    async def decrypt(ciphertext):
        return ciphertext

    monkeypatch.setattr(store, "_decrypt", decrypt)
    return install


class TestStoreAdditions:
    def test_branding_round_trip_and_merge(self, db):
        db([])
        assert asyncio.run(store.get_branding(TENANT)) == Branding()
        session = db([])
        asyncio.run(store.put_branding(TENANT, BRANDING_RAW, actor="u"))
        assert session.added[0].settings["branding"]["partner_name"] == "Northwind Payments"
        row = SimpleNamespace(settings={"start": "09:00"}, updated_by="", updated_at=None)
        db([row])
        asyncio.run(store.put_branding(TENANT, BRANDING_RAW, actor="u"))
        assert row.settings["start"] == "09:00" and row.settings["branding"]["auto_contact"] is True
        db([SimpleNamespace(settings=dict(row.settings))])
        assert asyncio.run(store.get_branding(TENANT)).partner_name == "Northwind Payments"
        db([row])
        asyncio.run(store.put_policy(TENANT, {"weekends": False}, actor="u"))
        assert row.settings["branding"]["partner_name"] == "Northwind Payments" and row.settings["weekends"] is False
        db([])
        with pytest.raises(DeskError) as caught:
            asyncio.run(store.put_branding(TENANT, {"partner_name": "x" * 99}, actor="u"))
        assert caught.value.code == "branding_invalid"

    def test_claim_attempt(self, db):
        db([])
        with pytest.raises(DeskError) as caught:
            asyncio.run(store.claim_attempt(TENANT, LEAD, "sms", "first_contact", now=NOW))
        assert caught.value.status == 404
        db([lead_row()], [1])
        with pytest.raises(DeskError) as caught:
            asyncio.run(store.claim_attempt(TENANT, LEAD, "sms", "first_contact", now=NOW))
        assert caught.value.code == "contact_in_progress"
        session = db([lead_row()], [0], [2])
        attempt = asyncio.run(store.claim_attempt(TENANT, LEAD, "sms", "reminder", now=NOW))
        assert attempt.status == "queued" and attempt.attempt_no == 3 and session.added == [attempt]

    def test_finish_and_refusal(self, db):
        attempt = SimpleNamespace(id=uuid.uuid4(), lead_id=LEAD, status="queued", provider_ref=None,
                                  refusal_reason=None, sent_at=None, channel="sms", kind="first_contact",
                                  attempt_no=1, created_at=NOW)
        lead = SimpleNamespace(status="new", contacted_at=None, next_contact_at=None, updated_at=None)
        db([attempt], [lead])
        asyncio.run(store.finish_attempt(TENANT, attempt.id, ok=True, provider_ref="SM1", error=None,
                                         next_contact_at=NOW))
        assert attempt.status == "sent" and lead.status == "contacted" and lead.next_contact_at == NOW
        failed = SimpleNamespace(**{**attempt.__dict__, "status": "queued"})
        later = SimpleNamespace(status="contacted", contacted_at=None, next_contact_at=None, updated_at=None)
        db([failed], [later])
        asyncio.run(store.finish_attempt(TENANT, failed.id, ok=False, provider_ref=None, error="send_failed:X"))
        assert failed.status == "failed" and failed.refusal_reason == "send_failed:X"
        assert later.next_contact_at is not None and later.status == "contacted"
        db([])
        asyncio.run(store.finish_attempt(TENANT, uuid.uuid4(), ok=True, provider_ref=None, error=None))
        session = db()
        asyncio.run(store.record_refusal(TENANT, LEAD, "sms", "first_contact", "no_consent"))
        assert session.added[0].status == "refused" and session.added[0].refusal_reason == "no_consent"
        assert store.attempt_view(attempt)["status"] == "sent"

    def test_scheduling_due_engaged_and_tenants(self, db, monkeypatch):
        lead = SimpleNamespace(next_contact_at=NOW)
        db([lead])
        asyncio.run(store.set_next_contact(TENANT, LEAD, None))
        assert lead.next_contact_at is None
        db([])
        asyncio.run(store.set_next_contact(TENANT, LEAD, NOW))
        db([lead])
        assert asyncio.run(store.due_leads(TENANT, now=NOW, first_contact_delay=timedelta(0), limit=5)) == [lead]
        db([lead])
        assert asyncio.run(store.list_attempts(TENANT, LEAD)) == [lead]
        engaged = SimpleNamespace(status="contacted", updated_at=None)
        db([engaged])
        assert asyncio.run(store.mark_engaged(TENANT, "+1 416 555 0123")) == 1 and engaged.status == "engaged"
        assert asyncio.run(store.mark_engaged(TENANT, "not a phone")) == 0
        assert asyncio.run(store.mark_engaged(TENANT, "")) == 0
        import core.database as database

        @asynccontextmanager
        async def platform():
            yield FakeSession([[TENANT]])

        monkeypatch.setattr(database, "get_platform_session", platform)
        assert asyncio.run(store.tenant_ids_with_desk()) == [TENANT]
        assert asyncio.run(store.decrypt_contact(SimpleNamespace(contact_ciphertext="c"))) == "c"

    def test_recording_consent_makes_the_lead_due(self, db, monkeypatch):
        from core.onboarding_desk import leads

        cleared = []

        async def set_next(tid, lid, at):
            cleared.append(at)

        monkeypatch.setattr(store, "set_next_contact", set_next)
        consent = leads.consent_from({"channel": "sms", "wording": "I agree to texts about payments.",
                                      "method": "web_form", "captured_at": "2026-10-20T10:00:00Z"}, now=NOW)
        db([SimpleNamespace(id=LEAD, status="new")], [])
        asyncio.run(store.record_consent(TENANT, LEAD, consent, actor="u"))
        db([SimpleNamespace(id=LEAD, status="contacted")], [])
        asyncio.run(store.record_consent(TENANT, LEAD, consent, actor="u"))
        assert cleared[0] is None and isinstance(cleared[1], datetime)


# ── Routes ───────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    from api import deps
    from api.v1 import onboarding_desk as api

    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[deps.get_current_tenant] = lambda: str(TENANT)
    app.dependency_overrides[deps.get_current_user] = lambda: {"sub": str(uuid.uuid4())}
    app.dependency_overrides[deps.require_tenant_admin.dependency] = lambda: None
    monkeypatch.setattr(settings, "onboarding_desk_enabled", True)
    return TestClient(app)


def twilio_signature(url: str, params: dict[str, str], token: str) -> str:
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()


class TestOutreachRoutes:
    def test_branding_contact_attempts_and_run(self, client, monkeypatch):
        async def get(tid):
            return full_branding()

        async def put(tid, raw, *, actor):
            if raw.get("partner_name") == "bad":
                raise DeskError(422, "branding_invalid", "partner_name: bad")
            return branding_mod.branding_from(raw)

        async def contact(tid, lid, *, channel, kind):
            if channel == "fax":
                raise DeskError(422, "kind_invalid", "x")
            return outreach.Outcome(True, "sent", channel or "sms", "a1")

        async def attempts(tid, lid):
            return [SimpleNamespace(id=uuid.uuid4(), channel="sms", kind="first_contact", status="sent",
                                    refusal_reason=None, provider_ref="SM1", attempt_no=1, created_at=NOW, sent_at=NOW)]

        async def run(tid, *, limit):
            return {"due": limit, "sent": 0, "deferred": 0, "refused": 0}

        monkeypatch.setattr(store, "get_branding", get)
        monkeypatch.setattr(store, "put_branding", put)
        monkeypatch.setattr(store, "list_attempts", attempts)
        monkeypatch.setattr(outreach, "contact_lead", contact)
        monkeypatch.setattr(outreach, "run_due", run)
        assert client.get("/api/v1/onboarding-desk/branding").json()["partner_name"] == "Northwind Payments"
        assert client.put("/api/v1/onboarding-desk/branding", json=BRANDING_RAW).status_code == 200
        assert client.put("/api/v1/onboarding-desk/branding", json={"partner_name": "bad"}).status_code == 422
        sent = client.post(f"/api/v1/onboarding-desk/leads/{LEAD}/contact", json={"channel": "SMS"}).json()
        assert sent["sent"] is True and sent["channel"] == "sms"
        assert client.post(f"/api/v1/onboarding-desk/leads/{LEAD}/contact", json={"channel": "fax"}).status_code == 422
        listed = client.get(f"/api/v1/onboarding-desk/leads/{LEAD}/attempts").json()
        assert listed["attempts"][0]["provider_ref"] == "SM1"
        assert client.post("/api/v1/onboarding-desk/outreach/run", json={"limit": 7}).json()["due"] == 7

    def test_unsubscribe_page_and_post(self, client, monkeypatch):
        opted = []

        async def get_lead(tid, lid):
            return SimpleNamespace(locale="fr", contact_ciphertext="c"), []

        async def get(tid):
            return full_branding()

        async def decrypt(lead):
            return json.dumps({"email": "a@a.example"})

        async def opt(tid, **kwargs):
            opted.append(kwargs)
            return {}

        monkeypatch.setattr(store, "get_lead", get_lead)
        monkeypatch.setattr(store, "get_branding", get)
        monkeypatch.setattr(store, "decrypt_contact", decrypt)
        monkeypatch.setattr(store, "opt_out", opt)
        token = tokens.sign("unsubscribe", TENANT, LEAD)
        page = client.get(f"/api/v1/onboarding-desk/unsubscribe?t={token}")
        assert page.status_code == 200 and 'lang="fr"' in page.text and 'method="post"' in page.text
        assert page.headers["cache-control"] == "no-store" and not opted
        done = client.post("/api/v1/onboarding-desk/unsubscribe", data={"t": token})
        assert "désabonné" in done.text
        assert opted == [{"email": "a@a.example", "phone": None, "channel": "email", "reason": "unsubscribe",
                          "source": "email_link"}]
        apply_token = tokens.sign("apply", TENANT, LEAD)
        assert "not valid" in client.get(f"/api/v1/onboarding-desk/unsubscribe?t={apply_token}").text
        assert "not valid" in client.post("/api/v1/onboarding-desk/unsubscribe", data={"t": "x.y"}).text
        monkeypatch.setattr(settings, "onboarding_desk_enabled", False)
        assert client.get(f"/api/v1/onboarding-desk/unsubscribe?t={token}").status_code == 404
        assert client.post("/api/v1/onboarding-desk/unsubscribe", data={"t": token}).status_code == 404

    def test_twilio_inbound(self, client, monkeypatch):
        import api.v1.agents as agents_api

        opted, engaged = [], []

        async def get(tid):
            return full_branding()

        async def resolve(tid, ids, a, b):
            return {"auth_token": "tok"}, ["twilio"]

        async def opt(tid, **kwargs):
            opted.append(kwargs)
            if kwargs["phone"] == "+19999":
                raise DeskError(422, "contact_invalid", "bad")
            return {}

        async def engage(tid, phone):
            engaged.append(phone)
            return 1

        monkeypatch.setattr(store, "get_branding", get)
        monkeypatch.setattr(agents_api, "_resolve_connector_configs", resolve)
        monkeypatch.setattr(store, "opt_out", opt)
        monkeypatch.setattr(store, "mark_engaged", engage)
        monkeypatch.setattr(settings, "public_api_base_url", "https://api.example.test")
        url = f"https://api.example.test/api/v1/onboarding-desk/webhooks/twilio/{TENANT}"
        path = f"/api/v1/onboarding-desk/webhooks/twilio/{TENANT}"
        stop = {"From": "+14165550123", "Body": "Stop", "MessageSid": "SM9"}
        response = client.post(path, data=stop, headers={"X-Twilio-Signature": twilio_signature(url, stop, "tok")})
        assert response.status_code == 200 and response.text.endswith("<Response/>")
        assert opted[0]["channel"] == "all" and opted[0]["reason"] == "stop_keyword"
        hello = {"From": "+14165550123", "Body": "Hi, how do I start?", "MessageSid": "SM10"}
        client.post(path, data=hello, headers={"X-Twilio-Signature": twilio_signature(url, hello, "tok")})
        assert engaged == ["+14165550123"]
        bad_phone = {"From": "+19999", "Body": "STOP"}
        assert client.post(path, data=bad_phone,
                           headers={"X-Twilio-Signature": twilio_signature(url, bad_phone, "tok")}).status_code == 200
        assert client.post(path, data=stop, headers={"X-Twilio-Signature": "forged"}).status_code == 403

        async def no_sms(tid):
            return Branding()

        monkeypatch.setattr(store, "get_branding", no_sms)
        assert client.post(path, data=stop).status_code == 403


class TestTaskAndMigration:
    def test_the_sweep_task(self, monkeypatch):
        from core.tasks import onboarding_desk_tasks as tasks

        monkeypatch.setattr(settings, "onboarding_desk_enabled", False)
        assert tasks.sweep_outreach.run() == {"skipped": "onboarding_desk_disabled"}
        monkeypatch.setattr(settings, "onboarding_desk_enabled", True)

        async def sweep(**kwargs):
            return {"tenants": 0}

        monkeypatch.setattr(outreach, "sweep", sweep)
        monkeypatch.setattr(tasks, "run_async", lambda coro: asyncio.run(coro))
        assert tasks.sweep_outreach.run() == {"tenants": 0}

    def test_migration_v6z97(self, monkeypatch):
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z97_onboarding_outreach.py"
        spec = importlib.util.spec_from_file_location("v6_z97_onboarding_outreach", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        assert module.revision == "v6z97_onboarding_outreach" and module.down_revision == "v6z96_onboarding_desk"
        statements = []
        monkeypatch.setattr(module.op, "execute", lambda sql: statements.append(" ".join(str(sql).split())))
        module.upgrade()
        joined = "\n".join(statements)
        assert "ADD COLUMN IF NOT EXISTS next_contact_at" in joined
        assert "ADD COLUMN IF NOT EXISTS contacted_at" in joined
        assert "onboarding_desk_policies_platform_ctx" in joined and "FOR SELECT" in joined
        statements.clear()
        module.downgrade()
        assert len(statements) == 4
