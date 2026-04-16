#!/usr/bin/env python3
"""Idempotently seed the demo tenant with agents required by the E2E suite.

Currently ensures:
  - at least one agent of type ``ap_processor`` in ``shadow`` state

``tests/synthetic_data/test_synthetic_flows.py`` looks up an agent of
that type and skips when one is not present. The demo tenant ships with
a different finance agent mix (ar_collections, bank_reconciliation,
gst_filing, tds_compliance), so the AP Processor tests always skipped
in CI. Running this script before the pytest step turns those skips
into real assertions.

Idempotent: a second invocation is a no-op. Exits non-zero only on
network failure or a 5xx from the API.

Usage:
  python3 scripts/seed_e2e_demo_agents.py \
      --base-url https://app.agenticorg.ai \
      --token "$E2E_TOKEN"
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import httpx


def _ensure_ap_processor(client: httpx.Client) -> None:
    resp = client.get("/api/v1/agents", params={"page": 1, "per_page": 200})
    resp.raise_for_status()
    items = resp.json().get("items", [])
    existing = [a for a in items if a.get("agent_type") == "ap_processor"]
    if existing:
        print(f"[seed] ap_processor already present (id={existing[0]['id']}) — skipping create")
        return

    payload: dict[str, Any] = {
        "name": "E2E AP Processor",
        "agent_type": "ap_processor",
        "domain": "finance",
        "employee_name": "E2E AP Processor",
        "designation": "AP Processor (E2E)",
        "initial_status": "shadow",
        "system_prompt_text": (
            "You are an AP Processor agent. Given an invoice plus matching "
            "PO and GRN data, decide whether to approve the invoice and "
            "return a JSON object with status, confidence, and "
            "processing_trace."
        ),
    }
    resp = client.post("/api/v1/agents", json=payload)
    if resp.status_code in (200, 201):
        print(f"[seed] created ap_processor agent id={resp.json().get('id', 'unknown')}")
        return
    if resp.status_code == 409:
        # Shadow fleet limit reached or duplicate — treat as benign.
        print(f"[seed] ap_processor create returned 409, continuing: {resp.text[:200]}")
        return
    resp.raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()

    headers = {
        "Authorization": f"Bearer {args.token}",
        "Content-Type": "application/json",
    }
    with httpx.Client(base_url=args.base_url.rstrip("/"), headers=headers, timeout=30) as client:
        _ensure_ap_processor(client)
    return 0


if __name__ == "__main__":
    sys.exit(main())
