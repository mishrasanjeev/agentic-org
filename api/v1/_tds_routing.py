"""TDS Compliance Agent deterministic chat route — issue #440.

The TDS Compliance Agent's user-visible BUG-17 symptom kept reproducing
even after PR #434 (tool binding) + PR #443 (prompt extraction language
+ backfill across 5 tenants × 85 agents). gpt-4o was treating the
prompt's worked example as a clarification template instead of as an
extraction guide:

    USER: Calculate TDS for vendor payment of INR 50,000 under Section 194C
          for April 2026 and file Form 26Q
    AGENT: Please provide the following details:
           1. Amount of payment (e.g., INR 50,000)   ← parroting the example
           2. Applicable TDS section (e.g., 194C)    ← parroting the example
           ...

This module implements a **narrowly-scoped runtime route** that bypasses
the LLM for the calculation step when the user's chat message clearly
contains the required parameters. Scope:

- TDS Compliance Agent ONLY (gated on agent_type)
- calculate_tds is a pure deterministic computation (no Zoho API call,
  just rate × amount math); safe to call without OAuth
- Form 26Q filing is NEVER invoked here — that requires HITL + GSTN
  credentials. If the user requests filing, this route returns the
  calculated TDS plus a fail-closed blocker enumerating what's missing
- If detection fails (no amount, no section, no action verb), returns
  None and the chat handler falls through to the existing LLM path

The point is to make BUG-11/17 closure independent of LLM behavior
tuning, not to replace the LLM globally. No tool_choice="required",
no global runtime change, no impact on any other agent.
"""

from __future__ import annotations

import re
from typing import Any

# ----------------------------------------------------------------------
# Detection patterns
# ----------------------------------------------------------------------

# Indian TDS section detector. Matches any ``194<letter>`` form (and
# 194A-Q variants). Codex P1 on PR #445: an earlier allowlist regex
# (``194[ACHIJOQ]``) silently dropped unsupported variants like
# ``194ZZZ`` to ``section=None`` → route returned None → fell through
# to the LLM, contradicting the "never silently fall through" promise.
# Now: detect any 194-series mention, let the calculator surface a
# clear "unsupported section" error to the user.
#
# Section 192 (salary TDS) is intentionally NOT in this regex — Codex
# P2: ``ZohoBooksConnector.calculate_tds`` has no 192 rate (salary TDS
# is slab-based with HRA/standard-deduction/regime variants the
# deterministic helper can't model). Salary prompts must fall through
# to the LLM which can reason about slab tiers. Including 192 here
# would intercept salary queries and surface a useless error.
_TDS_SECTION_RE = re.compile(
    r"\b(?:section\s+)?(194[A-Z]+)\b",
    re.IGNORECASE,
)

# Action verbs that signal computation/filing intent.
_TDS_ACTION_RE = re.compile(
    r"\b(calculate|compute|compute\s+tds|file|filing|submit|prepare|deduct|withhold)\b",
    re.IGNORECASE,
)

# Amount patterns — covers "INR 50,000", "Rs. 50000", "₹50,000", and the
# bare-number-after-amount-keyword case ("amount of 50000"). Captures
# the numeric group in either alternation.
_TDS_AMOUNT_RE = re.compile(
    r"(?:(?:INR|Rs\.?|₹)\s*([\d,]+(?:\.\d+)?))"
    r"|(?:\b(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?)\s*(?:INR|Rs\.?|rupees|/-)\b)"
    r"|(?:(?:amount\s+of|payment\s+of|paid|paying)\s+(?:INR|Rs\.?|₹)?\s*([\d,]+(?:\.\d+)?))",
    re.IGNORECASE,
)

# Period — month + year, quarter, or financial year.
_TDS_PERIOD_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December|Jan|Feb|Mar|Apr|Jun|"
    r"Jul|Aug|Sep|Oct|Nov|Dec)[A-Za-z]*\s+\d{4}\b"
    r"|\bQ[1-4]\s+(?:FY)?\s*\d{2,4}\b"
    r"|\bFY\s*\d{2,4}(?:-\d{2,4})?\b",
    re.IGNORECASE,
)

