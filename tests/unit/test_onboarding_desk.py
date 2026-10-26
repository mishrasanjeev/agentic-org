# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Merchant onboarding desk foundations: contact rules, contacts, leads, signatures, the store and the routes."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.config import settings
from core.onboarding_desk import contacts, leads, rules, store

TENANT = uuid.uuid4()
NOW = datetime(2026, 10, 27, 18, 0, tzinfo=UTC)  # a Tuesday


def local(zone: str, y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(zone)).astimezone(UTC)


# ── Contact rules ────────────────────────────────────────────────────────────


class TestHours:
    def test_every_region_zone_is_a_real_zone(self):
        for country, regions in rules.REGIONS.items():
            for region, zones in regions.items():
                assert zones, (country, region)
                for zone in zones:
                    ZoneInfo(zone)

    def test_united_states_floor(self):
        policy = rules.Policy()
        assert rules.within_hours("US", "NY", policy, local("America/New_York", 2026, 10, 27, 8, 0))
        assert rules.within_hours("US", "NY", policy, local("America/New_York", 2026, 10, 27, 20, 59))
        assert not rules.within_hours("US", "NY", policy, local("America/New_York", 2026, 10, 27, 21, 0))
        assert not rules.within_hours("US", "NY", policy, local("America/New_York", 2026, 10, 27, 7, 59))

    def test_stricter_states_stop_at_eight(self):
        policy = rules.Policy()
        for state in ("FL", "OK", "MD"):
            zone = rules.zones_for("US", state)[0]
            assert not rules.within_hours("US", state, policy, local(zone, 2026, 10, 27, 20, 30)), state
        assert rules.floor_attempts("US", "FL") == 3

    def test_canada_weekday_and_weekend(self):
        policy = rules.Policy()
        assert rules.within_hours("CA", "AB", policy, local("America/Edmonton", 2026, 10, 27, 21, 15))
        assert not rules.within_hours("CA", "AB", policy, local("America/Edmonton", 2026, 10, 27, 8, 45))
        saturday = local("America/Edmonton", 2026, 10, 31, 18, 30)
        assert not rules.within_hours("CA", "AB", policy, saturday)
        assert rules.within_hours("CA", "AB", policy, local("America/Edmonton", 2026, 10, 31, 11, 0))

    def test_multi_zone_states_use_only_the_shared_hours(self):
        policy = rules.Policy()
        # 08:30 in Chicago is 07:30 in Denver: El Paso is still asleep.
        assert not rules.within_hours("US", "TX", policy, local("America/Chicago", 2026, 10, 27, 8, 30))
        assert rules.within_hours("US", "TX", policy, local("America/Chicago", 2026, 10, 27, 9, 30))
        # 20:30 in Chicago is 19:30 in Denver: both are inside 08:00 to 21:00.
        assert rules.within_hours("US", "TX", policy, local("America/Chicago", 2026, 10, 27, 20, 30))

    def test_unknown_region_is_never_within_hours(self):
        assert not rules.within_hours("US", "ZZ", rules.Policy(), NOW)
        assert rules.next_allowed("US", "ZZ", rules.Policy(), NOW) is None

    def test_next_allowed_is_the_next_window_start(self):
        late = local("America/New_York", 2026, 10, 27, 22, 0)
        nxt = rules.next_allowed("US", "NY", rules.Policy(), late)
        assert nxt == local("America/New_York", 2026, 10, 28, 8, 0)


class TestPolicy:
    def test_policy_only_narrows_the_floor(self):
        policy = rules.policy_from({"start": "10:00", "end": "23:00", "weekends": False})
        assert not rules.within_hours("US", "NY", policy, local("America/New_York", 2026, 10, 27, 9, 30))
        assert not rules.within_hours("US", "NY", policy, local("America/New_York", 2026, 10, 27, 21, 30))
        assert not rules.within_hours("US", "NY", policy, local("America/New_York", 2026, 10, 31, 12, 0))
        assert rules.within_hours("US", "NY", policy, local("America/New_York", 2026, 10, 27, 12, 0))

    def test_round_trip_and_defaults(self):
        policy = rules.policy_from(None)
        assert policy == rules.Policy()
        again = rules.policy_from(rules.policy_from({"start": "09:15", "attempts_per_day": 2}).to_dict())
        assert again.start == dtime(9, 15) and again.attempts_per_day == 2

    @pytest.mark.parametrize(
        "raw",
        [
            {"channels": ["fax"]},
            {"channels": "sms"},
            {"start": "25:00"},
            {"start": "18:00", "end": "09:00"},
            {"attempts_per_day": 9},
            {"max_reminders": 7},
            {"min_hours_between_reminders": 1},
            {"weekends": "yes"},
        ],
    )
    def test_invalid_policies_are_refused(self, raw):
        with pytest.raises(ValueError):
            rules.policy_from(raw)


