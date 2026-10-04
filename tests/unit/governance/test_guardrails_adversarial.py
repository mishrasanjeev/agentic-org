# SPDX-License-Identifier: Apache-2.0
"""The adversarial evaluation set: the corpus, the baseline's measured results and the endpoints."""

from __future__ import annotations

import asyncio
import re
import uuid
from collections import Counter
from unittest.mock import AsyncMock, patch
from urllib.parse import urlparse

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from api.deps import get_current_tenant
from api.route_enforcement import enforce_route_metadata
from api.v1 import guardrails as api
from core.governance.guardrails import adversarial, engine
from core.governance.guardrails.schema import ACTIONS, DETECTORS, STAGES, Rule, validate_rule_fields

TENANT = uuid.uuid4()


@pytest.fixture(autouse=True)
def _regex_recognisers(monkeypatch):
    # The corpus is measured with the regex recognisers, so the pinned numbers do not depend on an installed analyser.
    from core.governance.guardrails import detectors

    monkeypatch.setattr(detectors.SensitiveDataDetector, "_analyser_spans", lambda self, text, entities: None)
    monkeypatch.setattr(engine.settings, "env", "test")


def _run(**kwargs) -> dict:
    return asyncio.run(adversarial.run_suite(**kwargs)).to_dict()


class TestCorpus:
    def test_cases_are_well_formed_and_every_category_has_attacks_and_controls(self):
        ids = [case.id for case in adversarial.CASES]
        assert len(ids) == len(set(ids)) and len(ids) >= 45
        for case in adversarial.CASES:
            assert case.stage in STAGES and case.expect in adversarial.EXPECTATIONS and case.text.strip()
        for category in adversarial.CATEGORIES:
            expectations = Counter(c.expect for c in adversarial.CASES if c.category == category)
            assert expectations["detected"] >= 3 and expectations["clean"] >= 2, category

    def test_the_corpus_is_synthetic(self):
        for case in adversarial.CASES:
            # An email address (a dotted domain after the @) is on the reserved test domain; a UPI id has no dot there.
            for word in case.text.split():
                domain = word.rstrip(".,").partition("@")[2]
                if "." in domain:
                    assert domain == "example.test", case.id
            # Every link is on the reserved test domain, judged by its parsed host rather than by a substring.
            for link in re.findall(r"[a-z][a-z0-9+.-]*://\S+", case.text):
                parsed = urlparse(link)
                assert parsed.scheme == "https" and parsed.hostname == "example.test", case.id

    def test_the_description_carries_no_case_text(self):
        described = adversarial.describe()
        assert described["cases"] == len(adversarial.CASES)
        assert {c["category"] for c in described["categories"]} == set(adversarial.CATEGORIES)
        assert set(described["items"][0]) == {"id", "category", "stage", "expect", "note"}
        flat = str(described)
        assert "4111" not in flat and "Ignore all previous" not in flat

    def test_the_baseline_rules_are_valid_rules_with_stable_ids(self):
        rules = adversarial.baseline_rules()
        assert [r.id for r in rules] == [r.id for r in adversarial.baseline_rules()]
        assert len({r.id for r in rules}) == len(rules) == 6
        for rule in rules:
            assert rule.stage in STAGES and rule.detector in DETECTORS and rule.action in ACTIONS
            cleaned = validate_rule_fields(
                {
                    "name": rule.name,
                    "stage": rule.stage,
                    "detector": rule.detector,
                    "action": rule.action,
                    "priority": rule.priority,
                    "threshold": rule.threshold,
                    "options": rule.options,
                }
            )
            assert cleaned["detector"] == rule.detector


