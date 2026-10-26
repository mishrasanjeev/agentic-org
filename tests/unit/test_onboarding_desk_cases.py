# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Onboarding desk part 4: the governed-case hand-off, evidence packs, reporting and their routes."""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.cases.states import CaseError
from core.config import settings
from core.onboarding_desk import applications, cases, evidence, questions, reports, store
from core.onboarding_desk.store import DeskError

TENANT = uuid.uuid4()
LEAD = uuid.uuid4()
APP = uuid.uuid4()
NOW = datetime(2026, 10, 27, 17, 0, tzinfo=UTC)
SEED = base64.b64encode(bytes(range(32))).decode()

ANSWERS = {
    "legal_name": "Northwind Coffee LLC",
    "business_structure": "llc",
    "registration_number": "202612345678",
    "tax_id": "12-3456789",
    "registered_address": {
        "lines": ["1 Market St"],
        "locality": "San Francisco",
        "region": "CA",
        "postal_code": "94105",
    },
    "activity": "food_service",
    "owners": [
        {"name": "Avery Example", "kind": "person", "role": "owner", "ownership_pct": 100.0, "date_of_birth": "1980-04"}
    ],
}


def app_row(**overrides):
    base = {
        "id": APP,
        "lead_id": LEAD,
        "status": "submitted",
        "merchant_type": "general",
        "country": "US",
        "region": "CA",
        "locale": "en",
        "answers_ciphertext": json.dumps({"values": ANSWERS, "sources": {}}),
        "transcript_ciphertext": json.dumps([{"from": "applicant", "text": "hi"}]),
        "answered_items": [],
        "pending_item": None,
        "last_channel": "web",
        "created_at": NOW - timedelta(hours=5),
        "updated_at": NOW,
        "submitted_at": NOW - timedelta(hours=2),
        "case_ref": None,
        "case_opened_at": None,
        "case_error": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeResult:
    def __init__(self, rows):
        self.rows = list(rows)

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        return list(self.rows)


class FakeSession:
    def __init__(self, results):
        self.results = [list(r) for r in results]
        self.added = []

    async def execute(self, statement):
        return FakeResult(self.results.pop(0) if self.results else [])

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None


@pytest.fixture
def db(monkeypatch):
    def install(*results):
        session = FakeSession(results)

        @asynccontextmanager
        async def fake(tenant_id):
            yield session

        monkeypatch.setattr(store, "_session", fake)
        return session

    async def identity(text):
        return text

    monkeypatch.setattr(store, "_decrypt", identity)
    return install


class Runtime:
    def __init__(self, enabled=True):
        self.enabled = enabled

    async def require_enabled(self, tenant_id):
        if not self.enabled:
            raise CaseError("governed_cases_disabled", status=404)


# ── Settings ─────────────────────────────────────────────────────────────────


class TestCaseSettings:
    def test_parse(self):
        assert cases.case_settings_from(None) == cases.CaseSettings()
        assert cases.case_settings_from({"auto_open": True}).to_dict() == {"auto_open": True, "investigate": False}
        for bad in ({"auto_open": "yes"}, {"investigate": 1}, {"approve": True}):
            with pytest.raises(DeskError, match="case settings|true or false"):
                cases.case_settings_from(bad)

    def test_get_and_put(self, db):
        db([])
        assert asyncio.run(cases.get_case_settings(TENANT)) == cases.CaseSettings()
        session = db([])
        asyncio.run(cases.put_case_settings(TENANT, {"auto_open": True, "investigate": True}, actor="u"))
        assert session.added[0].settings == {"cases": {"auto_open": True, "investigate": True}}
        row = SimpleNamespace(settings={"branding": {"partner_name": "P"}}, updated_by="", updated_at=None)
        db([row])
        asyncio.run(cases.put_case_settings(TENANT, {"auto_open": True}, actor="u"))
        assert row.settings["branding"] == {"partner_name": "P"} and row.settings["cases"]["auto_open"] is True
        db([row])
        assert asyncio.run(cases.get_case_settings(TENANT)).auto_open is True


# ── Opening a case ───────────────────────────────────────────────────────────


@pytest.fixture
def created(monkeypatch):
    calls = []

    async def create_case(session, **kwargs):
        calls.append(kwargs)
        if kwargs["application"]["legal_name"] == "Refused LLC":
            raise CaseError("application_invalid", "legal_name", status=422)
        return SimpleNamespace(case_ref="case_" + "a" * 24)

    import core.cases.store as case_store

    monkeypatch.setattr(case_store, "create_case", create_case)
    return calls


class TestOpenCase:
    def test_opens_once_and_links_the_application(self, db, created):
        row = app_row()
        db([row], [row])
        ref, made = asyncio.run(cases.open_case(TENANT, APP, actor="user:u1", runtime=Runtime(), now=NOW))
        assert made and ref == "case_" + "a" * 24 and row.case_ref == ref and row.case_opened_at == NOW
        call = created[0]
        assert call["policy_id"] == "business_onboarding_us" and call["purpose"] == "aml.cdd.onboarding"
        assert call["application"]["jurisdiction"] == "US-CA" and call["created_by"] == "user:u1"
        assert call["provider"] == settings.case_provider
        db([row])
        assert asyncio.run(cases.open_case(TENANT, APP, runtime=Runtime())) == (ref, False)
        assert len(created) == 1

    def test_a_concurrent_open_keeps_the_first_case(self, db, created):
        first, locked = app_row(), app_row(case_ref="case_" + "b" * 24)
        db([first], [locked])
        assert asyncio.run(cases.open_case(TENANT, APP, runtime=Runtime())) == ("case_" + "b" * 24, False)
        assert created == []

    def test_refusals(self, db, created):
        with pytest.raises(DeskError) as caught:
            asyncio.run(cases.open_case(TENANT, APP, runtime=Runtime(enabled=False)))
        assert caught.value.code == "governed_cases_disabled" and caught.value.status == 409
        db([app_row(status="in_progress")])
        with pytest.raises(DeskError, match="submitted"):
            asyncio.run(cases.open_case(TENANT, APP, runtime=Runtime()))
        incomplete = app_row(answers_ciphertext=json.dumps({"values": {"owners": []}, "sources": {}}))
        db([incomplete], [incomplete])
        with pytest.raises(DeskError) as caught:
            asyncio.run(cases.open_case(TENANT, APP, runtime=Runtime()))
        assert caught.value.code == "application_incomplete" and incomplete.case_error == "application_incomplete"
        refused = app_row(answers_ciphertext=json.dumps({"values": {**ANSWERS, "legal_name": "Refused LLC"}}))
        db([refused], [refused], [refused])
        with pytest.raises(DeskError) as caught:
            asyncio.run(cases.open_case(TENANT, APP, runtime=Runtime()))
        assert caught.value.code == "application_invalid" and refused.case_error == "application_invalid"
        assert refused.case_ref is None

    def test_a_real_case_is_created_from_a_desk_application(self, db):
        """The real create_case accepts what the desk builds, for both countries."""
        for country, region, answers, policy in (
            ("US", "NY", ANSWERS, "business_onboarding_us"),
            (
                "CA",
                "QC",
                {
                    "legal_name": "Café Nord Inc",
                    "tax_id": "123456789",
                    "registration_number": "1171234567",
                    "owners": [
                        {
                            "name": "Camille Exemple",
                            "kind": "person",
                            "role": "owner",
                            "ownership_pct": None,
                            "date_of_birth": None,
                        }
                    ],
                },
                "business_onboarding_ca",
            ),
        ):
            row = app_row(
                country=country, region=region, answers_ciphertext=json.dumps({"values": answers, "sources": {}})
            )
            session = db([row], [row])
            ref, made = asyncio.run(cases.open_case(TENANT, APP, runtime=Runtime(), now=NOW))
            case = session.added[0]
            assert made and case.case_ref == ref == row.case_ref and case.policy_id == policy
            assert case.state == "submitted" and case.application["legal_name"] == answers["legal_name"]

    def test_the_canadian_policy_is_mapped_and_loads(self):
        from core.cases.runtime import default_policy_id, load_case_policies

        assert default_policy_id("CA-QC") == "business_onboarding_ca"
        policy = load_case_policies()["business_onboarding_ca"]
        assert policy.status == "example" if hasattr(policy, "status") else True


class TestInvestigateAndQueue:
    def test_investigate(self, monkeypatch):
        import core.cases.runtime as runtime_mod

        async def ok(tenant_id, case_ref, *, runtime, actor):
            return {}

        async def refused(tenant_id, case_ref, *, runtime, actor):
            raise CaseError("transition_not_allowed", status=409)

        monkeypatch.setattr(runtime_mod, "investigate_case", ok)
        assert asyncio.run(cases.investigate(TENANT, "case_x", runtime=Runtime())) == {
            "case_ref": "case_x",
            "investigated": True,
        }
        monkeypatch.setattr(runtime_mod, "investigate_case", refused)
        assert asyncio.run(cases.investigate(TENANT, "case_x", runtime=Runtime()))["reason"] == "transition_not_allowed"

    def test_open_and_investigate_follows_the_settings(self, monkeypatch):
        investigated = []

        async def open_case(tid, aid, *, runtime=None):
            return "case_x", True

        async def run(tid, ref, *, runtime=None):
            investigated.append(ref)
            return {"case_ref": ref, "investigated": True}

        async def settings_on(tid):
            return cases.CaseSettings(auto_open=True, investigate=True)

        monkeypatch.setattr(cases, "open_case", open_case)
        monkeypatch.setattr(cases, "investigate", run)
        monkeypatch.setattr(cases, "get_case_settings", settings_on)
        assert asyncio.run(cases.open_and_investigate(TENANT, APP))["investigated"] is True
        assert asyncio.run(cases.open_and_investigate(TENANT, APP, investigate_now=False))["investigated"] is False
        assert investigated == ["case_x"]

    def test_after_submit_queues_only_when_asked(self, monkeypatch):
        from core.tasks import onboarding_desk_tasks as tasks

        queued = []
        state = {"settings": cases.CaseSettings()}

        async def get_settings(tid):
            return state["settings"]

        monkeypatch.setattr(cases, "get_case_settings", get_settings)
        monkeypatch.setattr(tasks.open_case_task, "delay", lambda *args: queued.append(args))
        assert asyncio.run(cases.after_submit(TENANT, APP)) is False and queued == []
        state["settings"] = cases.CaseSettings(auto_open=True)
        assert asyncio.run(cases.after_submit(TENANT, APP)) is True and queued == [(str(TENANT), str(APP))]

        def broken(*args):
            raise ConnectionError("broker down")

        monkeypatch.setattr(tasks.open_case_task, "delay", broken)
        assert asyncio.run(cases.after_submit(TENANT, APP)) is False

    def test_the_task(self, monkeypatch):
        from core.tasks import onboarding_desk_tasks as tasks

        monkeypatch.setattr(settings, "onboarding_desk_enabled", False)
        assert tasks.open_case_task.run(str(TENANT), str(APP)) == {"skipped": "onboarding_desk_disabled"}
        monkeypatch.setattr(settings, "onboarding_desk_enabled", True)
        monkeypatch.setattr(tasks, "run_async", lambda coro: asyncio.run(coro))
        outcomes = iter([{"case_ref": "case_x"}, DeskError(409, "governed_cases_disabled", "off")])

        async def go(tid, aid):
            item = next(outcomes)
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr(cases, "open_and_investigate", go)
        assert tasks.open_case_task.run(str(TENANT), str(APP)) == {"case_ref": "case_x"}
        assert tasks.open_case_task.run(str(TENANT), str(APP)) == {"opened": False, "reason": "governed_cases_disabled"}


def governed(**overrides):
    base = {
        "case_ref": "case_" + "a" * 24,
        "purpose": "aml.cdd.onboarding",
        "state": "decided",
        "provider": "mock",
        "policy_id": "business_onboarding_us",
        "application": {"legal_name": "Northwind Coffee LLC"},
        "subject": None,
        "memo": {"recommendation": {"proposed": "approve"}},
        "policy_result": {"tier": "low"},
        "screening_results": [{"hit": 1}],
        "screening_dispositions": [],
        "decision": {
            "outcome": "approve",
            "approvers": [{"approver": "u", "decision_grant_id": "g"}],
            "decided_at": (NOW - timedelta(hours=1)).isoformat(),
        },
        "failure_reason": None,
        "agent_records": [{"tool_calls": [{}, {}, {}]}, {"tool_calls": [{}]}],
        "created_at": NOW - timedelta(hours=2),
        "completed_at": NOW - timedelta(hours=1),
        "updated_at": NOW,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestCaseViews:
    def test_case_view_and_lookups(self, db):
        view = cases.case_view(governed())
        assert view["recommendation"] == "approve" and view["tier"] == "low" and view["decision"] == "approve"
        empty = cases.case_view(governed(memo=None, policy_result=None, decision=None, updated_at=None))
        assert empty["recommendation"] is None and empty["decision"] is None and empty["updated_at"] is None
        db([governed()])
        assert asyncio.run(cases.get_case(TENANT, "case_x")).state == "decided"
        db([app_row(case_ref="case_x")])
        assert asyncio.run(cases.application_for_case(TENANT, "case_x")).case_ref == "case_x"
        db([])
        with pytest.raises(DeskError, match="no application"):
            asyncio.run(cases.application_for_case(TENANT, "case_x"))


# ── Evidence ─────────────────────────────────────────────────────────────────


@pytest.fixture
def pack_inputs(monkeypatch):
    lead = SimpleNamespace(
        id=LEAD,
        source_kind="webhook",
        source_id=uuid.uuid4(),
        external_ref="form-1",
        business_name="Northwind",
        email_masked="o***@n.example",
        phone_masked="***0134",
        status="submitted",
        created_at=NOW - timedelta(days=1),
    )
    consent = SimpleNamespace(
        id=uuid.uuid4(),
        channel="sms",
        purpose="onboarding",
        wording="Yes, text me.",
        wording_sha256="ab" * 32,
        method="web_form",
        source_ref="https://p.example",
        written=False,
        captured_at=NOW - timedelta(days=1),
        expires_at=None,
        status="granted",
        withdrawn_at=None,
        withdrawn_via=None,
    )
    state = {"row": app_row(case_ref="case_" + "a" * 24), "case": governed(), "export": None}

    async def get_application(tid, aid):
        return state["row"]

    async def get_lead(tid, lid):
        return lead, [consent]

    async def attempts(tid, lid):
        return [
            SimpleNamespace(
                id=uuid.uuid4(),
                channel="sms",
                kind="first_contact",
                status="sent",
                refusal_reason=None,
                provider_ref="SM1",
                attempt_no=1,
                created_at=NOW,
                sent_at=NOW,
            )
        ]

    async def docs(tid, aid):
        return [
            SimpleNamespace(
                id=uuid.uuid4(),
                item="formation_document",
                filename="a.pdf",
                mime_type="application/pdf",
                size_bytes=5,
                extraction_status="suggested",
                created_at=NOW,
                sha256="cd" * 32,
            )
        ]

    async def handoffs(tid, aid):
        return [SimpleNamespace(channel="web", status="resolved", created_at=NOW, resolved_at=NOW, resolved_by="u")]

    async def get_case(tid, ref):
        return state["case"]

    async def identity(text):
        return text

    async def export(ref):
        return state["export"] or {"status": "off"}

    monkeypatch.setattr(applications, "get_application", get_application)
    monkeypatch.setattr(store, "get_lead", get_lead)
    monkeypatch.setattr(store, "list_attempts", attempts)
    monkeypatch.setattr(store, "_decrypt", identity)
    monkeypatch.setattr(applications, "_documents", docs)
    monkeypatch.setattr(evidence, "_handoffs", handoffs)
    monkeypatch.setattr(cases, "get_case", get_case)
    monkeypatch.setattr(evidence, "_case_export", export)
    return state


class TestEvidence:
    def test_a_signed_pack_verifies_offline_and_tampering_is_caught(self, pack_inputs, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "onboarding_desk_evidence_signing_key", SEED)
        pack = asyncio.run(evidence.build_pack(TENANT, APP, now=NOW))
        assert set(pack["sections"]) == set(evidence.SECTIONS)
        sections = pack["sections"]
        assert sections["consents"][0]["wording"] == "Yes, text me." and sections["lead"]["external_ref"] == "form-1"
        assert sections["documents"][0]["sha256"] == "cd" * 32 and sections["transcript"][0]["text"] == "hi"
        assert sections["case"]["decision"]["outcome"] == "approve" and sections["case_evidence"] == {"status": "off"}
        assert sections["application"]["answers"]["tax_id"] == "12-3456789"
        key = evidence.public_key()
        assert key["alg"] == "Ed25519" and pack["signature"]["key_id"] == key["key_id"]
        assert evidence.verify(pack, key["public_key"]) == []
        assert evidence.verify(json.loads(json.dumps(pack, default=str)), key["public_key"]) == []

        tampered = json.loads(json.dumps(pack, default=str))
        tampered["sections"]["consents"][0]["wording"] = "Something else"
        assert evidence.verify(tampered, key["public_key"]) == ["section consents does not match its digest"]
        resigned = json.loads(json.dumps(pack, default=str))
        resigned["manifest"]["sections"]["consents"] = evidence.digest(tampered["sections"]["consents"])
        resigned["sections"] = tampered["sections"]
        assert evidence.verify(resigned, key["public_key"]) == ["signature does not verify"]
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        other = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        assert evidence.verify(pack, base64.b64encode(other).decode()) == ["signed with a different key"]

        from scripts.verify_onboarding_evidence import main

        path = tmp_path / "pack.json"
        path.write_text(json.dumps(pack, default=str), encoding="utf-8")
        assert main([str(path), "--public-key", key["public_key"]]) == 0
        assert main([str(path)]) == 0
        path.write_text(json.dumps(tampered), encoding="utf-8")
        assert main([str(path), "--public-key", key["public_key"]]) == 1
        assert main([str(tmp_path / "missing.json")]) == 1

    def test_unsigned_packs_and_bad_input(self, pack_inputs, monkeypatch):
        monkeypatch.setattr(settings, "onboarding_desk_evidence_signing_key", "")
        pack_inputs["row"] = app_row(case_ref=None)
        pack = asyncio.run(evidence.build_pack(TENANT, APP, now=NOW))
        assert pack["signature"] is None and evidence.public_key() is None
        assert pack["sections"]["case"] is None and pack["sections"]["case_evidence"] == {"status": "no_case"}
        assert evidence.verify(pack) == [] and evidence.verify(pack, base64.b64encode(b"x" * 32).decode()) == [
            "pack is not signed"
        ]
        assert evidence.verify([]) == ["pack must have exactly manifest, signature and sections"]
        assert evidence.verify({"manifest": {}, "signature": None, "sections": {}}) == [
            f"manifest schema must be {evidence.SCHEMA}"
        ]
        missing = json.loads(json.dumps(pack, default=str))
        del missing["sections"]["lead"]
        assert "sections differ from the manifest" in evidence.verify(missing)
        monkeypatch.setattr(settings, "onboarding_desk_evidence_signing_key", "not base64!")
        with pytest.raises(DeskError, match="seed"):
            evidence.public_key()
        pack_inputs["row"] = app_row(case_ref="case_gone")
        pack_inputs["case"] = None
        monkeypatch.setattr(settings, "onboarding_desk_evidence_signing_key", "")
        assert asyncio.run(evidence.build_pack(TENANT, APP))["sections"]["case_evidence"] == {
            "status": "case_not_found"
        }

    def test_the_grantex_case_export(self, monkeypatch):
        import core.cases.evidence as case_evidence

        monkeypatch.setattr(case_evidence, "evidence_service", lambda: None)
        assert asyncio.run(evidence._case_export("case_x")) == {"status": "off"}

        class Service:
            def __init__(self, fail=False):
                self.fail = fail

            async def export(self, ref, *, sign):
                if self.fail:
                    raise case_evidence.EvidenceServiceError("evidence_service_unavailable", "x", status=502)
                return case_evidence.ExportedEvidence(data=b"{}", root="sha256:" + "a" * 64, anchor_hash="b" * 64)

        monkeypatch.setattr(case_evidence, "evidence_service", lambda: Service())
        included = asyncio.run(evidence._case_export("case_x"))
        assert included["status"] == "included" and base64.b64decode(included["export"]) == b"{}"
        monkeypatch.setattr(case_evidence, "evidence_service", lambda: Service(fail=True))
        assert asyncio.run(evidence._case_export("case_x")) == {
            "status": "unavailable",
            "reason": "evidence_service_unavailable",
        }

    def test_handoff_rows(self, db):
        db([SimpleNamespace(channel="sms")])
        assert len(asyncio.run(evidence._handoffs(TENANT, APP))) == 1


# ── Reports ──────────────────────────────────────────────────────────────────


PERIOD = reports.Period(NOW - timedelta(days=30), NOW)


def lead(status="submitted", hours_ago=10):
    return SimpleNamespace(id=uuid.uuid4(), status=status, created_at=NOW - timedelta(hours=hours_ago))


class TestReports:
    def test_summarise(self):
        a, b, c, d = lead(hours_ago=50), lead(hours_ago=30), lead("contacted"), lead("opted_out")
        apps = [
            app_row(lead_id=a.id, case_ref="case_a", submitted_at=a.created_at + timedelta(hours=4)),
            app_row(lead_id=b.id, case_ref="case_b", submitted_at=b.created_at + timedelta(hours=10)),
            app_row(lead_id=c.id, status="in_progress", submitted_at=None),
            app_row(lead_id=uuid.uuid4(), case_ref="case_other"),
        ]
        consents = [
            SimpleNamespace(lead_id=a.id, channel="sms", status="granted", expires_at=None),
            SimpleNamespace(lead_id=a.id, channel="email", status="granted", expires_at=None),
            SimpleNamespace(lead_id=b.id, channel="email", status="granted", expires_at=NOW - timedelta(days=1)),
            SimpleNamespace(lead_id=c.id, channel="sms", status="withdrawn", expires_at=None),
            SimpleNamespace(lead_id=d.id, channel="whatsapp", status="granted", expires_at=NOW + timedelta(days=9)),
            SimpleNamespace(lead_id=uuid.uuid4(), channel="sms", status="granted", expires_at=None),
        ]
        decided_at = (a.created_at + timedelta(hours=10)).isoformat()
        governed_cases = [
            governed(case_ref="case_a", decision={"outcome": "approve", "decided_at": decided_at}),
            governed(case_ref="case_b", state="awaiting_decision", decision=None, agent_records=[]),
            governed(case_ref="case_other", decision={"outcome": "decline", "decided_at": decided_at}),
        ]
        report = reports.summarise(PERIOD, [a, b, c, d], apps, consents, governed_cases)
        funnel = report["funnel"]
        assert funnel["leads"] == 4 and funnel["leads_by_status"] == {"contacted": 1, "opted_out": 1, "submitted": 2}
        assert funnel["applications"] == 3 and funnel["applications_by_status"] == {"in_progress": 1, "submitted": 2}
        assert funnel["cases_opened"] == 2 and funnel["cases_by_state"] == {"awaiting_decision": 1, "decided": 1}
        assert funnel["approved"] == 1 and funnel["declined"] == 0
        assert report["hours_to_submission"] == {"count": 2, "median": 4.0, "p90": 10.0}
        assert report["hours_to_decision"] == {"count": 1, "median": 6.0, "p90": 6.0}
        coverage = report["consent_coverage"]
        assert coverage == {"any_channel": 0.5, "sms": 0.25, "email": 0.25, "whatsapp": 0.25, "voice": 0.0}
        assert report["checks_per_merchant"] == {
            "cases_with_checks": 1,
            "provider_calls_mean": 4.0,
            "provider_calls_max": 4,
            "screening_results_mean": 1.0,
        }
        assert report["truncated"] is False and report["period"]["until"] == NOW.isoformat()

    def test_an_empty_period(self):
        report = reports.summarise(PERIOD, [], [], [], [])
        assert report["funnel"]["leads"] == 0 and report["consent_coverage"]["any_channel"] is None
        assert report["hours_to_decision"]["median"] is None
        assert report["checks_per_merchant"]["provider_calls_mean"] is None

    def test_periods_and_times(self):
        assert reports.period_from(None, None, now=NOW) == reports.Period(NOW - timedelta(days=30), NOW)
        for since, until in (
            (NOW, NOW),
            (NOW - timedelta(days=400), NOW),
            (NOW.replace(tzinfo=None), NOW + timedelta(days=1)),
        ):
            with pytest.raises(DeskError) as caught:
                reports.period_from(since, until)
            assert caught.value.code == "period_invalid"
        assert reports.parse_time(None) is None and reports.parse_time("") is None
        assert reports.parse_time("2026-10-01T00:00:00Z") == datetime(2026, 10, 1, tzinfo=UTC)
        with pytest.raises(DeskError):
            reports.parse_time("yesterday")
        assert reports._parse(42) is None and reports._hours(NOW, NOW - timedelta(hours=1)) is None

    def test_summary_reads_the_rows(self, db):
        first = lead()
        app = app_row(lead_id=first.id, case_ref="case_a")
        db([first], [app], [], [governed(case_ref="case_a")])
        report = asyncio.run(reports.summary(TENANT, PERIOD))
        assert report["funnel"]["cases_opened"] == 1 and report["funnel"]["approved"] == 1
        db([])
        assert asyncio.run(reports.summary(TENANT, PERIOD))["funnel"]["leads"] == 0


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


class TestRoutes:
    def test_case_settings_and_opening(self, client, monkeypatch):
        investigated = []

        async def get_settings(tid):
            return cases.CaseSettings()

        async def put_settings(tid, raw, *, actor):
            return cases.case_settings_from(raw)

        async def open_case(tid, aid, *, actor):
            if aid != APP:
                raise DeskError(409, "application_not_submitted", "x")
            return "case_x", True

        async def run(tid, ref, *, actor=None, runtime=None):
            investigated.append(ref)
            return {}

        monkeypatch.setattr(cases, "get_case_settings", get_settings)
        monkeypatch.setattr(cases, "put_case_settings", put_settings)
        monkeypatch.setattr(cases, "open_case", open_case)
        monkeypatch.setattr(cases, "investigate", run)
        base = "/api/v1/onboarding-desk"
        assert client.get(f"{base}/case-settings").json() == {"auto_open": False, "investigate": False}
        assert client.put(f"{base}/case-settings", json={"auto_open": True}).json()["auto_open"] is True
        assert client.put(f"{base}/case-settings", json={"auto_open": "yes"}).status_code == 422
        opened = client.post(f"{base}/applications/{APP}/case", json={"investigate": True}).json()
        assert opened == {"case_ref": "case_x", "created": True, "investigation_scheduled": True}
        assert investigated == ["case_x"]
        assert client.post(f"{base}/applications/{APP}/case", json={}).json()["investigation_scheduled"] is False
        assert client.post(f"{base}/applications/{uuid.uuid4()}/case", json={}).status_code == 409

    def test_case_lookup_application_view_and_submit_hook(self, client, monkeypatch):
        row = app_row(case_ref="case_x")
        queued = []

        async def for_case(tid, ref):
            if ref != "case_x":
                raise DeskError(404, "case_not_found", "x")
            return row

        async def get_lead(tid, lid):
            return SimpleNamespace(id=LEAD, external_ref="form-1", source_kind="webhook"), []

        async def get_case(tid, ref):
            return governed(case_ref=ref) if ref == "case_x" else None

        async def get_application(tid, aid):
            return row

        async def state(tid, r, *, operator=False):
            return {"id": str(r.id)}

        monkeypatch.setattr(cases, "application_for_case", for_case)
        monkeypatch.setattr(store, "get_lead", get_lead)
        monkeypatch.setattr(cases, "get_case", get_case)
        monkeypatch.setattr(applications, "get_application", get_application)
        monkeypatch.setattr(applications, "state", state)
        base = "/api/v1/onboarding-desk"
        found = client.get(f"{base}/cases/case_x").json()
        assert found["lead"]["external_ref"] == "form-1" and found["case"]["decision"] == "approve"
        assert found["application"]["case_ref"] == "case_x"
        assert client.get(f"{base}/cases/case_y").status_code == 404
        assert client.get(f"{base}/applications/{APP}").json()["case"]["state"] == "decided"
        row.case_ref = "case_gone"
        assert client.get(f"{base}/applications/{APP}").json()["case"] == {
            "case_ref": "case_gone",
            "state": "not_found",
        }
        row.case_ref = None
        assert client.get(f"{base}/applications/{APP}").json()["case"] is None

        from core.onboarding_desk import tokens

        async def open_for_lead(tid, lid, *, now=None):
            return row

        async def submit(tid, aid, *, now=None):
            return row

        outcomes = iter([True, DeskError(500, "x", "y")])

        async def after(tid, aid):
            item = next(outcomes)
            queued.append(aid)
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr(applications, "open_for_lead", open_for_lead)
        monkeypatch.setattr(applications, "submit", submit)
        monkeypatch.setattr(cases, "after_submit", after)
        headers = {"X-Applicant-Session": tokens.sign("session", TENANT, LEAD)}
        assert client.post(f"{base}/apply/submit", headers=headers).status_code == 200
        assert client.post(f"{base}/apply/submit", headers=headers).status_code == 200
        assert queued == [APP, APP]

    def test_evidence_download_key_and_report(self, client, monkeypatch):
        async def build(tid, aid):
            if aid != APP:
                raise DeskError(404, "application_not_found", "x")
            return {"manifest": {"application_id": str(aid)}, "signature": None, "sections": {}}

        async def summary(tid, period):
            return {"period": period.to_dict()}

        monkeypatch.setattr(evidence, "build_pack", build)
        monkeypatch.setattr(reports, "summary", summary)
        base = "/api/v1/onboarding-desk"
        pack = client.get(f"{base}/applications/{APP}/evidence")
        assert pack.json()["manifest"]["application_id"] == str(APP)
        assert pack.headers["content-disposition"] == f'attachment; filename="onboarding-evidence-{APP}.json"'
        assert client.get(f"{base}/applications/{uuid.uuid4()}/evidence").status_code == 404
        monkeypatch.setattr(settings, "onboarding_desk_evidence_signing_key", "")
        assert client.get(f"{base}/evidence-key").json()["detail"]["error"] == "evidence_unsigned"
        monkeypatch.setattr(settings, "onboarding_desk_evidence_signing_key", SEED)
        assert client.get(f"{base}/evidence-key").json()["alg"] == "Ed25519"
        monkeypatch.setattr(settings, "onboarding_desk_evidence_signing_key", "bad key")
        assert client.get(f"{base}/evidence-key").status_code == 503
        report = client.get(f"{base}/reports/summary?since=2026-10-01T00:00:00Z&until=2026-10-20T00:00:00Z").json()
        assert report["period"]["since"].startswith("2026-10-01")
        assert client.get(f"{base}/reports/summary").status_code == 200
        assert client.get(f"{base}/reports/summary?since=nope").status_code == 422
        monkeypatch.setattr(settings, "onboarding_desk_enabled", False)
        for path in (
            "/case-settings",
            f"/applications/{APP}/evidence",
            "/evidence-key",
            "/reports/summary",
            "/cases/case_x",
        ):
            assert client.get(f"{base}{path}").status_code == 404, path

    def test_middleware_exempts_only_the_key(self):
        from auth.grantex_middleware import GrantexAuthMiddleware

        prefixes = GrantexAuthMiddleware.EXEMPT_PREFIXES
        assert "/api/v1/onboarding-desk/evidence-key" in prefixes
        assert not any(p.startswith("/api/v1/onboarding-desk/applications") for p in prefixes)


class TestMigration:
    def test_v6z99(self, monkeypatch):
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z99_onboarding_cases.py"
        spec = importlib.util.spec_from_file_location("v6_z99_onboarding_cases", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        assert module.revision == "v6z99_onboarding_cases"
        assert module.down_revision == "v6z98_onboarding_applications"
        statements = []
        monkeypatch.setattr(module.op, "execute", lambda sql: statements.append(" ".join(str(sql).split())))
        module.upgrade()
        joined = "\n".join(statements)
        for column in ("case_ref", "case_opened_at", "case_error"):
            assert f"ADD COLUMN IF NOT EXISTS {column}" in joined
        assert "WHERE case_ref IS NOT NULL" in joined
        statements.clear()
        module.downgrade()
        assert len(statements) == 4
        assert questions.ITEMS  # the catalogue the case application is built from is loaded
