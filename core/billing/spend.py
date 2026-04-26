"""Platform-wide LLM spend reporter (and Gemini-specific breakdown).

Usage::

    # Today's Gemini spend, broken down by model.
    python -m core.billing.spend --period=daily --provider=gemini

    # Last 30 days, all providers, top 10 tenants.
    python -m core.billing.spend --period=monthly --top-tenants=10

    # JSON output for scraping.
    python -m core.billing.spend --period=daily --json

    # Compare local cost calc vs Google Cloud Billing Export
    # (requires the BigQuery billing export to be set up).
    python -m core.billing.spend --period=daily --reconcile-gcp

The script aggregates ``cost_usd`` from ``agent_task_results``
across all tenants. The local cost is computed from token counts
× ``GEMINI_PRICE_PER_1M`` at call time. The reconcile-gcp mode
compares that local total against the BigQuery
``gcp_billing_export_v1_<billing_account>`` table for the same
period and prints the divergence — large divergence usually means
the local pricing table is stale, or that the unaccounted-for
spend is from non-LLM services (storage, egress).
"""

from __future__ import annotations

import argparse
import asyncio
import json as _json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text

from core.database import async_session_factory

logger = structlog.get_logger()


@dataclass
class SpendRow:
    label: str
    value: str
    cost_usd: float
    calls: int
    tokens: int


def _period_start(period: str, now: datetime) -> datetime:
    """Return the UTC start of the named period."""
    if period == "daily":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "weekly":
        weekday = now.weekday()
        start = now - timedelta(days=weekday)
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "monthly":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"Unknown period {period!r}")


async def _spend_by_model(since: datetime, provider: str | None) -> list[SpendRow]:
    """Aggregate by ``llm_model``, optionally filtered to a provider prefix."""
    where = ["created_at >= :since"]
    params: dict[str, Any] = {"since": since}
    if provider:
        where.append("llm_model LIKE :prefix")
        params["prefix"] = f"{provider}%"
    where_sql = " AND ".join(where)
    q = (
        "SELECT COALESCE(llm_model, '(unknown)') AS model, "  # nosec B608 — `where_sql` joins argparse-validated literals only; user input lands in bind params
        "COUNT(*) AS calls, "
        "COALESCE(SUM(tokens_used), 0) AS tokens, "
        "COALESCE(SUM(cost_usd), 0) AS cost_usd "
        "FROM agent_task_results "
        f"WHERE {where_sql} "
        "GROUP BY llm_model "
        "ORDER BY cost_usd DESC"
    )
    async with async_session_factory() as session:
        result = await session.execute(text(q), params)
        return [
            SpendRow(
                label="model",
                value=row[0] or "(unknown)",
                cost_usd=float(row[3] or 0.0),
                calls=int(row[1] or 0),
                tokens=int(row[2] or 0),
            )
            for row in result.all()
        ]


async def _spend_by_tenant(
    since: datetime, top_n: int, provider: str | None
) -> list[SpendRow]:
    """Aggregate by ``tenant_id``, top N by cost."""
    where = ["created_at >= :since"]
    params: dict[str, Any] = {"since": since, "limit": top_n}
    if provider:
        where.append("llm_model LIKE :prefix")
        params["prefix"] = f"{provider}%"
    where_sql = " AND ".join(where)
    q = (
        "SELECT tenant_id::text, "  # nosec B608 — `where_sql` joins argparse-validated literals only; user input lands in bind params
        "COUNT(*) AS calls, "
        "COALESCE(SUM(tokens_used), 0) AS tokens, "
        "COALESCE(SUM(cost_usd), 0) AS cost_usd "
        "FROM agent_task_results "
        f"WHERE {where_sql} "
        "GROUP BY tenant_id "
        "ORDER BY cost_usd DESC "
        "LIMIT :limit"
    )
    async with async_session_factory() as session:
        result = await session.execute(text(q), params)
        return [
            SpendRow(
                label="tenant",
                value=row[0],
                cost_usd=float(row[3] or 0.0),
                calls=int(row[1] or 0),
                tokens=int(row[2] or 0),
            )
            for row in result.all()
        ]


def _format_table(title: str, rows: list[SpendRow]) -> str:
    """Render a fixed-width table for the terminal."""
    if not rows:
        return f"{title}: (no data)\n"
    out = [f"{title}:"]
    name_width = max(len(r.value) for r in rows)
    name_width = max(name_width, len(rows[0].label))
    out.append(
        f"  {rows[0].label:<{name_width}}  {'cost_usd':>12}  "
        f"{'calls':>10}  {'tokens':>14}"
    )
    out.append(f"  {'-' * name_width}  {'-' * 12}  {'-' * 10}  {'-' * 14}")
    total_cost = total_calls = total_tokens = 0.0
    for r in rows:
        out.append(
            f"  {r.value:<{name_width}}  ${r.cost_usd:>10.4f}  "
            f"{r.calls:>10}  {r.tokens:>14}"
        )
        total_cost += r.cost_usd
        total_calls += r.calls
        total_tokens += r.tokens
    out.append(f"  {'-' * name_width}  {'-' * 12}  {'-' * 10}  {'-' * 14}")
    out.append(
        f"  {'TOTAL':<{name_width}}  ${total_cost:>10.4f}  "
        f"{int(total_calls):>10}  {int(total_tokens):>14}"
    )
    return "\n".join(out) + "\n"


