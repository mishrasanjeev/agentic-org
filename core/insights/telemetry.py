# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""How the Insights dashboard itself performs: Prometheus metrics and the landing-view probe.

The metrics carry no tenant, user or run identifier, only the metric, view or
explorer key (a fixed, small set) and an export's kind and outcome:

- ``agenticorg_insights_metric_seconds{metric}``: time to resolve one metric;
- ``agenticorg_insights_view_seconds{view}``: time to answer one landing view;
- ``agenticorg_insights_explorer_seconds{explorer}``: time to answer one explorer page;
- ``agenticorg_insights_export_jobs_total{kind,status}``: background export jobs by final status;
- ``agenticorg_insights_rollup_last_success_timestamp``: when the closed-day rollup sweep last finished
  (alert on ``time() - ...`` for rollup lag).

``time_landing_views`` is the synthetic check ``insights_landing`` (observability/synthetic.py): it answers each
landing view for a tenant the way the API does and times it against the PRD's 1.5 s budget.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from prometheus_client import Counter, Gauge, Histogram

_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 1.5, 2.5, 3.0, 5.0, 10.0)

metric_seconds = Histogram(
    "agenticorg_insights_metric_seconds",
    "Time to resolve one Insights metric.",
    ["metric"],
    buckets=_BUCKETS,
)
view_seconds = Histogram(
    "agenticorg_insights_view_seconds",
    "Time to answer one Insights landing view (budget 1.5 s at p95).",
    ["view"],
    buckets=_BUCKETS,
)
explorer_seconds = Histogram(
    "agenticorg_insights_explorer_seconds",
    "Time to answer one Insights explorer page (budget 3 s at p95).",
    ["explorer"],
    buckets=_BUCKETS,
)
export_jobs_total = Counter(
    "agenticorg_insights_export_jobs_total",
    "Insights background export jobs by kind and final status.",
    ["kind", "status"],
)
rollup_last_success = Gauge(
    "agenticorg_insights_rollup_last_success_timestamp",
    "Unix time the closed-day Insights rollup sweep last finished.",
)


@contextmanager
def timed(histogram: Histogram, key: str) -> Iterator[None]:
    """Observe the block's duration under ``key`` (also when it raises)."""
    began = time.perf_counter()
    try:
        yield
    finally:
        histogram.labels(key).observe(time.perf_counter() - began)


async def time_landing_views(tenant_id: uuid.UUID, views: list[str]) -> dict[str, dict[str, Any]]:
    """Answer each landing view for the tenant (every domain, the last 30 days) and time it."""
    from api.v1.insights import _context, view_domains
    from core.database import get_tenant_session
    from core.insights import registry
    from core.ownership import machine_caller

    caller = machine_caller()
    out: dict[str, dict[str, Any]] = {}
    for view in views:
        began = time.perf_counter()
        try:
            async with get_tenant_session(tenant_id) as session:
                ctx = await _context(session, tenant_id, caller, start=None, end=None, compare="previous",
                                     company=None)
                ctx.domains = view_domains(view, None)
                for key in registry.VIEWS[view].keys:
                    await registry.resolve(registry.METRICS[key], ctx)
            out[view] = {"ms": round((time.perf_counter() - began) * 1000, 1)}
        # enterprise-gate: broad-except-ok reason=a-failing-view-is-reported-and-logged-as-the-probe-outcome
        except Exception as exc:
            out[view] = {"ms": round((time.perf_counter() - began) * 1000, 1), "error": type(exc).__name__}
        view_seconds.labels(view).observe(out[view]["ms"] / 1000)
    return out
