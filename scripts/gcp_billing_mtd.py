"""Query month-to-date GCP spend from the BigQuery billing export.

Usage:
    python scripts/gcp_billing_mtd.py                # MTD total + by-service
    python scripts/gcp_billing_mtd.py --month 2026-03 # specific month

Requires: gcloud auth application-default login (or ADC), BigQuery billing
export configured to perfect-period-305406.billing_export.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date

PROJECT = "perfect-period-305406"
DATASET = "billing_export"


def run_query(sql: str) -> list[dict]:
    token = subprocess.check_output(
        ["gcloud", "auth", "print-access-token"], text=True
    ).strip()
    import urllib.request

    req = urllib.request.Request(
        f"https://bigquery.googleapis.com/bigquery/v2/projects/{PROJECT}/queries",
        data=json.dumps({"query": sql, "useLegacySql": False}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read())
    if not body.get("jobComplete"):
        sys.exit("Query did not complete synchronously; rerun.")
    fields = [f["name"] for f in body["schema"]["fields"]]
    rows = []
    for r in body.get("rows", []):
        rows.append({fields[i]: c["v"] for i, c in enumerate(r["f"])})
    return rows


def find_billing_table() -> str:
    token = subprocess.check_output(
        ["gcloud", "auth", "print-access-token"], text=True
    ).strip()
    import urllib.request

    req = urllib.request.Request(
        f"https://bigquery.googleapis.com/bigquery/v2/projects/{PROJECT}/datasets/{DATASET}/tables",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read())
    tables = [t["tableReference"]["tableId"] for t in body.get("tables", [])]
    standard = [t for t in tables if t.startswith("gcp_billing_export_v1_")]
    if not standard:
        sys.exit(
            f"No billing export table found in {PROJECT}.{DATASET}. "
            "Enable the export in Console (Billing → Billing export → BigQuery export) "
            "and wait up to 24h for the first data."
        )
    return standard[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", help="YYYY-MM (default: current month)")
    args = parser.parse_args()

    if args.month:
        year, month = map(int, args.month.split("-"))
    else:
        today = date.today()
        year, month = today.year, today.month

    table = find_billing_table()
    fq = f"`{PROJECT}.{DATASET}.{table}`"

    total_sql = f"""
    SELECT
      ROUND(SUM(cost), 2) AS gross_cost,
      ROUND(SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)), 2) AS net_cost,
      currency
    FROM {fq}
    WHERE EXTRACT(YEAR FROM usage_start_time) = {year}
      AND EXTRACT(MONTH FROM usage_start_time) = {month}
    GROUP BY currency
    """
    by_service_sql = f"""
    SELECT
      service.description AS service,
      ROUND(SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)), 2) AS net_cost
    FROM {fq}
    WHERE EXTRACT(YEAR FROM usage_start_time) = {year}
      AND EXTRACT(MONTH FROM usage_start_time) = {month}
    GROUP BY service
    HAVING net_cost > 0
    ORDER BY net_cost DESC
    LIMIT 15
    """

    print(f"=== {year}-{month:02d} (project: {PROJECT}) ===\n")
    totals = run_query(total_sql)
    if not totals:
        print("No usage rows yet for this month.")
        return
    for t in totals:
        print(
            f"Gross: {t['gross_cost']} {t['currency']}    "
            f"Net (after credits): {t['net_cost']} {t['currency']}"
        )
    print("\nTop services:")
    for r in run_query(by_service_sql):
        print(f"  {r['net_cost']:>12}  {r['service']}")


if __name__ == "__main__":
    main()