async def _reconcile_with_gcp(since: datetime, local_total_usd: float) -> str:
    """Compare local LLM-only spend against the GCP billing export.

    Requires:
      - ``AGENTICORG_GCP_BILLING_BQ_TABLE`` set to the fully-qualified
        BigQuery table id (e.g.
        ``perfect-period-305406.billing_export.gcp_billing_export_v1_<account>``).
      - ``google-cloud-bigquery`` installed.

    Reads the SUM of ``cost`` for ``service.description`` containing
    'AI' or 'Generative Language' since ``since``. Note: GCP totals
    include all billed services (compute, storage, egress, etc.) so
    this filter is best-effort. Operators reading the divergence
    should focus on the LLM-related lines.
    """
    table = os.getenv("AGENTICORG_GCP_BILLING_BQ_TABLE")
    if not table:
        return (
            "reconcile-gcp: AGENTICORG_GCP_BILLING_BQ_TABLE not set — "
            "skipping. Point at the BQ billing-export table to enable.\n"
        )
    try:
        from google.cloud import bigquery
    except ImportError:
        return (
            "reconcile-gcp: google-cloud-bigquery not installed — "
            "`pip install google-cloud-bigquery`.\n"
        )
    client = bigquery.Client()
    query = (
        "SELECT SUM(cost) AS gcp_total "  # nosec B608 — `table` is from a trusted env var (AGENTICORG_GCP_BILLING_BQ_TABLE), not user input; @since is a BQ named param
        f"FROM `{table}` "
        "WHERE usage_start_time >= @since "
        "AND ("
        "  LOWER(service.description) LIKE '%generative%' "
        "  OR LOWER(service.description) LIKE '%vertex%' "
        "  OR LOWER(service.description) LIKE '%gemini%' "
        "  OR LOWER(service.description) LIKE '%language%api%'"
        ")"
    )
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("since", "TIMESTAMP", since),
        ]
    )
    try:
        result = client.query(query, job_config=job_config).result()
    except Exception as exc:  # noqa: BLE001
        return f"reconcile-gcp: BigQuery query failed: {exc}\n"
    gcp_total = next(iter(result), None)
    if gcp_total is None or gcp_total["gcp_total"] is None:
        gcp_total_usd = 0.0
    else:
        gcp_total_usd = float(gcp_total["gcp_total"])
    diff = local_total_usd - gcp_total_usd
    return (
        f"reconcile-gcp:\n"
        f"  Local cost (computed):   ${local_total_usd:>10.4f}\n"
        f"  GCP billing export:      ${gcp_total_usd:>10.4f}\n"
        f"  Divergence (local-gcp):  ${diff:>10.4f}\n"
        "  Note: GCP total filters service descriptions matching\n"
        "  'generative|vertex|gemini|language api'; cross-check\n"
        "  the BQ row directly when the divergence is large.\n"
    )


async def run(
    period: str,
    provider: str | None,
    top_tenants: int,
    output_json: bool,
    reconcile_gcp: bool,
) -> int:
    now = datetime.now(UTC)
    since = _period_start(period, now)

    by_model = await _spend_by_model(since, provider)
    by_tenant = await _spend_by_tenant(since, top_tenants, provider) if top_tenants else []

    local_total = sum(r.cost_usd for r in by_model)

    if output_json:
        payload = {
            "period": period,
            "since_utc": since.isoformat(),
            "now_utc": now.isoformat(),
            "provider_filter": provider,
            "by_model": [r.__dict__ for r in by_model],
            "by_tenant": [r.__dict__ for r in by_tenant],
            "total_cost_usd": local_total,
        }
        if reconcile_gcp:
            payload["reconcile_note"] = (await _reconcile_with_gcp(since, local_total))
        print(_json.dumps(payload, indent=2, default=str))
        return 0

    print(
        f"Spend window: {since.isoformat()} → {now.isoformat()} "
        f"(period={period}, provider={provider or 'all'})"
    )
    print()
    print(_format_table("By model", by_model))
    if by_tenant:
        print(_format_table(f"Top {top_tenants} tenants", by_tenant))
    if reconcile_gcp:
        print(await _reconcile_with_gcp(since, local_total))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--period",
        choices=["daily", "weekly", "monthly"],
        default="daily",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default=None,
        help=(
            "Filter llm_model to a provider prefix (e.g. 'gemini', "
            "'claude', 'gpt'). Default: all providers."
        ),
    )
    parser.add_argument(
        "--top-tenants",
        type=int,
        default=10,
        help="Show top N tenants by cost (set 0 to skip the per-tenant table).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    parser.add_argument(
        "--reconcile-gcp",
        action="store_true",
        help=(
            "Compare local cost computation against the GCP "
            "BigQuery billing export. Requires "
            "AGENTICORG_GCP_BILLING_BQ_TABLE."
        ),
    )
    args = parser.parse_args(argv)
    try:
        return asyncio.run(
            run(
                period=args.period,
                provider=args.provider,
                top_tenants=args.top_tenants,
                output_json=args.json,
                reconcile_gcp=args.reconcile_gcp,
            )
        )
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
