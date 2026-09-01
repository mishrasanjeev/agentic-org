"""Stress OCR and browser resources locally without external side effects."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.async_api import async_playwright

from core.rag.extractors import extract
from core.runtime_capacity import AsyncCapacityGate


@dataclass(frozen=True)
class ResourceResult:
    workload: str
    jobs: int
    concurrency_limit: int
    maximum_active: int
    elapsed_seconds: float
    successful: int
    failed: int


def _ocr_fixture() -> bytes:
    image = Image.new("RGB", (1600, 900), "white")
    draw = ImageDraw.Draw(image)
    for index in range(12):
        draw.text(
            (80, 60 + index * 65),
            f"AgenticOrg local OCR stress line {index + 1:02d} invoice total 1250.00",
            fill="black",
        )
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


async def _run_ocr(jobs: int, concurrency: int) -> ResourceResult:
    gate = AsyncCapacityGate("OCR stress", limit=concurrency, queue_timeout_seconds=120)
    fixture = _ocr_fixture()
    active = 0
    maximum_active = 0

    async def run_one(index: int) -> bool:
        nonlocal active, maximum_active
        async with gate.slot():
            active += 1
            maximum_active = max(maximum_active, active)
            try:
                result = await asyncio.to_thread(
                    extract,
                    fixture,
                    "image/png",
                    f"synthetic-stress-{index}.png",
                )
                return "AgenticOrg" in result.full_text()
            finally:
                active -= 1

    started = time.perf_counter()
    results = await asyncio.gather(*(run_one(index) for index in range(jobs)))
    elapsed = time.perf_counter() - started
    successful = sum(results)
    return ResourceResult(
        workload="tesseract_ocr",
        jobs=jobs,
        concurrency_limit=concurrency,
        maximum_active=maximum_active,
        elapsed_seconds=round(elapsed, 3),
        successful=successful,
        failed=jobs - successful,
    )


async def _run_browser(jobs: int, concurrency: int) -> ResourceResult:
    gate = AsyncCapacityGate("browser stress", limit=concurrency, queue_timeout_seconds=120)
    active = 0
    maximum_active = 0

    async with async_playwright() as playwright:

        async def run_one(index: int) -> bool:
            nonlocal active, maximum_active
            async with gate.slot():
                active += 1
                maximum_active = max(maximum_active, active)
                browser = await playwright.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.set_content(
                        f"<main><h1>AgenticOrg RPA stress {index}</h1>"
                        "<button id='submit'>Submit</button></main>"
                    )
                    await page.click("#submit")
                    screenshot = await page.screenshot()
                    return len(screenshot) > 100
                finally:
                    await browser.close()
                    active -= 1

        started = time.perf_counter()
        results = await asyncio.gather(*(run_one(index) for index in range(jobs)))
        elapsed = time.perf_counter() - started

    successful = sum(results)
    return ResourceResult(
        workload="playwright_chromium",
        jobs=jobs,
        concurrency_limit=concurrency,
        maximum_active=maximum_active,
        elapsed_seconds=round(elapsed, 3),
        successful=successful,
        failed=jobs - successful,
    )


async def _run(args: argparse.Namespace) -> dict[str, object]:
    results = [
        await _run_ocr(args.ocr_jobs, args.ocr_concurrency),
        await _run_browser(args.browser_jobs, args.browser_concurrency),
    ]
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "workload": "local_docker_resource_stress",
        "passed": all(result.failed == 0 for result in results),
        "results": [asdict(result) for result in results],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ocr-jobs", type=int, default=12)
    parser.add_argument("--ocr-concurrency", type=int, default=2)
    parser.add_argument("--browser-jobs", type=int, default=8)
    parser.add_argument("--browser-concurrency", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(_run(args))
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
