# SPDX-License-Identifier: Apache-2.0
"""Typed prompt parameters: declarations, template checks, value resolution, rendering and the endpoints."""

from __future__ import annotations

import contextlib
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from api.deps import get_current_tenant, get_current_user, get_user_domains
from api.route_enforcement import enforce_route_metadata
from api.v1 import prompt_templates as api
from core.config import settings
from core.prompts import parameters as pp

TENANT = uuid.uuid4()
TEXT = "You are the {{role}} agent for {{ org_name }}. Reply in at most {{max_words}} words. Tone: {{tone}}."
SPECS = [
    {"name": "role", "description": "what the agent does"},
    {"name": "org_name", "type": "string", "max_length": 40},
    {"name": "max_words", "type": "integer", "min": 10, "max": 500, "default": 120},
    {"name": "tone", "type": "enum", "choices": ["formal", "plain"], "default": "plain"},
]


class TestDeclarations:
    def test_a_bare_name_is_a_required_string_as_before(self):
        [parameter] = pp.parse_parameters([{"name": "role", "description": "what the agent does"}])
        assert (parameter.type, parameter.required, parameter.default) == ("string", True, None)
        assert parameter.to_dict() == {
            "name": "role",
            "type": "string",
            "required": True,
            "description": "what the agent does",
        }

    def test_a_default_makes_a_parameter_optional_and_is_checked_against_its_type(self):
        parameters = {p.name: p for p in pp.parse_parameters(SPECS)}
        assert parameters["max_words"].required is False and parameters["max_words"].default == 120
        assert parameters["tone"].choices == ("formal", "plain") and parameters["tone"].default == "plain"
        assert pp.parse_parameters(None) == [] and pp.parse_parameters([]) == []

    @pytest.mark.parametrize(
        ("spec", "message"),
        [
            ("role", "must be an object"),
            ({"name": "1st"}, "needs a name"),
            ({"name": "a b"}, "needs a name"),
            ({"name": "x", "type": "date"}, "type must be one of"),
            ({"name": "x", "kind": "string"}, "unknown keys: kind"),
            ({"name": "x", "type": "enum"}, "an enum needs a list"),
            ({"name": "x", "type": "enum", "choices": ["a", ""]}, "every choice is non-empty text"),
            ({"name": "x", "choices": ["a"]}, "choices apply to an enum only"),
            ({"name": "x", "min": 1}, "min applies to a number only"),
            ({"name": "x", "type": "number", "min": 5, "max": 1}, "min cannot exceed max"),
            ({"name": "x", "type": "integer", "max_length": 5}, "max_length applies to a string only"),
            ({"name": "x", "max_length": 0}, "max_length is a whole number"),
            ({"name": "x", "pattern": "(a+)+"}, "nests or repeats"),
            ({"name": "x", "pattern": "("}, "does not compile"),
            ({"name": "x", "type": "integer", "default": "many"}, "the default must be a number"),
            ({"name": "x", "type": "integer", "min": 10, "default": 5}, "the default must be at least 10"),
            ({"name": "x", "type": "enum", "choices": ["a"], "default": "b"}, "the default must be one of a"),
            ({"name": "x", "required": True, "default": "v"}, "a required parameter has no default"),
            ({"name": "x", "required": "yes"}, "required must be true or false"),
        ],
    )
    def test_an_unusable_declaration_says_what_is_wrong(self, spec, message):
        with pytest.raises(pp.ParameterError, match=message):
            pp.parse_parameters([spec])

    def test_every_problem_is_reported_together(self):
        with pytest.raises(pp.ParameterError) as caught:
            pp.parse_parameters([{"name": "a"}, {"name": "a"}, {"name": "b", "type": "date"}])
        assert caught.value.problems == ["a: declared more than once", "b: type must be one of " + ", ".join(pp.TYPES)]
        with pytest.raises(pp.ParameterError, match="must be a list"):
            pp.parse_parameters({"name": "a"})
        with pytest.raises(pp.ParameterError, match="at most 50"):
            pp.parse_parameters([{"name": f"p{i}"} for i in range(51)])


