"""Run real local voice, email, OCR, and optional public-demo RPA simulations.

This script never calls a telephony provider. The voice leg exercises signed
webhooks, STT text intake, TwiML TTS output, encrypted persistence, and tenant
isolation against fresh PostgreSQL. Email is delivered to local Mailpit. OCR
uses the installed Tesseract binary. RPA is opt-in because it navigates an
operator-approved public test site.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
COMPOSE_PROJECT = "agenticorg_multichannel_sim"
COMPOSE_FILES = (
    ROOT / "docker-compose.yml",
    ROOT / "docker-compose.local-e2e.yml",
    ROOT / "docker-compose.simulation.yml",
)


def _run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(  # noqa: S603 - argv is assembled only from fixed local runner commands
        command,
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        detail = (completed.stdout + "\n" + completed.stderr).strip()
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(command)}\n{detail}")
    return completed.stdout.strip()


def _compose(*args: str) -> list[str]:
    command = ["docker", "compose", "--project-name", COMPOSE_PROJECT]
    for path in COMPOSE_FILES:
        command.extend(["-f", str(path)])
    command.extend(args)
    return command


def _mapped_port(service: str, container_port: int) -> int:
    output = _run(_compose("port", service, str(container_port)))
    return int(output.rsplit(":", 1)[1])


def _mailpit_message(api_port: int, subject: str) -> dict[str, Any]:
    response = httpx.get(f"http://127.0.0.1:{api_port}/api/v1/messages", timeout=10)
    response.raise_for_status()
    payload = response.json()
    messages = payload.get("messages", [])
    message = next((item for item in messages if item.get("Subject") == subject), None)
    if not message:
        raise RuntimeError(f"Mailpit did not capture subject {subject!r}")
    return message


def _simulate_email(smtp_port: int, api_port: int, recipient: str) -> dict[str, Any]:
    from core.email import send_email

    subject = f"AgenticOrg local simulation {int(time.time())}"
    previous = os.environ.copy()
    os.environ.update(
        {
            "AGENTICORG_ENV": "local",
            "AGENTICORG_SMTP_HOST": "127.0.0.1",
            "AGENTICORG_SMTP_PORT": str(smtp_port),
            "AGENTICORG_SMTP_SECURITY": "plain",
            "AGENTICORG_SMTP_LOGIN": "",
            "AGENTICORG_GMAIL_APP_PASSWORD": "",
            "AGENTICORG_TEST_FAKE_MAIL": "",
            "AGENTICORG_DEMO_SENDER": "simulation@agenticorg.ai",
        }
    )
    try:
        if not send_email(recipient, subject, "<h1>AgenticOrg local delivery verified</h1>"):
            raise RuntimeError("send_email returned false during local Mailpit delivery")
    finally:
        os.environ.clear()
        os.environ.update(previous)
    captured = _mailpit_message(api_port, subject)
    return {
        "status": "passed",
        "transport": "local_mailpit_smtp",
        "subject": subject,
        "captured_message_id": captured.get("ID"),
    }


def _simulate_ocr(evidence_dir: Path) -> dict[str, Any]:
    from core.rag.extractors import extract

    phrase = "AGENTICORG INVOICE 2026 TOTAL INR 1250"
    image = Image.new("RGB", (2200, 700), "white")
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 70)
    except OSError:
        font = ImageFont.load_default()
    ImageDraw.Draw(image).text((100, 280), phrase, fill="black", font=font)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    source = evidence_dir / "synthetic-invoice.png"
    source.write_bytes(stream.getvalue())

    result = extract(stream.getvalue(), "image/png", source.name)
    normalized = result.full_text().upper()
    for token in ("AGENTICORG", "INVOICE", "2026", "1250"):
        if token not in normalized:
            raise RuntimeError(f"OCR output omitted required token {token!r}: {normalized!r}")
    return {
        "status": "passed",
        "method": result.extraction_method,
        "mean_confidence": result.extra.get("ocr_mean_confidence"),
        "page_count": result.extra.get("page_count"),
        "source": str(source),
    }


async def _simulate_rpa(evidence_dir: Path) -> dict[str, Any]:
    from core.rpa.executor import execute_rpa_script

    required = {
        "portal_url": "AGENTICORG_SIM_RPA_URL",
        "username": "AGENTICORG_SIM_RPA_USERNAME",
        "password": "AGENTICORG_SIM_RPA_PASSWORD",
        "wait_for": "AGENTICORG_SIM_RPA_WAIT_FOR",
        "extract_selector": "AGENTICORG_SIM_RPA_EXTRACT_SELECTOR",
    }
    params = {name: os.getenv(env_name, "") for name, env_name in required.items()}
    missing = [env_name for name, env_name in required.items() if not params[name]]
    if missing:
        raise RuntimeError("Public RPA simulation requires environment variables: " + ", ".join(missing))
    params["action"] = "extract"
    result = await execute_rpa_script(
        "generic_portal",
        params,
        timeout_s=75,
        screenshot_dir=str(evidence_dir / "rpa-screenshots"),
    )
    if not result.get("success") or not result.get("data", {}).get("logged_in"):
        raise RuntimeError(f"RPA simulation failed: {result.get('error') or result.get('data', {}).get('error')}")
    data = result["data"]
    return {
        "status": "passed",
        "elapsed_ms": result.get("elapsed_ms"),
        "page_title": data.get("page_title"),
        "current_url": data.get("current_url"),
        "extracted_text": data.get("extracted_text"),
        "screenshot_count": len(result.get("screenshots", [])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-public-rpa", action="store_true", help="Run approved public-site RPA inputs from env")
    parser.add_argument("--recipient", default="simulation@orchestrum.in", help="Address captured by local Mailpit")
    parser.add_argument("--evidence-dir", type=Path, default=ROOT / "codex-pytest-artifacts" / "multichannel")
    args = parser.parse_args()

    evidence_dir = args.evidence_dir.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "production_mutation": False,
        "paid_phone_call": False,
        "flows": {},
    }
    try:
        _run(_compose("up", "-d", "--wait", "postgres", "mailpit"))
        db_port = _mapped_port("postgres", 5432)
        smtp_port = _mapped_port("mailpit", 1025)
        mailpit_api_port = _mapped_port("mailpit", 8025)
        voice_env = os.environ.copy()
        voice_env["AGENTICORG_DB_URL"] = (
            f"postgresql+asyncpg://agenticorg:agenticorg_dev@127.0.0.1:{db_port}/agenticorg"
        )
        _run(
            [sys.executable, "-m", "pytest", "tests/integration/test_voice_runtime_e2e.py", "--no-cov", "-q"],
            env=voice_env,
        )
        report["flows"]["voice"] = {
            "status": "passed",
            "coverage": [
                "signed inbound webhook",
                "speech transcript intake",
                "TwiML text-to-speech response",
                "signed status callback",
                "encrypted transcript persistence",
                "masked history",
                "tenant isolation",
            ],
            "provider_last_mile": "not_called",
        }
        report["flows"]["email"] = _simulate_email(smtp_port, mailpit_api_port, args.recipient)
        report["flows"]["ocr"] = _simulate_ocr(evidence_dir)
        report["flows"]["rpa"] = (
            asyncio.run(_simulate_rpa(evidence_dir))
            if args.with_public_rpa
            else {"status": "skipped", "reason": "pass --with-public-rpa and approved test-site env inputs"}
        )
        report["status"] = "passed"
        return_code = 0
    except Exception as exc:  # enterprise-gate: broad-except-ok reason=simulation-report-must-record-failure
        report["status"] = "failed"
        report["error"] = str(exc)
        return_code = 1
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        report_path = evidence_dir / "simulation-report.json"
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(report, indent=2, sort_keys=True))
        try:
            _run(_compose("down", "--volumes", "--remove-orphans"))
        except Exception as cleanup_exc:  # enterprise-gate: broad-except-ok reason=preserve-primary-simulation-result
            print(f"Simulation cleanup failed: {cleanup_exc}", file=sys.stderr)
            if return_code == 0:
                return_code = 1
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
