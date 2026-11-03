# Insights Phase 4 and programme acceptance

The PRD's programme acceptance criteria (docs/INSIGHTS_DASHBOARD_PRD.md, Phased delivery plan) are the release gate
for the whole Insights programme. This page records, for each criterion, what demonstrates it, where the evidence
lives and what is still open. "In code" means the tests named run in CI on every push; "in production" means it has
been seen on the deployed service.

## Criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Every tile, chart and explorer references a registry metric key | Met in code | `ui/src/__tests__/insights.locales.test.ts` and the generated `metricKeys.generated.json` / `explorerKeys.generated.json` (`scripts/generate_insights_metric_keys.py`); a tile, chart or explorer link with an unknown key fails the UI tests |
| 2 | Every number downloads as CSV, XLSX and JSON with the filters applied; charts as PNG, SVG and PDF; views as PDF | Met in code | Metric export `format=pdf` (masked rows, the filters on the page), the chart menu's PNG, SVG and "Chart (PDF)", tile export menus, view PDF jobs, the platform figures as CSV, XLSX or JSON (`GET /api/v1/platform/insights/export`); browser journey "a chart exports as CSV, XLSX and JSON, and the view asks for its PDF" |
| 3 | Money saved shows its assumptions inline, exports with an assumptions sheet and is labelled `estimated` | Met (Phase 2) | Value view and its export; Phase 2 tests |
| 4 | No tile renders demo or placeholder values; missing data shows the empty state with its hint | Met in code | Legacy KPI pages deleted (they carried seeded figures); `_view_has_data` ignores snapshot metrics; every view's empty state is tested |
| 5 | Tenant-isolation tests pass for every explorer and export endpoint, under a `NOBYPASSRLS` role and each tenant's BI login role | Met in code | `tests/integration/test_insights_isolation_postgres.py` sweeps every view, metric, explorer, export and job for two seeded tenants; the BI login-role tests from Phase 1 |
| 6 | Landing views p95 under 1.5 s and explorers under 3 s on the 1M-runs synthetic tenant | Met locally; staging run open | `docs/insights/phase4-perf-local.json`: 1,000,000 runs, 20 requests each; the slowest view (Finance) p95 0.74 s, the slowest explorer (Agents) p95 0.45 s. Seeder `scripts/seed_perf_tenant.py` (refuses any non-local host), benchmark `scripts/insights_perf_bench.py`. A run against staging is the owner's to schedule |
| 7 | Rollup-vs-raw reconciliation within 0.01% for 14 consecutive nights | Waiting on time | The streak no longer resets on today's pending rollup (v6z112). Fourteen nights from the deploy of that fix, so mid-November 2026 at the earliest; the Platform view shows each tenant's streak |
| 8 | Playwright covers each role's landing view, one drill to Level 4, one export per format and one scheduled report delivery | Met in code, delivery by the worker tests | `ui/tests/browser/insights.spec.ts` (12 journeys, run by the deploy workflow after the text-encoding replay): nine role landings, the drill to one run, CSV/XLSX/JSON and the view PDF, and a weekly Finance schedule. Delivery of a due schedule is covered by the scheduled-report worker tests, not by the browser |
| 9 | WCAG 2.2 AA audit passes; every chart has a text summary and data table | Automated scan passes; manual audit open | axe (`@axe-core/playwright`, WCAG 2.0/2.1/2.2 A and AA rules) on each of the nine landings finds no violation; every chart card carries its text summary and a data table. A manual audit with assistive technology has not been done |
| 10 | Each legacy dashboard page redirects to the new views and is deleted once its working features have a new home; Report Scheduler stays | Met in code | `ui/src/__tests__/legacyRetirement.test.tsx`; the CEO, CFO, CHRO, COO, CBO, Cost, Observatory and Partner pages and the old dashboard are deleted; Marketing operations and Monitoring keep the working panels; the Report Scheduler remains the scheduling UI |

## Dashboard self-observability

- Prometheus: `agenticorg_insights_metric_seconds{metric}`, `agenticorg_insights_view_seconds{view}`,
  `agenticorg_insights_explorer_seconds{explorer}`, `agenticorg_insights_export_jobs_total{kind,status}` and
  `agenticorg_insights_rollup_last_success_timestamp`. No tenant, user or run label. Alert on the view histogram's
  p95 above 1.5 s, on `failed` export jobs, and on `time() - agenticorg_insights_rollup_last_success_timestamp`
  above two hours.
- Synthetic check `insights_landing` (docs/operations/synthetic-checks.md): answers each landing view for the tenant
  and fails a view slower than `max_view_ms` (1500) or one that errors.
- Platform view, **Dashboard operations**: export jobs made in the range by status, the export failure rate, the
  oldest open job, and tenants whose newest rollup is more than two hours old
  (`public.insights_platform_operations`, migration v6z114, aggregates only).

## Still open

- Criterion 6 on staging, criterion 7 (time), and the manual part of criterion 9.
- The Phase 2 and Phase 3 usability sessions need real participants (docs/insights/phase2-usability-test.md,
  docs/insights/phase3-acceptance.md); nothing here simulates them.
- No UI or API creates budget alerts, so the cost views say that spend is not tracked against a limit.