class TestTemplateCheck:
    def test_placeholders_are_read_in_order_and_tool_references_are_not_parameters(self):
        text = "Use {{tool:search_policy}} and {{tools.lookup}} for {{ org_name }}; {{role}}; {{org_name}}."
        assert pp.placeholders(text) == ["org_name", "role"]
        assert pp.placeholders("") == [] and pp.placeholders("no placeholders") == []

    def test_undeclared_and_unused_are_reported(self):
        parameters = pp.parse_parameters([{"name": "role"}, {"name": "spare"}])
        check = pp.check_template(TEXT, parameters)
        assert check.ok is False
        assert check.undeclared == ["org_name", "max_words", "tone"] and check.unused == ["spare"]
        assert pp.check_template(TEXT, pp.parse_parameters(SPECS)).to_dict() == {
            "ok": True,
            "placeholders": ["role", "org_name", "max_words", "tone"],
            "undeclared": [],
            "unused": [],
        }


class TestResolveAndRender:
    def test_values_are_checked_and_defaults_filled(self):
        parameters = pp.parse_parameters(SPECS)
        resolved = pp.resolve(parameters, {"role": "claims", "org_name": "Northwind", "max_words": "80"})
        assert resolved == {"role": "claims", "org_name": "Northwind", "max_words": 80, "tone": "plain"}

    def test_every_problem_with_the_values_is_reported_together(self):
        parameters = pp.parse_parameters(SPECS)
        with pytest.raises(pp.ParameterError) as caught:
            pp.resolve(parameters, {"org_name": "x" * 41, "max_words": 5, "tone": "rude", "extra": 1})
        assert caught.value.problems == [
            "unknown parameters: extra",
            "role is required",
            "org_name must be at most 40 characters",
            "max_words must be at least 10",
            "tone must be one of formal, plain",
        ]

    @pytest.mark.parametrize(
        ("spec", "value", "expected"),
        [
            ({"name": "n", "type": "integer"}, 7, 7),
            ({"name": "n", "type": "integer"}, "7", 7),
            ({"name": "n", "type": "number"}, "2.5", 2.5),
            ({"name": "n", "type": "boolean"}, "Yes", True),
            ({"name": "n", "type": "boolean"}, False, False),
            ({"name": "n", "pattern": "[A-Z]{3}"}, "INR", "INR"),
        ],
    )
    def test_a_value_is_read_as_its_type(self, spec, value, expected):
        assert pp.resolve(pp.parse_parameters([spec]), {"n": value}) == {"n": expected}

    @pytest.mark.parametrize(
        ("spec", "value", "message"),
        [
            ({"name": "n", "type": "integer"}, 2.5, "must be a whole number"),
            ({"name": "n", "type": "integer"}, True, "must be a number"),
            ({"name": "n", "type": "number"}, "nan", "must be a finite number"),
            ({"name": "n", "type": "number"}, "inf", "must be a finite number"),
            ({"name": "n", "type": "boolean"}, "maybe", "must be true or false"),
            ({"name": "n"}, 5, "must be text"),
            ({"name": "n", "pattern": "[A-Z]{3}"}, "inr", "does not match the required pattern"),
        ],
    )
    def test_a_value_of_the_wrong_type_is_refused(self, spec, value, message):
        with pytest.raises(pp.ParameterError, match=message):
            pp.resolve(pp.parse_parameters([spec]), {"n": value})

    def test_an_optional_parameter_with_no_default_renders_empty(self):
        parameters = pp.parse_parameters([{"name": "note", "required": False}])
        assert pp.render("Note: {{note}}.", parameters, {}) == "Note: ."

    def test_render_leaves_no_placeholder_behind(self):
        parameters = pp.parse_parameters(SPECS)
        text = pp.render(TEXT, parameters, {"role": "claims", "org_name": "Northwind", "max_words": 80.0})
        assert text == "You are the claims agent for Northwind. Reply in at most 80 words. Tone: plain."
        assert "{{" not in text

    def test_render_refuses_an_undeclared_placeholder_and_a_missing_value(self):
        with pytest.raises(pp.ParameterError, match="undeclared parameters: org_name, max_words, tone"):
            pp.render(TEXT, pp.parse_parameters([{"name": "role"}]), {"role": "claims"})
        with pytest.raises(pp.ParameterError, match="role is required"):
            pp.render(TEXT, pp.parse_parameters(SPECS), {"org_name": "Northwind"})

    def test_a_value_is_inserted_as_text_and_never_read_as_a_placeholder(self):
        parameters = pp.parse_parameters([{"name": "a"}, {"name": "b"}])
        assert pp.render("{{a}} and {{b}}", parameters, {"a": "{{b}}", "b": "x"}) == "{{b}} and x"

    def test_a_tool_reference_survives_rendering(self):
        parameters = pp.parse_parameters([{"name": "org_name"}])
        text = pp.render("Use {{tool:search_policy}} for {{org_name}}.", parameters, {"org_name": "Northwind"})
        assert text == "Use {{tool:search_policy}} for Northwind."


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