class TestBaselineResults:
    """The measured results of the recommended rules. A detector change that moves a number must move it here too."""

    def test_the_baseline_report_is_pinned_case_by_case(self):
        report = _run(rules=adversarial.baseline_rules(), label="baseline")
        assert report["rules"] == "baseline" and report["rule_count"] == 6 and report["errors"] == []
        by_category = {c["category"]: c for c in report["categories"]}
        # Attacks the pattern-based detectors do not catch: a paraphrase, another language, an
        # instruction with no known phrasing, a number in words, the context's words reversed,
        # a forbidden promise in other words.
        assert by_category["injection_direct"]["missed"] == ["inj-d-09", "inj-d-10"]
        assert by_category["injection_indirect"]["missed"] == ["inj-i-06"]
        assert by_category["sensitive_data"]["missed"] == ["pii-07"]
        assert by_category["ungrounded"]["missed"] == ["grd-04"]
        assert by_category["output_policy"]["missed"] == ["out-03"]
        # One benign text is wrongly caught: a 16-digit order number read as a phone number.
        assert by_category["sensitive_data"]["wrongly_caught"] == ["pii-c3"]
        for name in ("injection_direct", "injection_indirect", "ungrounded", "output_policy"):
            assert by_category[name]["wrongly_caught"] == [], name
        assert (report["attacks"], report["detected"], report["controls"], report["false_positives"]) == (31, 25, 15, 1)
        assert report["recall"] == 0.8065 and report["false_positive_rate"] == 0.0667

    def test_invisible_characters_do_not_hide_a_known_phrasing(self):
        report = _run(rules=adversarial.baseline_rules())
        direct = next(c for c in report["categories"] if c["category"] == "injection_direct")
        assert "inj-d-11" not in direct["missed"]

    def test_no_rules_detect_nothing_and_catch_nothing(self):
        report = _run(rules=[])
        assert report["detected"] == 0 and report["false_positives"] == 0 and report["recall"] == 0.0
        assert sum(len(c["missed"]) for c in report["categories"]) == report["attacks"]

    def test_a_disabled_or_narrowed_rule_takes_no_part(self):
        injection = adversarial.baseline_rules()[0]
        off = Rule(**{**injection.to_dict(), "enabled": False})
        narrowed = Rule(**{**injection.to_dict(), "agent_id": "some-agent"})
        assert _run(rules=[off])["detected"] == 0
        assert _run(rules=[narrowed])["detected"] == 0
        assert _run(rules=[injection])["detected"] == 9