def view(**overrides):
    base = {"country": "US", "region": "NY", "has_email": True, "has_phone": True}
    base.update(overrides)
    return rules.LeadView(**base)


SMS = [rules.ConsentView("sms", True)]
NOON_NY = local("America/New_York", 2026, 10, 27, 12, 0)


class TestDecide:
    def test_allowed_with_consent_inside_hours(self):
        decision = rules.decide(view(), "sms", SMS, policy=rules.Policy(), now=NOON_NY)
        assert decision.allowed and decision.reason == "allowed"

    @pytest.mark.parametrize(
        ("lead", "channel", "consents", "reason"),
        [
            (view(), "pigeon", SMS, "channel_unknown"),
            (view(), "web", SMS, "channel_not_initiated"),
            (view(country="GB"), "sms", SMS, "country_not_supported"),
            (view(region="ZZ"), "sms", SMS, "region_required"),
            (view(opted_out=True), "sms", SMS, "opted_out"),
            (view(suppressed_channels=frozenset({"all"})), "sms", SMS, "opted_out"),
            (view(suppressed_channels=frozenset({"sms"})), "sms", SMS, "opted_out"),
            (view(has_email=False), "email", [rules.ConsentView("email", True)], "no_email"),
            (view(has_phone=False), "sms", SMS, "no_phone"),
            (view(), "sms", [], "no_consent"),
            (view(), "sms", [rules.ConsentView("sms", False)], "no_consent"),
            (view(), "sms", [rules.ConsentView("sms", True, False, NOON_NY - timedelta(days=1))], "consent_expired"),
            (view(), "voice", [rules.ConsentView("voice", True, written=False)], "voice_needs_written_consent"),
        ],
    )
    def test_refusals(self, lead, channel, consents, reason):
        decision = rules.decide(lead, channel, consents, policy=rules.Policy(), now=NOON_NY)
        assert not decision.allowed and decision.reason == reason

    def test_channel_outside_policy(self):
        decision = rules.decide(view(), "sms", SMS, policy=rules.Policy(channels=("email",)), now=NOON_NY)
        assert decision.reason == "channel_not_allowed"

    def test_written_voice_consent_is_allowed(self):
        consents = [rules.ConsentView("voice", True, written=True)]
        assert rules.decide(view(), "voice", consents, policy=rules.Policy(), now=NOON_NY).allowed

    def test_outside_hours_names_the_next_window(self):
        late = local("America/New_York", 2026, 10, 27, 22, 0)
        decision = rules.decide(view(), "sms", SMS, policy=rules.Policy(), now=late)
        assert decision.reason == "outside_hours" and decision.next_allowed_at is not None
        assert decision.to_dict()["next_allowed_at"].startswith("2026-10-28")

    def test_email_is_not_bound_to_hours(self):
        late = local("America/New_York", 2026, 10, 27, 23, 0)
        assert rules.decide(
            view(), "email", [rules.ConsentView("email", True)], policy=rules.Policy(), now=late
        ).allowed

    def test_attempt_limit(self):
        decision = rules.decide(view(), "sms", SMS, policy=rules.Policy(), now=NOON_NY, attempts_last_24h=3)
        assert decision.reason == "attempt_limit" and decision.to_dict()["details"] == {"limit": 3}
        narrowed = rules.Policy(attempts_per_day=1)
        assert (
            rules.decide(view(), "sms", SMS, policy=narrowed, now=NOON_NY, attempts_last_24h=1).reason
            == "attempt_limit"
        )

    def test_reminders(self):
        policy = rules.Policy(max_reminders=2, min_hours_between_reminders=24)
        assert (
            rules.decide(view(), "sms", SMS, policy=policy, now=NOON_NY, is_reminder=True, reminders_sent=2).reason
            == "reminder_limit"
        )
        soon = rules.decide(
            view(),
            "sms",
            SMS,
            policy=policy,
            now=NOON_NY,
            is_reminder=True,
            last_attempt_at=NOON_NY - timedelta(hours=2),
        )
        assert soon.reason == "reminder_too_soon" and soon.next_allowed_at == NOON_NY + timedelta(hours=22)
        assert rules.decide(
            view(),
            "sms",
            SMS,
            policy=policy,
            now=NOON_NY,
            is_reminder=True,
            last_attempt_at=NOON_NY - timedelta(days=2),
        ).allowed

    def test_default_locale(self):
        assert rules.default_locale("CA", "QC") == "fr" and rules.default_locale("CA", "ON") == "en"