class _Session:
    def __init__(self, template=None) -> None:
        self.template = template
        self.added: list = []

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self.template)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None


def _client(session: _Session, domains: list[str] | None = None) -> TestClient:
    app = FastAPI(dependencies=[Depends(enforce_route_metadata)])

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        request.state.auth_mode = "api_key"
        request.state.claims = {"sub": "apikey:key_01"}
        request.state.scopes = ["agenticorg:admin"]
        request.state.tenant_id = str(TENANT)
        return await call_next(request)

    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[get_current_tenant] = lambda: str(TENANT)
    app.dependency_overrides[get_current_user] = lambda: {"sub": str(uuid.uuid4())}
    app.dependency_overrides[get_user_domains] = lambda: domains

    @contextlib.asynccontextmanager
    async def _ctx(_tid):
        yield session

    return TestClient(app), patch.object(api, "get_tenant_session", _ctx)


@pytest.fixture(autouse=True)
def _no_rate_limit_redis():
    with patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)):
        yield


def _stored(**over) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "name": "claims agent",
        "agent_type": "claims_agent",
        "domain": "ops",
        "template_text": TEXT,
        "variables": SPECS,
        "description": "The claims agent prompt.",
        "is_builtin": False,
        "is_active": True,
        "created_at": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


CREATE = {
    "name": "claims agent",
    "agent_type": "claims_agent",
    "domain": "ops",
    "template_text": TEXT,
    "description": "The claims agent prompt.",
}


class TestCheckEndpoint:
    def test_a_template_is_checked_without_being_stored(self):
        session = _Session()
        client, sessions = _client(session)
        with sessions:
            good = client.post("/api/v1/prompt-templates/check", json={"template_text": TEXT, "variables": SPECS})
            partial = client.post(
                "/api/v1/prompt-templates/check", json={"template_text": TEXT, "variables": [{"name": "role"}]}
            )
            bad = client.post(
                "/api/v1/prompt-templates/check",
                json={"template_text": TEXT, "variables": [{"name": "role", "type": "date"}]},
            )
        assert good.status_code == 200 and good.json()["ok"] is True and good.json()["problems"] == []
        assert [p["name"] for p in good.json()["parameters"]] == ["role", "org_name", "max_words", "tone"]
        assert partial.json()["ok"] is False and partial.json()["undeclared"] == ["org_name", "max_words", "tone"]
        assert bad.json()["ok"] is False and "type must be one of" in bad.json()["problems"][0]
        assert session.added == []


