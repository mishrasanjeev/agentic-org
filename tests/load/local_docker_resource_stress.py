"""Stress OCR and browser resources locally without external side effects."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.async_api import async_playwright

from api.v1.knowledge import _DOCUMENT_EXTRACTION_CAPACITY
from core.rag.extractors import extract
from core.rpa.executor import _RPA_CAPACITY


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
    if concurrency != _DOCUMENT_EXTRACTION_CAPACITY.limit:
        raise ValueError(
            "--ocr-concurrency must match "
            f"AGENTICORG_DOCUMENT_EXTRACTION_MAX_CONCURRENCY={_DOCUMENT_EXTRACTION_CAPACITY.limit}"
        )
    fixture = _ocr_fixture()
    active = 0
    maximum_active = 0
    active_lock = threading.Lock()

    async def run_one(index: int) -> bool:
        def extract_one() -> bool:
            nonlocal active, maximum_active
            with active_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                result = extract(
                    fixture,
                    mime_type="image/png",
                    filename=f"synthetic-stress-{index}.png",
                )
                return "AgenticOrg" in result.full_text()
            finally:
                with active_lock:
                    active -= 1

        return await _DOCUMENT_EXTRACTION_CAPACITY.run_blocking(extract_one)

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
    if concurrency != _RPA_CAPACITY.limit:
        raise ValueError(
            "--browser-concurrency must match "
            f"AGENTICORG_RPA_MAX_CONCURRENCY={_RPA_CAPACITY.limit}"
        )
    active = 0
    maximum_active = 0

    async with async_playwright() as playwright:

        async def run_one(index: int) -> bool:
            nonlocal active, maximum_active

            async def run_browser_job() -> bool:
                nonlocal active, maximum_active
                active += 1
                maximum_active = max(maximum_active, active)
                try:
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
                finally:
                    active -= 1

            return await _RPA_CAPACITY.run(run_browser_job)

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
