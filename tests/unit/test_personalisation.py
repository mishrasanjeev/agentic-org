# SPDX-License-Identifier: Apache-2.0
"""Personalisation: consent validity, refusals recorded, rules by priority, allowed attributes, encrypted profiles."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import operators
from sqlalchemy.sql.dml import Insert
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList, Grouping

from api.v1 import personalisation as api
from core.config import settings
from core.personalisation import rules as checks
from core.personalisation import service
from core.personalisation.rules import PersonalisationError

TENANT = uuid.uuid4()
USER = {"agenticorg:user_id": str(uuid.uuid4())}
ACTOR = USER["agenticorg:user_id"]
SUBJECT = "CUST-000123"


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


def _matches(clause, row) -> bool:
    if clause is None:
        return True
    if isinstance(clause, BooleanClauseList):
        return all(_matches(part, row) for part in clause.clauses)
    if isinstance(clause, Grouping):
        return _matches(clause.element, row)
    if isinstance(clause, BinaryExpression) and clause.operator is operators.eq:
        return getattr(row, clause.left.name) == clause.right.value
    raise NotImplementedError(str(clause))


_KEYS = {
    "personalisation_consents": ("tenant_id", "subject_ref", "purpose"),
    "personalisation_profiles": ("tenant_id", "subject_ref"),
}


class _Session:
    def __init__(self):
        self.rows = []
        self.log = []

    async def execute(self, statement):
        self.log.append("execute")
        if isinstance(statement, Insert):
            from core.models.personalisation import PersonalisationConsent, PersonalisationProfile

            table = statement.table.name
            values = statement.compile(dialect=postgresql.dialect()).params
            model = {
                "personalisation_consents": PersonalisationConsent,
                "personalisation_profiles": PersonalisationProfile,
            }[table]
            assert statement._post_values_clause is not None  # on_conflict_do_nothing
            key = _KEYS[table]
            if not any(r.__tablename__ == table and all(getattr(r, c) == values[c] for c in key) for r in self.rows):
                self.rows.append(model(**values))
            return _Result([])
        table = statement.get_final_froms()[0].name
        return _Result([r for r in self.rows if r.__tablename__ == table and _matches(statement.whereclause, r)])

    def add(self, row):
        row.id = row.id or uuid.uuid4()
        self.rows.append(row)

    async def delete(self, row):
        self.rows.remove(row)

    async def flush(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def of(self, table):
        return [r for r in self.rows if r.__tablename__ == table]


@pytest.fixture
def session(monkeypatch):
    import core.database

    store = _Session()
    monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: store)
    monkeypatch.setattr(settings, "personalisation_enabled", True)

    async def encrypt(text, tenant_id):
        store.log.append("encrypt")
        return "enc:" + text[::-1]

    monkeypatch.setattr(service, "encrypt_for_tenant", encrypt)
    monkeypatch.setattr(service, "decrypt_for_tenant", lambda text: text[4:][::-1])
    return store


def _rule(name, *, purpose="marketing", priority=100, conditions=None, template="Hello {{first_name}}", **over):
    raw = {
        "name": name,
        "purpose": purpose,
        "priority": priority,
        "conditions": conditions or [],
        "variant": {"template": template, "label": name.upper()},
        "allowed_attributes": ["first_name", "segment", "balance", "city"],
    }
    raw.update(over)
    return raw


PROFILE = {"first_name": "Asha", "segment": "gold", "balance": 250000.0, "city": "Pune", "salaried": True}


async def _grant(purpose="marketing", subject=SUBJECT, **over):
    return await service.grant_consent(
        TENANT, subject, purpose, evidence=over.pop("evidence", "branch form 7, 2026-10-01"), actor=ACTOR, **over
    )


# ---------------------------------------------------------------- checks


class TestChecks:
    def test_a_subject_is_the_tenants_reference_never_an_email(self):
        assert checks.check_subject("  CUST-1:a.b_c ") == "CUST-1:a.b_c"
        for bad in ("", "someone@example.com", "x" * 129, "-lead", "a b", None):
            with pytest.raises(PersonalisationError) as info:
                checks.check_subject(bad)
            assert info.value.code == "subject_invalid" and info.value.status == 422

    def test_purposes_and_channels_are_closed_lists(self):
        assert checks.check_purpose(" Marketing ") == "marketing"
        assert checks.check_channel("SMS") == "sms"
        with pytest.raises(PersonalisationError) as info:
            checks.check_purpose("profiling")
        assert info.value.code == "purpose_unknown"
        with pytest.raises(PersonalisationError) as info:
            checks.check_channel("pigeon")
        assert info.value.code == "channel_unknown"

    def test_a_profile_is_flat_and_bounded(self):
        assert checks.check_attributes({"first_name": "Asha", "age": 41, "vip": False}) == {
            "first_name": "Asha",
            "age": 41,
            "vip": False,
        }
        bad = [
            ([], "profile_invalid"),
            ({"Bad-Name": 1}, "attribute_invalid"),
            ({"nested": {"a": 1}}, "value_invalid"),
            ({"items": [1]}, "value_invalid"),
            ({"note": "x" * (checks.MAX_VALUE + 1)}, "value_invalid"),
            ({f"a{i}": i for i in range(checks.MAX_PROFILE_ATTRIBUTES + 1)}, "profile_too_large"),
            ({f"a{i}": "x" * 400 for i in range(60)}, "profile_too_large"),
            ({"nan": float("nan")}, "value_invalid"),
        ]
        for raw, code in bad:
            with pytest.raises(PersonalisationError) as info:
                checks.check_attributes(raw)
            assert info.value.code == code, raw

    def test_placeholders_are_names_in_order_and_malformed_ones_are_refused(self):
        assert checks.placeholders("Hi {{ first_name }}, {{city}} {{first_name}}") == ["first_name", "city"]
        assert checks.placeholders("no placeholders {here}") == []
        with pytest.raises(PersonalisationError) as info:
            checks.placeholders("Hi {{ First-Name }}")
        assert info.value.code == "placeholder_invalid"
        for broken in ("Hi {{first_name", "Hi first_name}}", "{{a}} {{"):
            with pytest.raises(PersonalisationError) as info:
                checks.placeholders(broken)
            assert info.value.code == "template_invalid"
        with pytest.raises(PersonalisationError) as info:
            checks.check_template("x" * (checks.MAX_TEMPLATE + 1))
        assert info.value.code == "template_too_long"
        for empty in ("", "   ", None, 5):
            with pytest.raises(PersonalisationError) as info:
                checks.check_template(empty)
            assert info.value.code == "template_invalid"

    def test_a_template_renders_only_allowed_and_present_attributes(self):
        content, used = checks.render_template(
            "Dear {{first_name}}, balance {{balance}}, salaried {{salaried}}, score {{score}}",
            {"first_name": "Asha", "balance": 2500.0, "salaried": True, "score": 7.5},
            ["first_name", "balance", "salaried", "score"],
        )
        assert content == "Dear Asha, balance 2500, salaried yes, score 7.5"
        assert used == ["first_name", "balance", "salaried", "score"]
        assert checks.render_template("{{a}}", {"a": False}, ["a"])[0] == "no"
        with pytest.raises(PersonalisationError) as info:
            checks.render_template("Dear {{first_name}} of {{city}}", {"first_name": "A", "city": "P"}, ["first_name"])
        assert info.value.code == "placeholder_not_allowed" and "city" in info.value.message
        for profile in ({}, {"first_name": ""}, {"first_name": None}):
            with pytest.raises(PersonalisationError) as info:
                checks.render_template("Dear {{first_name}}", profile, ["first_name"])
            assert info.value.code == "placeholder_unresolved"
        with pytest.raises(PersonalisationError) as info:
            checks.render_template("{{a}}{{a}}{{a}}", {"a": "x" * 3000}, ["a"])
        assert info.value.code == "output_too_long"
        assert checks.content_hash("abc") == hashlib.sha256(b"abc").hexdigest()

    def test_conditions_are_checked(self):
        assert checks.check_condition({"attribute": "vip", "op": "exists"}) == {
            "attribute": "vip",
            "op": "exists",
            "value": True,
        }
        assert checks.check_condition({"attribute": "city", "op": "IN", "value": ["Pune", 4]})["value"] == ["Pune", 4]
        assert checks.check_condition({"attribute": "balance", "op": "gte", "value": 10})["value"] == 10
        assert checks.check_condition({"attribute": "since", "op": "lte", "value": "2026-01-01"})["op"] == "lte"
        bad = [
            "x",
            {"attribute": "Bad", "op": "eq", "value": 1},
            {"attribute": "a", "op": "like", "value": 1},
            {"attribute": "a", "op": "exists", "value": "yes"},
            {"attribute": "a", "op": "in", "value": []},
            {"attribute": "a", "op": "in", "value": "Pune"},
            {"attribute": "a", "op": "in", "value": [{"x": 1}]},
            {"attribute": "a", "op": "gte", "value": True},
            {"attribute": "a", "op": "lte", "value": [1]},
            {"attribute": "a", "op": "eq", "value": None},
        ]
        for raw in bad:
            with pytest.raises(PersonalisationError):
                checks.check_condition(raw)

    def test_conditions_match_by_operator_and_kind(self):
        profile = {"segment": "gold", "balance": 250000.0, "vip": True, "since": "2020-05-01", "blank": ""}

        def m(attribute, op, value=None):
            return checks.condition_matches({"attribute": attribute, "op": op, "value": value}, profile)

        assert m("segment", "eq", "gold") and not m("segment", "eq", "silver")
        assert m("segment", "ne", "silver") and not m("segment", "ne", "gold")
        assert m("segment", "in", ["gold", "platinum"]) and not m("segment", "in", ["silver"])
        assert m("balance", "gte", 100000) and not m("balance", "lte", 100000) and m("balance", "lte", 250000)
        assert m("since", "lte", "2021-01-01") and not m("since", "gte", "2021-01-01")
        assert m("vip", "eq", True) and not m("vip", "eq", 1)  # a flag is not a number
        assert not m("balance", "gte", "100")  # a number is not text
        assert not m("vip", "in", [1])
        assert m("vip", "exists", True) and m("missing", "exists", False) and m("blank", "exists", False)
        assert not m("missing", "eq", "x") and not m("missing", "ne", "x")
        rule = {"conditions": [{"attribute": "segment", "op": "eq", "value": "gold"}]}
        assert checks.rule_matches(rule, profile) and checks.rule_matches({"conditions": []}, {})

    def test_a_rule_is_checked_whole_and_declares_what_it_reads(self):
        checked = checks.check_rule(
            {
                "name": " Gold welcome ",
                "purpose": "marketing",
                "conditions": [{"attribute": "segment", "op": "eq", "value": "gold"}],
                "variant": {"template": "Hi {{first_name}}"},
                "allowed_attributes": ["first_name", "segment", "first_name"],
            }
        )
        assert checked["name"] == "Gold welcome" and checked["priority"] == 100 and checked["enabled"] is True
        assert checked["variant"] == {"template": "Hi {{first_name}}", "label": "Gold welcome"}
        assert checked["allowed_attributes"] == ["first_name", "segment"]
        with pytest.raises(PersonalisationError) as info:
            checks.check_rule(_rule("r", allowed_attributes=["segment"]))
        assert info.value.code == "attribute_not_allowed" and "first_name" in info.value.message
        with pytest.raises(PersonalisationError) as info:
            checks.check_rule(
                _rule(
                    "r",
                    allowed_attributes=["first_name"],
                    conditions=[{"attribute": "segment", "op": "eq", "value": "gold"}],
                )
            )
        assert info.value.code == "attribute_not_allowed" and "segment" in info.value.message
        bad = [
            "rule",
            _rule(""),
            _rule("x" * 101),
            _rule("r", priority=-1),
            _rule("r", priority=True),
            _rule("r", priority="1"),
            _rule("r", enabled="yes"),
            _rule("r", conditions="segment"),
            _rule("r", variant="Hi"),
            _rule("r", allowed_attributes="first_name"),
            _rule("r", allowed_attributes=["x"] * (checks.MAX_ALLOWED + 1)),
            _rule("r", purpose="profiling"),
        ]
        for raw in bad:
            with pytest.raises(PersonalisationError) as info:
                checks.check_rule(raw)
            assert info.value.status == 422

    def test_times_are_parsed(self):
        assert checks.parse_time(None, "t") is None and checks.parse_time("", "t") is None
        assert checks.parse_time("2026-10-01T09:00:00Z", "t") == datetime(2026, 10, 1, 9, tzinfo=UTC)
        assert checks.parse_time("2026-10-01T09:00:00", "t").tzinfo is UTC
        assert checks.parse_time(datetime(2026, 10, 1, tzinfo=UTC).replace(tzinfo=None), "t").tzinfo is UTC
        with pytest.raises(PersonalisationError) as info:
            checks.parse_time("soon", "t")
        assert info.value.code == "time_invalid"

    def test_the_console_allow_list_exists_only_while_personalisation_is_on(self, monkeypatch):
        from core.workbench import console

        monkeypatch.setattr(settings, "personalisation_enabled", False)
        assert service.CALLER_TEMPLATE_SETTING not in console.definitions()
        monkeypatch.setattr(settings, "personalisation_enabled", True)
        setting = console.definition(service.CALLER_TEMPLATE_SETTING)
        assert setting.group in {key for key, _ in console.GROUPS} and setting.default == []
        assert console.check(service.CALLER_TEMPLATE_SETTING, ["first_name"]) == ["first_name"]


# ---------------------------------------------------------------- consents


class TestConsents:
    def test_validity_is_granted_not_withdrawn_and_not_expired(self):
        from core.models.personalisation import PersonalisationConsent

        now = datetime.now(UTC)

        def row(**over):
            base = {"status": "granted", "withdrawn_at": None, "expires_at": None}
            base.update(over)
            return PersonalisationConsent(**base)

        assert service.consent_valid(row(), now)
        assert service.consent_valid(row(expires_at=now + timedelta(days=1)), now)
        assert not service.consent_valid(row(expires_at=now - timedelta(seconds=1)), now)
        assert not service.consent_valid(row(expires_at=(now - timedelta(days=1)).replace(tzinfo=None)), now)
        assert not service.consent_valid(row(status="withdrawn"), now)
        assert not service.consent_valid(row(withdrawn_at=now), now)
        assert not service.consent_valid(None, now)

    @pytest.mark.asyncio
    async def test_a_grant_is_recorded_once_per_subject_and_purpose_and_withdrawal_keeps_it(self, session):
        granted = await _grant(expires_at=datetime.now(UTC) + timedelta(days=30))
        assert granted["valid"] is True and granted["status"] == "granted" and granted["recorded_by"] == ACTOR
        assert granted["evidence"] == "branch form 7, 2026-10-01" and granted["expires_at"]
        again = await _grant(evidence="mobile app, 2026-10-02")
        assert again["id"] == granted["id"] and again["expires_at"] is None and again["evidence"].startswith("mobile")
        await _grant("service")
        assert len(session.of("personalisation_consents")) == 2
        withdrawn = await service.withdraw_consent(TENANT, SUBJECT, "marketing", actor="")
        assert withdrawn["status"] == "withdrawn" and withdrawn["valid"] is False and withdrawn["withdrawn_at"]
        assert withdrawn["recorded_by"] == ACTOR  # nobody named: the record keeps who recorded it
        repeat = await service.withdraw_consent(TENANT, SUBJECT, "marketing", actor="other-user")
        assert repeat["withdrawn_at"] == withdrawn["withdrawn_at"]
        assert len(session.of("personalisation_consents")) == 2  # the row stays
        listed = await service.list_consents(TENANT, SUBJECT)
        assert [(c["purpose"], c["valid"]) for c in listed] == [("marketing", False), ("service", True)]
        regranted = await _grant()
        assert regranted["valid"] is True and regranted["withdrawn_at"] is None

    @pytest.mark.asyncio
    async def test_a_grant_needs_an_actor_evidence_and_a_future_expiry(self, session):
        with pytest.raises(PersonalisationError) as info:
            await service.grant_consent(TENANT, SUBJECT, "marketing", evidence="form", actor="")
        assert info.value.status == 401 and info.value.code == "actor_required"
        with pytest.raises(PersonalisationError) as info:
            await _grant(evidence="  ")
        assert info.value.code == "evidence_required"
        with pytest.raises(PersonalisationError) as info:
            await _grant(evidence="x" * 501)
        assert info.value.code == "evidence_invalid"
        with pytest.raises(PersonalisationError) as info:
            await _grant(expires_at="2020-01-01T00:00:00Z")
        assert info.value.code == "expiry_past"
        with pytest.raises(PersonalisationError) as info:
            await service.withdraw_consent(TENANT, SUBJECT, "collections")
        assert info.value.status == 404 and info.value.code == "consent_unknown"
        assert session.of("personalisation_consents") == []


# ---------------------------------------------------------------- profiles


class TestProfiles:
    @pytest.mark.asyncio
    async def test_attributes_are_encrypted_at_rest_before_any_lock(self, session):
        kept = await service.put_profile(TENANT, SUBJECT, PROFILE, actor=ACTOR)
        assert kept["attributes"] == sorted(PROFILE) and "Asha" not in json.dumps(kept)
        assert session.log[0] == "encrypt"  # encrypted before the session locked anything
        (row,) = session.of("personalisation_profiles")
        assert set(row.attributes) == {"_encrypted"}
        stored = json.dumps(row.attributes)
        for value in ("Asha", "Pune", "gold", "250000"):
            assert value not in stored
        read = await service.get_profile(TENANT, SUBJECT)
        assert read["attributes"] == PROFILE and read["updated_by"] == ACTOR
        await service.put_profile(TENANT, SUBJECT, {"first_name": "Asha"}, actor=ACTOR)
        assert len(session.of("personalisation_profiles")) == 1
        assert (await service.get_profile(TENANT, SUBJECT))["attributes"] == {"first_name": "Asha"}

    @pytest.mark.asyncio
    async def test_a_profile_write_needs_an_actor_and_an_unreadable_profile_refuses(self, session, monkeypatch):
        with pytest.raises(PersonalisationError) as info:
            await service.put_profile(TENANT, SUBJECT, PROFILE, actor=None)
        assert info.value.code == "actor_required" and session.log == []
        with pytest.raises(PersonalisationError) as info:
            await service.get_profile(TENANT, SUBJECT)
        assert info.value.status == 404 and info.value.code == "profile_unknown"
        await service.put_profile(TENANT, SUBJECT, PROFILE, actor=ACTOR)
        (row,) = session.of("personalisation_profiles")
        row.attributes = {"first_name": "Asha"}  # a plain row is never read
        with pytest.raises(PersonalisationError) as info:
            await service.get_profile(TENANT, SUBJECT)
        assert info.value.status == 500 and info.value.code == "profile_unreadable"
        row.attributes = {"_encrypted": "enc:" + json.dumps(["a"])[::-1]}
        with pytest.raises(PersonalisationError) as info:
            await service.get_profile(TENANT, SUBJECT)
        assert info.value.code == "profile_unreadable"

        def broken(text):
            raise ValueError("bad token")

        monkeypatch.setattr(service, "decrypt_for_tenant", broken)
        row.attributes = {"_encrypted": "enc:xyz"}
        with pytest.raises(PersonalisationError) as info:
            await service.get_profile(TENANT, SUBJECT)
        assert info.value.code == "profile_unreadable"


# ---------------------------------------------------------------- rules


class TestRules:
    @pytest.mark.asyncio
    async def test_rules_are_kept_listed_changed_and_removed(self, session, monkeypatch):
        made = await service.create_rule(TENANT, _rule("welcome", priority=20), actor=ACTOR)
        assert made["updated_by"] == ACTOR and made["variant"]["label"] == "WELCOME"
        await service.create_rule(TENANT, _rule("first", priority=10), actor=ACTOR)
        await service.create_rule(TENANT, _rule("svc", purpose="service"), actor=ACTOR)
        with pytest.raises(PersonalisationError) as info:
            await service.create_rule(TENANT, _rule("welcome"), actor=ACTOR)
        assert info.value.status == 409 and info.value.code == "rule_exists"
        monkeypatch.setattr(checks, "MAX_RULES", 3)
        with pytest.raises(PersonalisationError) as info:
            await service.create_rule(TENANT, _rule("fourth"), actor=ACTOR)
        assert info.value.code == "rules_full"
        assert [r["name"] for r in await service.list_rules(TENANT)] == ["first", "welcome", "svc"]
        assert [r["name"] for r in await service.list_rules(TENANT, purpose="service")] == ["svc"]
        rule_id = uuid.UUID(made["id"])
        changed = await service.update_rule(
            TENANT, rule_id, {"priority": 5, "enabled": False, "name": "x"}, actor=ACTOR
        )
        assert changed["priority"] == 5 and changed["enabled"] is False and changed["name"] == "welcome"
        with pytest.raises(PersonalisationError) as info:
            await service.update_rule(TENANT, rule_id, {"variant": {"template": "Hi {{pan}}"}}, actor=ACTOR)
        assert info.value.code == "attribute_not_allowed"
        with pytest.raises(PersonalisationError) as info:
            await service.update_rule(TENANT, rule_id, ["priority"], actor=ACTOR)
        assert info.value.code == "rule_invalid"
        with pytest.raises(PersonalisationError) as info:
            await service.update_rule(TENANT, uuid.uuid4(), {"priority": 1}, actor=ACTOR)
        assert info.value.status == 404
        for call in (
            service.create_rule(TENANT, _rule("anon"), actor=""),
            service.update_rule(TENANT, rule_id, {"priority": 1}, actor=None),
            service.delete_rule(TENANT, rule_id, actor=""),
        ):
            with pytest.raises(PersonalisationError) as info:
                await call
            assert info.value.code == "actor_required"
        await service.delete_rule(TENANT, rule_id, actor=ACTOR)
        assert [r["name"] for r in await service.list_rules(TENANT)] == ["first", "svc"]


# ---------------------------------------------------------------- rendering


async def _setup(session, *rules, profile=PROFILE, purposes=("marketing",)):
    for purpose in purposes:
        await _grant(purpose)
    if profile is not None:
        await service.put_profile(TENANT, SUBJECT, profile, actor=ACTOR)
    return [await service.create_rule(TENANT, raw, actor=ACTOR) for raw in rules]


class TestRender:
    @pytest.mark.asyncio
    async def test_without_a_valid_consent_the_render_is_refused_and_the_refusal_recorded(self, session):
        await service.put_profile(TENANT, SUBJECT, PROFILE, actor=ACTOR)
        await service.create_rule(TENANT, _rule("welcome"), actor=ACTOR)
        with pytest.raises(PersonalisationError) as info:
            await service.render(TENANT, SUBJECT, "marketing", channel="email", actor=ACTOR)
        assert info.value.status == 403 and info.value.code == "consent_required"
        (event,) = session.of("personalisation_events")
        assert event.outcome == "refused" and event.refusal == "consent_required" and event.consent_id is None
        assert event.attributes_used == [] and event.content_hash == "" and event.rule_id is None
        assert event.actor == ACTOR and event.channel == "email"
        # consent for another purpose does not count
        await _grant("service")
        with pytest.raises(PersonalisationError) as info:
            await service.render(TENANT, SUBJECT, "marketing", channel="email")
        assert info.value.code == "consent_required"
        # withdrawn: refused, and the event names the withdrawn record
        consent = await _grant("marketing")
        await service.withdraw_consent(TENANT, SUBJECT, "marketing", actor=ACTOR)
        with pytest.raises(PersonalisationError):
            await service.render(TENANT, SUBJECT, "marketing", channel="sms")
        assert str(session.of("personalisation_events")[-1].consent_id) == consent["id"]
        # expired: refused
        await _grant("marketing")
        for row in session.of("personalisation_consents"):
            if row.purpose == "marketing":
                row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        with pytest.raises(PersonalisationError) as info:
            await service.render(TENANT, SUBJECT, "marketing", channel="sms")
        assert info.value.code == "consent_required"
        events = session.of("personalisation_events")
        assert len(events) == 4 and all(e.outcome == "refused" and e.content_hash == "" for e in events)

    @pytest.mark.asyncio
    async def test_the_first_matching_enabled_rule_by_priority_renders_and_is_recorded(self, session):
        made = await _setup(
            session,
            _rule("everyone", priority=50, template="Hello {{first_name}}"),
            _rule(
                "gold",
                priority=10,
                template="{{first_name}}, a gold offer in {{city}}",
                conditions=[{"attribute": "segment", "op": "eq", "value": "gold"}],
            ),
            _rule("silver", priority=1, conditions=[{"attribute": "segment", "op": "eq", "value": "silver"}]),
            _rule("off", priority=0, enabled=False),
        )
        out = await service.render(TENANT, SUBJECT, "marketing", channel="email", actor=ACTOR)
        assert out["content"] == "Asha, a gold offer in Pune"
        assert out["rule"] == {"id": made[1]["id"], "name": "gold", "label": "GOLD"}
        assert out["attributes_used"] == ["city", "first_name", "segment"]  # placeholders and the condition
        assert out["consent"]["purpose"] == "marketing" and out["consent"]["expires_at"] is None
        assert out["content_hash"] == hashlib.sha256(out["content"].encode()).hexdigest() and not out["preview"]
        (event,) = session.of("personalisation_events")
        assert str(event.id) == out["event_id"] and event.outcome == "rendered" and event.refusal == ""
        assert str(event.rule_id) == made[1]["id"] and str(event.consent_id) == out["consent"]["id"]
        assert event.attributes_used == ["city", "first_name", "segment"] and event.content_hash == out["content_hash"]
        recorded = json.dumps(
            {c.name: str(getattr(event, c.name)) for c in event.__table__.columns}
        )  # names, never values
        for value in ("Asha", "Pune", "gold", out["content"]):
            assert value not in recorded
        await service.put_profile(TENANT, SUBJECT, dict(PROFILE, segment="bronze"), actor=ACTOR)
        assert (await service.render(TENANT, SUBJECT, "marketing", channel="email"))["rule"]["name"] == "everyone"

    @pytest.mark.asyncio
    async def test_no_rule_and_unresolved_placeholders_are_refused_and_recorded(self, session):
        await _setup(
            session,
            _rule("gold", conditions=[{"attribute": "segment", "op": "eq", "value": "platinum"}]),
            _rule("svc", purpose="service", template="Your branch in {{city}}"),
            purposes=("marketing", "service"),
            profile={"first_name": "Asha"},
        )
        with pytest.raises(PersonalisationError) as info:
            await service.render(TENANT, SUBJECT, "marketing", channel="email")
        assert info.value.status == 404 and info.value.code == "no_rule_matched"
        with pytest.raises(PersonalisationError) as info:
            await service.render(TENANT, SUBJECT, "service", channel="email")
        assert info.value.status == 422 and info.value.code == "placeholder_unresolved"
        refused = session.of("personalisation_events")
        assert [e.refusal for e in refused] == ["no_rule_matched", "placeholder_unresolved"]
        assert refused[1].rule_id is not None and refused[1].attributes_used == []

    @pytest.mark.asyncio
    async def test_a_named_rule_must_exist_fit_the_purpose_be_enabled_and_match(self, session):
        await _setup(
            session,
            _rule("welcome"),
            _rule("svc", purpose="service"),
            _rule("off", enabled=False),
            _rule("plat", conditions=[{"attribute": "segment", "op": "eq", "value": "platinum"}]),
        )
        out = await service.render(TENANT, SUBJECT, "marketing", rule="welcome", channel="push")
        assert out["content"] == "Hello Asha" and out["rule"]["name"] == "welcome"
        for name, code in (
            ("nothing", "rule_unknown"),
            ("svc", "rule_purpose_mismatch"),
            ("off", "rule_disabled"),
            ("plat", "rule_not_matched"),
        ):
            with pytest.raises(PersonalisationError) as info:
                await service.render(TENANT, SUBJECT, "marketing", rule=name, channel="push")
            assert info.value.code == code

    @pytest.mark.asyncio
    async def test_a_caller_template_uses_only_the_console_allow_list(self, session, monkeypatch):
        from core.workbench import console

        await _setup(session)
        allowed = AsyncMock(return_value=["first_name", "Not-A-Name", 7])
        monkeypatch.setattr(console, "value", allowed)
        assert await service.caller_allow_list(TENANT) == ["first_name"]
        out = await service.render(TENANT, SUBJECT, "marketing", template="Hi {{first_name}}!", channel="sms")
        assert out["content"] == "Hi Asha!" and out["rule"] is None and out["attributes_used"] == ["first_name"]
        assert allowed.call_args.args[1] == service.CALLER_TEMPLATE_SETTING
        with pytest.raises(PersonalisationError) as info:
            await service.render(TENANT, SUBJECT, "marketing", template="In {{city}}", channel="sms")
        assert info.value.code == "placeholder_not_allowed"
        assert session.of("personalisation_events")[-1].refusal == "placeholder_not_allowed"
        # nothing set in the console: every placeholder of a caller template is refused
        monkeypatch.setattr(console, "value", AsyncMock(return_value=[]))
        with pytest.raises(PersonalisationError) as info:
            await service.render(TENANT, SUBJECT, "marketing", template="Hi {{first_name}}", channel="sms")
        assert info.value.code == "placeholder_not_allowed"
        plain = await service.render(TENANT, SUBJECT, "marketing", template="A fixed notice.", channel="sms")
        assert plain["content"] == "A fixed notice." and plain["attributes_used"] == []
        before = len(session.of("personalisation_events"))
        with pytest.raises(PersonalisationError) as info:
            await service.render(TENANT, SUBJECT, "marketing", template="x", rule="welcome", channel="sms")
        assert info.value.code == "template_or_rule"
        for bad in ({"channel": "fax"}, {"channel": "sms", "template": "{{x"}):
            with pytest.raises(PersonalisationError):
                await service.render(TENANT, SUBJECT, "marketing", **bad)
        assert len(session.of("personalisation_events")) == before  # invalid input is not an attempt

    @pytest.mark.asyncio
    async def test_a_preview_needs_consent_and_records_nothing(self, session):
        await service.create_rule(TENANT, _rule("welcome"), actor=ACTOR)
        await service.put_profile(TENANT, SUBJECT, PROFILE, actor=ACTOR)
        with pytest.raises(PersonalisationError) as info:
            await service.render(TENANT, SUBJECT, "marketing", channel="email", preview=True)
        assert info.value.code == "consent_required"
        await _grant()
        out = await service.render(TENANT, SUBJECT, "marketing", channel="email", preview=True)
        assert out["content"] == "Hello Asha" and out["preview"] is True and out["event_id"] is None
        assert session.of("personalisation_events") == []

    @pytest.mark.asyncio
    async def test_without_a_profile_only_a_template_without_placeholders_renders(self, session):
        await _setup(session, _rule("welcome"), _rule("plain", priority=200, template="Visit us."), profile=None)
        with pytest.raises(PersonalisationError) as info:
            await service.render(TENANT, SUBJECT, "marketing", channel="web")
        assert info.value.code == "placeholder_unresolved"
        out = await service.render(TENANT, SUBJECT, "marketing", rule="plain", channel="web")
        assert out["content"] == "Visit us." and out["attributes_used"] == []

    @pytest.mark.asyncio
    async def test_events_are_read_back_newest_first_without_content(self, session):
        await _setup(session, _rule("welcome"))
        await service.render(TENANT, SUBJECT, "marketing", channel="email", actor=ACTOR)
        with pytest.raises(PersonalisationError):
            await service.render(TENANT, SUBJECT, "service", channel="email", actor=ACTOR)
        events = session.of("personalisation_events")
        events[0].created_at = datetime(2026, 10, 1, tzinfo=UTC)
        events[1].created_at = datetime(2026, 10, 2, tzinfo=UTC)
        found = await service.list_events(TENANT, subject_ref=SUBJECT, limit=10)
        assert [e["outcome"] for e in found] == ["refused", "rendered"]
        assert found[0]["refusal"] == "consent_required" and found[1]["refusal"] is None
        assert found[1]["attributes_used"] == ["first_name"] and "content" not in found[1]
        assert len(await service.list_events(TENANT)) == 2


# ---------------------------------------------------------------- routes


class TestRoutes:
    @pytest.mark.asyncio
    async def test_off_the_status_says_so_and_the_rest_is_not_found(self, monkeypatch):
        monkeypatch.setattr(settings, "personalisation_enabled", False)
        tid = str(TENANT)
        state = await api.status(tenant_id=tid)
        assert state["enabled"] is False and "marketing" in state["purposes"] and "exists" in state["ops"]
        assert state["limits"]["template"] == checks.MAX_TEMPLATE
        rule_id = uuid.uuid4()
        for call in (
            api.grant_consent(api.ConsentIn(subject_ref=SUBJECT, purpose="marketing", evidence="f"), tid, USER),
            api.withdraw_consent(api.WithdrawIn(subject_ref=SUBJECT, purpose="marketing"), tid, USER),
            api.list_consents(SUBJECT, tid),
            api.put_profile(api.ProfileIn(subject_ref=SUBJECT, attributes={}), tid, USER),
            api.get_profile(SUBJECT, tid),
            api.list_rules(None, tid),
            api.create_rule(api.RuleIn(**_rule("r")), tid, USER),
            api.update_rule(rule_id, api.RulePatch(priority=1), tid, USER),
            api.delete_rule(rule_id, tid, USER),
            api.render(api.RenderIn(subject_ref=SUBJECT, purpose="marketing", channel="sms"), tid, USER),
            api.list_events(SUBJECT, 10, tid),
        ):
            with pytest.raises(HTTPException) as refused:
                await call
            assert refused.value.status_code == 404 and refused.value.detail["error"] == "personalisation_disabled"

    @pytest.mark.asyncio
    async def test_on_the_whole_flow_runs_through_the_routes(self, session):
        tid = str(TENANT)
        assert (await api.status(tenant_id=tid))["enabled"] is True
        with pytest.raises(HTTPException) as refused:
            await api.grant_consent(
                api.ConsentIn(subject_ref=SUBJECT, purpose="marketing", evidence="f"), tenant_id=tid, user={}
            )
        assert refused.value.status_code == 401 and refused.value.detail["error"] == "actor_required"
        consent = await api.grant_consent(
            api.ConsentIn(
                subject_ref=SUBJECT,
                purpose="marketing",
                evidence="branch form 7",
                expires_at=datetime.now(UTC) + timedelta(days=90),
            ),
            tenant_id=tid,
            user=USER,
        )
        assert consent["valid"] is True
        assert (await api.list_consents(subject_ref=SUBJECT, tenant_id=tid))["total"] == 1
        kept = await api.put_profile(api.ProfileIn(subject_ref=SUBJECT, attributes=PROFILE), tenant_id=tid, user=USER)
        assert kept["attributes"] == sorted(PROFILE)
        assert (await api.get_profile(subject_ref=SUBJECT, tenant_id=tid))["attributes"] == PROFILE
        rule = await api.create_rule(api.RuleIn(**_rule("welcome")), tenant_id=tid, user=USER)
        assert (await api.list_rules(purpose="marketing", tenant_id=tid))["total"] == 1
        rule_id = uuid.UUID(rule["id"])
        patched = await api.update_rule(rule_id, api.RulePatch(priority=7), tenant_id=tid, user=USER)
        assert patched["priority"] == 7
        rendered = await api.render(
            api.RenderIn(subject_ref=SUBJECT, purpose="marketing", channel="email"), tenant_id=tid, user=USER
        )
        assert rendered["content"] == "Hello Asha" and rendered["event_id"]
        with pytest.raises(HTTPException) as refused:
            await api.render(
                api.RenderIn(subject_ref=SUBJECT, purpose="service", channel="email"), tenant_id=tid, user=USER
            )
        assert refused.value.status_code == 403 and refused.value.detail["error"] == "consent_required"
        events = await api.list_events(subject_ref=SUBJECT, limit=10, tenant_id=tid)
        assert events["total"] == 2
        withdrawn = await api.withdraw_consent(
            api.WithdrawIn(subject_ref=SUBJECT, purpose="marketing"), tenant_id=tid, user=USER
        )
        assert withdrawn["valid"] is False
        assert (await api.delete_rule(rule_id, tenant_id=tid, user=USER)).status_code == 204
        for call in (
            api.delete_rule(rule_id, tenant_id=tid, user=USER),
            api.get_profile(subject_ref="someone@example.com", tenant_id=tid),
            api.list_consents(subject_ref="a b", tenant_id=tid),
            api.list_rules(purpose="profiling", tenant_id=tid),
            api.list_events(subject_ref="a b", limit=5, tenant_id=tid),
            api.create_rule(api.RuleIn(**_rule("bad", allowed_attributes=[])), tenant_id=tid, user=USER),
            api.update_rule(rule_id, api.RulePatch(priority=1), tenant_id=tid, user=USER),
            api.put_profile(api.ProfileIn(subject_ref=SUBJECT, attributes={"a": {}}), tenant_id=tid, user=USER),
            api.withdraw_consent(api.WithdrawIn(subject_ref=SUBJECT, purpose="retention"), tenant_id=tid, user=USER),
        ):
            with pytest.raises(HTTPException) as refused:
                await call
            assert refused.value.status_code in (404, 422)

    def test_the_bodies_are_bounded(self):
        from pydantic import ValidationError

        for build in (
            lambda: api.ProfileIn(subject_ref=SUBJECT, attributes={f"a{i}": i for i in range(101)}),
            lambda: api.RenderIn(subject_ref=SUBJECT, purpose="marketing", channel="sms", template="x" * 4001),
            lambda: api.ConsentIn(subject_ref="x" * 129, purpose="marketing", evidence="f"),
            lambda: api.RuleIn(**_rule("r"), extra=1),
        ):
            with pytest.raises(ValidationError):
                build()

    def test_the_router_is_registered_behind_the_personalisation_scope_family(self):
        from api.main import app
        from api.route_enforcement import SCOPE_FAMILIES

        paths = set(app.openapi()["paths"])
        assert {
            "/api/v1/personalisation/status",
            "/api/v1/personalisation/consents",
            "/api/v1/personalisation/consents/withdraw",
            "/api/v1/personalisation/profiles",
            "/api/v1/personalisation/rules",
            "/api/v1/personalisation/rules/{rule_id}",
            "/api/v1/personalisation/render",
            "/api/v1/personalisation/events",
        } <= paths
        assert SCOPE_FAMILIES["personalisation"] == ("audit:read", "approvals:write")


def _migration():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_v6_z77_personalisation", Path("migrations/versions/v6_z77_personalisation.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_and_the_models_are_shaped():
    from core.models.personalisation import (
        PersonalisationConsent,
        PersonalisationEvent,
        PersonalisationProfile,
        PersonalisationRule,
    )

    migration = _migration()

    text = Path("migrations/versions/v6_z77_personalisation.py").read_text(encoding="utf-8")
    assert migration.revision == "v6z77_personalisation" and migration.down_revision == "v6z76_lineage_sync"
    assert len(migration.revision) <= 32
    tables = (
        "personalisation_consents",
        "personalisation_profiles",
        "personalisation_rules",
        "personalisation_events",
    )
    assert migration.TABLES == tables
    for table in tables:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in text
    assert 'op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")' in text
    assert 'op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")' in text
    assert text.count("_tenant_policy(") == 5  # the helper and one call per table
    assert "consent_id UUID NULL REFERENCES personalisation_consents(id) ON DELETE SET NULL" in text
    assert "rule_id UUID NULL REFERENCES personalisation_rules(id) ON DELETE SET NULL" in text
    assert "ix_personalisation_events_consent ON personalisation_events(consent_id)" in text
    assert "ix_personalisation_events_rule ON personalisation_events(rule_id)" in text
    assert "ux_personalisation_consents_tenant_subject_purpose" in text
    assert "ux_personalisation_profiles_tenant_subject" in text and "ux_personalisation_rules_tenant_name" in text
    models = (PersonalisationConsent, PersonalisationProfile, PersonalisationRule, PersonalisationEvent)
    assert tuple(model.__tablename__ for model in models) == tables
    assert {fk.column.table.name for fk in PersonalisationEvent.__table__.foreign_keys} == {
        "personalisation_consents",
        "personalisation_rules",
    }
    for fk in PersonalisationEvent.__table__.foreign_keys:
        assert fk.ondelete == "SET NULL"
        leading = {index.columns[0].name for index in PersonalisationEvent.__table__.indexes}
        assert fk.parent.name in leading
    assert PersonalisationConsent.__table__.c.subject_ref.type.length == 128


class _Op:
    def __init__(self):
        self.sql = []

    def execute(self, sql):
        self.sql.append(" ".join(str(sql).split()))


def test_the_migration_forces_row_level_security_on_every_table(monkeypatch):
    migration = _migration()

    op = _Op()
    monkeypatch.setattr(migration, "op", op)
    migration.upgrade()
    for table in migration.TABLES:
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;" in op.sql
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;" in op.sql
        assert any(s.startswith(f"CREATE POLICY {table}_tenant_isolation ON {table} USING") for s in op.sql)
    migration.downgrade()
    assert op.sql[-1] == "DROP TABLE IF EXISTS personalisation_consents;"