# Form 26Q / 24Q filing intent.
_FILING_FORM_RE = re.compile(
    r"\bform\s*(26[A-Z]|24[A-Z])\b|\b(26[A-Z]|24[A-Z])\s*return\b",
    re.IGNORECASE,
)

# Deductee type.
_DEDUCTEE_TYPE_RE = re.compile(
    r"\b(individual|huf|company|firm|partnership|trust)\b",
    re.IGNORECASE,
)

# PAN — 5 letters, 4 digits, 1 letter.
_PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")

# Explicit "PAN unavailable" signal in the prompt — without this we
# treat PAN as available for calculation purposes and surface a
# 206AA caveat. This avoids the wrong-rate trap where every
# "calculate TDS for X" query without a PAN would silently apply
# the 20% penalty rate instead of the section rate.
_NO_PAN_RE = re.compile(
    r"\b(no\s+pan|without\s+pan|pan\s+(?:not|un)\s*(?:available|provided|furnished)|missing\s+pan)\b",
    re.IGNORECASE,
)


def _extract_amount(query: str) -> float | None:
    match = _TDS_AMOUNT_RE.search(query)
    if not match:
        return None
    raw = next((g for g in match.groups() if g), None)
    if not raw:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _extract_section(query: str) -> str | None:
    match = _TDS_SECTION_RE.search(query)
    if not match:
        return None
    return match.group(1).upper()


def _extract_period(query: str) -> str | None:
    match = _TDS_PERIOD_RE.search(query)
    return match.group(0) if match else None


def _extract_deductee_type(query: str) -> str | None:
    match = _DEDUCTEE_TYPE_RE.search(query)
    return match.group(1).lower() if match else None


def _extract_pan(query: str) -> str | None:
    match = _PAN_RE.search(query)
    return match.group(0) if match else None


def _filing_requested(query: str) -> str | None:
    """Return the form name if filing is requested, else None."""
    match = _FILING_FORM_RE.search(query)
    if not match:
        return None
    return (match.group(1) or match.group(2) or "").upper() or None


# ----------------------------------------------------------------------
# Public entrypoint
# ----------------------------------------------------------------------


# Agent types for which this route is active. Single-element tuple today;
# explicit so adding a sibling agent later is a one-line change.
TDS_ROUTE_AGENT_TYPES: tuple[str, ...] = ("tds_compliance_agent",)