class TestRenderEndpoint:
    def test_values_are_checked_and_the_text_rendered(self):
        template = _stored()
        client, sessions = _client(_Session(template))
        with sessions:
            ok = client.post(
                f"/api/v1/prompt-templates/{template.id}/render",
                json={"values": {"role": "claims", "org_name": "Northwind"}},
            )
            refused = client.post(f"/api/v1/prompt-templates/{template.id}/render", json={"values": {"tone": "rude"}})
        assert ok.status_code == 200
        assert ok.json()["text"] == "You are the claims agent for Northwind. Reply in at most 120 words. Tone: plain."
        assert refused.status_code == 422
        detail = refused.json()["detail"]
        assert detail["error"] == "invalid_values" and "role is required" in detail["problems"]

    def test_a_missing_template_or_another_domains_template_is_not_found(self):
        client, sessions = _client(_Session(None))
        with sessions:
            assert client.post(f"/api/v1/prompt-templates/{uuid.uuid4()}/render", json={}).status_code == 404
        template = _stored(domain="finance")
        client, sessions = _client(_Session(template), domains=["ops"])
        with sessions:
            assert client.post(f"/api/v1/prompt-templates/{template.id}/render", json={}).status_code == 404

    def test_a_template_stored_the_old_way_still_renders(self):
        template = _stored(template_text="You are the {{role}} agent.", variables=[{"name": "role"}])
        client, sessions = _client(_Session(template))
        with sessions:
            resp = client.post(f"/api/v1/prompt-templates/{template.id}/render", json={"values": {"role": "claims"}})
        assert resp.status_code == 200 and resp.json()["text"] == "You are the claims agent."


class TestWritesBehindTheFlag:
    def test_off_by_default_a_template_is_stored_as_it_was_given(self):
        assert settings.prompt_typed_parameters_enabled is False
        session = _Session()
        client, sessions = _client(session)
        with sessions:
            resp = client.post("/api/v1/prompt-templates", json={**CREATE, "variables": [{"name": "role"}]})
        assert resp.status_code == 201 and resp.json()["created"] is True
        assert session.added[0].variables == [{"name": "role"}]

    def test_on_a_create_stores_checked_parameters_and_refuses_bad_ones(self, monkeypatch):
        monkeypatch.setattr(settings, "prompt_typed_parameters_enabled", True)
        session = _Session()
        client, sessions = _client(session)
        with sessions:
            created = client.post("/api/v1/prompt-templates", json={**CREATE, "variables": SPECS})
            undeclared = client.post("/api/v1/prompt-templates", json={**CREATE, "variables": [{"name": "role"}]})
            invalid = client.post(
                "/api/v1/prompt-templates", json={**CREATE, "variables": [{"name": "role", "type": "date"}]}
            )
        assert created.status_code == 201
        stored = session.added[0].variables
        assert [p["name"] for p in stored] == ["role", "org_name", "max_words", "tone"]
        assert stored[0] == {"name": "role", "type": "string", "required": True, "description": "what the agent does"}
        assert stored[2]["default"] == 120 and stored[2]["required"] is False
        assert undeclared.status_code == 422
        assert undeclared.json()["detail"]["undeclared"] == ["org_name", "max_words", "tone"]
        assert invalid.status_code == 422 and invalid.json()["detail"]["error"] == "invalid_parameters"
        assert len(session.added) == 1

    def test_on_an_update_is_checked_as_the_template_it_leaves(self, monkeypatch):
        monkeypatch.setattr(settings, "prompt_typed_parameters_enabled", True)
        template = _stored()
        session = _Session(template)
        client, sessions = _client(session)
        with sessions:
            refused = client.put(
                f"/api/v1/prompt-templates/{template.id}",
                json={"template_text": TEXT + " Sign off as {{signature}}."},
            )
            accepted = client.put(
                f"/api/v1/prompt-templates/{template.id}",
                json={
                    "template_text": TEXT + " Sign off as {{signature}}.",
                    "variables": [*SPECS, {"name": "signature", "default": "the team"}],
                },
            )
        assert refused.status_code == 422 and refused.json()["detail"]["undeclared"] == ["signature"]
        assert accepted.status_code == 200
        assert template.variables[-1] == {
            "name": "signature",
            "type": "string",
            "required": False,
            "default": "the team",
        }
