"""Unit tests for production CA firm components.

Covers: Tally bridge protocol, AA consent flow, bridge routing,
and connector integration.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from xml.etree.ElementTree import Element, SubElement

import pytest

# ---------------------------------------------------------------------------
# Tally Bridge Tests
# ---------------------------------------------------------------------------


class TestTallyBridge:
    """Test the Tally bridge agent."""

    def test_bridge_init(self):
        from bridge.tally_bridge import TallyBridge

        bridge = TallyBridge(
            cloud_url="wss://app.agenticorg.ai/api/v1/ws/bridge",
            bridge_id="test-bridge-123",
            bridge_token="test-token",
            tally_host="localhost",
            tally_port=9000,
        )
        assert bridge.bridge_id == "test-bridge-123"
        assert bridge.tally_url == "http://localhost:9000"
        assert not bridge.is_connected
        assert not bridge.tally_healthy

    @pytest.mark.asyncio
    async def test_forward_to_tally(self):
        """Verify XML is forwarded to Tally via HTTP POST."""
        from bridge.tally_bridge import TallyBridge

        bridge = TallyBridge(
            cloud_url="wss://test",
            bridge_id="b1",
            bridge_token="t1",
        )

        xml_body = "<ENVELOPE><HEADER/></ENVELOPE>"

        mock_resp = MagicMock()
        mock_resp.text = "<ENVELOPE><BODY><DATA><CREATED>1</CREATED></DATA></BODY></ENVELOPE>"
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", return_value=mock_resp) as mock_post:
            result = await bridge._forward_to_tally(xml_body)

            assert "<CREATED>1</CREATED>" in result
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            assert call_kwargs.kwargs["headers"]["Content-Type"] == "application/xml"

    @pytest.mark.asyncio
    async def test_health_check_returns_true_on_200(self):
        from bridge.tally_bridge import TallyBridge

        bridge = TallyBridge(
            cloud_url="wss://test",
            bridge_id="b1",
            bridge_token="t1",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", return_value=mock_resp):
            result = await bridge._tally_health_check()
            assert result is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_connect_error(self):
        import httpx

        from bridge.tally_bridge import TallyBridge

        bridge = TallyBridge(
            cloud_url="wss://test",
            bridge_id="b1",
            bridge_token="t1",
        )

        with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("refused")):
            result = await bridge._tally_health_check()
            assert result is False

    @pytest.mark.asyncio
    async def test_handle_xml_request(self):
        """Verify bridge processes XML request and sends response."""
        from bridge.tally_bridge import TallyBridge

        bridge = TallyBridge(
            cloud_url="wss://test",
            bridge_id="b1",
            bridge_token="t1",
        )

        # Mock WebSocket
        bridge._ws = AsyncMock()
        bridge._ws.send = AsyncMock()

        tally_resp = "<ENVELOPE><BODY><CREATED>1</CREATED></BODY></ENVELOPE>"
        with patch.object(bridge, "_forward_to_tally", return_value=tally_resp):
            msg = {
                "type": "post_xml",
                "request_id": "req-001",
                "xml_body": "<ENVELOPE/>",
            }
            await bridge._handle_xml_request(msg)

            # Check response was sent back
            bridge._ws.send.assert_called_once()
            sent = json.loads(bridge._ws.send.call_args[0][0])
            assert sent["request_id"] == "req-001"
            assert sent["status"] == "ok"
            assert "<CREATED>1</CREATED>" in sent["xml_response"]


# ---------------------------------------------------------------------------
# Tally Connector Bridge Routing Tests
# ---------------------------------------------------------------------------


class TestTallyConnectorBridgeRouting:
    """Test TallyConnector in bridge mode vs direct mode."""

    def test_direct_mode_by_default(self):
        from connectors.finance.tally import TallyConnector

        connector = TallyConnector()
        assert not connector._use_bridge
        assert connector.base_url == "http://localhost:9000"

    def test_bridge_mode_when_configured(self):
        from connectors.finance.tally import TallyConnector

        connector = TallyConnector(config={
            "bridge_url": "http://cloud/api/v1/bridge/route/tally",
            "bridge_id": "bridge-123",
            "bridge_token": "token-456",
        })
        assert connector._use_bridge

    @pytest.mark.asyncio
    async def test_send_xml_direct_mode(self):
        from connectors.finance.tally import TallyConnector

        connector = TallyConnector()

        elem = Element("ENVELOPE")
        SubElement(elem, "BODY")

        with patch.object(connector, "_post_xml", new_callable=AsyncMock, return_value=elem):
            result = await connector._send_xml("<ENVELOPE/>")
            assert "BODY" in result

    @pytest.mark.asyncio
    async def test_send_xml_bridge_mode(self):
        from connectors.finance.tally import TallyConnector

        connector = TallyConnector(config={
            "bridge_url": "http://cloud/api/v1/bridge/route/tally",
            "bridge_id": "bridge-123",
            "bridge_token": "token-456",
        })

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "ok",
            "xml_response": "<ENVELOPE><BODY><CREATED>1</CREATED></BODY></ENVELOPE>",
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", return_value=mock_resp):
            result = await connector._send_via_bridge("<ENVELOPE/>")
            assert result["BODY"]["CREATED"] == "1"

    @pytest.mark.asyncio
    async def test_bridge_error_raises(self):
        from connectors.finance.tally import TallyConnector

        connector = TallyConnector(config={
            "bridge_url": "http://cloud/api/v1/bridge/route/tally",
            "bridge_id": "bridge-123",
            "bridge_token": "token-456",
        })

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "error",
            "error": "Tally not reachable",
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", return_value=mock_resp):
            # Post-UR-Bug-6: TallyBridgeError (a RuntimeError subclass) —
            # the caller can still catch RuntimeError for backward compat.
            with pytest.raises(RuntimeError, match="Tally bridge error"):
                await connector._send_via_bridge("<ENVELOPE/>")


# ---------------------------------------------------------------------------
# AA Consent Flow Tests
# ---------------------------------------------------------------------------


class TestAAConsentFlow:
    """Test the Account Aggregator consent lifecycle."""

    @pytest.mark.asyncio
    async def test_create_consent_request(self):
        from connectors.finance.aa_consent import AAConsentManager
        from connectors.finance.aa_consent_types import (
            ConsentRequest,
            FIType,
            PurposeCode,
        )

        manager = AAConsentManager(
            base_url="https://aa.finvu.in/api/v1",
            client_id="test-id",
            client_secret="test-secret",
            callback_url="https://app.agenticorg.ai/api/v1/aa/consent/callback",
            fiu_id="test-fiu",
        )
        manager._token = "test-token"  # Skip auth

        request = ConsentRequest(
            customer_vua="user@finvu",
            fi_types=[FIType.DEPOSIT],
            purpose_code=PurposeCode.ACCOUNT_AGGREGATION,
            from_date="2026-01-01",
            to_date="2026-03-31",
        )

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ConsentHandle": "consent-handle-123"}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", return_value=mock_resp):
            result = await manager.create_consent_request(request)

            assert result["consent_handle"] == "consent-handle-123"
            assert "redirect_url" in result
            assert "finvu.in/consent" in result["redirect_url"]

    @pytest.mark.asyncio
    async def test_handle_consent_callback_approved(self):
        from connectors.finance.aa_consent import AAConsentManager
        from connectors.finance.aa_consent_types import ConsentStatus

        manager = AAConsentManager()
        manager._consents["handle-1"] = {
            "consent_handle": "handle-1",
            "status": ConsentStatus.PENDING,
            "consent_id": "",
            "customer_vua": "user@finvu",
        }

        result = await manager.handle_consent_callback(
            consent_handle="handle-1",
            consent_status=ConsentStatus.APPROVED,
            consent_id="consent-id-456",
        )

        assert result["status"] == "APPROVED"
        assert result["consent_id"] == "consent-id-456"
        assert manager._consents["handle-1"]["status"] == ConsentStatus.APPROVED

    @pytest.mark.asyncio
    async def test_handle_consent_callback_rejected(self):
        from connectors.finance.aa_consent import AAConsentManager
        from connectors.finance.aa_consent_types import ConsentStatus

        manager = AAConsentManager()
        manager._consents["handle-2"] = {
            "consent_handle": "handle-2",
            "status": ConsentStatus.PENDING,
            "consent_id": "",
        }

        result = await manager.handle_consent_callback(
            consent_handle="handle-2",
            consent_status=ConsentStatus.REJECTED,
        )
        assert result["status"] == "REJECTED"

    @pytest.mark.asyncio
    async def test_unknown_consent_handle(self):
        from connectors.finance.aa_consent import AAConsentManager
        from connectors.finance.aa_consent_types import ConsentStatus

        manager = AAConsentManager()
        result = await manager.handle_consent_callback(
            consent_handle="unknown",
            consent_status=ConsentStatus.APPROVED,
        )
        assert "error" in result

    def test_get_consent_status(self):
        from connectors.finance.aa_consent import AAConsentManager
        from connectors.finance.aa_consent_types import ConsentStatus

        manager = AAConsentManager()
        manager._consents["h1"] = {
            "consent_handle": "h1",
            "status": ConsentStatus.APPROVED,
            "consent_id": "c1",
        }

        status = manager.get_consent_status("h1")
        assert status["status"] == "APPROVED"
        assert status["consent_id"] == "c1"

        missing = manager.get_consent_status("nonexistent")
        assert "error" in missing


# ---------------------------------------------------------------------------
# Banking AA Connector with Consent Tests
# ---------------------------------------------------------------------------


class TestBankingAAConnectorConsent:
    """Test the consent-aware Banking AA connector."""

    def test_connector_without_consent_has_5_tools(self):
        from connectors.finance.banking_aa import BankingAaConnector

        connector = BankingAaConnector()
        tools = list(connector._tool_registry.keys())
        assert "request_consent" in tools
        assert "fetch_fi_data" in tools
        assert "fetch_bank_statement" in tools
        assert len(tools) == 5

    def test_no_consent_manager_without_callback_url(self):
        from connectors.finance.banking_aa import BankingAaConnector

        connector = BankingAaConnector()
        assert connector._consent_manager is None

    def test_consent_manager_with_callback_url(self):
        from connectors.finance.banking_aa import BankingAaConnector

        connector = BankingAaConnector(config={
            "callback_url": "https://app.agenticorg.ai/api/v1/aa/consent/callback",
            "client_id": "test",
            "client_secret": "test",
        })
        assert connector._consent_manager is not None

    @pytest.mark.asyncio
    async def test_request_consent_without_config_returns_error(self):
        from connectors.finance.banking_aa import BankingAaConnector

        connector = BankingAaConnector()
        result = await connector.request_consent(
            customer_vua="user@finvu",
            fi_types=["DEPOSIT"],
            purpose_code=103,
            from_date="2026-01-01",
            to_date="2026-03-31",
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_fetch_bank_statement_direct_mode(self):
        """Without consent_id, falls back to direct API call."""
        from connectors.finance.banking_aa import BankingAaConnector

        connector = BankingAaConnector()
        mock_resp = {"transactions": [{"amount": 100}]}

        with (
            patch.object(connector, "_authenticate", new_callable=AsyncMock),
            patch.object(connector, "_post", new_callable=AsyncMock, return_value=mock_resp),
        ):
            await connector.connect()
            result = await connector.fetch_bank_statement(account_id="ACC-1")
            assert result["transactions"][0]["amount"] == 100


# ---------------------------------------------------------------------------
# AA Consent Types Tests
# ---------------------------------------------------------------------------


class TestAAConsentTypes:
    """Test the Pydantic models for AA consent."""

    def test_consent_request_defaults(self):
        from connectors.finance.aa_consent_types import (
            ConsentRequest,
            FIType,
            PurposeCode,
        )

        req = ConsentRequest(
            customer_vua="user@finvu",
            fi_types=[FIType.DEPOSIT, FIType.MUTUAL_FUNDS],
            purpose_code=PurposeCode.ACCOUNT_AGGREGATION,
            from_date="2026-01-01",
            to_date="2026-03-31",
        )
        assert req.fetch_type == "ONETIME"
        assert req.consent_mode == "VIEW"
        assert req.data_life_unit == "MONTH"

    def test_consent_status_enum(self):
        from connectors.finance.aa_consent_types import ConsentStatus

        assert ConsentStatus.PENDING.value == "PENDING"
        assert ConsentStatus.APPROVED.value == "APPROVED"
        assert ConsentStatus("REJECTED") == ConsentStatus.REJECTED

    def test_fi_type_enum(self):
        from connectors.finance.aa_consent_types import FIType

        assert FIType.DEPOSIT.value == "DEPOSIT"
        assert FIType.MUTUAL_FUNDS.value == "MUTUAL_FUNDS"
        assert len(FIType) >= 10

    def test_purpose_code_enum(self):
        from connectors.finance.aa_consent_types import PurposeCode

        assert PurposeCode.ACCOUNT_AGGREGATION.value == 103
        assert PurposeCode.LENDING.value == 104


# ---------------------------------------------------------------------------
# Bridge Server Handler Tests
# ---------------------------------------------------------------------------


class TestBridgeServerHandler:
    """Test the cloud-side bridge connection manager."""

    def test_get_status_disconnected(self):
        from bridge.server_handler import get_bridge_status

        status = get_bridge_status("nonexistent-bridge")
        assert status["connected"] is False

    def test_list_active_bridges_empty(self):
        from bridge.server_handler import list_active_bridges

        bridges = list_active_bridges()
        assert isinstance(bridges, list)
