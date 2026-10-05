# SPDX-License-Identifier: Apache-2.0
"""Prompt comparison across models and evaluation against a reference dataset."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from api.deps import get_current_tenant, get_current_user, get_user_domains
from api.route_enforcement import RATE_LIMIT_CLASSES, enforce_route_metadata
from api.v1 import prompt_templates as api
from core.config import settings
from core.prompts import compare

TENANT = uuid.uuid4()
PROMPT = "You answer questions about the savings account in one sentence."
MODELS = ["gpt-4o-mini", "gemini-2.5-flash"]


def _answer(content: str, **over) -> SimpleNamespace:
    base = {
        "content": content,
        "model": "served-model",
        "latency_ms": 120,
        "tokens_used": 30,
        "input_tokens": 20,
        "output_tokens": 10,
        "cost_usd": 0.0004,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _models(by_model: dict):
    """Replace the model call: an answer, or an exception to raise, per model."""

    async def _complete(_tenant, model, messages, max_tokens):
        outcome = by_model[model]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome(messages) if callable(outcome) else outcome

    return patch.object(compare, "_complete", _complete)


class TestModels:
    def test_a_comparison_can_call_the_router_families_only(self):
        listed = compare.comparable_models()
        names = {entry["model"] for entry in listed}
        assert {"gpt-4o-mini", "gemini-2.5-flash"} <= names
        assert {entry["provider"] for entry in listed} <= set(compare.ROUTER_PROVIDERS)
        # A deployment-named entry would be sent to a public API by its family name; a model
        # with no family the router knows cannot be called at all.
        assert not any(name.startswith("deployment:") for name in names) and "o1" not in names

    def test_models_are_validated_and_deduplicated(self):
        assert compare.validate_models(["gpt-4o-mini", " gpt-4o-mini ", "gemini-2.5-flash"]) == MODELS
        for bad, message in (
            ([], "non-empty list"),
            ("gpt-4o", "non-empty list"),
            (["gpt-4o", "made-up-model"], "not a model a comparison can call: made-up-model"),
            (["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gemini-2.5-flash", "gemini-2.5-pro"], "at most 4"),
        ):
            with pytest.raises(ValueError, match=message):
                compare.validate_models(bad)

    def test_the_answer_length_is_bounded(self):
        assert compare.validate_max_tokens(None) == compare.DEFAULT_MAX_TOKENS
        assert compare.validate_max_tokens(64) == 64
        for bad in (0, compare.MAX_MAX_TOKENS + 1, "64", True):
            with pytest.raises(ValueError):
                compare.validate_max_tokens(bad)


class TestCompare:
    def test_each_model_gets_its_own_result_with_latency_tokens_and_cost(self):
        seen: list = []

        def _record(messages):
            seen.append(messages)
            return _answer("It earns 3.5% a year.")

        with _models({"gpt-4o-mini": _record, "gemini-2.5-flash": _answer("3.5% yearly.", cost_usd=0.0001)}):
            report = asyncio.run(
                compare.compare(TENANT, system_text=PROMPT, user_input="What does it earn?", models=MODELS)
            )
        first, second = report["results"]
        assert (first["model"], first["ok"], first["output"]) == ("gpt-4o-mini", True, "It earns 3.5% a year.")
        assert (first["latency_ms"], first["tokens"], first["cost_usd"]) == (120, 30, 0.0004)
        assert second["output"] == "3.5% yearly." and report["total_cost_usd"] == 0.0005
        assert report["max_tokens"] == compare.DEFAULT_MAX_TOKENS
        assert seen == [[{"role": "system", "content": PROMPT}, {"role": "user", "content": "What does it earn?"}]]

    def test_one_models_failure_is_its_own_result_and_keeps_no_message(self):
        failing = RuntimeError("provider said: secret detail")
        with _models({"gpt-4o-mini": failing, "gemini-2.5-flash": _answer("ok")}):
            report = asyncio.run(compare.compare(TENANT, system_text=PROMPT, user_input="q", models=MODELS))
        failed, worked = report["results"]
        assert failed["ok"] is False and failed["error_type"] == "RuntimeError" and failed["output"] == ""
        assert "secret detail" not in str(report) and worked["ok"] is True

    def test_the_call_goes_through_the_router_as_the_tenant(self):
        complete = AsyncMock(return_value=_answer("ok"))
        with patch("core.llm.router.llm_router.complete", complete):
            asyncio.run(
                compare.compare(TENANT, system_text=PROMPT, user_input="q", models=["gpt-4o-mini"], max_tokens=64)
            )
        complete.assert_awaited_once_with(
            [{"role": "system", "content": PROMPT}, {"role": "user", "content": "q"}],
            model_override="gpt-4o-mini",
            max_tokens=64,
            tenant_id=str(TENANT),
        )

    def test_empty_or_oversized_text_is_refused_before_any_call(self):
        called = AsyncMock()
        with patch.object(compare, "_complete", called):
            for kwargs in (
                {"system_text": " ", "user_input": "q"},
                {"system_text": PROMPT, "user_input": ""},
                {"system_text": PROMPT, "user_input": "x" * (compare.MAX_INPUT_CHARS + 1)},
            ):
                with pytest.raises(ValueError):
                    asyncio.run(compare.compare(TENANT, models=MODELS, **kwargs))
        called.assert_not_awaited()

    def test_calls_run_a_few_at_a_time(self):
        running = 0
        peak = 0

        async def _slow(_tenant, _model, _messages, _max_tokens):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1
            return _answer("ok")

        cases = compare.parse_cases([{"input": f"q{i}", "contains": ["ok"]} for i in range(12)])
        with patch.object(compare, "_complete", _slow):
            asyncio.run(compare.evaluate(TENANT, variants=[("v1", PROMPT)], cases=cases, model="gpt-4o-mini"))
        assert 1 < peak <= compare.CONCURRENCY


class TestCases:
    def test_a_case_is_an_input_with_at_least_one_expectation(self):
        [case] = compare.parse_cases(
            [{"id": "rate", "input": "What does it earn?", "contains": [" 3.5% "], "not_contains": ["guaranteed"]}]
        )
        assert (case.id, case.contains, case.not_contains) == ("rate", ("3.5%",), ("guaranteed",))
        assert [c.id for c in compare.parse_cases([{"input": "a", "equals": "x"}, {"input": "b", "equals": "y"}])] == [
            "1",
            "2",
        ]

    @pytest.mark.parametrize(
        ("cases", "message"),
        [
            ([], "non-empty list"),
            ([{"input": "q", "equals": "a"}] * 26, "at most 25"),
            (["q"], "must be an object"),
            ([{"input": "q"}], "needs at least one of"),
            ([{"input": "", "equals": "a"}], "input must be non-empty text"),
            ([{"input": "q", "equals": "a", "score": 1}], "unknown keys: score"),
            ([{"input": "q", "contains": "3.5%"}], "contains is a list"),
            ([{"input": "q", "contains": [""]}], "non-empty text"),
            ([{"input": "q", "matches": "(a|aa)+$"}], "repeats a group"),
            ([{"id": "x", "input": "q", "equals": "a"}, {"id": "x", "input": "r", "equals": "b"}], "distinct"),
        ],
    )
    def test_an_unusable_dataset_says_what_is_wrong(self, cases, message):
        with pytest.raises(ValueError, match=message):
            compare.parse_cases(cases)

    def test_scoring_is_deterministic_and_names_what_failed(self):
        [case] = compare.parse_cases(
            [{"input": "q", "contains": ["3.5%", "quarter"], "not_contains": ["guaranteed"], "matches": "[0-9]+%"}]
        )
        assert compare.score(case, "It earns 3.5% a year, credited every QUARTER.") == []
        assert compare.score(case, "It earns a guaranteed return.") == [
            "contains:3.5%",
            "contains:quarter",
            "not_contains:guaranteed",
            "matches",
        ]
        [exact] = compare.parse_cases([{"input": "q", "equals": "yes"}])
        assert compare.score(exact, " yes \n") == [] and compare.score(exact, "yes.") == ["equals"]


class TestEvaluate:
    CASES = [
        {"id": "rate", "input": "What does it earn?", "contains": ["3.5%"]},
        {"id": "minimum", "input": "What is the minimum balance?", "contains": ["5,000"]},
    ]

    def test_each_variant_gets_a_pass_rate_and_its_failed_cases_and_no_answer_is_returned(self):
        def _by_prompt(messages):
            brief = "brief" in messages[0]["content"]
            asked_rate = "earn" in messages[1]["content"]
            if asked_rate:
                return _answer("It earns 3.5% a year.")
            return _answer("5,000 rupees." if brief else "There is a minimum balance.")

        cases = compare.parse_cases(self.CASES)
        variants = [("current", PROMPT), ("brief", PROMPT + " Be brief.")]
        with _models({"gpt-4o-mini": _by_prompt}):
            report = asyncio.run(compare.evaluate(TENANT, variants=variants, cases=cases, model="gpt-4o-mini"))
        current, brief = report["variants"]
        assert (current["passed"], current["failed"], current["pass_rate"]) == (1, 1, 0.5)
        assert current["results"][1] == {"id": "minimum", "result": "failed", "failed_checks": ["contains:5,000"]}
        assert (brief["passed"], brief["pass_rate"]) == (2, 1.0) and brief["cost_usd"] == 0.0008
        assert "It earns" not in str(report) and "minimum balance." not in str(report)

    def test_a_failed_call_is_an_error_not_a_pass_or_a_failure(self):
        cases = compare.parse_cases(self.CASES)
        with _models({"gpt-4o-mini": TimeoutError()}):
            report = asyncio.run(compare.evaluate(TENANT, variants=[("v", PROMPT)], cases=cases, model="gpt-4o-mini"))
        [variant] = report["variants"]
        assert (variant["passed"], variant["failed"], variant["errors"], variant["pass_rate"]) == (0, 0, 2, 0.0)
        assert variant["results"][0] == {"id": "rate", "result": "error", "error_type": "TimeoutError"}

    def test_variants_are_bounded_and_distinct(self):
        cases = compare.parse_cases(self.CASES)
        for variants, message in (
            ([], "1 to 3 variants"),
            ([(f"v{i}", PROMPT) for i in range(4)], "1 to 3 variants"),
            ([("v", PROMPT), ("v", PROMPT)], "distinct"),
        ):
            with pytest.raises(ValueError, match=message):
                asyncio.run(compare.evaluate(TENANT, variants=variants, cases=cases, model="gpt-4o-mini"))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _client(template=None, scopes: list[str] | None = None):
    app = FastAPI(dependencies=[Depends(enforce_route_metadata)])

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        request.state.auth_mode = "api_key"
        request.state.claims = {"sub": "apikey:key_01"}
        request.state.scopes = scopes if scopes is not None else ["agenticorg:admin"]
        request.state.tenant_id = str(TENANT)
        return await call_next(request)

    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[get_current_tenant] = lambda: str(TENANT)
    app.dependency_overrides[get_current_user] = lambda: {"sub": "apikey:key_01"}
    app.dependency_overrides[get_user_domains] = lambda: None

    class _Session:
        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: template)

    @contextlib.asynccontextmanager
    async def _ctx(_tid):
        yield _Session()

    return TestClient(app), patch.object(api, "get_tenant_session", _ctx)


@pytest.fixture(autouse=True)
def _no_rate_limit_redis():
    with patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)):
        yield


BODY = {"template_text": "You answer for {{org}}.", "variables": [{"name": "org"}], "values": {"org": "Northwind"}}


class TestEndpoints:
    def test_off_by_default_nothing_is_called(self):
        assert settings.prompt_compare_enabled is False
        client, sessions = _client()
        with sessions, patch.object(compare, "_complete", AsyncMock(side_effect=AssertionError("no call"))):
            listed = client.get("/api/v1/prompt-templates/compare/models")
            compared = client.post("/api/v1/prompt-templates/compare", json={**BODY, "input": "q", "models": MODELS})
            evaluated = client.post(
                "/api/v1/prompt-templates/evaluate",
                json={
                    "variants": [{"name": "v", **BODY}],
                    "cases": [{"input": "q", "equals": "a"}],
                    "model": "gpt-4o-mini",
                },
            )
        assert listed.status_code == 200 and listed.json()["enabled"] is False
        assert listed.json()["limits"] == {"models": 4, "variants": 3, "cases": 25, "max_tokens": 2048}
        assert compared.status_code == 409 and evaluated.status_code == 409

    def test_admin_only_with_a_tight_rate_class(self):
        client, sessions = _client(scopes=["agents:write"])
        with sessions:
            assert client.get("/api/v1/prompt-templates/compare/models").status_code == 403
            assert client.post("/api/v1/prompt-templates/compare", json={}).status_code == 403
            assert client.post("/api/v1/prompt-templates/evaluate", json={}).status_code == 403
        assert RATE_LIMIT_CLASSES["prompt-compare"] == (6, 60)
        for handler in (api.compare_prompt, api.evaluate_prompt):
            assert handler.__enterprise_route_metadata__["rate_limit"] == "prompt-compare"

    def test_compare_renders_the_prompt_with_its_values(self, monkeypatch):
        monkeypatch.setattr(settings, "prompt_compare_enabled", True)
        seen: list = []

        def _record(messages):
            seen.append(messages[0]["content"])
            return _answer("ok")

        client, sessions = _client()
        with sessions, _models({"gpt-4o-mini": _record, "gemini-2.5-flash": _answer("ok")}):
            resp = client.post("/api/v1/prompt-templates/compare", json={**BODY, "input": "q", "models": MODELS})
            missing = client.post(
                "/api/v1/prompt-templates/compare", json={**BODY, "values": {}, "input": "q", "models": MODELS}
            )
            unknown = client.post(
                "/api/v1/prompt-templates/compare", json={**BODY, "input": "q", "models": ["made-up-model"]}
            )
            both = client.post(
                "/api/v1/prompt-templates/compare",
                json={**BODY, "template_id": str(uuid.uuid4()), "input": "q", "models": MODELS},
            )
        assert resp.status_code == 200 and len(resp.json()["results"]) == 2 and seen == ["You answer for Northwind."]
        assert missing.status_code == 422 and "org is required" in missing.text
        assert unknown.status_code == 422 and both.status_code == 422

    def test_a_stored_template_is_used_with_its_domain_check(self, monkeypatch):
        monkeypatch.setattr(settings, "prompt_compare_enabled", True)
        template = SimpleNamespace(
            id=uuid.uuid4(), domain="ops", template_text="You answer for {{org}}.", variables=[{"name": "org"}]
        )
        body = {
            "template_id": str(template.id),
            "values": {"org": "Northwind"},
            "input": "q",
            "models": ["gpt-4o-mini"],
        }
        client, sessions = _client(template)
        with sessions, _models({"gpt-4o-mini": _answer("ok")}):
            assert client.post("/api/v1/prompt-templates/compare", json=body).status_code == 200
        client, sessions = _client(None)
        with sessions:
            assert client.post("/api/v1/prompt-templates/compare", json=body).status_code == 404

    def test_evaluate_reports_pass_rates(self, monkeypatch):
        monkeypatch.setattr(settings, "prompt_compare_enabled", True)
        client, sessions = _client()
        body = {
            "variants": [{"name": "current", **BODY}],
            "cases": [{"id": "c1", "input": "q", "contains": ["ok"]}, {"id": "c2", "input": "r", "contains": ["no"]}],
            "model": "gpt-4o-mini",
        }
        with sessions, _models({"gpt-4o-mini": _answer("ok")}):
            resp = client.post("/api/v1/prompt-templates/evaluate", json=body)
            bad = client.post("/api/v1/prompt-templates/evaluate", json={**body, "cases": [{"input": "q"}]})
        assert resp.status_code == 200
        [variant] = resp.json()["variants"]
        assert (variant["passed"], variant["failed"], variant["pass_rate"]) == (1, 1, 0.5)
        assert bad.status_code == 422 and "needs at least one of" in bad.text
