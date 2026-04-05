"""Tests for SOC2 / ISO 27001 compliance controls."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── helpers ──────────────────────────────────────────────────────────────────

_DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"


def _mock_session_context():
    """Create a mock async session context manager that returns count scalars."""
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar.return_value = 5
    session.execute = AsyncMock(return_value=result_mock)

    class _Ctx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            pass

    return _Ctx()


# ── tests ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_evidence_package_has_10_controls():
    """Evidence package endpoint returns all 10 SOC2 control sections."""
    with (
        patch("api.v1.compliance.get_tenant_session", return_value=_mock_session_context()),
    ):
        from api.v1.compliance import evidence_package

        # Build a mock request with tenant_id
        result = await evidence_package(tenant_id="00000000-0000-0000-0000-000000000001")

    sections = result["sections"]
    assert len(sections) >= 10, f"Expected 10 control sections, got {len(sections)}"


def test_access_log_fields_present():
    """SOC2 controls doc references access log evidence."""
    soc2_path = _DOCS_DIR / "soc2-controls.md"
    assert soc2_path.exists(), "soc2-controls.md missing"
    content = soc2_path.read_text(encoding="utf-8")
    assert "Access Control" in content
    assert "Audit Logging" in content
    assert "evidence" in content.lower()


def test_session_limit_config_exists():
    """SOC2 controls doc references session management limits."""
    soc2_path = _DOCS_DIR / "soc2-controls.md"
    content = soc2_path.read_text(encoding="utf-8")
    assert "Session Management" in content
    assert "5 per user" in content or "concurrent" in content.lower()


def test_password_policy_documented():
    """SOC2 controls doc references password policy requirements."""
    soc2_path = _DOCS_DIR / "soc2-controls.md"
    content = soc2_path.read_text(encoding="utf-8")
    assert "Password" in content or "password" in content
    assert "12" in content  # minimum character length
    assert "Bcrypt" in content or "bcrypt" in content