async def try_tds_deterministic_route(
    *,
    agent_type: str | None,
    query: str,
) -> dict[str, Any] | None:
    """Issue #440 / BUG-17: if this is the TDS Compliance Agent and the
    user's message contains amount + section + an action verb, invoke
    calculate_tds deterministically (pure math, no Zoho API), construct
    a tool_calls trace, and return early with a fully-formed answer.

    Returns ``None`` when:
      * agent isn't TDS Compliance, or
      * query lacks any required signal (amount, section, action verb).

    When ``None``, the caller falls through to the existing LLM path.

    NEVER invokes the actual filing API. If the user requested Form
    26Q/24Q filing, the response includes a fail-closed blocker that
    enumerates the genuinely-missing fields (PAN, deductee_type,
    challan, HITL approval, GSTN credential) — without re-asking for
    amount/section/period that were already in the prompt.
    """
    if agent_type not in TDS_ROUTE_AGENT_TYPES:
        return None
    if not query or not _TDS_ACTION_RE.search(query):
        return None

    amount = _extract_amount(query)
    section = _extract_section(query)
    if amount is None or section is None:
        return None

    period = _extract_period(query)
    deductee_type = _extract_deductee_type(query) or "individual"
    pan = _extract_pan(query)
    requested_form = _filing_requested(query)

    # PAN handling: only treat PAN as unavailable when the user
    # *explicitly* says so. A query like "calculate TDS for INR 50,000
    # under Section 194C" is asking for the section rate, not the
    # Section 206AA penalty rate that would apply if PAN were missing.
    # Surface the 206AA caveat as a separate warning instead of baking
    # it into the rate silently.
    pan_explicitly_missing = bool(_NO_PAN_RE.search(query))
    pan_available_for_calc = bool(pan) or not pan_explicitly_missing

    # Pure-math deterministic helper — same code path as the connector's
    # calculate_tds tool, no OAuth or network needed for the calculation.
    from connectors.finance.zoho_books import ZohoBooksConnector

    conn = ZohoBooksConnector(config={})
    calc = await conn.calculate_tds(
        amount=amount,
        section=section,
        deductee_type=deductee_type,
        pan_available=pan_available_for_calc,
    )
    if "error" in calc:
        # Section unsupported or other input error — surface and bail
        # so the LLM path is never silently reached without telling the
        # user we tried.
        answer = (
            f"Unable to calculate TDS deterministically: {calc['error']}. "
            "Please rephrase with a supported section "
            "(194A, 194C, 194H, 194I, 194J, 194O, 194Q)."
        )
        return {
            "answer": answer,
            "tool_calls": [],
            "confidence": 0.45,
            "tools_used": False,
        }

    # Build a tool_calls trace mirroring what the LangGraph runner
    # would emit for a calculate_tds invocation. Downstream consumers
    # (audit log, chat-history persistence, future analytics) read this
    # field expecting the same shape.
    tool_call = {
        "tool": "calculate_tds",
        "connector": "zoho_books",
        "arguments": {
            "amount": amount,
            "section": section,
            "deductee_type": deductee_type,
            "pan_available": pan_available_for_calc,
        },
        "result": calc,
        "deterministic_route": True,
        "route_reason": "issue_440_tds_runtime_routing",
    }

    parts: list[str] = []
    parts.append(
        f"Calculated TDS deterministically for amount=INR {amount:,.0f} "
        f"under Section {section}"
        + (f" (period: {period})" if period else "")
        + ":"
    )
    parts.append(
        f"  • Rate applied: {calc['rate'] * 100:.2f}%\n"
        f"  • TDS amount: INR {calc['tds_amount']:,.2f}\n"
        f"  • Net payable to vendor: INR {calc['net_payable']:,.2f}"
    )
    if not pan:
        if pan_explicitly_missing:
            parts.append(
                "  • PAN explicitly missing — Section 206AA HIGHER rate "
                "of 20% applied above (already reflected in tds_amount)."
            )
        else:
            parts.append(
                "  • PAN not specified in the prompt — calculation used "
                "the Section "
                + section
                + " rate. If PAN is unavailable for the deductee, "
                "Section 206AA would apply a higher 20% rate; re-send "
                "with 'PAN not available' if that's the case."
            )

    if requested_form:
        # Filing was requested. We do NOT call the real filing API.
        # Enumerate genuinely-missing fields (those NOT already in the
        # prompt). Never re-ask for amount, section, or period — those
        # were extracted above.
        missing: list[str] = []
        if not pan:
            missing.append("PAN of the deductee")
        if deductee_type == "individual" and not _DEDUCTEE_TYPE_RE.search(query):
            missing.append("deductee type (individual / HUF / company / firm)")
        # Always required for filing, never extractable from chat:
        missing.extend(
            [
                "TAN of the deductor",
                "BSR code + challan serial + deposit date "
                "(Challan 281 payment evidence)",
                "explicit human-in-the-loop approval (filings cannot "
                "auto-submit per CA pack policy)",
                "active GSTN/Income-Tax portal credential for the "
                "filing endpoint",
            ]
        )
        parts.append(
            f"\nForm {requested_form} filing requested but blocked "
            "fail-closed. The calculation above is final; filing "
            "requires the following fields/conditions before any "
            "submission attempt:"
        )
        parts.extend(f"  ✗ {item}" for item in missing)
        parts.append(
            "\nNo actual filing call was made. Re-send the request "
            "with the missing fields and route through the partner-"
            "review (HITL) queue to proceed."
        )
        confidence = 0.85  # Calculation is exact; filing is correctly blocked
    else:
        confidence = 0.92  # Pure calculation, no follow-up gate needed

    return {
        "answer": "\n".join(parts),
        "tool_calls": [tool_call],
        "confidence": confidence,
        "tools_used": True,
    }
