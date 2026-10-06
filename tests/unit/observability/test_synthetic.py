# SPDX-License-Identifier: Apache-2.0
"""Synthetic checks: configuration, the probes, running, the sweep and the endpoints."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from api.deps import get_current_tenant
from api.route_enforcement import enforce_route_metadata
from api.v1 import observability as api
from core.config import settings
from observability import synthetic

TENANT = uuid.uuid4()
ROOT = Path(__file__).resolve().parents[3]


def _check(kind: str = "model", config: dict | None = None, **over) -> synthetic.Check:
    defaults = {
        "model": {"prompt": "Reply with the word ready."},
        "knowledge": {"query": "leave policy", "top_k": 5, "min_results": 1},
        "guardrail": {"stage": "input", "text": "card 4111 1111 1111 1111", "expect": "detected"},
        "audit_chain": {"recent": 100},
    }
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "name": f"{kind} check",
        "kind": kind,
        "config": config if config is not None else defaults[kind],
    }
    base.update(over)
    return synthetic.Check(**base)


class TestConfig:
    def test_each_kind_fills_its_defaults_and_drops_empty_options(self):
        assert synthetic.validate_config("model", {"prompt": " hello "}) == {"prompt": "hello"}
        assert synthetic.validate_config("model", {"prompt": "p", "contains": "ready", "max_latency_ms": 5000}) == {
            "prompt": "p",
            "contains": "ready",
            "max_latency_ms": 5000,
        }
        assert synthetic.validate_config("knowledge", {"query": "q"}) == {"query": "q", "top_k": 5, "min_results": 1}
        assert synthetic.validate_config("guardrail", {"stage": "Input", "text": "t"}) == {
            "stage": "input",
            "text": "t",
            "expect": "detected",
        }
        assert synthetic.validate_config("audit_chain", {}) == {"recent": 1000}

    @pytest.mark.parametrize(
        ("kind", "config", "message"),
        [
            ("http", {}, "kind must be one of"),
            ("model", [], "config must be an object"),
            ("model", {}, "prompt is required"),
            ("model", {"prompt": "p", "url": "https://example.test"}, "unknown config keys"),
            ("model", {"prompt": "x" * 2001}, "at most 2000"),
            ("model", {"prompt": "p", "max_latency_ms": 0}, "between 1 and"),
            ("model", {"prompt": "p", "max_latency_ms": True}, "whole number"),
            ("knowledge", {"query": "q", "top_k": 3, "min_results": 4}, "cannot exceed top_k"),
            ("knowledge", {"query": "q", "top_k": 21}, "between 1 and 20"),
            ("guardrail", {"stage": "nowhere", "text": "t"}, "stage must be one of"),
            ("guardrail", {"stage": "input", "text": "t", "expect": "maybe"}, "expect must be one of"),
            ("audit_chain", {"recent": 0}, "between 1 and 10000"),
            ("adversarial", {"min_recall": 101}, "between 0 and 100"),
            ("adversarial", {"max_false_positives": -1}, "between 0 and 1000"),
            ("adversarial", {"recent": 5}, "unknown config keys"),
            ("eval_dataset", {"system": "s", "model": "m"}, "dataset_id must be"),
            ("eval_dataset", {"dataset_id": "not-a-uuid", "system": "s", "model": "m"}, "dataset_id must be"),
            ("eval_dataset", {"dataset_id": str(uuid.uuid4()), "model": "m"}, "system is required"),
            ("eval_dataset", {"dataset_id": str(uuid.uuid4()), "system": "s"}, "model is required"),
            (
                "eval_dataset",
                {"dataset_id": str(uuid.uuid4()), "system": "s", "model": "m", "judges": ["vibes"]},
                "unknown judge",
            ),
            (
                "eval_dataset",
                {"dataset_id": str(uuid.uuid4()), "system": "s", "model": "m", "judges": ["relevance"]},
                "judge_model is required",
            ),
            (
                "eval_dataset",
                {"dataset_id": str(uuid.uuid4()), "system": "s", "model": "m", "limit": 26},
                "between 1 and 25",
            ),
            (
                "eval_dataset",
                {"dataset_id": str(uuid.uuid4()), "system": "s", "model": "m", "min_pass_rate": 101},
                "between 0 and 100",
            ),
        ],
    )
    def test_an_invalid_configuration_says_what_is_wrong(self, kind, config, message):
        with pytest.raises(ValueError, match=message):
            synthetic.validate_config(kind, config)

    def test_the_adversarial_and_dataset_kinds_take_their_defaults(self):
        assert synthetic.validate_config("adversarial", {}) == {"min_recall": 50, "max_false_positives": 0}
        dataset_id = str(uuid.uuid4())
        clean = synthetic.validate_config(
            "eval_dataset",
            {
                "dataset_id": dataset_id,
                "system": " Decide. ",
                "model": "gpt-4o",
                "judges": ["relevance", "relevance"],
                "judge_model": "gpt-4o-mini",
            },
        )
        assert clean == {
            "dataset_id": dataset_id,
            "system": "Decide.",
            "model": "gpt-4o",
            "judges": ["relevance"],
            "judge_model": "gpt-4o-mini",
            "limit": 25,
            "min_pass_rate": 100,
        }
        assert (
            synthetic.validate_config(
                "eval_dataset", {"dataset_id": dataset_id, "system": "s", "model": "m", "version": 3}
            )["version"]
            == 3
        )

    def test_interval_and_name_bounds(self):
        assert synthetic.validate_interval(5) == 5 and synthetic.validate_interval(1440) == 1440
        for bad in (4, 1441, "60", True):
            with pytest.raises(ValueError):
                synthetic.validate_interval(bad)
        assert synthetic.validate_name("  nightly model  ") == "nightly model"
        for bad_name in ("", " ", "x" * 121):
            with pytest.raises(ValueError):
                synthetic.validate_name(bad_name)

    def test_a_check_is_due_once_its_interval_has_passed(self):
        now = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
        assert _check().due(now) is True
        assert _check(last_run_at=now - timedelta(minutes=59), interval_minutes=60).due(now) is False
        assert _check(last_run_at=now - timedelta(minutes=60), interval_minutes=60).due(now) is True
        assert _check(enabled=False).due(now) is False


class TestProbes:
    def test_the_model_probe_goes_through_the_router_as_the_tenant(self):
        answer = SimpleNamespace(content="Ready.", model="gpt-4o-mini", tokens_used=12)
        complete = AsyncMock(return_value=answer)
        with patch("core.llm.router.llm_router.complete", complete):
            reasons, detail = asyncio.run(
                synthetic._probe_model(TENANT, {"prompt": "Reply with ready.", "contains": "READY", "model": "m1"})
            )
        assert reasons == [] and detail == {"model": "gpt-4o-mini", "tokens": 12}
        complete.assert_awaited_once_with(
            [{"role": "user", "content": "Reply with ready."}],
            model_override="m1",
            max_tokens=synthetic.MODEL_MAX_TOKENS,
            tenant_id=str(TENANT),
        )

    def test_the_model_probe_reports_an_empty_or_wrong_answer_without_keeping_it(self):
        for content, expected in (
            ("", ["empty_answer", "answer_missing_expected_text"]),
            ("no", ["answer_missing_expected_text"]),
        ):
            answer = SimpleNamespace(content=content, model="m", tokens_used=1)
            with patch("core.llm.router.llm_router.complete", AsyncMock(return_value=answer)):
                reasons, detail = asyncio.run(synthetic._probe_model(TENANT, {"prompt": "p", "contains": "ready"}))
            assert reasons == expected and "content" not in detail and content not in map(str, detail.values())

    def test_the_knowledge_probe_counts_results(self):
        found = SimpleNamespace(results=[object(), object()])
        search = AsyncMock(return_value=found)
        with patch("api.v1.knowledge._search_knowledge", search):
            ok = asyncio.run(synthetic._probe_knowledge(TENANT, {"query": "q", "top_k": 5, "min_results": 2}))
            short = asyncio.run(synthetic._probe_knowledge(TENANT, {"query": "q", "top_k": 5, "min_results": 3}))
        assert ok == ([], {"results": 2}) and short == (["too_few_results"], {"results": 2})
        request, tenant = search.await_args.args
        assert (request.query, request.top_k, tenant) == ("q", 5, str(TENANT))

    @pytest.mark.parametrize(
        ("expect", "allowed", "findings", "outcomes", "reasons"),
        [
            ("blocked", False, 1, 1, []),
            ("blocked", True, 1, 1, ["guardrail_not_blocked"]),
            ("detected", True, 2, 1, []),
            ("detected", True, 0, 0, ["guardrail_not_detected"]),
            ("clean", True, 0, 0, []),
            ("clean", True, 1, 1, ["guardrail_unexpected_finding"]),
        ],
    )
    def test_the_guardrail_probe_is_a_dry_run_held_to_its_expectation(
        self, expect, allowed, findings, outcomes, reasons
    ):
        result = SimpleNamespace(allowed=allowed, findings=findings, outcomes=[object()] * outcomes, enforced=False)
        evaluate = AsyncMock(return_value=result)
        with patch("core.governance.guardrails.evaluate", evaluate):
            got, detail = asyncio.run(
                synthetic._probe_guardrail(TENANT, {"stage": "input", "text": "synthetic", "expect": expect})
            )
        assert got == reasons
        assert detail == {"findings": findings, "rules_matched": outcomes, "allowed": allowed, "enforced": False}
        evaluate.assert_awaited_once_with("input", "synthetic", tenant_id=TENANT, dry_run=True)

    def test_the_audit_chain_probe_verifies_the_newest_links(self):
        from core.governance import audit_chain

        intact = audit_chain.Verification(
            tenant_id=TENANT, head=audit_chain.Head(seq=250, hash="h"), unsealed=0, checked_from=151
        )
        intact.verified = 100
        verify = AsyncMock(return_value=intact)
        status = AsyncMock(return_value={"head": {"seq": 250}})
        with patch.object(audit_chain, "status", status), patch.object(audit_chain, "verify", verify):
            reasons, detail = asyncio.run(synthetic._probe_audit_chain(TENANT, {"recent": 100}))
        assert reasons == [] and detail == {"status": "verified", "head_seq": 250, "verified": 100}
        verify.assert_awaited_once_with(TENANT, from_seq=151, limit=100)
        broken = audit_chain.Verification(
            tenant_id=TENANT, head=audit_chain.Head(seq=250, hash="h"), unsealed=0, checked_from=151
        )
        broken.first_break = audit_chain.Break(seq=200, row_id="r", reason="link_hash")
        with (
            patch.object(audit_chain, "status", status),
            patch.object(audit_chain, "verify", AsyncMock(return_value=broken)),
        ):
            reasons, detail = asyncio.run(synthetic._probe_audit_chain(TENANT, {"recent": 100}))
        assert reasons == ["audit_chain_broken"] and (detail["break_seq"], detail["break_reason"]) == (200, "link_hash")


class TestScheduledEvaluation:
    def _suite(self, recall, false_positives=0, errors=()):
        from core.governance.guardrails import adversarial

        attacks = 20
        detected = int(round(recall * attacks))
        category = adversarial.CategoryReport(
            category="injection_direct",
            attacks=attacks,
            detected=detected,
            controls=9,
            false_positives=false_positives,
            missed=[f"inj-d-{index:02d}" for index in range(attacks - detected)],
        )
        return adversarial.SuiteReport(rules="tenant", rule_count=4, categories=[category], errors=list(errors))

    def test_the_adversarial_probe_holds_the_rules_to_a_minimum_recall(self):
        with patch("core.governance.guardrails.adversarial.run_suite", AsyncMock(return_value=self._suite(0.6))) as run:
            reasons, detail = asyncio.run(
                synthetic._probe_adversarial(TENANT, {"min_recall": 50, "max_false_positives": 0})
            )
            assert run.await_args.kwargs == {"tenant_id": TENANT}
        assert reasons == [] and detail["recall"] == 0.6 and detail["detected"] == 12 and detail["rule_count"] == 4
        assert detail["categories"][0]["missed"][0] == "inj-d-00" and "text" not in str(detail)
        with patch(
            "core.governance.guardrails.adversarial.run_suite",
            AsyncMock(return_value=self._suite(0.4, 2, ["inj-d-03"])),
        ):
            reasons, detail = asyncio.run(
                synthetic._probe_adversarial(TENANT, {"min_recall": 50, "max_false_positives": 1})
            )
        assert reasons == [
            "adversarial_recall_below_minimum",
            "adversarial_controls_wrongly_caught",
            "adversarial_cases_not_evaluated",
        ]
        assert detail["errors"] == 1

    def test_the_dataset_probe_runs_a_version_keeps_the_run_and_holds_it_to_a_pass_rate(self, monkeypatch):
        from contextlib import asynccontextmanager

        from core.evals import datasets, runs

        monkeypatch.setattr(settings, "evals_v2_enabled", True)
        monkeypatch.setattr(settings, "prompt_compare_enabled", True)
        dataset_id = uuid.uuid4()
        stored, content_hash = datasets.normalise_cases(
            [{"id": f"c{index}", "input": f"q{index}", "equals": "yes"} for index in range(29)]
            + [{"id": "c29", "input": "q29", "label": "decline"}]
        )
        version = SimpleNamespace(
            id=uuid.uuid4(),
            dataset_id=dataset_id,
            version=2,
            cases=stored,
            case_count=30,
            content_hash=content_hash,
            note=None,
            created_by_user=None,
            created_at=None,
        )
        added: list = []

        class _Session:
            def add(self, row):
                added.append(row)

            async def flush(self):
                return None

        @asynccontextmanager
        async def _session(_tenant):
            yield _Session()

        asked: dict = {}

        async def _run_version(tenant_id, **kwargs):
            asked.update(kwargs, tenant_id=tenant_id)
            return {
                "dataset_id": str(dataset_id),
                "version": 2,
                "content_hash": content_hash,
                "cases_total": 30,
                "offset": 0,
                "cases_run": len(kwargs["cases"]),
                "complete": False,
                "model": kwargs["model"],
                "judge_model": kwargs["judge_model"],
                "judges": list(kwargs["judges"]),
                "prompt_hash": "p" * 64,
                "max_tokens": 512,
                "passed": 8,
                "failed": 1,
                "errors": 1,
                "pass_rate": round(8 / 9, 4),
                "avg_latency_ms": 10,
                "cost_usd": 0.01,
                "metrics": {"pass_rate": round(8 / 9, 4)},
                "scores": {"relevance": {"cases": 9, "mean": 0.8, "errors": 0}},
                "results": [{"id": "c0", "result": "passed"}],
                "reasons": {"c0": {"relevance": "fine"}},
            }

        monkeypatch.setattr("core.database.get_tenant_session", _session)
        monkeypatch.setattr(datasets, "get_version", AsyncMock(return_value=version))
        monkeypatch.setattr(runs, "run_version", _run_version)
        config = synthetic.validate_config(
            "eval_dataset",
            {
                "dataset_id": str(dataset_id),
                "version": 2,
                "system": "Decide.",
                "model": "gpt-4o",
                "judges": ["relevance"],
                "judge_model": "gpt-4o-mini",
                "limit": 10,
                "min_pass_rate": 90,
            },
        )
        reasons, detail = asyncio.run(synthetic._probe_eval_dataset(TENANT, config))
        assert len(asked["cases"]) == 10 and asked["offset"] == 0 and asked["judges"] == ("relevance",)
        # The version's labels come from every case, not only the ten that run.
        assert asked["labels"] == ("decline",)
        assert asked["system_text"] == "Decide." and asked["model"] == "gpt-4o" and asked["tenant_id"] == TENANT
        [run] = added
        assert (
            run.prompt_label == "scheduled:gpt-4o"
            and run.created_by_user is None
            and run.results == [{"id": "c0", "result": "passed"}]
        )
        assert reasons == ["eval_cases_not_answered", "eval_pass_rate_below_minimum"]
        assert detail == {
            "run_id": str(run.id),
            "version": 2,
            "cases_run": 10,
            "complete": False,
            "passed": 8,
            "failed": 1,
            "errors": 1,
            "pass_rate": round(8 / 9, 4),
            "scores": {"relevance": 0.8},
            "cost_usd": 0.01,
        }
        assert "fine" not in str(detail) and "Decide." not in str(detail)

    def test_the_dataset_probe_is_an_error_while_evaluation_is_off(self):
        assert settings.evals_v2_enabled is False
        with pytest.raises(RuntimeError, match="off"):
            asyncio.run(
                synthetic._probe_eval_dataset(
                    TENANT,
                    {"dataset_id": str(uuid.uuid4()), "system": "s", "model": "m", "limit": 25, "min_pass_rate": 100},
                )
            )


class TestRun:
    def test_dataset_deadline_scales_with_serial_work_and_stays_below_schedule_interval(self):
        assert synthetic.probe_timeout_seconds("model", {}) == 60
        assert synthetic.probe_timeout_seconds("eval_dataset", {"limit": 1}) == 70
        assert synthetic.probe_timeout_seconds("eval_dataset", {"limit": 5, "judges": ["relevance"]}) == 160
        assert synthetic.probe_timeout_seconds("eval_dataset", {"limit": 25}) == 240
        assert synthetic.probe_timeout_seconds("eval_dataset", {"limit": 25, "judges": ["relevance"]}) == 240
        assert synthetic.MAX_EVAL_PROBE_TIMEOUT_SECONDS < synthetic.MIN_INTERVAL_MINUTES * 60

    def test_dataset_outlives_single_probe_deadline_but_keeps_latency_failure(self, monkeypatch):
        async def _dataset(_tenant, _config):
            await asyncio.sleep(0.03)
            return [], {"run_id": "synthetic-run"}

        monkeypatch.setattr(synthetic, "PROBE_TIMEOUT_SECONDS", 0.001)
        monkeypatch.setattr(synthetic, "EVAL_CALL_BUDGET_SECONDS", 0.1)
        monkeypatch.setattr(synthetic, "_probe_eval_dataset", _dataset)
        check = _check("eval_dataset", config={
            "dataset_id": str(uuid.uuid4()), "system": "Synthetic prompt", "model": "m", "limit": 1,
        })
        check.config["max_latency_ms"] = 1
        result = asyncio.run(synthetic.probe(check))
        assert result.status == "failed" and result.reasons == ["too_slow"]
        assert result.detail == {"run_id": "synthetic-run"}
        monkeypatch.setattr(synthetic, "MAX_EVAL_PROBE_TIMEOUT_SECONDS", 0.001)
        expired = asyncio.run(synthetic.probe(check))
        assert expired.status == "error" and expired.detail == {"error_type": "TimeoutError"}

    def test_ok_failed_and_error_results(self, monkeypatch):
        monkeypatch.setattr(synthetic, "_probe_model", AsyncMock(return_value=([], {"model": "m", "tokens": 3})))
        ok = asyncio.run(synthetic.probe(_check()))
        assert (ok.status, ok.reasons, ok.detail, ok.trigger) == ("ok", [], {"model": "m", "tokens": 3}, "schedule")
        assert ok.started_at.tzinfo is not None and ok.latency_ms >= 0

        monkeypatch.setattr(synthetic, "_probe_model", AsyncMock(return_value=(["empty_answer"], {"model": "m"})))
        failed = asyncio.run(synthetic.probe(_check(), trigger="manual"))
        assert (failed.status, failed.reasons, failed.trigger) == ("failed", ["empty_answer"], "manual")

        monkeypatch.setattr(synthetic, "_probe_model", AsyncMock(side_effect=RuntimeError("provider secret detail")))
        error = asyncio.run(synthetic.probe(_check()))
        assert error.status == "error" and error.detail == {"error_type": "RuntimeError"} and error.reasons == []

    def test_a_slow_probe_fails_and_a_hung_one_is_an_error(self, monkeypatch):
        async def _slow(_tenant, _config):
            await asyncio.sleep(0.05)
            return [], {}

        monkeypatch.setattr(synthetic, "_probe_model", _slow)
        slow = asyncio.run(synthetic.probe(_check(config={"prompt": "p", "max_latency_ms": 1})))
        assert (slow.status, slow.reasons) == ("failed", ["too_slow"])
        monkeypatch.setattr(synthetic, "PROBE_TIMEOUT_SECONDS", 0.01)
        hung = asyncio.run(synthetic.probe(_check()))
        assert hung.status == "error" and hung.detail == {"error_type": "TimeoutError"}

    def test_a_stored_configuration_that_is_no_longer_valid_is_an_error(self):
        result = asyncio.run(synthetic.probe(_check(config={"prompt": ""})))
        assert result.status == "error" and result.detail == {"error_type": "ValueError"}

    def test_run_check_stores_and_meters_the_result(self, monkeypatch):
        from observability.metrics import synthetic_checks_total

        stored: list = []

        async def _store(check, result):
            stored.append((check, result))

        claimed = AsyncMock(return_value=True)
        monkeypatch.setattr(synthetic, "claim", claimed)
        monkeypatch.setattr(synthetic, "_store_result", _store)
        monkeypatch.setattr(
            synthetic, "_probe_knowledge", AsyncMock(return_value=(["too_few_results"], {"results": 0}))
        )
        counter = synthetic_checks_total.labels(kind="knowledge", result="failed")
        before = counter._value.get()
        check = _check("knowledge")
        result = asyncio.run(synthetic.run_check(check, trigger="manual"))
        assert stored == [(check, result)] and result.status == "failed"
        assert counter._value.get() == before + 1
        claimed.assert_awaited_once_with(check)

    def test_a_check_another_runner_holds_is_not_probed(self, monkeypatch):
        probed = AsyncMock(return_value=([], {}))
        store = AsyncMock()
        monkeypatch.setattr(synthetic, "claim", AsyncMock(return_value=False))
        monkeypatch.setattr(synthetic, "_probe_model", probed)
        monkeypatch.setattr(synthetic, "_store_result", store)
        assert asyncio.run(synthetic.run_check(_check())) is None
        probed.assert_not_awaited()
        store.assert_not_awaited()

    def test_the_claim_moves_last_run_at_only_from_the_value_the_runner_read(self, monkeypatch):
        import contextlib

        statements: list = []
        answers = [1, 0]

        class _Session:
            async def execute(self, statement):
                statements.append(statement)
                return SimpleNamespace(rowcount=answers.pop(0))

        @contextlib.asynccontextmanager
        async def _ctx(_tid):
            yield _Session()

        monkeypatch.setattr("core.database.get_tenant_session", _ctx)
        seen = datetime(2026, 10, 4, 11, 0, tzinfo=UTC)
        now = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
        assert asyncio.run(synthetic.claim(_check(last_run_at=seen), now=now)) is True
        assert asyncio.run(synthetic.claim(_check(), now=now)) is False
        first, second = (str(s.compile(compile_kwargs={"literal_binds": False})) for s in statements)
        assert "UPDATE synthetic_checks SET last_run_at=" in first
        assert "synthetic_checks.last_run_at = " in first and "synthetic_checks.last_run_at IS NULL" in second

    def test_creations_take_the_tenant_lock_before_the_count(self):
        statements: list[tuple[str, dict]] = []

        class _Session:
            async def execute(self, statement, params):
                statements.append((str(statement), params))

        asyncio.run(synthetic._lock_tenant(_Session(), TENANT))
        assert len(statements) == 1 and "pg_advisory_xact_lock" in statements[0][0]
        assert statements[0][1] == {"key": f"synthetic_checks:{TENANT}"}
        src = (ROOT / "observability" / "synthetic.py").read_text(encoding="utf-8")
        body = src[src.index("async def create_check(") : src.index("async def update_check(")]
        assert body.index("await _lock_tenant(session, tenant_id)") < body.index("func.count()")

    def test_the_metric_carries_no_tenant_label(self):
        from observability.metrics import synthetic_checks_total

        assert tuple(synthetic_checks_total._labelnames) == ("kind", "result")

    def test_due_checks_are_the_longest_waiting_first(self, monkeypatch):
        now = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
        never = _check(name="never")
        old = _check(name="old", last_run_at=now - timedelta(hours=5))
        older = _check(name="older", last_run_at=now - timedelta(hours=9))
        fresh = _check(name="fresh", last_run_at=now - timedelta(minutes=1))
        off = _check(name="off", enabled=False)
        monkeypatch.setattr(synthetic, "list_checks", AsyncMock(return_value=[fresh, old, off, never, older]))
        due = asyncio.run(synthetic.due_checks(TENANT, now=now))
        assert [check.name for check in due] == ["never", "older", "old"]
        assert [check.name for check in asyncio.run(synthetic.due_checks(TENANT, now=now, limit=1))] == ["never"]


class TestTasks:
    def test_the_sweep_is_off_by_default(self):
        from core.tasks import synthetic_tasks

        assert settings.synthetic_checks_enabled is False
        with patch.object(synthetic_tasks, "_tenants_with_checks", AsyncMock(side_effect=AssertionError("never read"))):
            assert asyncio.run(synthetic_tasks._run_synthetic_checks_async()) == {
                "enabled": False,
                "tenants": 0,
                "ran": 0,
                "not_ok": 0,
                "errors": 0,
            }

    def test_the_sweep_runs_due_checks_and_isolates_failures(self, monkeypatch):
        from core.tasks import synthetic_tasks

        monkeypatch.setattr(settings, "synthetic_checks_enabled", True)
        t1, t2, t3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        good, bad, broken = _check(name="good"), _check(name="bad"), _check(name="broken")

        async def _due(tenant_id, **_k):
            if tenant_id == t2:
                raise RuntimeError("tenant database unavailable")
            return [good, bad, broken] if tenant_id == t1 else []

        async def _run(check, **_k):
            if check is broken:
                raise RuntimeError("result not stored")
            status = "ok" if check is good else "failed"
            return synthetic.Result(check_id=check.id, status=status, latency_ms=1, started_at=datetime.now(UTC))

        with (
            patch.object(synthetic_tasks, "_tenants_with_checks", AsyncMock(return_value=[t1, t2, t3])),
            patch.object(synthetic, "due_checks", _due),
            patch.object(synthetic, "run_check", _run),
        ):
            result = asyncio.run(synthetic_tasks._run_synthetic_checks_async())
        assert result == {"enabled": True, "tenants": 3, "ran": 2, "not_ok": 1, "errors": 2}

    def test_the_sweep_skips_a_check_another_runner_holds(self, monkeypatch):
        from core.tasks import synthetic_tasks

        monkeypatch.setattr(settings, "synthetic_checks_enabled", True)
        with (
            patch.object(synthetic_tasks, "_tenants_with_checks", AsyncMock(return_value=[uuid.uuid4()])),
            patch.object(synthetic, "due_checks", AsyncMock(return_value=[_check()])),
            patch.object(synthetic, "run_check", AsyncMock(return_value=None)),
        ):
            result = asyncio.run(synthetic_tasks._run_synthetic_checks_async())
        assert result == {"enabled": True, "tenants": 1, "ran": 0, "not_ok": 0, "errors": 0}

    def test_the_tasks_are_scheduled(self):
        from core.tasks.celery_app import app

        assert "core.tasks.synthetic_tasks" in app.conf.include
        schedule = app.conf.beat_schedule
        assert schedule["run-synthetic-checks"]["task"] == "core.tasks.synthetic_tasks.run_synthetic_checks"
        assert schedule["prune-synthetic-results"]["task"] == "core.tasks.synthetic_tasks.prune_synthetic_results"

    def test_the_tables_are_tenant_scoped_under_row_level_security(self):
        migration = (ROOT / "migrations" / "versions" / "v6_z42_synthetic_checks.py").read_text(encoding="utf-8")
        assert 'down_revision = "v6z41_tamper_evident_audit"' in migration
        assert '_TABLES = ("synthetic_checks", "synthetic_check_results")' in migration
        assert "FORCE ROW LEVEL SECURITY" in migration and "current_setting('agenticorg.tenant_id', true)" in migration
        later = (ROOT / "migrations" / "versions" / "v6_z46_synthetic_check_kinds.py").read_text(encoding="utf-8")
        assert 'down_revision = "v6z45_eval_runs"' in later
        for kind in synthetic.KINDS:
            # The first four kinds were created with the table; the later two widened its check constraint.
            assert f"'{kind}'" in migration or f"'{kind}'" in later
        assert "DROP CONSTRAINT IF EXISTS ck_synthetic_checks_kind" in later
        from core.models.synthetic_check import SyntheticCheck

        constraint = next(c for c in SyntheticCheck.__table__.constraints if c.name == "ck_synthetic_checks_kind")
        for kind in synthetic.KINDS:
            assert f"'{kind}'" in str(constraint.sqltext)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _app(scopes: list[str]) -> FastAPI:
    app = FastAPI(dependencies=[Depends(enforce_route_metadata)])

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        request.state.auth_mode = "api_key"
        request.state.claims = {"sub": "apikey:key_01"}
        request.state.scopes = scopes
        request.state.tenant_id = str(TENANT)
        return await call_next(request)

    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[get_current_tenant] = lambda: str(TENANT)
    return app


@pytest.fixture
def _no_rate_limit_redis():
    # The route rate limiter counts in Redis; a unit test never reaches one (FINDINGS A-59).
    with patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)):
        yield


@pytest.mark.usefixtures("_no_rate_limit_redis")
class TestEndpoints:
    def test_every_route_is_admin_only(self):
        client = TestClient(_app(["agents:write"]))
        cid = uuid.uuid4()
        assert client.get("/api/v1/observability/checks").status_code == 403
        assert client.post("/api/v1/observability/checks", json={"name": "n", "kind": "model"}).status_code == 403
        assert client.patch(f"/api/v1/observability/checks/{cid}", json={"enabled": False}).status_code == 403
        assert client.delete(f"/api/v1/observability/checks/{cid}").status_code == 403
        assert client.post(f"/api/v1/observability/checks/{cid}/run").status_code == 403
        assert client.get(f"/api/v1/observability/checks/{cid}/results").status_code == 403

    def test_the_list_says_whether_the_sweep_is_on(self, monkeypatch):
        check = _check()
        monkeypatch.setattr(synthetic, "list_checks", AsyncMock(return_value=[check]))
        client = TestClient(_app(["agenticorg:admin"]))
        body = client.get("/api/v1/observability/checks").json()
        assert body["enabled"] is False and body["kinds"] == list(synthetic.KINDS) and body["limit"] == 20
        assert body["checks"] == [check.to_dict()]

    def test_create_attributes_the_caller_and_refuses_an_invalid_check(self, monkeypatch):
        created = _check(created_by="api_key:apikey:key_01")
        create = AsyncMock(return_value=created)
        monkeypatch.setattr(synthetic, "create_check", create)
        client = TestClient(_app(["agenticorg:admin"]))
        payload = {"name": "model check", "kind": "model", "config": {"prompt": "p"}, "interval_minutes": 30}
        # Off (the default), nothing is added.
        refused_off = client.post("/api/v1/observability/checks", json=payload)
        assert refused_off.status_code == 409 and create.await_count == 0
        monkeypatch.setattr(settings, "synthetic_checks_enabled", True)
        resp = client.post("/api/v1/observability/checks", json=payload)
        assert resp.status_code == 201 and resp.json()["id"] == str(created.id)
        create.assert_awaited_once_with(
            TENANT,
            actor_id="api_key:apikey:key_01",
            name="model check",
            kind="model",
            config={"prompt": "p"},
            interval_minutes=30,
            enabled=True,
        )
        monkeypatch.setattr(synthetic, "create_check", AsyncMock(side_effect=ValueError("prompt is required")))
        refused = client.post("/api/v1/observability/checks", json={"name": "n", "kind": "model"})
        assert refused.status_code == 422 and "prompt is required" in refused.text
        assert client.post("/api/v1/observability/checks", json={**payload, "interval_minutes": 1}).status_code == 422

    def test_update_delete_and_their_404s(self, monkeypatch):
        check = _check()
        update = AsyncMock(return_value=check)
        monkeypatch.setattr(synthetic, "update_check", update)
        client = TestClient(_app(["agenticorg:admin"]))
        resp = client.patch(f"/api/v1/observability/checks/{check.id}", json={"enabled": False})
        assert resp.status_code == 200
        update.assert_awaited_once_with(TENANT, check.id, actor_id="api_key:apikey:key_01", changes={"enabled": False})
        assert client.patch(f"/api/v1/observability/checks/{check.id}", json={}).status_code == 422
        monkeypatch.setattr(synthetic, "update_check", AsyncMock(return_value=None))
        assert client.patch(f"/api/v1/observability/checks/{check.id}", json={"enabled": True}).status_code == 404
        monkeypatch.setattr(synthetic, "delete_check", AsyncMock(return_value=True))
        assert client.delete(f"/api/v1/observability/checks/{check.id}").status_code == 204
        monkeypatch.setattr(synthetic, "delete_check", AsyncMock(return_value=False))
        assert client.delete(f"/api/v1/observability/checks/{check.id}").status_code == 404

    def test_run_now_and_the_results(self, monkeypatch):
        check = _check()
        result = synthetic.Result(
            check_id=check.id, status="ok", latency_ms=420, started_at=datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
        )
        run = AsyncMock(return_value=result)
        monkeypatch.setattr(synthetic, "get_check", AsyncMock(return_value=check))
        monkeypatch.setattr(synthetic, "run_check", run)
        monkeypatch.setattr(synthetic, "results", AsyncMock(return_value=[result]))
        client = TestClient(_app(["agenticorg:admin"]))
        # Off (the default), nothing runs; what is stored can still be read.
        assert client.post(f"/api/v1/observability/checks/{check.id}/run").status_code == 409
        assert run.await_count == 0
        assert client.get(f"/api/v1/observability/checks/{check.id}/results").status_code == 200
        monkeypatch.setattr(settings, "synthetic_checks_enabled", True)
        resp = client.post(f"/api/v1/observability/checks/{check.id}/run")
        assert resp.status_code == 200 and resp.json() == result.to_dict()
        run.assert_awaited_once_with(check, trigger="manual")
        monkeypatch.setattr(synthetic, "run_check", AsyncMock(return_value=None))
        busy = client.post(f"/api/v1/observability/checks/{check.id}/run")
        assert busy.status_code == 409 and "already running" in busy.text
        monkeypatch.setattr(synthetic, "run_check", run)
        listed = client.get(f"/api/v1/observability/checks/{check.id}/results", params={"limit": 5})
        assert listed.status_code == 200 and listed.json() == {"results": [result.to_dict()]}
        assert client.get(f"/api/v1/observability/checks/{check.id}/results", params={"limit": 0}).status_code == 422
        monkeypatch.setattr(synthetic, "get_check", AsyncMock(return_value=None))
        assert client.post(f"/api/v1/observability/checks/{check.id}/run").status_code == 404
        assert client.get(f"/api/v1/observability/checks/{check.id}/results").status_code == 404


# ---------------------------------------------------------------------------
# Storage and the task plumbing, against a scripted session
# ---------------------------------------------------------------------------


class _Answer:
    def __init__(self, value=None, rowcount=0):
        self.value = value
        self.rowcount = rowcount

    def all(self):
        return list(self.value or [])

    def one(self):
        return self.value


class _ScriptedSession:
    """A session whose reads answer from a queue, recording what was added and executed."""

    def __init__(self, answers: list) -> None:
        self.answers = list(answers)
        self.added: list = []
        self.executed: list = []

    def _next(self):
        return self.answers.pop(0)

    async def scalars(self, statement):
        self.executed.append(statement)
        return _Answer(self._next())

    async def scalar(self, statement):
        self.executed.append(statement)
        return self._next()

    async def execute(self, statement, params=None):
        self.executed.append(statement)
        answer = self._next() if self.answers else None
        return answer if isinstance(answer, _Answer) else _Answer(answer)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None

    async def refresh(self, row):
        return None


@pytest.fixture
def scripted(monkeypatch):
    import contextlib

    def _use(answers: list) -> _ScriptedSession:
        session = _ScriptedSession(answers)

        @contextlib.asynccontextmanager
        async def _ctx(_tid=None):
            yield session

        monkeypatch.setattr("core.database.get_tenant_session", _ctx)
        monkeypatch.setattr("core.database.async_session_factory", _ctx)
        return session

    return _use


def _stored(**over) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "name": "stored",
        "kind": "model",
        "config": {"prompt": "p"},
        "interval_minutes": 30,
        "enabled": True,
        "last_run_at": None,
        "last_status": None,
        "created_by": "user:1",
        "created_at": datetime(2026, 10, 4, 9, 0, tzinfo=UTC),
        "updated_by": None,
        "updated_at": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


class TestStorage:
    def test_list_get_and_results_map_rows(self, scripted):
        row = _stored()
        scripted([[row]])
        assert [check.name for check in asyncio.run(synthetic.list_checks(TENANT))] == ["stored"]
        scripted([row])
        found = asyncio.run(synthetic.get_check(TENANT, row.id))
        assert found is not None and found.to_dict()["created_at"] == "2026-10-04T09:00:00+00:00"
        scripted([None])
        assert asyncio.run(synthetic.get_check(TENANT, row.id)) is None
        result_row = SimpleNamespace(
            id=uuid.uuid4(),
            check_id=row.id,
            status="failed",
            latency_ms=12,
            started_at=datetime(2026, 10, 4, 10, 0, tzinfo=UTC),
            reasons=["too_slow"],
            detail={"results": 1},
            trigger="schedule",
        )
        scripted([[result_row]])
        listed = asyncio.run(synthetic.results(TENANT, row.id, limit=5))
        assert [r.to_dict()["reasons"] for r in listed] == [["too_slow"]]

    def test_create_validates_locks_and_holds_the_limit_and_the_name(self, scripted):
        session = scripted([None, _Answer((3, 0))])
        created = asyncio.run(
            synthetic.create_check(TENANT, actor_id="user:1", name=" nightly ", kind="audit_chain", config={})
        )
        assert (created.name, created.config, created.created_by) == ("nightly", {"recent": 1000}, "user:1")
        assert len(session.added) == 1 and "pg_advisory_xact_lock" in str(session.executed[0])
        scripted([None, _Answer((3, 1))])
        with pytest.raises(ValueError, match="already exists"):
            asyncio.run(synthetic.create_check(TENANT, actor_id="u", name="n", kind="audit_chain", config={}))
        scripted([None, _Answer((synthetic.MAX_CHECKS, 0))])
        with pytest.raises(ValueError, match="at most 20"):
            asyncio.run(synthetic.create_check(TENANT, actor_id="u", name="n", kind="audit_chain", config={}))
        with pytest.raises(ValueError, match="prompt is required"):
            asyncio.run(synthetic.create_check(TENANT, actor_id="u", name="n", kind="model", config={}))

    def test_update_changes_only_what_is_allowed(self, scripted):
        row = _stored()
        scripted([row, None])
        changes = {"name": "renamed", "config": {"prompt": "q"}, "interval_minutes": 15, "enabled": False}
        updated = asyncio.run(synthetic.update_check(TENANT, row.id, actor_id="user:2", changes=changes))
        assert (updated.name, updated.config, updated.interval_minutes, updated.enabled, updated.updated_by) == (
            "renamed",
            {"prompt": "q"},
            15,
            False,
            "user:2",
        )
        scripted([_stored(), uuid.uuid4()])
        with pytest.raises(ValueError, match="already exists"):
            asyncio.run(synthetic.update_check(TENANT, row.id, actor_id="u", changes={"name": "taken"}))
        scripted([None])
        assert asyncio.run(synthetic.update_check(TENANT, row.id, actor_id="u", changes={"enabled": True})) is None
        with pytest.raises(ValueError, match="cannot change: kind"):
            asyncio.run(synthetic.update_check(TENANT, row.id, actor_id="u", changes={"kind": "model"}))

    def test_delete_removes_the_results_with_the_check(self, scripted):
        session = scripted([_Answer(rowcount=4), _Answer(rowcount=1)])
        assert asyncio.run(synthetic.delete_check(TENANT, uuid.uuid4())) is True
        assert "synthetic_check_results" in str(session.executed[0]) and "synthetic_checks" in str(session.executed[1])
        scripted([_Answer(rowcount=0), _Answer(rowcount=0)])
        assert asyncio.run(synthetic.delete_check(TENANT, uuid.uuid4())) is False

    def test_a_result_is_stored_with_the_last_status_of_its_check(self, scripted):
        session = scripted([])
        check = _check()
        result = synthetic.Result(
            check_id=check.id,
            status="failed",
            latency_ms=7,
            started_at=datetime(2026, 10, 4, 12, 0, tzinfo=UTC),
            reasons=["empty_answer"],
        )
        asyncio.run(synthetic._store_result(check, result))
        assert [(row.status, row.reasons, row.tenant_id) for row in session.added] == [
            ("failed", ["empty_answer"], TENANT)
        ]
        assert "UPDATE synthetic_checks" in str(session.executed[-1])

    def test_enabled_follows_the_setting(self, monkeypatch):
        assert synthetic.enabled() is False
        monkeypatch.setattr(settings, "synthetic_checks_enabled", True)
        assert synthetic.enabled() is True


class TestTaskPlumbing:
    def test_tenant_reads_cross_tenants_with_row_security_off(self, scripted):
        from core.tasks import synthetic_tasks

        t1 = uuid.uuid4()
        session = scripted([None, [t1]])
        assert asyncio.run(synthetic_tasks._tenants_with_checks()) == [t1]
        assert "row_security = off" in str(session.executed[0])
        session = scripted([None, [t1]])
        assert asyncio.run(synthetic_tasks._result_tenants()) == [t1]
        assert "row_security = off" in str(session.executed[0])

    def test_the_prune_deletes_old_results_per_tenant_and_isolates_a_failure(self, scripted, monkeypatch):
        import contextlib

        from core.tasks import synthetic_tasks

        t1, t2 = uuid.uuid4(), uuid.uuid4()
        monkeypatch.setattr(synthetic_tasks, "_result_tenants", AsyncMock(return_value=[t1, t2]))
        scripted([_Answer(rowcount=6), _Answer(rowcount=2)])
        outcome = asyncio.run(synthetic_tasks._prune_synthetic_results_async(days=7))
        assert outcome["tenants"] == 2 and outcome["deleted"] == 8 and outcome["errors"] == 0
        cutoff = datetime.fromisoformat(outcome["cutoff"])
        assert timedelta(days=6, hours=23) < datetime.now(UTC) - cutoff < timedelta(days=7, hours=1)

        @contextlib.asynccontextmanager
        async def _broken(_tid):
            raise RuntimeError("tenant database unavailable")
            yield  # pragma: no cover

        monkeypatch.setattr("core.database.get_tenant_session", _broken)
        failed = asyncio.run(synthetic_tasks._prune_synthetic_results_async())
        assert failed["errors"] == 2 and failed["deleted"] == 0

    def test_the_celery_tasks_run_their_coroutines(self, monkeypatch):
        from core.tasks import synthetic_tasks

        monkeypatch.setattr(synthetic_tasks, "run_async", lambda coroutine: coroutine.close() or {"ran": True})
        assert synthetic_tasks.run_synthetic_checks() == {"ran": True}
        assert synthetic_tasks.prune_synthetic_results(3) == {"ran": True}