# ── Contacts ─────────────────────────────────────────────────────────────────


class TestContacts:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("+1 415 555 0134", "+14155550134"), ("(415) 555-0134", "+14155550134"), ("1-416-555-0199", "+14165550199")],
    )
    def test_phone_forms(self, raw, expected):
        assert contacts.normalise_phone(raw) == expected

    @pytest.mark.parametrize("raw", ["555-0134", "+44 20 7946 0958", "+1 015 555 0134", "+1 415 155 0134", "abc"])
    def test_bad_phones(self, raw):
        with pytest.raises(contacts.ContactError):
            contacts.normalise_phone(raw)

    def test_email(self):
        assert contacts.normalise_email("  Owner@Example.TEST ") == "owner@example.test"
        assert contacts.normalise_email(None) is None and contacts.normalise_phone("") is None
        with pytest.raises(contacts.ContactError):
            contacts.normalise_email("not-an-email")

    def test_masks_and_hashes(self):
        assert contacts.mask_email("owner@example.test") == "o***@example.test"
        assert contacts.mask_phone("+14155550134") == "+1******0134"
        assert contacts.mask_email(None) is None and contacts.mask_phone(None) is None
        one = contacts.contact_hash("k", "t1", "phone", "+14155550134")
        assert one != contacts.contact_hash("k", "t2", "phone", "+14155550134")
        assert one != contacts.contact_hash("other", "t1", "phone", "+14155550134")
        assert one != hashlib.sha256(b"+14155550134").hexdigest()

    @pytest.mark.parametrize("text", ["STOP", " stop ", "Stop.", "UNSUBSCRIBE", "Arrêt", "opt out", "stopall"])
    def test_opt_out_keywords(self, text):
        assert contacts.is_opt_out(text)

    @pytest.mark.parametrize("text", ["", None, "stop please", "please stop", "start", "yes"])
    def test_not_opt_out(self, text):
        assert not contacts.is_opt_out(text)


# ── Leads ────────────────────────────────────────────────────────────────────

CONSENT = {
    "channel": "sms",
    "wording": "Yes, text me about setting up card payments. Reply STOP to opt out.",
    "method": "web_form",
    "source_ref": "https://partner.example/apply",
    "captured_at": "2026-10-20T15:04:00Z",
}


def lead_raw(**overrides):
    raw = {
        "business_name": "Northwind Coffee LLC",
        "contact_name": "Avery Example",
        "email": "owner@northwind.example",
        "phone": "+1 415 555 0134",
        "country": "us",
        "region": "ca",
        "merchant_type": "cafe",
        "external_ref": "form-0042",
        "consents": [CONSENT],
    }
    raw.update(overrides)
    return raw


