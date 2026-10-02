# SPDX-License-Identifier: Apache-2.0
"""The guardrail engine: flag-only and enforced evaluation, blocks, caching, audited rule changes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.governance.guardrails import engine
from core.governance.guardrails.schema import GuardrailBlocked, Rule

TENANT = uuid.uuid4()
ROOT = Path(__file__).resolve().parents[3]
CARD = "4111 1111 1111 1111"


def _rule(**over) -> Rule:
    base = {
        "id": str(uuid.uuid4()),
        "name": "cards",
        "stage": "output",
        "detector": "sensitive_data",
        "action": "redact",
    }
    base.update(over)
    return Rule(**base)


def _rows(enabled: bool):
    from core.feature_flags import FlagRows

    row = {"enabled": True, "rollout_percentage": 100} if enabled else None
    return FlagRows(global_row=row, tenant_row=None)


@pytest.fixture(autouse=True)
def _regex_recognisers(monkeypatch):
    from core.governance.guardrails import detectors

    monkeypatch.setattr(detectors.SensitiveDataDetector, "_analyser_spans", lambda self, text, entities: None)
    monkeypatch.setattr(engine.settings, "env", "test")


def _with(rules: list[Rule]):
    return patch.object(engine, "active_rules", AsyncMock(return_value=rules))


def _enforce(on: bool):
    return patch.object(engine, "enforcing", AsyncMock(return_value=on))


class TestSwitch:
    def test_off_by_default_and_the_authority_flag_turns_it_on(self, monkeypatch):
        monkeypatch.setattr(engine.settings, "guardrails_enforce", False)
        with patch("core.feature_flags.load_flag_rows_strict", AsyncMock(return_value=_rows(False))):
            assert asyncio.run(engine.enforcing(TENANT)) is False
        with patch("core.feature_flags.load_flag_rows_strict", AsyncMock(return_value=_rows(True))):
            assert asyncio.run(engine.enforcing(TENANT)) is True
        monkeypatch.setattr(engine.settings, "guardrails_enforce", True)
        assert asyncio.run(engine.enforcing(None)) is True
        monkeypatch.setattr(engine.settings, "guardrails_enforce", False)
        assert asyncio.run(engine.enforcing(None)) is False

    def test_the_flag_is_operator_managed(self):
        from core.feature_flags import RESERVED_FLAG_KEYS

        assert engine.FLAG_KEY in RESERVED_FLAG_KEYS


class TestFlagOnly:
    def test_findings_are_recorded_but_the_text_is_unchanged(self):
        meter = []
        with (
            _with([_rule()]),
            _enforce(False),
            patch.object(engine, "_meter", lambda *a: meter.append(a)),
        ):
            result = asyncio.run(engine.evaluate("output", f"card {CARD}", tenant_id=TENANT, correlation_id="c1"))
        assert result.allowed and result.enforced is False and result.text == f"card {CARD}"
        [outcome] = result.outcomes
        assert outcome.action == "redact" and outcome.applied is False and outcome.transformed is False
        assert outcome.findings == 1 and outcome.kinds == ["CREDIT_CARD"]
        assert meter == [("output", "sensitive_data", "redact", "flag_only")]
        assert result.correlation_id == "c1" and result.findings == 1 and result.flagged

    def test_a_block_rule_only_records_in_flag_only_mode(self):
        with _with([_rule(action="block")]), _enforce(False), patch.object(engine, "_meter", lambda *a: None):
            result = asyncio.run(engine.evaluate("output", f"card {CARD}", tenant_id=TENANT))
        assert result.allowed and result.outcomes[0].blocked is False

    def test_no_match_no_outcome_and_no_tenant_no_rules(self):
        with _with([_rule(stage="input")]), patch.object(engine, "_meter", lambda *a: None):
            result = asyncio.run(engine.evaluate("output", f"card {CARD}", tenant_id=TENANT))
        assert result.outcomes == [] and result.allowed
        with patch.object(engine, "active_rules", AsyncMock()) as reads:
            result = asyncio.run(engine.evaluate("output", "x", tenant_id=None))
        reads.assert_not_called()
        assert result.outcomes == [] and len(result.correlation_id) == 32


class TestEnforced:
    def test_transforms_apply_in_priority_order_and_are_audited(self):
        audits = []

        async def fake_audit(scope, outcome):
            audits.append((scope.correlation_id, outcome.action, outcome.transformed))

        rules = [
            _rule(name="emails", priority=20, action="tokenise", options={"entities": ["EMAIL"]}),
            _rule(name="cards", priority=10, action="redact", options={"entities": ["CREDIT_CARD"]}),
        ]
        with (
            _with(sorted(rules, key=lambda r: r.priority)),
            _enforce(True),
            patch.object(engine, "_meter", lambda *a: None),
            patch.object(engine, "_audit_outcome", fake_audit),
        ):
            result = asyncio.run(
                engine.evaluate("output", f"card {CARD} mail a@b.co", tenant_id=TENANT, correlation_id="c2")
            )
        assert result.text == "card <CREDIT_CARD> mail <EMAIL_1>" and result.token_map == {"<EMAIL_1>": "a@b.co"}
        assert [o.rule_name for o in result.outcomes] == ["cards", "emails"]
        assert all(o.applied and o.transformed for o in result.outcomes)
        assert audits == [("c2", "redact", True), ("c2", "tokenise", True)]

    def test_a_block_refuses_the_stage_with_the_rule_named(self):
        audits = []

        async def fake_audit(scope, outcome):
            audits.append(outcome.blocked)

        rules = [_rule(name="no-cards", action="block"), _rule(name="flag-too", action="flag", priority=200)]
        with (
            _with(rules),
            _enforce(True),
            patch.object(engine, "_meter", lambda *a: None),
            patch.object(engine, "_audit_outcome", fake_audit),
        ):
            with pytest.raises(GuardrailBlocked) as info:
                asyncio.run(engine.evaluate("output", f"card {CARD}", tenant_id=TENANT, correlation_id="c3"))
        assert info.value.rule_name == "no-cards" and info.value.stage == "output" and info.value.code == "E1016"
        assert info.value.to_error()["guardrail"]["correlation_id"] == "c3"
        assert audits == [True]  # the flag rule writes no audit row

    def test_a_flag_rule_is_not_audited_even_when_enforcing(self):
        audit = AsyncMock()
        with (
            _with([_rule(action="flag")]),
            _enforce(True),
            patch.object(engine, "_meter", lambda *a: None),
            patch.object(engine, "_audit_outcome", audit),
        ):
            result = asyncio.run(engine.evaluate("output", f"card {CARD}", tenant_id=TENANT))
        assert result.outcomes[0].applied is True and result.text.endswith(CARD)
        audit.assert_not_called()

    def test_a_toxicity_rule_applies_at_its_threshold(self, monkeypatch):
        monkeypatch.setattr(
            "core.content_safety.checker._check_toxicity",
            lambda text, threshold: (0.6, [{"type": "toxicity", "detail": "keyword", "severity": "medium"}]),
        )
        rule = _rule(detector="toxicity", action="block", threshold=0.7)
        with _with([rule]), _enforce(True), patch.object(engine, "_meter", lambda *a: None):
            result = asyncio.run(engine.evaluate("output", "some text", tenant_id=TENANT))
        assert result.outcomes == []  # 0.6 is under the rule's threshold
        low = _rule(detector="toxicity", action="block", threshold=0.5)
        with (
            _with([low]),
            _enforce(True),
            patch.object(engine, "_meter", lambda *a: None),
            patch.object(engine, "_audit_outcome", AsyncMock()),
        ):
            with pytest.raises(GuardrailBlocked):
                asyncio.run(engine.evaluate("output", "some text", tenant_id=TENANT))

    def test_a_failing_or_unknown_detector_is_skipped(self):
        broken = _rule(detector="pattern", options={"patterns": ["x"]})
        with (
            _with([broken, _rule()]),
            _enforce(True),
            patch.object(engine, "_meter", lambda *a: None),
            patch.object(engine, "_audit_outcome", AsyncMock()),
            patch.dict(
                engine.REGISTRY, {"pattern": SimpleNamespace(detect=MagicMock(side_effect=RuntimeError("boom")))}
            ),
        ):
            result = asyncio.run(engine.evaluate("output", f"x {CARD}", tenant_id=TENANT))
        assert [o.rule_name for o in result.outcomes] == ["cards"] and "<CREDIT_CARD>" in result.text
        with (
            _with([_rule(detector="magic")]),
            _enforce(True),
            patch.object(engine, "_meter", lambda *a: None),
        ):
            assert asyncio.run(engine.evaluate("output", CARD, tenant_id=TENANT)).outcomes == []


class TestDryRun:
    def test_a_dry_run_applies_the_rules_to_the_returned_text_and_meters_nothing(self):
        meter = []
        audit = AsyncMock()
        with (
            _with([_rule(action="block", priority=1), _rule(name="redact", action="redact", priority=2)]),
            _enforce(False),
            patch.object(engine, "_meter", lambda *a: meter.append(a)),
            patch.object(engine, "_audit_outcome", audit),
        ):
            result = asyncio.run(engine.evaluate("output", f"card {CARD}", tenant_id=TENANT, dry_run=True))
        assert result.allowed is False and result.enforced is False and result.text == "card <CREDIT_CARD>"
        assert [o.applied for o in result.outcomes] == [True, True] and result.outcomes[0].blocked
        assert meter == [] and audit.await_count == 0
        assert result.to_dict()["outcomes"][1]["transformed"] is True


class TestFailures:
    def test_unreadable_rules_refuse_in_strict_and_pass_unguarded_in_relaxed(self, monkeypatch):
        monkeypatch.setattr(engine.settings, "env", "production")
        with patch.object(engine, "active_rules", AsyncMock(side_effect=RuntimeError("db down"))):
            with pytest.raises(GuardrailBlocked, match="could not be read"):
                asyncio.run(engine.evaluate("output", CARD, tenant_id=TENANT))
            monkeypatch.setattr(engine.settings, "env", "test")
            result = asyncio.run(engine.evaluate("output", CARD, tenant_id=TENANT))
        assert result.outcomes == [] and result.allowed

    def test_an_unreadable_flag_refuses_in_strict_and_flags_only_in_relaxed(self, monkeypatch):
        monkeypatch.setattr(engine.settings, "env", "production")
        with (
            _with([_rule()]),
            patch.object(engine, "enforcing", AsyncMock(side_effect=RuntimeError("flags down"))),
            patch.object(engine, "_meter", lambda *a: None),
        ):
            with pytest.raises(GuardrailBlocked, match="enforcement flag"):
                asyncio.run(engine.evaluate("output", CARD, tenant_id=TENANT))
            monkeypatch.setattr(engine.settings, "env", "test")
            result = asyncio.run(engine.evaluate("output", CARD, tenant_id=TENANT))
        assert result.enforced is False and result.outcomes[0].applied is False

    def test_the_audit_write_is_best_effort(self, monkeypatch):
        @contextlib.asynccontextmanager
        async def _ctx(_tid):
            raise RuntimeError("db down")
            yield  # pragma: no cover

        monkeypatch.setattr("core.database.get_tenant_session", _ctx)
        scope = engine._Scope(TENANT, "output", "a1", None, None, "c9")
        outcome = engine.Outcome(
            "r", "cards", "output", "sensitive_data", "redact", 1, 1.0, ["CREDIT_CARD"], True, transformed=True
        )
        asyncio.run(engine._audit_outcome(scope, outcome))  # logged, never raised

    def test_the_audit_row_is_signed_and_carries_the_correlation_id(self, monkeypatch):
        monkeypatch.setattr(engine.settings, "secret_key", "ci-test-secret-key-minimum-16")
        added: list = []

        @contextlib.asynccontextmanager
        async def _ctx(_tid):
            yield SimpleNamespace(add=added.append)

        monkeypatch.setattr("core.database.get_tenant_session", _ctx)
        scope = engine._Scope(TENANT, "output", "a1", None, None, "c9")
        outcome = engine.Outcome(
            "r1", "cards", "output", "sensitive_data", "block", 1, 1.0, ["CREDIT_CARD"], True, blocked=True
        )
        asyncio.run(engine._audit_outcome(scope, outcome))
        [row] = added
        assert row.event_type == "guardrail.outcome" and row.actor_type == "system" and row.outcome == "blocked"
        assert row.trace_id == "c9" and row.details["correlation_id"] == "c9" and row.signature

    def test_the_metric_is_best_effort(self):
        with patch("observability.metrics.guardrail_outcomes_total", None):
            engine._meter("output", "pattern", "flag", "flag_only")


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.added: list = []
        self.deleted: list = []

    async def execute(self, _query, _params=None):
        result = MagicMock()
        result.scalars.return_value = iter(self.rows)
        result.scalar_one_or_none.return_value = self.rows[0] if self.rows else None
        return result

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None and hasattr(obj, "created_by"):
                obj.id = uuid.uuid4()


def _row(**over):
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "name": "cards",
        "stage": "output",
        "detector": "sensitive_data",
        "action": "redact",
        "priority": 10,
        "enabled": True,
        "threshold": 0.5,
        "agent_id": None,
        "use_case": None,
        "risk_tier": None,
        "options": {"entities": ["CREDIT_CARD"]},
        "reason": "",
        "created_by": "user:1",
        "created_at": datetime.now(UTC),
        "updated_by": None,
        "updated_at": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def session(monkeypatch):
    sess = _Session([])

    @contextlib.asynccontextmanager
    async def _ctx(_tid):
        yield sess

    monkeypatch.setattr("core.database.get_tenant_session", _ctx)
    monkeypatch.setattr(engine.settings, "secret_key", "ci-test-secret-key-minimum-16")
    with patch("core.async_redis.get_async_redis", AsyncMock(return_value=None)):
        yield sess


def _audits(session):
    return [obj for obj in session.added if type(obj).__name__ == "AuditLog"]


class TestStore:
    def test_rows_map_to_rules_and_the_cache_serves_them(self, session):
        session.rows = [_row()]
        loaded = asyncio.run(engine._load_rules(TENANT))
        assert loaded[0].options == {"entities": ["CREDIT_CARD"]} and loaded[0].threshold == 0.5
        store: dict[str, str] = {}
        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=lambda key: store.get(key))
        redis.set = AsyncMock(side_effect=lambda key, value, ex=None: store.__setitem__(key, value))
        redis.delete = AsyncMock(side_effect=lambda key: store.pop(key, None))
        with patch("core.async_redis.get_async_redis", AsyncMock(return_value=redis)):
            assert len(asyncio.run(engine.active_rules(TENANT))) == 1
            assert json.loads(next(iter(store.values())))[0]["name"] == "cards"
            session.rows = []
            assert len(asyncio.run(engine.active_rules(TENANT))) == 1
            asyncio.run(engine.invalidate(TENANT))
            assert asyncio.run(engine.active_rules(TENANT)) == []

    def test_set_update_and_delete_write_signed_audit_rows(self, session):
        rule = asyncio.run(
            engine.set_rule(
                TENANT,
                actor_id="user:1",
                name="cards",
                stage="output",
                detector="sensitive_data",
                action="redact",
                options={"entities": ["credit_card"]},
            )
        )
        assert rule.options == {"entities": ["CREDIT_CARD"]}
        rows = [obj for obj in session.added if type(obj).__name__ == "GuardrailRule"]
        assert len(rows) == 1 and str(rows[0].id) == rule.id
        assert _audits(session)[0].event_type == "guardrail_rule.set" and _audits(session)[0].signature
        with pytest.raises(ValueError, match="stage must be"):
            asyncio.run(engine.set_rule(TENANT, actor_id="user:1", name="x", stage="nowhere", detector="toxicity"))
        row = _row()
        session.rows = [row]
        updated = asyncio.run(
            engine.update_rule(TENANT, row.id, actor_id="user:2", changes={"action": "block", "priority": 5})
        )
        assert updated is not None and updated.action == "block" and row.priority == 5 and row.updated_by == "user:2"
        assert _audits(session)[-1].event_type == "guardrail_rule.update" and _audits(session)[-1].details[
            "changes"
        ] == {"action": "block", "priority": 5}
        with pytest.raises(ValueError, match="sensitive data only"):
            asyncio.run(
                engine.update_rule(
                    TENANT, row.id, actor_id="user:2", changes={"detector": "toxicity", "action": "tokenise"}
                )
            )
        assert asyncio.run(engine.delete_rule(TENANT, row.id, actor_id="user:3")) is True
        assert session.deleted == [row] and _audits(session)[-1].event_type == "guardrail_rule.delete"
        session.rows = []
        assert asyncio.run(engine.update_rule(TENANT, uuid.uuid4(), actor_id="u", changes={"priority": 1})) is None
        assert asyncio.run(engine.delete_rule(TENANT, uuid.uuid4(), actor_id="u")) is False


class TestMigrationAndErrors:
    def test_revision_chain_and_rls(self):
        import importlib.util

        path = ROOT / "migrations" / "versions" / "v6_z38_guardrail_rules.py"
        spec = importlib.util.spec_from_file_location("v6_z38_guardrail_rules", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.revision == "v6z38_guardrail_rules" and len(module.revision) <= 32
        assert module.down_revision == "v6z37_cost_aware_routing"
        src = path.read_text(encoding="utf-8")
        assert "ALTER TABLE guardrail_rules ENABLE ROW LEVEL SECURITY" in src
        assert "FORCE ROW LEVEL SECURITY" in src and "WITH CHECK" in src

    def test_the_error_code_is_registered(self):
        from core.schemas.errors import ERROR_META, ErrorCode

        assert ErrorCode.GUARDRAIL_BLOCKED.value == "E1016"
        assert ERROR_META["E1016"]["name"] == "GUARDRAIL_BLOCKED" and ERROR_META["E1016"]["retryable"] is False
