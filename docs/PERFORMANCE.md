# Performance Baselines

This document captures the performance numbers customers can expect
from AgenticOrg. All measurements are from the production deployment
at `app.agenticorg.ai` running on GKE with the resource allocation
documented in `docs/SCALING.md`.

## How we measure

- **Source of truth**: Grafana dashboards backed by Prometheus.
- **Load generator**: Python E2E smoke tests run every hour from a
  dedicated worker in the same GCP region. End-to-end measurements
  include TLS handshake and service-side processing.
- **Window**: percentiles are computed over a trailing 24-hour
  window.

Raw numbers live in the dashboard at `/d/perf-baseline` (Grafana).

## API latency (public endpoints)

| Endpoint                                    | p50   | p95   | p99    |
|---------------------------------------------|-------|-------|--------|
| `GET  /api/v1/health`                       |  8 ms |  20 ms|  40 ms |
| `GET  /api/v1/agents`                       | 35 ms | 90 ms | 140 ms |
| `POST /api/v1/agents/{id}/run` (LLM path)   | 1.8 s | 6.0 s | 12 s   |
| `POST /api/v1/workflows/{id}/trigger`       | 60 ms | 180 ms| 320 ms |
| `POST /api/v1/approvals/{id}/decide`        | 45 ms | 140 ms| 260 ms |
| `GET  /api/v1/kpis/{domain}`                | 120 ms| 350 ms| 600 ms |
| `POST /api/v1/billing/subscribe/india`      | 300 ms| 900 ms| 1.5 s  |

The LLM path dominates the tail — 95% of latency > 1 s is time spent
waiting on Anthropic/Gemini. The non-LLM p99 target is 500 ms across
all endpoints.

## Throughput

- **Sustained**: 120 req/s per API pod (p95 < 200 ms).
- **Burst**: 400 req/s per pod for up to 30 seconds before HPA kicks in.
- **At 20 pods**: ~2400 req/s sustained.

## Workflow execution

| Scenario                              | p50   | p95    |
|---------------------------------------|-------|--------|
| Sequential workflow, 5 steps, no LLM  | 1.2 s | 2.8 s  |
| Sequential workflow, 5 steps, 1 LLM   | 4.8 s | 12 s   |
| Parallel workflow, 10 branches        | 6.2 s | 14 s   |
| Workflow with HITL (excluding wait)   | 250 ms| 700 ms |

Max workflow duration is capped at 30 minutes. Workflows exceeding
this are auto-cancelled and the caller sees a `workflow_timeout`
error.

## Database

- p95 query latency: 3 ms (read), 12 ms (write).
- Connection pool size: 20 per API pod, 30 per worker.
- Vacuum runs automatically; long running queries are killed after 60 s.

## Known cliffs

1. **> 1000 rows in a single KPI response**: pagination recommended;
   the response body becomes the bottleneck.
2. **Workflow with > 100 parallel steps**: the LangGraph checkpointer
   serializes on a single DB row for the run state. Keep fan-out below
   100 or split into sub-workflows.
3. **Search over > 1M documents in the knowledge base**: RAGFlow is
   tuned for 500K docs. Above that, shard the index.

## Load test methodology

Run locally with:
```
cd tests/load
python locustfile.py --users 500 --spawn-rate 20 --host https://app.agenticorg.ai
```

Load tests run weekly on a staging cluster; results are attached to
the Monday release review.

## History

| Version | Date       | p95 /agents/run | Notes                       |
|---------|------------|-----------------|------------------------------|
| v3.0.0  | 2026-01-10 | 9.2 s           | Baseline                    |
| v4.0.0  | 2026-02-14 | 7.1 s           | Prompt caching              |
| v4.3.0  | 2026-03-08 | 6.5 s           | Gemini fallback for simple  |
| v4.6.0  | 2026-04-11 | 6.0 s           | Current                     |
