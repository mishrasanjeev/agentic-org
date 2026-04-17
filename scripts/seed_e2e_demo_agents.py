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


_AP_PROCESSOR_SYSTEM_PROMPT = (
    "You are an AP Processor agent. Every run receives one invoice plus its "
    "PO (and optionally GRN) data inside ``inputs``. The action name "
    "('process_invoice') is the instruction — never reply 'not a supported "
    "action'. Work deterministically on the payload; do not call external "
    "tools unless explicitly needed.\n\n"
    "Processing rules:\n"
    "1. Compare invoice.total against po_data.amount. If |delta| / "
    "po_data.amount is within 2%%, status=matched; else status=mismatch and "
    "record the delta.\n"
    "2. HIGH-VALUE GATE: if invoice.total > 500000 (the ₹5L threshold), set "
    "hitl_triggered=true and include a hitl_reason that explicitly names the "
    "threshold (500000) AND the invoice total. Use words like 'high value' "
    "and 'exceeds threshold' so auditors can follow the decision.\n"
    "3. Duplicate: if invoice_id has been processed before, status=duplicate.\n"
    "4. GSTIN: if the GSTIN is malformed, status=gstin_invalid.\n\n"
    "Output JSON with: status "
    "(matched|mismatch|duplicate|gstin_invalid|escalated), confidence "
    "(0.0-1.0), hitl_triggered (bool), hitl_reason (string; required when "
    "hitl_triggered), invoice_id, total, match_delta, processing_trace "
    "(list of short strings describing every step including the high-value "
    "check, the threshold value, and the actual amount).\n\n"
    "For any invoice above the threshold the processing_trace MUST cite "
    "both 500000 and the invoice total verbatim."
)


# Full AP Processor tool set — finance + payments. The /agents/{id}/run
# endpoint filters unresolvable tool names at call time, so listing
# everything here is safe even when a connector is missing.
_AP_PROCESSOR_TOOLS: list[str] = [
    "fetch_bank_statement",
    "check_account_balance",
    "post_voucher",
    "get_ledger_balance",
    "get_trial_balance",
    "create_order",
    "check_order_status",
]


def _ensure_ap_processor(client: httpx.Client) -> None:
    resp = client.get("/api/v1/agents", params={"page": 1, "per_page": 200})
    resp.raise_for_status()
    items = resp.json().get("items", [])
    existing = [a for a in items if a.get("agent_type") == "ap_processor"]
    if existing:
        agent_id = existing[0]["id"]
        # Older seed runs created a bare-bones agent whose LLM replies
        # "action 'process_invoice' is not a supported action". Refresh
        # just the system_prompt_text in place so the synthetic tests can
        # actually exercise high-value HITL logic. The tool list is left
        # untouched — run-time filtering handles unresolvable names, and
        # the PATCH validator can 422 on tools that existed yesterday but
        # not today.
        patch = client.patch(
            f"/api/v1/agents/{agent_id}",
            json={"system_prompt_text": _AP_PROCESSOR_SYSTEM_PROMPT},
        )
        if patch.status_code in (200, 204):
            print(f"[seed] ap_processor {agent_id} prompt refreshed")
        else:
            print(
                f"[seed] patch returned {patch.status_code}, continuing: "
                f"{patch.text[:200]}"
            )
        return

    payload: dict[str, Any] = {
        "name": "E2E AP Processor",
        "agent_type": "ap_processor",
        "domain": "finance",
        "employee_name": "E2E AP Processor",
        "designation": "AP Processor (E2E)",
        "initial_status": "shadow",
        "system_prompt_text": _AP_PROCESSOR_SYSTEM_PROMPT,
        "authorized_tools": _AP_PROCESSOR_TOOLS,
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