class TestLeads:
    def test_valid_lead(self):
        lead = leads.lead_from(lead_raw(), now=NOW)
        assert lead.country == "US" and lead.region == "CA" and lead.phone == "+14155550134"
        assert lead.locale == "en" and lead.consents[0].captured_at.tzinfo is not None
        assert leads.lead_from(lead_raw(country="CA", region="QC", phone="+1 514 555 0100"), now=NOW).locale == "fr"

    @pytest.mark.parametrize(
        ("overrides", "field"),
        [
            ({"business_name": ""}, "business_name"),
            ({"country": "GB"}, "country"),
            ({"region": "ZZ"}, "region"),
            ({"locale": "de"}, "locale"),
            ({"merchant_type": "Coffee Shop"}, "merchant_type"),
            ({"email": "bad"}, "email"),
            ({"phone": "123"}, "phone"),
            ({"email": None, "phone": None, "consents": []}, "email"),
            ({"consents": "sms"}, "consents"),
            ({"consents": [{**CONSENT, "channel": "fax"}]}, "channel"),
            ({"consents": [{**CONSENT, "wording": "yes"}]}, "wording"),
            ({"consents": [{**CONSENT, "method": "telepathy"}]}, "method"),
            ({"consents": [{**CONSENT, "captured_at": "2030-01-01T00:00:00Z"}]}, "captured_at"),
            ({"consents": [{**CONSENT, "captured_at": "2026-10-20T15:04:00"}]}, "captured_at"),
            ({"consents": [{**CONSENT, "written": "yes"}]}, "written"),
            ({"consents": [{**CONSENT, "expires_at": "2026-10-19T00:00:00Z"}]}, "expires_at"),
            ({"phone": None, "consents": [CONSENT]}, "consents"),
            ({"email": None, "consents": [{**CONSENT, "channel": "email"}]}, "consents"),
        ],
    )
    def test_invalid_leads(self, overrides, field):
        with pytest.raises(leads.LeadError) as caught:
            leads.lead_from(lead_raw(**overrides), now=NOW)
        assert caught.value.field == field

    def test_webhook_bodies(self):
        assert len(leads.leads_from_webhook(lead_raw(), now=NOW)) == 1
        assert len(leads.leads_from_webhook({"leads": [lead_raw(), lead_raw(external_ref="b")]}, now=NOW)) == 2
        for bad in ({"leads": []}, {"leads": "x"}, {"leads": [lead_raw()] * 101}):
            with pytest.raises(leads.LeadError):
                leads.leads_from_webhook(bad, now=NOW)

    def test_csv_with_and_without_attestation(self):
        data = (
            b"business_name,email,phone,country,region,external_ref,consent_channels,consent_captured_at\n"
            b"Acme Diner,a@acme.example,4165550101,CA,ON,r1,sms|email,2026-10-20T10:00:00Z\n"
            b"Bad Row,,,US,NY,r2,,\n"
            b"Lone Star Tacos,t@tacos.example,,US,TX,r3,email,2026-10-20T10:00:00Z\n"
        )
        attested = leads.leads_from_csv(
            data, leads.Attestation("I agree to be contacted about card payments."), now=NOW
        )
        assert [lead.external_ref for lead in attested.leads] == ["r1", "r3"]
        assert {c.channel for c in attested.leads[0].consents} == {"sms", "email"}
        assert attested.leads[0].consents[0].method == "csv_attestation"
        assert attested.errors == [{"row": 3, "field": "email", "error": "a lead needs an email or a phone number"}]
        bare = leads.leads_from_csv(data, None, now=NOW)
        assert all(not lead.consents for lead in bare.leads)

    @pytest.mark.parametrize(
        ("data", "message"),
        [
            (b"\xff\xfe\x00", "UTF-8"),
            (b"email,phone\nx,y\n", "missing"),
            (b"business_name,country,region,shoe_size\nA,US,NY,9\n", "unknown columns"),
            (b"x" * (leads.MAX_CSV_BYTES + 1), "larger"),
        ],
        ids=["not-utf8", "missing-columns", "unknown-columns", "too-large"],
    )
    def test_csv_file_errors(self, data, message):
        with pytest.raises(leads.LeadError, match=message):
            leads.leads_from_csv(data, None, now=NOW)

    def test_csv_row_limit(self, monkeypatch):
        monkeypatch.setattr(leads, "MAX_CSV_ROWS", 2)
        data = b"business_name,country,region,email\n" + b"A,US,NY,a@a.example\n" * 3
        with pytest.raises(leads.LeadError, match="at most 2 rows"):
            leads.leads_from_csv(data, None, now=NOW)

    def test_attestation(self):
        assert leads.attestation_from({"wording": "I agree to be contacted."}).method == "csv_attestation"
        for bad in (
            {"wording": "ok"},
            {"wording": "I agree to be contacted.", "method": "x"},
            {"wording": "I agree to be contacted.", "written": "y"},
        ):
            with pytest.raises(leads.LeadError):
                leads.attestation_from(bad)


# ── Webhook signatures ───────────────────────────────────────────────────────


def signed(secret: str, body: bytes, stamp: int | None = None) -> str:
    stamp = int(time.time()) if stamp is None else stamp
    digest = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


class TestSignature:
    def test_valid_stale_tampered_and_rotated(self):
        from api.v1.onboarding_desk import verify_signature

        body = b'{"business_name":"x"}'
        assert verify_signature("s3cret", signed("s3cret", body), body)
        assert not verify_signature("s3cret", signed("s3cret", body, int(time.time()) - 400), body)
        assert not verify_signature("s3cret", signed("s3cret", body), body + b" ")
        assert not verify_signature("s3cret", signed("other", body), body)
        assert not verify_signature("s3cret", "", body) and not verify_signature("s3cret", "t=abc,v1=00", body)
        rotated = signed("s3cret", body) + ",v1=" + "0" * 64
        assert verify_signature("s3cret", rotated, body)


# ── Store, against a fake tenant session ─────────────────────────────────────


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
        self.added: list = []
        self.statements: list = []

    async def execute(self, statement):
        self.statements.append(statement)
        return FakeResult(self.results.pop(0) if self.results else [])

    def add(self, row):
        self.added.append(row)


