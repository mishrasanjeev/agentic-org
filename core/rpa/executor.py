"""RPA script executor — runs Playwright scripts in a headless browser with audit trail.

Each script is a Python module in ``rpa/scripts/`` that exposes an
``async def run(page, params) -> dict`` function.

All Playwright imports are guarded so the module can be imported without
``playwright`` installed. Install with ``pip install agenticorg[v4]`` and run
``playwright install chromium`` outside the production container image.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import importlib
import time
from pathlib import Path
from typing import Any

import structlog

from core.config import settings
from core.runtime_capacity import AsyncCapacityGate, CapacityLimitError

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Guarded Playwright import
# ---------------------------------------------------------------------------
try:
    from playwright.async_api import async_playwright  # type: ignore[import-not-found]

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
MAX_NAVIGATION_SCREENSHOTS = 10

_RPA_CAPACITY = AsyncCapacityGate(
    "RPA browser execution",
    limit=settings.rpa_max_concurrency,
    queue_timeout_seconds=settings.rpa_queue_timeout_seconds,
)


async def _capture_screenshot(
    page: Any,
    screenshots: list[str],
    *,
    screenshot_dir: str | None,
    label: str,
) -> None:
    """Capture a bounded audit screenshot to disk or memory."""
    if len(screenshots) >= MAX_NAVIGATION_SCREENSHOTS:
        return
    try:
        image = await page.screenshot(type="png")
        if screenshot_dir:
            directory = Path(screenshot_dir).expanduser().resolve()
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"{label}-{len(screenshots) + 1:02d}.png"
            target.write_bytes(image)
            screenshots.append(str(target))
        else:
            screenshots.append(base64.b64encode(image).decode("ascii"))
    except Exception:  # enterprise-gate: broad-except-ok reason=screenshot-failure-does-not-change-run-result
        return


def _result_error(result_data: Any) -> str:
    """Interpret explicit script-level failures instead of reporting success."""
    if not isinstance(result_data, dict):
        return ""
    if result_data.get("success") is False:
        return str(result_data.get("error") or "script reported failure")
    error = result_data.get("error")
    return str(error) if error else ""


async def _execute_rpa_script(
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
    except ModuleNotFoundError as exc:
        if exc.name != module_path:
            return {
                "success": False,
                "data": None,
                "screenshots": [],
                "elapsed_ms": 0,
                "error": f"RPA script dependency {exc.name!r} is not installed",
            }
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

    browser: Any = None
    context: Any = None
    navigation_tasks: set[asyncio.Task[None]] = set()
    try:
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

            def _on_navigation(frame: Any) -> None:
                if getattr(frame, "parent_frame", None) is not None:
                    return
                task = asyncio.create_task(
                    _capture_screenshot(
                        page,
                        screenshots,
                        screenshot_dir=screenshot_dir,
                        label="navigation",
                    )
                )
                navigation_tasks.add(task)
                task.add_done_callback(navigation_tasks.discard)

            page.on("framenavigated", _on_navigation)
            result_data = await asyncio.wait_for(
                script_module.run(page, params),
                timeout=max(1, timeout_s),
            )
            if navigation_tasks:
                await asyncio.gather(*tuple(navigation_tasks), return_exceptions=True)
            await _capture_screenshot(
                page,
                screenshots,
                screenshot_dir=screenshot_dir,
                label="final",
            )

            explicit_error = _result_error(result_data)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            if explicit_error:
                return {
                    "success": False,
                    "data": result_data,
                    "screenshots": screenshots,
                    "elapsed_ms": elapsed_ms,
                    "error": explicit_error,
                }

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
    except TimeoutError:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return {
            "success": False,
            "data": None,
            "screenshots": screenshots,
            "elapsed_ms": elapsed_ms,
            "error": f"RPA script exceeded the {timeout_s}s execution timeout",
        }
    except Exception as exc:  # enterprise-gate: broad-except-ok reason=rpa-script-failure-returns-success-false
        elapsed_ms = int((time.perf_counter() - start) * 1000)
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
    finally:
        for task in navigation_tasks:
            task.cancel()
        if navigation_tasks:
            with contextlib.suppress(Exception):
                await asyncio.gather(*tuple(navigation_tasks), return_exceptions=True)
        if context is not None:
            with contextlib.suppress(Exception):
                await context.close()
        if browser is not None:
            with contextlib.suppress(Exception):
                await browser.close()


async def execute_rpa_script(
    script_name: str,
    params: dict[str, Any],
    timeout_s: int = DEFAULT_TIMEOUT_S,
    screenshot_dir: str | None = None,
) -> dict[str, Any]:
    """Execute one browser job within the per-container capacity budget."""
    try:
        return await _RPA_CAPACITY.run(
            lambda: _execute_rpa_script(
                script_name=script_name,
                params=params,
                timeout_s=timeout_s,
                screenshot_dir=screenshot_dir,
            )
        )
    except CapacityLimitError as exc:
        logger.warning(
            "rpa_capacity_exhausted",
            limit=_RPA_CAPACITY.limit,
            waiting=_RPA_CAPACITY.snapshot.waiting,
            queue_timeout_seconds=exc.timeout_seconds,
        )
        return {
            "success": False,
            "data": None,
            "screenshots": [],
            "elapsed_ms": 0,
            "error": str(exc),
            "error_class": "rpa_capacity_exhausted",
            "retryable": True,
        }
