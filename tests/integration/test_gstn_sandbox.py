"""Integration tests for GSTN connector + DSC signing + Adaequare sandbox.

Tests are fully mocked for CI but structured so they can run against
the real Adaequare sandbox by setting env vars:
    GSTN_SANDBOX_USER, GSTN_SANDBOX_PASS, GSTN_SANDBOX_ASPID
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def self_signed_pfx():
    """Generate a self-signed PFX certificate for testing DSC signing."""
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    # Generate RSA key
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Self-signed certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Karnataka"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "TestCorp Pvt Ltd"),
        x509.NameAttribute(NameOID.COMMON_NAME, "Test DSC"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )

    # Export as PFX
    pfx_data = pkcs12.serialize_key_and_certificates(
        name=b"test-dsc",
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(b"test1234"),
    )

    with tempfile.NamedTemporaryFile(suffix=".pfx", delete=False) as f:
        f.write(pfx_data)
        pfx_path = f.name

    yield pfx_path

    os.unlink(pfx_path)


@pytest.fixture
def sandbox_config():
    return {
        "username": os.getenv("GSTN_SANDBOX_USER", "sandbox-user"),
        "password": os.getenv("GSTN_SANDBOX_PASS", "sandbox-pass"),
        "api_key": os.getenv("GSTN_SANDBOX_ASPID", "sandbox-aspid"),
        "gstin": "29AADCB2230M1ZT",
    }


# ---------------------------------------------------------------------------
# DSC Signing Tests
# ---------------------------------------------------------------------------


class TestDSCSigning:
    """Test the DSCAdapter with real cryptographic operations."""

    @pytest.mark.asyncio
    async def test_sign_request_produces_valid_signature(self, self_signed_pfx):
        """Verify DSC signing produces a base64-encoded RSA-SHA256 signature."""
        from connectors.framework.auth_adapters import DSCAdapter

        adapter = DSCAdapter(dsc_path=self_signed_pfx, dsc_password="test1234")
        data = b'{"gstin":"29AADCB2230M1ZT","return_period":"032026"}'

        signature = await adapter.sign_request(data)

        # Signature should be base64 encoded
        import base64
        raw_sig = base64.b64decode(signature)
        assert len(raw_sig) == 256  # RSA-2048 produces 256-byte signature

    @pytest.mark.asyncio
    async def test_sign_and_get_headers(self, self_signed_pfx):
        """Verify sign_and_get_headers returns proper headers."""
        from connectors.framework.auth_adapters import DSCAdapter

        adapter = DSCAdapter(dsc_path=self_signed_pfx, dsc_password="test1234")
        data = b"test payload"

        headers = await adapter.sign_and_get_headers(data)

        assert headers["X-DSC-Signed"] == "true"
        assert "X-DSC-Signature" in headers
        assert len(headers["X-DSC-Signature"]) > 100  # Base64 RSA sig

    def test_verify_certificate_details(self, self_signed_pfx):
        """Verify certificate inspection returns correct details."""
        from connectors.framework.auth_adapters import DSCAdapter

        adapter = DSCAdapter(dsc_path=self_signed_pfx, dsc_password="test1234")
        info = adapter.verify_certificate()

        assert info["subject"]["commonName"] == "Test DSC"
        assert info["subject"]["countryName"] == "IN"
        assert info["issuer"]["organizationName"] == "TestCorp Pvt Ltd"
        assert info["is_expired"] is False
        assert info["days_until_expiry"] > 360

    def test_wrong_password_raises(self, self_signed_pfx):
        """Verify wrong password produces a clear error."""
        from connectors.framework.auth_adapters import DSCAdapter

        adapter = DSCAdapter(dsc_path=self_signed_pfx, dsc_password="wrong")
        with pytest.raises(ValueError, match="wrong password"):
            adapter.verify_certificate()

    def test_missing_file_raises(self):
        """Verify missing PFX file produces a clear error."""
        from connectors.framework.auth_adapters import DSCAdapter

        adapter = DSCAdapter(dsc_path="/nonexistent/cert.pfx")
        with pytest.raises(FileNotFoundError, match="not found"):
            adapter.verify_certificate()


# ---------------------------------------------------------------------------
# GSTN Connector Auth Flow Tests
# ---------------------------------------------------------------------------


class TestGstnAuthFlow:
    """Test the Adaequare GSP authentication flow."""

    @pytest.mark.asyncio
    async def test_authenticate_gets_session_token(self, sandbox_config):
        """Verify 2-step auth flow: POST to /authenticate → get auth-token."""
        from unittest.mock import MagicMock

        from connectors.finance.gstn import GstnConnector

        connector = GstnConnector(config=sandbox_config)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"auth-token": "test-session-token-123"}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", return_value=mock_resp) as mock_post:
            await connector._authenticate()

            # Verify it posted to /authenticate
            call_args = mock_post.call_args
            assert "/authenticate" in str(call_args)

            # Verify auth headers are set
            assert connector._auth_headers["auth-token"] == "test-session-token-123"
            assert connector._auth_headers["aspid"] == "sandbox-aspid"
            assert connector._auth_headers["gstin"] == "29AADCB2230M1ZT"

    @pytest.mark.asyncio
    async def test_base_url_is_correct(self, sandbox_config):
        """Verify base URL does not include /authenticate."""
        from connectors.finance.gstn import GstnConnector

        connector = GstnConnector(config=sandbox_config)
        assert connector.base_url == "https://gsp.adaequare.com/gsp"
        assert "/authenticate" not in connector.base_url


# ---------------------------------------------------------------------------
# GSTN + DSC Filing Tests
# ---------------------------------------------------------------------------


class TestGstnFilingWithDSC:
    """Test that filing operations use DSC signing."""

    @pytest.mark.asyncio
    async def test_file_gstr3b_signs_with_dsc(self, self_signed_pfx, sandbox_config):
        """Verify GSTR-3B filing signs the payload with DSC."""
        sandbox_config["dsc_path"] = self_signed_pfx
        sandbox_config["dsc_password"] = "test1234"

        from connectors.finance.gstn import GstnConnector

        connector = GstnConnector(config=sandbox_config)

        # Mock the HTTP client — json() and raise_for_status() are sync calls
        from unittest.mock import MagicMock

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status_cd": "1", "message": "Filed"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        connector._client = mock_client

        result = await connector.file_gstr3b(
            gstin="29AADCB2230M1ZT",
            return_period="032026",
        )

        assert result["status_cd"] == "1"

        # Verify DSC headers were included
        call_kwargs = mock_client.post.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers["X-DSC-Signed"] == "true"
        assert "X-DSC-Signature" in headers

    @pytest.mark.asyncio
    async def test_file_gstr3b_without_dsc_falls_back(self, sandbox_config):
        """Verify filing works without DSC (sandbox/testing mode)."""
        from connectors.finance.gstn import GstnConnector

        connector = GstnConnector(config=sandbox_config)
        assert connector._dsc_adapter is None  # No DSC configured

        mock_resp = {"status_cd": "1", "message": "Filed (no DSC)"}
        with (
            patch.object(connector, "_authenticate", new_callable=AsyncMock),
            patch.object(connector, "_post", new_callable=AsyncMock, return_value=mock_resp),
        ):
            await connector.connect()
            result = await connector.file_gstr3b(
                gstin="29AADCB2230M1ZT",
                return_period="032026",
            )
            assert result["status_cd"] == "1"

    @pytest.mark.asyncio
    async def test_get_dsc_info(self, self_signed_pfx, sandbox_config):
        """Verify get_dsc_info returns certificate details."""
        sandbox_config["dsc_path"] = self_signed_pfx
        sandbox_config["dsc_password"] = "test1234"

        from connectors.finance.gstn import GstnConnector

        connector = GstnConnector(config=sandbox_config)
        info = connector.get_dsc_info()

        assert info["subject"]["commonName"] == "Test DSC"
        assert info["is_expired"] is False


# ---------------------------------------------------------------------------
# Sandbox Connector Tests
# ---------------------------------------------------------------------------


class TestGstnSandbox:
    """Test the sandbox connector and test data."""

    def test_sandbox_base_url(self):
        """Verify sandbox uses the test enriched GSP URL."""
        from connectors.finance.gstn_sandbox import GstnSandboxConnector

        connector = GstnSandboxConnector()
        assert "test/enriched" in connector.base_url

    def test_sandbox_default_gstin(self):
        """Verify sandbox uses a valid test GSTIN."""
        from connectors.finance.gstn_sandbox import (
            DEFAULT_SANDBOX_GSTIN,
            SANDBOX_GSTINS,
        )

        assert DEFAULT_SANDBOX_GSTIN in SANDBOX_GSTINS
        assert len(DEFAULT_SANDBOX_GSTIN) == 15  # Valid GSTIN length

    def test_sample_gstr1_data_structure(self):
        """Verify sample GSTR-1 test data has correct structure."""
        from connectors.finance.gstn_sandbox import SAMPLE_GSTR1_B2B

        assert "gstin" in SAMPLE_GSTR1_B2B
        assert "return_period" in SAMPLE_GSTR1_B2B
        assert "b2b" in SAMPLE_GSTR1_B2B
        assert len(SAMPLE_GSTR1_B2B["b2b"]) > 0
        assert "ctin" in SAMPLE_GSTR1_B2B["b2b"][0]
        assert "inv" in SAMPLE_GSTR1_B2B["b2b"][0]

    @pytest.mark.asyncio
    async def test_sandbox_push_gstr1(self, sandbox_config):
        """Test pushing GSTR-1 data to sandbox."""
        from connectors.finance.gstn_sandbox import (
            SAMPLE_GSTR1_B2B,
            GstnSandboxConnector,
        )

        connector = GstnSandboxConnector(config=sandbox_config)

        mock_resp = {
            "status_cd": "1",
            "reference_id": "SANDBOX-REF-001",
        }
        with (
            patch.object(connector, "_authenticate", new_callable=AsyncMock),
            patch.object(connector, "_post", new_callable=AsyncMock, return_value=mock_resp),
        ):
            await connector.connect()
            result = await connector.push_gstr1_data(**SAMPLE_GSTR1_B2B)
            assert result["status_cd"] == "1"

    @pytest.mark.asyncio
    async def test_sandbox_check_filing_status(self, sandbox_config):
        """Test checking filing status on sandbox."""
        from connectors.finance.gstn_sandbox import GstnSandboxConnector

        connector = GstnSandboxConnector(config=sandbox_config)

        mock_resp = {"status_cd": "1", "filing_status": "Filed"}
        with (
            patch.object(connector, "_authenticate", new_callable=AsyncMock),
            patch.object(connector, "_get", new_callable=AsyncMock, return_value=mock_resp),
        ):
            await connector.connect()
            result = await connector.check_filing_status(
                gstin="29AADCB2230M1ZT",
                return_period="032026",
            )
            assert result["filing_status"] == "Filed"