@pytest.fixture
def db(monkeypatch):
    holder = {}

    def install(*results):
        session = FakeSession(results)
        holder["session"] = session

        @asynccontextmanager
        async def fake(tenant_id):
            yield session

        monkeypatch.setattr(store, "_session", fake)
        return session

    async def encrypt(tenant_id, plaintext):
        return "enc:" + plaintext

    async def decrypt(ciphertext):
        return ciphertext.removeprefix("enc:")

    monkeypatch.setattr(store, "_encrypt", encrypt)
    monkeypatch.setattr(store, "_decrypt", decrypt)
    monkeypatch.setattr(settings, "onboarding_desk_enabled", True)
    return install


def stored_lead(**overrides):
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "business_name": "Northwind Coffee LLC",
        "country": "US",
        "region": "NY",
        "locale": "en",
        "merchant_type": "cafe",
        "email_masked": "o***@n.example",
        "phone_masked": "+1******0134",
        "email_hash": "eh",
        "phone_hash": "ph",
        "status": "new",
        "source_kind": "manual",
        "external_ref": None,
        "contact_ciphertext": "enc:" + json.dumps({"email": "o@n.example", "phone": "+14155550134"}),
        "created_at": NOW,
        "updated_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def stored_consent(channel="sms", status="granted", written=False):
    return SimpleNamespace(
        id=uuid.uuid4(),
        channel=channel,
        status=status,
        written=written,
        expires_at=None,
        wording="I agree.",
        wording_sha256="h",
        method="web_form",
        source_ref="",
        captured_at=NOW,
        withdrawn_at=None,
        withdrawn_via=None,
    )


