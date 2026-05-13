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

import math
import re
from datetime import date, timedelta
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
    r"\b(calculate|compute|compute\s+tds|file|filing|submit|prepare|generate|deduct|withhold|pay)\b",
    re.IGNORECASE,
)

HIGH_VALUE_TDS_TRANSACTION_THRESHOLD = 500_000.0

_CHALLAN_281_RE = re.compile(
    r"\b(?:challan\s*281|281\s+challan|tds\s+payment)\b",
    re.IGNORECASE,
)

_LATE_FILING_RE = re.compile(
    r"\b(?:late\s+fil(?:e|ing)|delayed\s+fil(?:e|ing)|234E|201\s*\(?1A\)?|penalt(?:y|ies)|interest)\b",
    re.IGNORECASE,
)

_DELAY_DAYS_RE = re.compile(
    r"\b(?:delayed\s+by|delay\s+of|late\s+by)\s+(?P<days>\d{1,4})\s+days?\b",
    re.IGNORECASE,
)

# Amount patterns cover "INR 50,000", "Rs. 50000", "₹50,000", and
# "amount/payment of 50000". Keep these deliberately simple: chat
# messages are untrusted input and CodeQL flags overlapping/nested
# extraction regexes as ReDoS-prone.
_TDS_AMOUNT_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:INR|Rs\.?|₹)\s*(?P<num>\d[\d,]*(?:\.\d+)?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?:INR|Rs\.?|rupees|/-)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:amount\s+of|payment\s+of|paid|paying)\s+"
        r"(?:INR|Rs\.?|₹)?\s*(?P<num>\d[\d,]*(?:\.\d+)?)",
        re.IGNORECASE,
    ),
)

# Period: month + year, quarter, or financial year.
_PERIOD_TOKEN_RE = re.compile(r"[A-Za-z0-9-]+")
_MONTH_TOKENS = {
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "jan",
    "feb",
    "mar",
    "apr",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
}

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
    raw = None
    for pattern in _TDS_AMOUNT_RES:
        match = pattern.search(query)
        if match:
            raw = match.group("num")
            break
    if not raw:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _extract_tds_amount(query: str) -> float | None:
    for pattern in (
        re.compile(
            r"\bTDS\s+(?:amount|payable|due)\s+(?:of\s+)?(?:INR|Rs\.?|₹)?\s*(?P<num>\d[\d,]*(?:\.\d+)?)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:INR|Rs\.?|₹)\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s+(?:TDS|tax\s+deducted)\b",
            re.IGNORECASE,
        ),
    ):
        match = pattern.search(query)
        if match:
            try:
                return float(match.group("num").replace(",", ""))
            except ValueError:
                return None
    return None


def _extract_section(query: str) -> str | None:
    match = _TDS_SECTION_RE.search(query)
    if not match:
        return None
    return match.group(1).upper()


def _looks_like_year_token(token: str) -> bool:
    parts = token.split("-")
    return all(part.isdigit() and 2 <= len(part) <= 4 for part in parts)


def _format_quarter_period(tokens: list[str], index: int) -> str | None:
    quarter = tokens[index].upper()
    if len(quarter) != 2 or quarter[0] != "Q" or quarter[1] not in "1234":
        return None
    if index + 1 >= len(tokens):
        return None

    next_token = tokens[index + 1]
    next_upper = next_token.upper()
    if next_upper == "FY" and index + 2 < len(tokens):
        year_token = tokens[index + 2]
        if _looks_like_year_token(year_token):
            return f"{quarter} FY {year_token}"
    if next_upper.startswith("FY") and _looks_like_year_token(next_token[2:]):
        return f"{quarter} {next_token}"
    if _looks_like_year_token(next_token):
        return f"{quarter} {next_token}"
    return None


def _format_financial_year_period(tokens: list[str], index: int) -> str | None:
    token = tokens[index]
    upper = token.upper()
    if upper == "FY" and index + 1 < len(tokens):
        year_token = tokens[index + 1]
        if _looks_like_year_token(year_token):
            return f"FY {year_token}"
    if upper.startswith("FY") and _looks_like_year_token(token[2:]):
        return token
    return None


