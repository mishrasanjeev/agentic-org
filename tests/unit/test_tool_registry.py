# SPDX-License-Identifier: Apache-2.0
"""Tool registration: schemas checked at registration, inputs refused before a call leaves the gateway, the envelope."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.v1 import tool_registry as api
from core.config import settings
from core.tool_gateway import registry

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = {
    "type": "object",
    "properties": {"invoice_id": {"type": "string", "minLength": 3}, "amount": {"type": "number", "minimum": 0}},
    "required": ["invoice_id"],
    "additionalProperties": False,
}


def _row(**over):
    base = {
        "id": uuid.uuid4(),
        "name": "erp:get_invoice",
        "description": "",
        "input_schema": SCHEMA,
        "output_schema": None,
        "risk": "read",
        "timeout_seconds": 5,
        "max_output_bytes": 2_000,
        "untrusted_output": True,
        "enabled": True,
        "created_at": None,
        "updated_at": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


class TestRegistration:
    def test_a_registration_is_parsed_with_its_schema_checked_now(self):
        fields = registry.parse_fields({"name": "ERP:Get_Invoice", "input_schema": SCHEMA, "risk": "read"})
        assert fields["name"] == "erp:get_invoice" and fields["timeout_seconds"] == registry.DEFAULT_TIMEOUT_SECONDS
        assert fields["untrusted_output"] is True and fields["enabled"] is True and fields["input_schema"] == SCHEMA
        partial = registry.parse_fields({"enabled": False, "output_schema": None}, partial=True)
        assert partial == {"enabled": False, "output_schema": None}

    @pytest.mark.parametrize(
        ("raw", "code"),
        [
            ({"name": "bad name!", "input_schema": SCHEMA}, "name"),
            ({"name": "a:b:c:d", "input_schema": SCHEMA}, "name"),
            ({"name": "t", "input_schema": "not a schema"}, "input_schema"),
            (
                {"name": "t", "input_schema": {"type": "object", "properties": {"x": {"$ref": "#/defs/x"}}}},
                "input_schema",
            ),
            ({"name": "t", "input_schema": {"type": "not-a-type"}}, "input_schema"),
            ({"name": "t", "input_schema": SCHEMA, "risk": "lethal"}, "risk"),
            ({"name": "t", "input_schema": SCHEMA, "timeout_seconds": 0}, "timeout_seconds"),
            ({"name": "t", "input_schema": SCHEMA, "max_output_bytes": 10}, "max_output_bytes"),
            ({"name": "t", "input_schema": SCHEMA, "colour": "red"}, "unknown_field"),
        ],
    )
    def test_bad_registrations_are_refused(self, raw, code):
        with pytest.raises(registry.RegistryError) as refused:
            registry.parse_fields(raw)
        assert refused.value.code == code and refused.value.status == 422

    def test_a_schema_is_bounded(self):
        big = {
            "type": "object",
            "properties": {f"f{i}": {"type": "string", "description": "x" * 100} for i in range(400)},
        }
        with pytest.raises(registry.RegistryError):
            registry.check_schema(big, label="input_schema")

    def test_off_by_default(self):
        assert settings.tool_registry_enabled is False and registry.enabled() is False
        assert settings.tool_registry_require_registration is False and registry.registration_required() is False


class TestCheck:
    def test_inputs_are_checked_against_the_registration_by_connector_or_bare_name(self, monkeypatch):
        entries = {"erp:get_invoice": registry.registration_of(_row())}
        ok = registry.check_call(entries, "erp", "get_invoice", {"invoice_id": "INV-1", "amount": 10})
        assert ok.registration is not None and ok.errors == [] and ok.refused is False
        bad = registry.check_call(entries, "ERP", "get_invoice", {"invoice_id": "x", "amount": -1, "extra": 1})
        assert bad.refused and len(bad.errors) == 3 and any(e.startswith("invoice_id:") for e in bad.errors)
        missing = registry.check_call(entries, "erp", "get_invoice", None)
        assert missing.refused and "(root)" in missing.errors[0]
        unregistered = registry.check_call(entries, "erp", "delete_invoice", {})
        assert unregistered.registration is None and unregistered.refused is False
        monkeypatch.setattr(settings, "tool_registry_enabled", True)
        monkeypatch.setattr(settings, "tool_registry_require_registration", True)
        required = registry.check_call(entries, "erp", "delete_invoice", {})
        assert required.unregistered and required.refused
        bare = registry.check_call(
            {"search": registry.registration_of(_row(name="search"))}, None, "search", {"invoice_id": "abc"}
        )
        assert bare.registration is not None and bare.errors == []
        assert registry.tool_names("erp", "get_invoice") == ["erp:get_invoice", "get_invoice"]
        assert registry.tool_names(None, "composio:crm:get") == ["composio:crm:get"]

    def test_a_refusal_takes_the_gateway_error_shape(self):
        refused = registry.refusal(registry.Check(None, ["invoice_id: 'x' is too short"]), "get_invoice")
        assert refused["error"]["code"] == "E1012" and refused["error"]["message"].startswith("tool_input_invalid:")
        assert refused["error"]["tool_input_errors"] == ["invoice_id: 'x' is too short"]
        unregistered = registry.refusal(registry.Check(None, [], unregistered=True), "get_invoice")
        assert unregistered["error"]["message"].startswith("tool_unregistered:")

    def test_the_cache_is_per_tenant_and_invalidated(self):
        registry.invalidate()
        tid = uuid.uuid4()
        registry._CACHE[str(tid)] = registry._Cache(
            loaded_at=10**12, entries={"x": registry.registration_of(_row(name="x"))}
        )
        assert asyncio.run(registry.load(tid)) == registry._CACHE[str(tid)].entries
        registry.invalidate(tid)
        assert str(tid) not in registry._CACHE


class TestEnvelope:
    @pytest.mark.asyncio
    async def test_the_envelope_times_out_caps_output_checks_the_schema_and_marks_untrusted(self):
        registration = registry.registration_of(
            _row(timeout_seconds=1, max_output_bytes=1_000, output_schema={"type": "object", "required": ["total"]})
        )

        async def slow():
            await asyncio.sleep(5)
            return {}

        registration_fast = registry.Registration(
            name="erp:get_invoice",
            input_schema=SCHEMA,
            output_schema={"type": "object", "required": ["total"]},
            timeout_seconds=1,
            max_output_bytes=1_000,
        )
        timed = await registry.enveloped(registry.Registration("erp:get_invoice", SCHEMA, timeout_seconds=1), slow)
        assert timed["error"]["message"].startswith("tool_timeout:") and timed["error"]["code"] == "E1012"

        async def big():
            return {"total": 1, "blob": "x" * 5_000}

        too_large = await registry.enveloped(registration_fast, big)
        assert too_large["error"]["message"].startswith("tool_output_too_large:")

        async def wrong():
            return {"amount": 1}

        invalid = await registry.enveloped(registration_fast, wrong)
        assert invalid["error"]["message"].startswith("tool_output_invalid:") and invalid["error"]["tool_output_errors"]

        async def fine():
            return {"total": 42}

        good = await registry.enveloped(registration, fine)
        assert good == {"total": 42, "_untrusted": True}
        trusted = await registry.enveloped(registry.Registration("t", SCHEMA, untrusted_output=False), fine)
        assert trusted == {"total": 42}

        async def errored():
            return {"error": {"code": "E1001", "message": "boom"}}

        passed_through = await registry.enveloped(registration_fast, errored)
        assert passed_through == {"error": {"code": "E1001", "message": "boom"}}


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class _Session:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.added = []
        self.deleted = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, _statement):
        return _Result(self.answers.pop(0) if self.answers else [])

    def add(self, row):
        self.added.append(row)
        row.id = row.id or uuid.uuid4()

    async def delete(self, row):
        self.deleted.append(row)

    async def flush(self):
        return None


class TestEndpoints:
    @pytest.mark.asyncio
    async def test_off_the_endpoints_are_not_found(self):
        tid = str(uuid.uuid4())
        with pytest.raises(HTTPException) as refused:
            await api.list_registrations(tenant_id=tid)
        assert refused.value.status_code == 404 and refused.value.detail["error"] == "tool_registry_disabled"
        with pytest.raises(HTTPException) as refused:
            await api.check_inputs(api.CheckIn(params={}), name="erp:get_invoice", tenant_id=tid)
        assert refused.value.status_code == 404

    @pytest.mark.asyncio
    async def test_on_a_tool_is_registered_updated_checked_and_removed(self, monkeypatch):
        monkeypatch.setattr(settings, "tool_registry_enabled", True)
        tid = str(uuid.uuid4())
        user = {"agenticorg:user_id": str(uuid.uuid4())}
        session = _Session([])
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: session)
        created = await api.register_tool(
            api.RegistrationIn(name="ERP:get_invoice", input_schema=SCHEMA, risk="read"), tenant_id=tid, user=user
        )
        assert (
            created["name"] == "erp:get_invoice"
            and created["timeout_seconds"] == 30
            and session.added[0].created_by is not None
        )
        with pytest.raises(HTTPException) as refused:
            await api.register_tool(
                api.RegistrationIn(name="t", input_schema={"type": "nope"}), tenant_id=tid, user=user
            )
        assert refused.value.status_code == 422 and refused.value.detail["error"] == "input_schema"
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: _Session([_row()]))
        with pytest.raises(HTTPException) as refused:
            await api.register_tool(
                api.RegistrationIn(name="erp:get_invoice", input_schema=SCHEMA), tenant_id=tid, user=user
            )
        assert refused.value.status_code == 409
        row = _row()
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: _Session([row]))
        updated = await api.update_registration(
            "erp:get_invoice", api.RegistrationPatch(timeout_seconds=9, enabled=False), tenant_id=tid, user=user
        )
        assert updated["timeout_seconds"] == 9 and updated["enabled"] is False
        listing_session = _Session([row])
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: listing_session)
        listed = await api.list_registrations(tenant_id=tid)
        assert listed["total"] == 1 and listed["registration_required"] is False

        async def _load(_tid):
            return {"erp:get_invoice": registry.registration_of(_row())}

        monkeypatch.setattr(registry, "load", _load)
        checked = await api.check_inputs(api.CheckIn(params={"invoice_id": "x"}), name="erp:get_invoice", tenant_id=tid)
        assert checked["registered"] and checked["refused"] and checked["errors"][0].startswith("invoice_id:")
        delete_session = _Session([row])
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: delete_session)
        assert await api.delete_registration("erp:get_invoice", tenant_id=tid) is None and delete_session.deleted == [
            row
        ]

    def test_the_gateway_checks_inputs_before_dispatch_and_runs_the_envelope(self):
        src = (ROOT / "core" / "tool_gateway" / "gateway.py").read_text(encoding="utf-8")
        check = src.index("registry_check = tool_registry.check_call(registrations, connector_name, tool_name, params)")
        assert check < src.index('connector = self._connectors.get(("_global", None, connector_name))')
        assert check < src.index("await self._resolve_connector(tenant_id, company_id, connector_name)")
        assert "return tool_registry.refusal(registry_check, tool_name)" in src
        assert 'action="input_rejected"' in src
        assert "tool_registry.enveloped(" in src
        main = (ROOT / "api" / "main.py").read_text(encoding="utf-8")
        assert "app.include_router(tool_registry.router" in main
        migration = (ROOT / "migrations" / "versions" / "v6_z58_tool_registrations.py").read_text(encoding="utf-8")
        assert (
            'down_revision = "v6z57_agent_memories"' in migration and "ux_tool_registrations_tenant_name" in migration
        )
        from core.models.tool_registration import ToolRegistration

        assert ToolRegistration.__tablename__ == "tool_registrations"
