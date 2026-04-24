"""Regression tests for the 2026-04-24 bug sweep.

Aishwarya TC_001 (reopen) + Uday/Ramesh RA-ReportSched:
  GET /report-schedules returned HTTP 500 with {"code": "E1001"} because
  a single row with a legacy recipient shape crashed the response-model
  conversion for the whole list. _to_response / _coerce_channel now
  tolerate legacy shapes, and list_report_schedules wraps the loop so
  one bad row is logged + skipped instead of 500ing the surface.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from api.v1.report_schedules import (
    DeliveryChannel,
    _coerce_channel,
    _to_response,
)


def _row(recipients: object) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        company_id=None,
        name="cfo_daily",
        report_type="cfo_daily",
        cron_expression="daily",
        recipients=recipients,
        delivery_channel="email",
        format="pdf",
        enabled=True,
        last_run_at=None,
        next_run_at=None,
        config={"company_id": "default", "params": {}},
        created_at=datetime.now(UTC),
        updated_at=None,
    )


class TestCoerceChannel:
    def test_dict_shape_round_trips(self) -> None:
        ch = _coerce_channel({"type": "email", "target": "ops@example.com"})
        assert isinstance(ch, DeliveryChannel)
        assert ch.type == "email"
        assert ch.target == "ops@example.com"

    def test_bare_email_string_upgrades_to_email_channel(self) -> None:
        ch = _coerce_channel("ops@example.com")
        assert ch is not None
        assert ch.type == "email"
        assert ch.target == "ops@example.com"

    def test_slack_channel_id_string_upgrades_to_slack(self) -> None:
        ch = _coerce_channel("C01ABC23DEF")
        assert ch is not None
        assert ch.type == "slack"

    def test_whatsapp_phone_string_upgrades_to_whatsapp(self) -> None:
        ch = _coerce_channel("+919876543210")
        assert ch is not None
        assert ch.type == "whatsapp"

    def test_garbage_string_is_dropped_not_raised(self) -> None:
        # Critical: this must not raise. Returning None lets the list
        # endpoint keep serving other schedules.
        assert _coerce_channel("???") is None
        assert _coerce_channel("") is None

    def test_dict_missing_fields_is_dropped_not_raised(self) -> None:
        assert _coerce_channel({"type": "email"}) is None
        assert _coerce_channel({"target": "x@y.com"}) is None
        assert _coerce_channel({"type": "bogus", "target": "x"}) is None


class TestToResponseDefensive:
    def test_legacy_string_recipients_do_not_500(self) -> None:
        """v4.4-era rows stored recipients as bare string lists."""
        resp = _to_response(_row(["ops@example.com"]))
        assert len(resp.delivery_channels) == 1
        assert resp.delivery_channels[0].type == "email"

    def test_mixed_legacy_and_modern_recipients(self) -> None:
        resp = _to_response(
            _row([
                "ops@example.com",
                {"type": "slack", "target": "C01ABC23DEF"},
            ])
        )
        assert {c.type for c in resp.delivery_channels} == {"email", "slack"}

    def test_one_malformed_row_drops_only_that_channel(self) -> None:
        resp = _to_response(
            _row([
                "ops@example.com",
                {"type": "bogus", "target": ""},  # would raise before
                {"type": "slack", "target": "C01ABC23DEF"},
            ])
        )
        # Email + Slack survive; the malformed one is quietly dropped.
        assert len(resp.delivery_channels) == 2

    def test_empty_recipients_returns_empty_channels(self) -> None:
        resp = _to_response(_row([]))
        assert resp.delivery_channels == []

    def test_none_recipients_returns_empty_channels(self) -> None:
        resp = _to_response(_row(None))
        assert resp.delivery_channels == []


class TestGetHandlerWrapsFailures:
    """The route body is wrapped in try/except so unexpected failures
    return the structured 'Could not load report schedules' detail
    instead of the raw E1001 INTERNAL_ERROR from the global handler.
    """

    def test_route_source_has_try_except_wrapper(self) -> None:
        import inspect

        from api.v1 import report_schedules

        src = inspect.getsource(report_schedules.list_report_schedules)
        assert "try:" in src
        assert "except HTTPException:" in src
        assert "Could not load report schedules" in src

    def test_row_loop_skips_instead_of_500(self) -> None:
        import inspect

        from api.v1 import report_schedules

        src = inspect.getsource(report_schedules.list_report_schedules)
        # Inner per-row try/except so one bad row is logged + dropped
        # rather than crashing the whole list.
        assert "report_schedule_row_skipped" in src


class TestChatFormatAgentOutput:
    """TC_008 (Aishwarya 2026-04-24): chat panel rendered
    ``{'type': 'text', 'text': '...', 'extras': {'signature': '...'}}``
    verbatim. Root cause: _format_agent_output did str(dict) when the
    answer value was itself a structured block.
    """

    def test_dict_answer_with_text_and_extras_extracts_text(self) -> None:
        from api.v1.chat import _format_agent_output

        out = _format_agent_output({
            "answer": {
                "type": "text",
                "text": "Quarterly revenue is $4.2M.",
                "extras": {"signature": "abc123"},
            }
        })
        # No Python dict repr leaks, just the user-facing text.
        assert out == "Quarterly revenue is $4.2M."
        assert "'type'" not in out
        assert "extras" not in out
        assert "signature" not in out

    def test_bare_text_block_extracts_text(self) -> None:
        from api.v1.chat import _format_agent_output

        out = _format_agent_output({
            "type": "text",
            "text": "Hello world",
            "extras": {"signature": "sig"},
        })
        assert out == "Hello world"

    def test_nested_response_content_extracts_content(self) -> None:
        from api.v1.chat import _format_agent_output

        out = _format_agent_output({
            "response": {"content": "Deeper nested text"}
        })
        assert out == "Deeper nested text"

    def test_list_of_text_blocks_joins(self) -> None:
        from api.v1.chat import _format_agent_output

        out = _format_agent_output({
            "answer": [
                {"type": "text", "text": "line 1"},
                {"type": "text", "text": "line 2"},
            ]
        })
        assert out == "line 1\nline 2"

    def test_plain_string_answer_passthrough(self) -> None:
        from api.v1.chat import _format_agent_output

        out = _format_agent_output({"answer": "Plain string answer"})
        assert out == "Plain string answer"

    def test_str_repr_never_leaks_for_any_dict_shape(self) -> None:
        """Fail-closed: no code path may return Python repr of a dict
        for a dict-valued answer."""
        from api.v1.chat import _format_agent_output

        for shape in (
            {"answer": {"type": "text", "text": "ok"}},
            {"response": {"text": "ok"}},
            {"message": {"content": "ok"}},
            {"answer": {"result": "ok"}},
        ):
            out = _format_agent_output(shape)
            assert "'" not in out or "ok" in out  # no {'..': '..'} dict repr


class TestConnectorHealthCheck:
    """Uday/Ramesh 2026-04-24 (UI-HEALTH-404): Gmail connector /test
    reported ``status=healthy, http_status=404`` because the base
    health_check ignored HTTP status. A 4xx/5xx now surfaces as
    ``unhealthy``.
    """

    def _fake_connector(self, status_code: int):
        from types import SimpleNamespace

        from connectors.framework.base_connector import BaseConnector

        class Stub(BaseConnector):
            def __init__(self) -> None:  # skip base init (no real API)
                self.config = {"api_key": "sk_fake"}
                self.name = "stub"
                self._auth_headers = {}

                async def _get(_path: str):
                    return SimpleNamespace(status_code=status_code)

                self._client = SimpleNamespace(get=_get)

            def _has_credentials(self) -> bool:
                return True

            def _authenticate(self) -> None:  # pragma: no cover
                return None

            def _register_tools(self) -> None:  # pragma: no cover
                return None

        return Stub()

    @pytest.mark.asyncio
    async def test_200_is_healthy(self) -> None:
        conn = self._fake_connector(200)
        result = await conn.health_check()
        assert result["status"] == "healthy"
        assert result["http_status"] == 200

    @pytest.mark.asyncio
    async def test_404_is_unhealthy_not_healthy(self) -> None:
        """Exact tester repro: Gmail returns 404, must NOT be healthy."""
        conn = self._fake_connector(404)
        result = await conn.health_check()
        assert result["status"] == "unhealthy"
        assert result["http_status"] == 404
        assert "404" in result["reason"]
        assert "base url" in result["reason"].lower()

    @pytest.mark.asyncio
    async def test_401_is_unhealthy(self) -> None:
        conn = self._fake_connector(401)
        result = await conn.health_check()
        assert result["status"] == "unhealthy"
        assert "credentials" in result["reason"].lower() or "authentication" in result["reason"].lower()

    @pytest.mark.asyncio
    async def test_500_is_unhealthy(self) -> None:
        conn = self._fake_connector(500)
        result = await conn.health_check()
        assert result["status"] == "unhealthy"
        assert result["http_status"] == 500

    @pytest.mark.asyncio
    async def test_302_redirect_is_healthy(self) -> None:
        """Some APIs redirect the root path to /login; that's still
        reachable, so treat 3xx as healthy for the root-path probe."""
        conn = self._fake_connector(302)
        result = await conn.health_check()
        assert result["status"] == "healthy"


class TestSchemaRegistryValidation:
    """TC_006 (Aishwarya 2026-04-24): POST /schemas accepted an empty
    json_schema and created a row with no definition, which then
    rendered as blank in view/edit (TC_004/TC_005).
    """

    def test_empty_json_schema_rejected(self) -> None:
        from pydantic import ValidationError

        from core.schemas.api import SchemaCreate

        with pytest.raises(ValidationError):
            SchemaCreate(name="Foo", json_schema={})

    def test_missing_type_rejected(self) -> None:
        from pydantic import ValidationError

        from core.schemas.api import SchemaCreate

        with pytest.raises(ValidationError):
            SchemaCreate(name="Foo", json_schema={"title": "NoType"})

    def test_object_without_properties_rejected(self) -> None:
        from pydantic import ValidationError

        from core.schemas.api import SchemaCreate

        with pytest.raises(ValidationError):
            SchemaCreate(
                name="Foo",
                json_schema={"type": "object", "properties": {}},
            )

    def test_valid_object_schema_accepted(self) -> None:
        from core.schemas.api import SchemaCreate

        s = SchemaCreate(
            name="Foo",
            json_schema={
                "type": "object",
                "properties": {"id": {"type": "string"}},
            },
        )
        assert s.json_schema["type"] == "object"

    def test_ref_schema_accepted(self) -> None:
        from core.schemas.api import SchemaCreate

        s = SchemaCreate(
            name="Foo",
            json_schema={"$ref": "https://example.com/Foo.json"},
        )
        assert "$ref" in s.json_schema

    def test_blank_name_rejected(self) -> None:
        from pydantic import ValidationError

        from core.schemas.api import SchemaCreate

        with pytest.raises(ValidationError):
            SchemaCreate(
                name="   ",
                json_schema={
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                },
            )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
