"""Unit tests for TallyConnector health check + error hierarchy
(UR-Bug-4 / UR-Bug-6, Uday/Ramesh 2026-04-21).

Pins:
- ``health_check`` POSTs XML instead of the inherited base-class GET.
- The exception hierarchy is in place so API callers can distinguish
  connection / bridge / response / XML failures.
- ``_extract_tally_error`` finds Tally's inline error markers at
  varying depths in a parsed response.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from connectors.finance.tally import (
    TallyBridgeError,
    TallyConnectionError,
    TallyConnector,
    TallyError,
    TallyResponseError,
    TallyXMLError,
    _extract_tally_error,
)


class TestTallyExceptionHierarchy:
    def test_all_subclasses_inherit_from_tally_error(self) -> None:
        for cls in (
            TallyConnectionError,
            TallyBridgeError,
            TallyResponseError,
            TallyXMLError,
        ):
            assert issubclass(cls, TallyError), f"{cls.__name__} is not TallyError"

    def test_tally_error_carries_detail_and_request_id(self) -> None:
        exc = TallyResponseError(
            "Tally rejected it",
            detail="LINEERROR: Company not open",
            request_id="req-123",
        )
        assert str(exc) == "Tally rejected it"
        assert exc.detail == "LINEERROR: Company not open"
        assert exc.request_id == "req-123"


class TestExtractTallyError:
    def test_returns_line_error(self) -> None:
        assert (
            _extract_tally_error({"LINEERROR": "Company not open"})
            == "Company not open"
        )

    def test_returns_exceptions_nested(self) -> None:
        parsed = {"EXCEPTIONS": {"ERROR": "LEDGER missing"}}
        assert _extract_tally_error(parsed) == "LEDGER missing"

    def test_returns_status_desc(self) -> None:
        parsed = {"STATUS": {"DESC": "Invalid voucher"}}
        assert _extract_tally_error(parsed) == "Invalid voucher"

    def test_clean_response_returns_none(self) -> None:
        assert _extract_tally_error({"DATA": {"VOUCHERS": "ok"}}) is None

    def test_deeply_nested_error_is_found(self) -> None:
        parsed = {
            "IMPORTDATA": {
                "REQUESTDESC": {
                    "EXCEPTIONS": "no company selected",
                }
            }
        }
        assert _extract_tally_error(parsed) == "no company selected"


class TestTallyHealthCheckBridge:
    @pytest.mark.asyncio
    async def test_health_check_uses_xml_post_via_bridge_not_get(self) -> None:
        """UR-Bug-4: the base-class default GET against Tally returns
        405 every time because Tally only accepts POST. TallyConnector
        must override and send an XML POST instead."""
        connector = TallyConnector({
            "bridge_url": "http://bridge.test/api",
            "bridge_id": "bid-1",
            "bridge_token": "tok-1",
        })
        with patch(
            "connectors.finance.tally.TallyConnector._send_via_bridge",
            AsyncMock(return_value={"ENVELOPE": {}}),
        ):
            result = await connector.health_check()
        assert result["status"] == "healthy"
        assert result["transport"] == "bridge"

    @pytest.mark.asyncio
    async def test_health_check_reports_unreachable_when_bridge_is_down(self) -> None:
        connector = TallyConnector({
            "bridge_url": "http://unreachable.test/api",
            "bridge_id": "bid-1",
            "bridge_token": "tok-1",
        })
        with patch(
            "connectors.finance.tally.TallyConnector._send_via_bridge",
            AsyncMock(side_effect=TallyConnectionError(
                "ngrok offline", detail="ECONNREFUSED",
            )),
        ):
            result = await connector.health_check()
        assert result["status"] == "unreachable"
        assert "ngrok offline" in result["error"]

    @pytest.mark.asyncio
    async def test_health_check_reports_bridge_error_separately(self) -> None:
        connector = TallyConnector({
            "bridge_url": "http://bridge.test/api",
            "bridge_id": "bid-1",
            "bridge_token": "tok-1",
        })
        with patch(
            "connectors.finance.tally.TallyConnector._send_via_bridge",
            AsyncMock(side_effect=TallyBridgeError(
                "bad token", detail="HTTP 401",
            )),
        ):
            result = await connector.health_check()
        assert result["status"] == "bridge_error"

    @pytest.mark.asyncio
    async def test_health_check_never_raises(self) -> None:
        """Connector health must return a status dict no matter what —
        the dashboard's connector-health poller crashes otherwise."""
        connector = TallyConnector({
            "bridge_url": "http://bridge.test/api",
            "bridge_id": "bid-1",
            "bridge_token": "tok-1",
        })
        with patch(
            "connectors.finance.tally.TallyConnector._send_via_bridge",
            AsyncMock(side_effect=RuntimeError("anything can happen")),
        ):
            result = await connector.health_check()
        assert "status" in result  # just must not raise


class TestTallyHealthCheckDirect:
    """Health check without a bridge — hits Tally directly over XML."""

    @pytest.mark.asyncio
    async def test_direct_healthy_when_xml_parses(self) -> None:
        connector = TallyConnector({})
        connector._use_bridge = False
        # Fake _post_xml to return a non-None element.
        fake_elem = MagicMock()
        fake_elem.tag = "ENVELOPE"
        with patch.object(connector, "_post_xml", AsyncMock(return_value=fake_elem)):
            result = await connector.health_check()
        assert result["status"] == "healthy"
        assert result["transport"] == "direct"

    @pytest.mark.asyncio
    async def test_direct_unhealthy_when_empty_response(self) -> None:
        connector = TallyConnector({})
        connector._use_bridge = False
        with patch.object(connector, "_post_xml", AsyncMock(return_value=None)):
            result = await connector.health_check()
        assert result["status"] == "unhealthy"
        assert "empty XML" in result["error"]
