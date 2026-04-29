"""Shared P&L connector chain for finance agents.

Ramesh/Uday 2026-04-28: FP&A and Close agents previously called
``tally.get_profit_and_loss`` — a tool name no connector in this repo
registers. Tally exposes ``get_trial_balance``, Zoho Books exposes
``get_profit_loss`` (no ``and_``), QuickBooks exposes ``get_profit_loss``.

This module owns the ordered chain plus the response-shape normaliser so
both agents stay consistent and a future connector can be added in one
place. Field-name aliases are deliberately permissive — connector
envelopes drift over time and the cost of a missed alias is a confidence
floor, not a crash.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.schemas.messages import ToolCallRecord


# (connector, tool, period_arg_style)
PNL_CHAIN: tuple[tuple[str, str, str], ...] = (
    ("zoho_books", "get_profit_loss", "from_to"),
    ("quickbooks", "get_profit_loss", "from_to"),
    ("tally", "get_trial_balance", "period_company"),
)


_PNL_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "revenue": ("total_revenue", "revenue", "income", "total_income", "net_revenue"),
    "cogs": ("cost_of_goods_sold", "cogs", "cost_of_sales"),
    "gross_profit": ("gross_profit", "gross_margin"),
    "opex": ("operating_expenses", "opex", "total_operating_expenses"),
    "ebitda": ("ebitda",),
    "depreciation": ("depreciation",),
    "interest": ("interest_expense", "interest"),
    "tax": ("tax_expense", "income_tax", "tax"),
    "net_profit": ("net_profit", "net_income", "profit"),
    "expenses": ("total_expenses", "expenses", "expenditure"),
    "employee_costs": ("employee_costs", "salaries", "personnel_expenses"),
    "marketing_spend": ("marketing_expenses", "marketing_spend"),
    "admin_expenses": ("admin_expenses", "administrative_expenses"),
}


def period_to_date_range(period: str) -> tuple[str, str]:
    """Convert ``YYYY-MM`` (or ``YYYY``) to ISO from/to dates.

    Empty period → empty strings; connectors treat empty params as
    "use the report's default window" which is the safest fallback.
    """
    if not period:
        return "", ""
    parts = period.split("-")
    try:
        if len(parts) == 2:
            year = int(parts[0])
            month = int(parts[1])
            from_date = f"{year:04d}-{month:02d}-01"
            next_month = month + 1
            next_year = year
            if next_month > 12:
                next_month = 1
                next_year += 1
            to_date = f"{next_year:04d}-{next_month:02d}-01"
            return from_date, to_date
        if len(parts) == 1 and parts[0].isdigit():
            year = int(parts[0])
            return f"{year:04d}-01-01", f"{year + 1:04d}-01-01"
    except ValueError:
        pass
    return "", ""


def _coerce_number(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", "").strip())
        except ValueError:
            return None
    return None


def normalize_pnl(result: dict[str, Any]) -> dict[str, float]:
    """Extract canonical P&L fields from a connector response.

    Connectors return varied envelopes:
    - Tally trial-balance: list of ledger groups with debit/credit totals.
    - Zoho Books P&L: ``{"profit_and_loss": [<sections>]}`` after _unwrap.
    - QuickBooks P&L: ``{"Rows": [...], "Summary": {...}}``.

    The flat-key path catches direct matches; the rows path handles the
    list-of-dicts shape. Anything more exotic returns an empty dict and
    the caller's confidence math reflects the missing data.
    """
    canonical: dict[str, float] = {}

    for canon, aliases in _PNL_FIELD_ALIASES.items():
        for alias in aliases:
            if alias in result:
                value = _coerce_number(result[alias])
                if value is not None:
                    canonical[canon] = value
                    break

    rows: list[Any] = []
    for key in ("rows", "values", "line_items", "Rows"):
        if key in result and isinstance(result[key], list):
            rows = result[key]
            break
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(
            row.get("line_item") or row.get("category") or row.get("name") or ""
        ).lower().replace(" ", "_")
        if not label:
            continue
        amount = _coerce_number(
            row.get("amount") or row.get("value") or row.get("total")
        )
        if amount is None:
            continue
        for canon, aliases in _PNL_FIELD_ALIASES.items():
            if label in aliases or label == canon:
                canonical.setdefault(canon, amount)
                break

    return canonical


async def fetch_pnl_via_chain(
    agent: Any,
    period: str,
    company: str,
    trace: list[str],
    tool_records: list[ToolCallRecord],
) -> tuple[dict[str, float], str]:
    """Try each connector in PNL_CHAIN until one returns data.

    The agent must expose ``_safe_tool_call(connector, tool, params,
    trace, tool_records)``. Returns ``(actuals_dict, source_label)``;
    source is the connector name that succeeded, or ``""`` if every
    attempt failed. Each individual failure is traced so the absent-data
    signal stays visible for the confidence math without masking which
    step failed.
    """
    from_date, to_date = period_to_date_range(period)
    for connector_name, tool_name, period_style in PNL_CHAIN:
        params: dict[str, Any]
        if period_style == "from_to":
            params = {"from_date": from_date, "to_date": to_date}
        else:
            params = {"period": period, "company": company}
        result = await agent._safe_tool_call(
            connector_name, tool_name, params, trace, tool_records,
        )
        if result and "error" not in result:
            actuals = normalize_pnl(result)
            if actuals.get("revenue", 0) > 0 or actuals.get("net_profit", 0) != 0:
                return actuals, connector_name
            trace.append(
                f"[tool] {connector_name}.{tool_name} returned no P&L numbers"
            )
    return {}, ""