class TestStore:
    def test_policy_get_and_put(self, db):
        db([])
        assert asyncio.run(store.get_policy(TENANT)) == rules.Policy()
        session = db([])
        policy = asyncio.run(store.put_policy(TENANT, {"start": "09:00"}, actor="u1"))
        assert policy.start == dtime(9, 0) and session.added[0].settings["start"] == "09:00"
        existing = SimpleNamespace(settings={}, updated_by="", updated_at=None)
        db([existing])
        asyncio.run(store.put_policy(TENANT, {"weekends": False}, actor="u2"))
        assert existing.settings["weekends"] is False and existing.updated_by == "u2"
        db([])
        with pytest.raises(store.DeskError) as caught:
            asyncio.run(store.put_policy(TENANT, {"start": "bad"}, actor="u"))
        assert caught.value.code == "policy_invalid"

    def test_sources(self, db):
        session = db()
        row, secret = asyncio.run(store.create_source(TENANT, " Website form ", "webhook", actor="u"))
        assert secret and row.secret_ciphertext == "enc:" + secret and row.name == "Website form"
        assert session.added == [row] and store.source_view(row)["signed"] is True
        _, none = asyncio.run(store.create_source(TENANT, "CSV", "csv", actor="u"))
        assert none is None
        with pytest.raises(store.DeskError):
            asyncio.run(store.create_source(TENANT, "x", "pigeon", actor="u"))
        db([row])
        assert asyncio.run(store.list_sources(TENANT)) == [row]
        db([SimpleNamespace(enabled=True, kind="webhook", secret_ciphertext="enc:abc")])
        assert asyncio.run(store.webhook_secret(TENANT, uuid.uuid4()))[1] == "abc"
        db([SimpleNamespace(enabled=False, kind="webhook", secret_ciphertext="enc:abc")])
        assert asyncio.run(store.webhook_secret(TENANT, uuid.uuid4())) is None
        db([])
        assert asyncio.run(store.webhook_secret(TENANT, uuid.uuid4())) is None

    def test_create_lead_encrypts_hashes_and_is_idempotent(self, db):
        lead = leads.lead_from(lead_raw(), now=NOW)
        session = db([], [])
        outcome = asyncio.run(store.create_lead(TENANT, lead, source_kind="webhook", actor="src"))
        row = outcome.lead
        assert outcome.created and row.status == "new" and row.contact_ciphertext.startswith("enc:")
        assert "owner@northwind.example" not in (row.email_masked or "") and row.phone_masked == "+1******0134"
        assert row.email_hash == contacts.contact_hash(settings.secret_key, str(TENANT), "email", lead.email)
        consents = [r for r in session.added if r is not row]
        assert (
            len(consents) == 1 and consents[0].wording_sha256 == hashlib.sha256(CONSENT["wording"].encode()).hexdigest()
        )
        db([row])
        again = asyncio.run(store.create_lead(TENANT, lead, source_kind="webhook"))
        assert not again.created and again.lead is row

    def test_a_suppressed_contact_arrives_opted_out(self, db):
        lead = leads.lead_from(lead_raw(external_ref=None), now=NOW)
        db([SimpleNamespace(contact_hash="x", channel="all")])
        assert asyncio.run(store.create_lead(TENANT, lead)).lead.status == "opted_out"

    def test_import_csv_counts(self, db):
        data = b"business_name,country,region,email,external_ref\nA,US,NY,a@a.example,r1\nB,US,NY,,r2\n"
        db([], [])
        result = asyncio.run(store.import_csv(TENANT, data, None, actor="u", source_id=None))
        assert result["created"] == 1 and result["rejected"] == 1 and result["errors"][0]["row"] == 3

    def test_get_and_views(self, db):
        lead = stored_lead()
        db([lead], [stored_consent()])
        found, consents = asyncio.run(store.get_lead(TENANT, lead.id))
        assert found is lead and store.lead_view(found)["email"] == "o***@n.example"
        assert store.consent_view(consents[0])["channel"] == "sms"
        db([])
        with pytest.raises(store.DeskError) as caught:
            asyncio.run(store.get_lead(TENANT, uuid.uuid4()))
        assert caught.value.status == 404
        db([lead])
        assert asyncio.run(store.list_leads(TENANT, status="new", limit=9999)) == [lead]

    def test_record_and_withdraw_consent(self, db):
        lead = stored_lead()
        consent = leads.consent_from(CONSENT, now=NOW)
        session = db([lead], [])
        row = asyncio.run(store.record_consent(TENANT, lead.id, consent, actor="u"))
        assert row in session.added and row.channel == "sms"
        db([stored_lead(status="opted_out")], [])
        with pytest.raises(store.DeskError) as caught:
            asyncio.run(store.record_consent(TENANT, lead.id, consent, actor="u"))
        assert caught.value.code == "lead_opted_out"
        granted = stored_consent()
        db([granted])
        assert asyncio.run(store.withdraw_consents(TENANT, lead.id, "sms", via="request", actor="u")) == 1
        assert granted.status == "withdrawn" and granted.withdrawn_via == "request"
        with pytest.raises(store.DeskError):
            asyncio.run(store.withdraw_consents(TENANT, lead.id, "fax", via="x", actor="u"))

    def test_opt_out_suppresses_withdraws_and_marks(self, db):
        lead = stored_lead()
        consent = stored_consent()
        db([], [lead], [consent])
        result = asyncio.run(
            store.opt_out(
                TENANT, email=None, phone="+1 415 555 0134", channel="all", reason="stop_keyword", source="sms"
            )
        )
        assert result == {"suppressed": 1, "leads": 1, "consents_withdrawn": 1}
        assert lead.status == "opted_out" and consent.status == "withdrawn"
        for kwargs, code in (
            ({"email": None, "phone": None, "channel": "all", "reason": "request"}, "contact_required"),
            ({"email": "bad", "phone": None, "channel": "all", "reason": "request"}, "contact_invalid"),
            ({"email": "a@a.example", "phone": None, "channel": "fax", "reason": "request"}, "channel_invalid"),
            ({"email": "a@a.example", "phone": None, "channel": "all", "reason": "whim"}, "reason_invalid"),
        ):
            with pytest.raises(store.DeskError) as caught:
                asyncio.run(store.opt_out(TENANT, source="op", **kwargs))
            assert caught.value.code == code

    def test_contact_decision_end_to_end(self, db):
        lead = stored_lead()
        # get_lead (lead, consents), suppressions, attempts (last day, reminders, last), policy
        db([lead], [stored_consent()], [], [0], [0], [None], [])
        decision = asyncio.run(store.contact_decision(TENANT, lead.id, "sms", now=NOON_NY))
        assert decision.allowed
        db([lead], [stored_consent()], [SimpleNamespace(contact_hash="ph", channel="sms")], [0], [0], [None], [])
        assert asyncio.run(store.contact_decision(TENANT, lead.id, "sms", now=NOON_NY)).reason == "opted_out"
        db([lead], [stored_consent()], [], [3], [0], [None], [])
        assert asyncio.run(store.contact_decision(TENANT, lead.id, "sms", now=NOON_NY)).reason == "attempt_limit"
        db(
            [lead],
            [stored_consent(channel="email")],
            [SimpleNamespace(contact_hash="eh", channel="all")],
            [0],
            [0],
            [None],
            [],
        )
        assert asyncio.run(store.contact_decision(TENANT, lead.id, "email", now=NOON_NY)).reason == "opted_out"


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
    return TestClient(app), api