def _extract_period(query: str) -> str | None:
    tokens = _PERIOD_TOKEN_RE.findall(query)
    for index, token in enumerate(tokens[:-1]):
        if token.lower() in _MONTH_TOKENS and tokens[index + 1].isdigit() and len(tokens[index + 1]) == 4:
            return f"{token} {tokens[index + 1]}"
    for index in range(len(tokens)):
        quarter = _format_quarter_period(tokens, index)
        if quarter:
            return quarter
    for index in range(len(tokens)):
        financial_year = _format_financial_year_period(tokens, index)
        if financial_year:
            return financial_year
    return None


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


def _challan_281_requested(query: str) -> bool:
    return bool(_CHALLAN_281_RE.search(query))


def _late_filing_requested(query: str) -> bool:
    return bool(_LATE_FILING_RE.search(query) and _FILING_FORM_RE.search(query))


def _extract_delay_days(query: str) -> int | None:
    match = _DELAY_DAYS_RE.search(query)
    if not match:
        return None
    try:
        return int(match.group("days"))
    except ValueError:
        return None


def _tds_return_due_date(period: str | None, form_name: str | None) -> date | None:
    if not period or not form_name:
        return None
    # TDS quarterly returns: Q1 Jul 31, Q2 Oct 31, Q3 Jan 31, Q4 May 31.
    # For an FY token "2025-26", the FY starts in 2025 and ends in 2026.
    match = re.search(r"\bQ(?P<quarter>[1-4])\b.*?\bFY\s*(?P<start>\d{4})(?:-(?P<end>\d{2,4}))?", period, re.IGNORECASE)
    if not match:
        return None
    quarter = int(match.group("quarter"))
    start_year = int(match.group("start"))
    end_token = match.group("end")
    if end_token:
        end_year = int(end_token)
        if end_year < 100:
            end_year = (start_year // 100) * 100 + end_year
    else:
        end_year = start_year + 1
    if quarter == 1:
        return date(start_year, 7, 31)
    if quarter == 2:
        return date(start_year, 10, 31)
    if quarter == 3:
        return date(end_year, 1, 31)
    return date(end_year, 5, 31)


def _build_challan_281_response(
    *,
    amount: float,
    period: str | None,
    section: str | None,
    pan: str | None,
    deductee_type: str | None,
) -> dict[str, Any]:
    critical_missing: list[str] = []
    if not section:
        critical_missing.append("TDS section / nature-of-payment code, e.g. 194C / 194J")
    critical_missing.extend(
        [
            "TAN of the deductor",
            "assessment year and minor head confirmation (200 or 400)",
            "explicit partner-review approval before payment submission",
        ]
    )
    deferred_fields = [
        "deductee PAN and deductee type for the later TDS return mapping",
        "bank BSR code, challan serial, and deposit date after payment",
    ]

    parts = [
        "Challan 281 draft prepared from the values already present in the prompt:",
        f"  • TDS amount to pay: INR {amount:,.2f}",
    ]
    if period:
        parts.append(f"  • Period: {period}")
    if section:
        parts.append(f"  • Section: {section}")
    parts.append(
        "\nNo amount clarification is needed. The draft can proceed; actual Challan 281 "
        "payment submission is blocked fail-closed until these payment-critical fields "
        "and approvals are supplied:"
    )
    parts.extend(f"  - {item}" for item in critical_missing)
    parts.append("\nDeferred for later compliance evidence; not blocking this draft:")
    parts.extend(f"  - {item}" for item in deferred_fields)
    parts.append("\nNo payment call was made.")

    return {
        "answer": "\n".join(parts),
        "tool_calls": [],
        "confidence": 0.82,
        "tools_used": False,
        "hitl_trigger": "challan_281_payment_requires_partner_review",
        "hitl_context": {
            "workflow": "challan_281",
            "transaction_amount": amount,
            "tds_amount": amount,
            "period": period,
            "section": section,
            "missing_fields": critical_missing,
            "deferred_fields": deferred_fields,
        },
    }


def _build_late_filing_response(
    *,
    query: str,
    period: str | None,
    form_name: str | None,
) -> dict[str, Any]:
    tds_amount = _extract_tds_amount(query)
    delay_days = _extract_delay_days(query)
    due_date = _tds_return_due_date(period, form_name)

    assumptions: list[str] = []
    if tds_amount is None:
        tds_amount = 100_000.0
        assumptions.append(
            "TDS amount payable was not supplied; used INR 1,00,000 as a working assumption."
        )
    if delay_days is None:
        delay_days = 30
        assumed_date = (due_date + timedelta(days=delay_days)).isoformat() if due_date else "30 days after due date"
        assumptions.append(
            f"Actual filing/deposit date was not supplied; assumed {delay_days} days late ({assumed_date})."
        )

    parts = [
        f"Section 234E / 201(1A) computation route selected for Form {form_name or '26Q/24Q'}.",
    ]
    if period:
        parts.append(f"  • Period extracted: {period}")
    if due_date:
        parts.append(f"  • Statutory filing due date: {due_date.isoformat()}")
    if assumptions:
        parts.append("  • Assumptions used for this provisional computation:")
        parts.extend(f"    - {item}" for item in assumptions)

    late_fee = min(tds_amount, 200.0 * delay_days)
    months_for_interest = max(1, math.ceil(delay_days / 30))
    late_deduction_interest = round(tds_amount * 0.01 * months_for_interest, 2)
    late_deposit_interest = round(tds_amount * 0.015 * months_for_interest, 2)
    parts.extend(
        [
            f"  • Section 234E late fee: INR {late_fee:,.2f} "
            f"(INR 200/day × {delay_days} days, capped at TDS amount)",
            f"  • Section 201(1A) late-deduction interest scenario: INR {late_deduction_interest:,.2f} "
            f"(1% per month or part month × {months_for_interest} month(s))",
            f"  • Section 201(1A) late-deposit interest scenario: INR {late_deposit_interest:,.2f} "
            f"(1.5% per month or part month × {months_for_interest} month(s))",
            "  • Replace assumptions with actual TDS payable, deduction date, deposit date, "
            "and filing date before filing or payment. No filing/payment call was made.",
        ]
    )

    return {
        "answer": "\n".join(parts),
        "tool_calls": [],
        "confidence": 0.82 if assumptions else 0.90,
        "tools_used": False,
        "hitl_trigger": "tds_late_fee_or_interest_requires_partner_review",
        "hitl_context": {
            "workflow": "tds_late_filing",
            "period": period,
            "form": form_name,
            "due_date": due_date.isoformat() if due_date else None,
            "tds_amount": tds_amount,
            "delay_days": delay_days,
            "assumptions": assumptions,
        },
    }


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

    period = _extract_period(query)
    requested_form = _filing_requested(query)
    if _late_filing_requested(query):
        return _build_late_filing_response(
            query=query,
            period=period,
            form_name=requested_form,
        )

    amount = _extract_amount(query)
    section = _extract_section(query)
    pan = _extract_pan(query)
    deductee_type = _extract_deductee_type(query)
    if _challan_281_requested(query) and amount is not None:
        return _build_challan_281_response(
            amount=amount,
            period=period,
            section=section,
            pan=pan,
            deductee_type=deductee_type,
        )
    if amount is None or section is None:
        return None

    deductee_type = _extract_deductee_type(query) or "individual"

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
        "governance": {
            "transaction_amount": amount,
            "tds_amount": calc["tds_amount"],
            "high_value_threshold": HIGH_VALUE_TDS_TRANSACTION_THRESHOLD,
        },
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

    high_value_trigger = ""
    if amount > HIGH_VALUE_TDS_TRANSACTION_THRESHOLD:
        high_value_trigger = (
            "transaction_amount "
            f"{amount:.2f} > {HIGH_VALUE_TDS_TRANSACTION_THRESHOLD:.2f}"
        )
        parts.append(
            "\nHITL triggered: gross transaction amount "
            f"INR {amount:,.2f} exceeds the INR "
            f"{HIGH_VALUE_TDS_TRANSACTION_THRESHOLD:,.0f} threshold. "
            "The calculation above is prepared for partner review; no filing, "
            "challan payment, voucher posting, or other processing step was "
            "auto-submitted."
        )

    return {
        "answer": "\n".join(parts),
        "tool_calls": [tool_call],
        "confidence": confidence,
        "tools_used": True,
        "hitl_trigger": high_value_trigger or None,
        "hitl_context": {
            "workflow": "tds_calculation",
            "transaction_amount": amount,
            "tds_amount": calc["tds_amount"],
            "section": section,
            "period": period,
            "requested_form": requested_form,
        },
    }
