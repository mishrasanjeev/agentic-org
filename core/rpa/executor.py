"""RPA script executor — runs Playwright scripts in a headless browser with audit trail.

Each script is a Python module in ``rpa/scripts/`` that exposes an
``async def run(page, params) -> dict`` function.

All Playwright imports are guarded so the module can be imported without
``playwright`` installed.  Install with ``pip install agenticorg[v4]``.
"""

from __future__ import annotations

import importlib
import time
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Guarded Playwright import
# ---------------------------------------------------------------------------
try:
    from playwright.async_api import async_playwright  # type: ignore[import-untyped]

    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False
    async_playwright = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Script discovery root
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "rpa" / "scripts"

# Default execution timeout (seconds)
DEFAULT_TIMEOUT_S = 60


async def execute_rpa_script(
    script_name: str,
    params: dict[str, Any],
    timeout_s: int = DEFAULT_TIMEOUT_S,
    screenshot_dir: str | None = None,
) -> dict[str, Any]:
    """Load and execute an RPA script.

    Parameters
    ----------
    script_name : str
        Module name inside ``rpa/scripts/`` (without ``.py``).
        E.g., ``"epfo_ecr_download"`` or ``"mca_company_search"``.
    params : dict
        Parameters passed to the script's ``run()`` function.
    timeout_s : int
        Maximum execution time in seconds (default 60).
    screenshot_dir : str | None
        Directory to save navigation screenshots for audit.
        If None, screenshots are captured in memory and returned
        as base64 in the result dict.

    Returns
    -------
    dict
        ``{success: bool, data: Any, screenshots: list[str], elapsed_ms: int, error: str}``
    """
    if not _PLAYWRIGHT_AVAILABLE:
        return {
            "success": False,
            "data": None,
            "screenshots": [],
            "elapsed_ms": 0,
            "error": "playwright is not installed. Install with: pip install playwright && playwright install chromium",
        }

    # Dynamically import the script module
    module_path = f"rpa.scripts.{script_name}"
    try:
        script_module = importlib.import_module(module_path)
    except ModuleNotFoundError:
        return {
            "success": False,
            "data": None,
            "screenshots": [],
            "elapsed_ms": 0,
            "error": f"RPA script '{script_name}' not found in {_SCRIPTS_DIR}",
        }

    if not hasattr(script_module, "run"):
        return {
            "success": False,
            "data": None,
            "screenshots": [],
            "elapsed_ms": 0,
            "error": f"RPA script '{script_name}' has no 'run(page, params)' function",
        }

    start = time.perf_counter()
    screenshots: list[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        context.set_default_timeout(timeout_s * 1000)

        page = await context.new_page()

        # Capture screenshot on every navigation
        async def _on_navigation(response: Any) -> None:
            try:
                screenshot_bytes = await page.screenshot(type="png")
                import base64

                screenshots.append(base64.b64encode(screenshot_bytes).decode("ascii"))
            except Exception:  # noqa: S110
                pass  # navigation screenshot is best-effort

        page.on("load", lambda _: None)  # placeholder for type
        # Use framenavigated for post-navigation screenshots
        page.on(
            "framenavigated",
            lambda frame: _schedule_screenshot(frame, page, screenshots),
        )

        try:
            result_data = await script_module.run(page, params)
            elapsed_ms = int((time.perf_counter() - start) * 1000)

            # Final screenshot
            try:
                import base64

                final_ss = await page.screenshot(type="png")
                screenshots.append(base64.b64encode(final_ss).decode("ascii"))
            except Exception:  # noqa: S110
                pass

            await browser.close()

            logger.info(
                "rpa_script_completed",
                script=script_name,
                elapsed_ms=elapsed_ms,
                screenshots=len(screenshots),
            )

            return {
                "success": True,
                "data": result_data,
                "screenshots": screenshots,
                "elapsed_ms": elapsed_ms,
                "error": "",
            }

        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            await browser.close()

            logger.error(
                "rpa_script_failed",
                script=script_name,
                elapsed_ms=elapsed_ms,
                error=str(exc),
            )

            return {
                "success": False,
                "data": None,
                "screenshots": screenshots,
                "elapsed_ms": elapsed_ms,
                "error": str(exc),
            }


def _schedule_screenshot(
    frame: Any,
    page: Any,
    screenshots: list[str],
) -> None:
    """Schedule an async screenshot capture (fire-and-forget)."""
    import asyncio

    async def _capture() -> None:
        try:
            import base64

            ss = await page.screenshot(type="png")
            screenshots.append(base64.b64encode(ss).decode("ascii"))
        except Exception:  # noqa: S110
            pass

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_capture())
    except RuntimeError:
        pass