class TestRoutes:
    def test_status_answers_and_everything_else_is_404_while_off(self, client, monkeypatch):
        http, _ = client
        monkeypatch.setattr(settings, "onboarding_desk_enabled", False)
        assert http.get("/api/v1/onboarding-desk/status").json()["enabled"] is False
        for method, path in (
            ("get", "/leads"),
            ("get", "/policy"),
            ("post", f"/webhooks/leads/{TENANT}/{uuid.uuid4()}"),
        ):
            response = getattr(http, method)(f"/api/v1/onboarding-desk{path}")
            assert response.status_code == 404, path
            assert response.json()["detail"]["error"] == "onboarding_desk_disabled"

    def test_create_lead_and_validation(self, client, monkeypatch):
        http, _ = client
        lead_row = stored_lead()

        async def create(tenant_id, lead, **kwargs):
            return store.Created(lead_row, True)

        monkeypatch.setattr(store, "create_lead", create)
        response = http.post("/api/v1/onboarding-desk/leads", json=lead_raw())
        assert response.status_code == 200 and response.json()["created"] is True
        assert response.json()["lead"]["phone"] == "+1******0134"
        bad = http.post("/api/v1/onboarding-desk/leads", json=lead_raw(country="GB"))
        assert bad.status_code == 422 and bad.json()["detail"]["field"] == "country"

    def test_policy_routes(self, client, monkeypatch):
        http, _ = client

        async def put(tenant_id, raw, *, actor):
            if raw.get("start") == "bad":
                raise store.DeskError(422, "policy_invalid", "start must be HH:MM")
            return rules.policy_from(raw)

        async def get(tenant_id):
            return rules.Policy()

        monkeypatch.setattr(store, "put_policy", put)
        monkeypatch.setattr(store, "get_policy", get)
        assert http.get("/api/v1/onboarding-desk/policy").json()["max_reminders"] == 3
        assert http.put("/api/v1/onboarding-desk/policy", json={"start": "10:00"}).json()["start"] == "10:00"
        assert http.put("/api/v1/onboarding-desk/policy", json={"start": "bad"}).status_code == 422

    def test_signed_webhook(self, client, monkeypatch):
        http, _ = client
        source = uuid.uuid4()
        seen = []

        async def secret_for(tenant_id, source_id):
            return (SimpleNamespace(id=source_id), "s3cret") if source_id == source else None

        async def create(tenant_id, lead, **kwargs):
            seen.append(kwargs)
            return store.Created(stored_lead(), True)

        monkeypatch.setattr(store, "webhook_secret", secret_for)
        monkeypatch.setattr(store, "create_lead", create)
        body = json.dumps({"leads": [lead_raw()]}).encode()
        path = f"/api/v1/onboarding-desk/webhooks/leads/{TENANT}/{source}"
        ok = http.post(path, content=body, headers={"x-agenticorg-signature": signed("s3cret", body)})
        assert ok.status_code == 200 and ok.json() == {"created": 1, "existing": 0}
        assert seen[0]["source_kind"] == "webhook" and seen[0]["source_id"] == source
        assert (
            http.post(path, content=body, headers={"x-agenticorg-signature": signed("wrong", body)}).status_code == 401
        )
        assert (
            http.post(
                f"/api/v1/onboarding-desk/webhooks/leads/{TENANT}/{uuid.uuid4()}",
                content=body,
                headers={"x-agenticorg-signature": signed("s3cret", body)},
            ).status_code
            == 401
        )
        bad = json.dumps({"leads": [lead_raw(region="ZZ")]}).encode()
        assert (
            http.post(path, content=bad, headers={"x-agenticorg-signature": signed("s3cret", bad)}).status_code == 422
        )
        junk = b"{not json"
        assert (
            http.post(path, content=junk, headers={"x-agenticorg-signature": signed("s3cret", junk)}).status_code == 400
        )
        assert http.post(path, content=b"x" * (300 * 1024)).status_code == 413
        assert http.post(f"/api/v1/onboarding-desk/webhooks/leads/not-a-uuid/{source}", content=body).status_code == 404

    def test_source_lead_consent_and_opt_out_routes(self, client, monkeypatch):
        http, _ = client
        source_row = SimpleNamespace(
            id=uuid.uuid4(), name="Form", kind="webhook", enabled=True, secret_ciphertext="enc:x", created_at=NOW
        )

        async def create_source(tenant_id, name, kind, *, actor):
            return source_row, "shh"

        async def list_sources(tenant_id):
            return [source_row]

        async def list_leads(tenant_id, **kwargs):
            return [stored_lead()]

        async def get_lead(tenant_id, lead_id):
            return stored_lead(), [stored_consent()]

        async def record(tenant_id, lead_id, consent, *, actor):
            return stored_consent()

        async def withdraw(tenant_id, lead_id, channel, *, via, actor):
            return 2

        async def opt(tenant_id, **kwargs):
            return {"suppressed": 1, "leads": 0, "consents_withdrawn": 0}

        async def decision(tenant_id, lead_id, channel, **kwargs):
            return rules.Decision(False, "outside_hours", NOW)

        async def import_csv(tenant_id, data, attestation, **kwargs):
            return {"created": 1, "existing": 0, "rejected": 0, "errors": [], "attested": attestation is not None}

        for name, fn in (
            ("create_source", create_source),
            ("list_sources", list_sources),
            ("list_leads", list_leads),
            ("get_lead", get_lead),
            ("record_consent", record),
            ("withdraw_consents", withdraw),
            ("opt_out", opt),
            ("contact_decision", decision),
            ("import_csv", import_csv),
        ):
            monkeypatch.setattr(store, name, fn)
        made = http.post("/api/v1/onboarding-desk/lead-sources", json={"name": "Form", "kind": "webhook"}).json()
        assert made["signing_secret"] == "shh" and made["webhook_path"].endswith(str(source_row.id))
        assert http.get("/api/v1/onboarding-desk/lead-sources").json()["sources"][0]["signed"] is True
        assert len(http.get("/api/v1/onboarding-desk/leads?status=new").json()["leads"]) == 1
        lid = uuid.uuid4()
        assert http.get(f"/api/v1/onboarding-desk/leads/{lid}").json()["consents"][0]["channel"] == "sms"
        assert http.get("/api/v1/onboarding-desk/leads/not-a-uuid").status_code == 404
        assert http.post(f"/api/v1/onboarding-desk/leads/{lid}/consents", json=CONSENT).status_code == 200
        assert (
            http.post(f"/api/v1/onboarding-desk/leads/{lid}/consents", json={**CONSENT, "method": "x"}).status_code
            == 422
        )
        assert http.post(f"/api/v1/onboarding-desk/leads/{lid}/consents/withdraw", json={"channel": "sms"}).json() == {
            "withdrawn": 2
        }
        assert http.post("/api/v1/onboarding-desk/opt-outs", json={"phone": "+14155550134"}).json()["suppressed"] == 1
        decided = http.get(f"/api/v1/onboarding-desk/leads/{lid}/contact-decision?channel=SMS").json()
        assert decided["reason"] == "outside_hours" and decided["next_allowed_at"].startswith("2026-10-27")
        files = {"file": ("leads.csv", b"business_name,country,region,email\nA,US,NY,a@a.example\n", "text/csv")}
        imported = http.post(
            "/api/v1/onboarding-desk/leads/import",
            files=files,
            data={"consent_wording": "I agree to be contacted about card payments."},
        ).json()
        assert imported["created"] == 1 and imported["attested"] is True
        refused = http.post("/api/v1/onboarding-desk/leads/import", files=files, data={"consent_wording": "ok"})
        assert refused.status_code == 422