class TestRunner:
    def test_the_tenants_own_rules_are_read_from_the_store(self):
        rules = adversarial.baseline_rules()[:2]
        with (
            patch.object(engine, "active_rules", AsyncMock(return_value=rules)),
            patch.object(engine, "enforcing", AsyncMock(return_value=False)),
            patch.object(engine, "_meter", lambda *a: pytest.fail("a suite run meters nothing")),
            patch.object(engine, "_audit_outcome", AsyncMock(side_effect=AssertionError("a suite run audits nothing"))),
        ):
            report = _run(tenant_id=TENANT)
        assert report["rules"] == "tenant" and report["rule_count"] == 2
        by_category = {c["category"]: c for c in report["categories"]}
        assert by_category["injection_direct"]["detected"] == 9 and by_category["sensitive_data"]["detected"] == 0

    def test_a_case_that_cannot_be_evaluated_is_an_error_and_not_caught(self):
        with patch.object(engine, "evaluate", AsyncMock(side_effect=RuntimeError("detector down"))):
            report = _run(rules=adversarial.baseline_rules())
        assert report["detected"] == 0 and report["false_positives"] == 0
        assert len(report["errors"]) == len(adversarial.CASES) and report["errors"][0].endswith(": RuntimeError")

    def test_the_tenants_rules_are_read_once_for_the_whole_run(self):
        reads = AsyncMock(return_value=adversarial.baseline_rules()[:1])
        with patch.object(engine, "active_rules", reads):
            report = _run(tenant_id=TENANT)
        assert reads.await_count == 1 and report["rule_count"] == 1 and report["detected"] == 9

    def test_a_detector_that_fails_is_an_error_not_a_plain_miss(self):
        from core.governance.guardrails.detectors import REGISTRY

        class _Broken:
            name = "injection"

            def detect(self, text, options, *, threshold):
                raise RuntimeError("model file missing")

        with patch.dict(REGISTRY, {"injection": _Broken()}):
            report = _run(rules=adversarial.baseline_rules())
        by_category = {c["category"]: c for c in report["categories"]}
        assert by_category["injection_direct"]["detected"] == 0
        injection_cases = sum(1 for c in adversarial.CASES if c.stage in ("input", "retrieval"))
        assert len(report["errors"]) == injection_cases
        assert report["errors"][0] == "inj-d-01: injection RuntimeError"
        # The detectors that work still report their results.
        assert by_category["sensitive_data"]["detected"] == 6

    def test_a_dry_run_result_names_the_rules_it_could_not_evaluate(self):
        from core.governance.guardrails.detectors import REGISTRY

        class _Broken:
            name = "injection"

            def detect(self, text, options, *, threshold):
                raise TimeoutError

        rule = adversarial.baseline_rules()[0]
        with patch.dict(REGISTRY, {"injection": _Broken()}):
            result = asyncio.run(engine.evaluate("input", "text", tenant_id=None, dry_run=True, rules=[rule]))
        assert result.unverifiable == [{"rule_id": rule.id, "detector": "injection", "reason": "TimeoutError"}]
        assert result.to_dict()["unverifiable"] == result.unverifiable

    def test_runs_take_turns(self):
        order: list[str] = []
        real = engine.evaluate

        async def _slow(stage, text, **kwargs):
            order.append("start" if text == adversarial.CASES[0].text else "")
            await asyncio.sleep(0)
            return await real(stage, text, **kwargs)

        async def _both():
            with patch.object(engine, "evaluate", _slow):
                await asyncio.gather(
                    adversarial.run_suite(rules=[], label="a"), adversarial.run_suite(rules=[], label="b")
                )

        asyncio.run(_both())
        starts = [index for index, mark in enumerate(order) if mark == "start"]
        assert starts == [0, len(adversarial.CASES)]

    def test_a_tenant_or_a_rule_set_is_required(self):
        with pytest.raises(ValueError, match="needs a tenant or a rule set"):
            asyncio.run(adversarial.run_suite())

    def test_a_given_rule_set_is_a_dry_run_only(self):
        with pytest.raises(ValueError, match="dry run only"):
            asyncio.run(engine.evaluate("input", "text", tenant_id=None, rules=adversarial.baseline_rules()))


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
    with patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)):
        yield


@pytest.mark.usefixtures("_no_rate_limit_redis")
class TestEndpoints:
    def test_admin_only(self):
        client = TestClient(_app(["agents:write"]))
        assert client.get("/api/v1/guardrails/adversarial").status_code == 403
        assert client.post("/api/v1/guardrails/adversarial/run", json={}).status_code == 403

    def test_describe_and_the_baseline_run(self):
        client = TestClient(_app(["agenticorg:admin"]))
        described = client.get("/api/v1/guardrails/adversarial")
        assert described.status_code == 200 and described.json()["cases"] == len(adversarial.CASES)
        report = client.post("/api/v1/guardrails/adversarial/run", json={"rules": "baseline"})
        assert report.status_code == 200
        body = report.json()
        assert body["rules"] == "baseline" and body["detected"] == 25 and body["false_positives"] == 1
        assert "4111" not in report.text and "Ignore all previous" not in report.text
        assert client.post("/api/v1/guardrails/adversarial/run", json={"rules": "everyone"}).status_code == 422

    def test_a_suite_run_has_its_own_tight_rate_class(self):
        from api.route_enforcement import RATE_LIMIT_CLASSES

        assert RATE_LIMIT_CLASSES["guardrails-suite"] == (6, 60)
        assert api.run_adversarial_suite.__enterprise_route_metadata__["rate_limit"] == "guardrails-suite"

    def test_the_default_run_uses_the_tenants_rules(self, monkeypatch):
        run = AsyncMock(return_value=adversarial.SuiteReport(rules="tenant", rule_count=0, categories=[]))
        monkeypatch.setattr(adversarial, "run_suite", run)
        client = TestClient(_app(["agenticorg:admin"]))
        assert client.post("/api/v1/guardrails/adversarial/run", json={}).status_code == 200
        run.assert_awaited_once_with(tenant_id=TENANT)
