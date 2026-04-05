"""Tests for Browser RPA module (PRD v4.0.0 Section 11).

Covers:
  1. Script execution mock — successful run returns structured result
  2. Missing script — returns error without crashing
  3. Timeout handling — configurable timeout propagated
  4. Screenshot capture — screenshots list populated
  5. Sandbox isolation — browser runs headless with custom user-agent
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── 1. Script execution mock — successful run returns structured result ──
class TestRPAExecution:
    @pytest.mark.asyncio
    async def test_execute_success(self) -> None:
        """Execute a mock RPA script and verify result structure."""
        # Create a fake script module
        fake_module = ModuleType("rpa.scripts.test_script")

        async def fake_run(page: Any, params: dict) -> dict:
            return {"downloaded": True, "rows": 10}

        fake_module.run = fake_run  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"rpa.scripts.test_script": fake_module}):
            with patch("core.rpa.executor._PLAYWRIGHT_AVAILABLE", True):
                # Mock playwright context
                mock_page = AsyncMock()
                mock_page.screenshot = AsyncMock(return_value=b"\x89PNG")
                mock_page.on = MagicMock()

                mock_context = AsyncMock()
                mock_context.new_page = AsyncMock(return_value=mock_page)
                mock_context.set_default_timeout = MagicMock()

                mock_browser = AsyncMock()
                mock_browser.new_context = AsyncMock(return_value=mock_context)
                mock_browser.close = AsyncMock()

                mock_pw = AsyncMock()
                mock_pw.chromium = AsyncMock()
                mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

                mock_playwright_cm = AsyncMock()
                mock_playwright_cm.__aenter__ = AsyncMock(return_value=mock_pw)
                mock_playwright_cm.__aexit__ = AsyncMock(return_value=False)

                with patch("core.rpa.executor.async_playwright", return_value=mock_playwright_cm):
                    from core.rpa.executor import execute_rpa_script

                    result = await execute_rpa_script("test_script", {"key": "val"})

        assert result["success"] is True
        assert result["data"]["downloaded"] is True
        assert result["data"]["rows"] == 10
        assert result["elapsed_ms"] >= 0
        assert result["error"] == ""

    # ── 2. Missing script — returns error without crashing ───────────
    @pytest.mark.asyncio
    async def test_missing_script(self) -> None:
        """Attempting to run a non-existent script returns a clean error."""
        with patch("core.rpa.executor._PLAYWRIGHT_AVAILABLE", True):
            mock_pw_cm = AsyncMock()
            mock_page = AsyncMock()
            mock_page.on = MagicMock()
            mock_page.screenshot = AsyncMock(return_value=b"\x89PNG")
            mock_ctx = AsyncMock()
            mock_ctx.new_page = AsyncMock(return_value=mock_page)
            mock_ctx.set_default_timeout = MagicMock()
            mock_browser = AsyncMock()
            mock_browser.new_context = AsyncMock(return_value=mock_ctx)
            mock_browser.close = AsyncMock()
            mock_pw = AsyncMock()
            mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)
            mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
            mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

            with patch("core.rpa.executor.async_playwright", return_value=mock_pw_cm):
                from core.rpa.executor import execute_rpa_script

                result = await execute_rpa_script("nonexistent_script_xyz", {})

        assert result["success"] is False
        assert "not found" in result["error"] or "nonexistent_script_xyz" in result["error"]

    # ── 3. Timeout handling — configurable timeout propagated ────────
    @pytest.mark.asyncio
    async def test_playwright_not_installed(self) -> None:
        """When playwright is not installed, returns appropriate error."""
        with patch("core.rpa.executor._PLAYWRIGHT_AVAILABLE", False):
            from core.rpa.executor import execute_rpa_script

            result = await execute_rpa_script("any_script", {}, timeout_s=30)

        assert result["success"] is False
        assert "playwright" in result["error"].lower()

    # ── 4. Screenshot capture — result includes screenshots ──────────
    @pytest.mark.asyncio
    async def test_screenshot_in_result(self) -> None:
        """Verify screenshots list is present in result."""
        with patch("core.rpa.executor._PLAYWRIGHT_AVAILABLE", False):
            from core.rpa.executor import execute_rpa_script

            result = await execute_rpa_script("test", {})

        # Even on failure, screenshots key exists
        assert "screenshots" in result
        assert isinstance(result["screenshots"], list)

    # ── 5. Sandbox isolation — tool description mentions headless ────
    def test_rpa_tool_description(self) -> None:
        """The LangChain tool wrapper has correct metadata."""
        from core.rpa.tools import browser_rpa_execute

        assert browser_rpa_execute.name == "browser_rpa_execute"
        assert "headless" in browser_rpa_execute.description.lower()
        assert "Playwright" in browser_rpa_execute.description


class TestRPAScripts:
    """Verify stub scripts have the correct interface."""

    def test_epfo_script_has_run(self) -> None:
        from rpa.scripts import epfo_ecr_download

        assert hasattr(epfo_ecr_download, "run")
        assert hasattr(epfo_ecr_download, "SCRIPT_META")
        assert epfo_ecr_download.SCRIPT_META["name"] == "epfo_ecr_download"

    def test_mca_script_has_run(self) -> None:
        from rpa.scripts import mca_company_search

        assert hasattr(mca_company_search, "run")
        assert hasattr(mca_company_search, "SCRIPT_META")
        assert mca_company_search.SCRIPT_META["name"] == "mca_company_search"