class TestMigration:
    def test_v6z96_tables_rls_and_chain(self, monkeypatch):
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z96_onboarding_desk.py"
        spec = importlib.util.spec_from_file_location("v6_z96_onboarding_desk", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        assert module.revision == "v6z96_onboarding_desk" and len(module.revision) <= 32
        assert module.down_revision == "v6z95_network_listings"
        statements: list[str] = []
        monkeypatch.setattr(module.op, "execute", lambda sql: statements.append(" ".join(str(sql).split())))
        module.upgrade()
        joined = "\n".join(statements)
        for table in module.TABLES:
            assert f"CREATE TABLE IF NOT EXISTS {table}" in joined
            assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in joined
            assert f"CREATE POLICY {table}_tenant_isolation" in joined
        assert "ux_onboarding_leads_source_ref" in joined and "WHERE external_ref IS NOT NULL" in joined
        assert "ux_onboarding_suppressions_hash_channel" in joined
        statements.clear()
        module.downgrade()
        assert len(statements) == len(module.TABLES)

    def test_models_match_the_migration_constraints(self):
        from core.models.onboarding_desk import OnboardingConsent, OnboardingLead

        lead_checks = {c.name for c in OnboardingLead.__table__.constraints}
        assert {
            "ck_onboarding_leads_country",
            "ck_onboarding_leads_locale",
            "ck_onboarding_leads_status",
        } <= lead_checks
        assert "ck_onboarding_consents_channel" in {c.name for c in OnboardingConsent.__table__.constraints}
