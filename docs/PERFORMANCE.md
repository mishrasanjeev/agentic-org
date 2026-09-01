# Performance and Load Testing

AgenticOrg performance claims must come from a reproducible result. This page
defines the supported local Docker checks and the boundary between measured
evidence and production capacity planning.

## Current evidence

The latest checked-in workstation run is:

- [Local Docker stress and performance report](reports/agenticorg-local-docker-stress-performance-2026-09-01.md)

That report measures one Docker Desktop host. It is useful for regression and
resource-safety decisions, but it is not a Cloud Run SLA or a claim about
tenant, LLM, telephony, payment-provider, or third-party connector capacity.

## Runtime protections

AgenticOrg applies bounded admission control to the two most expensive local
runtime paths:

| Workload | Default concurrency per worker | Queue deadline | Saturation behavior |
|---|---:|---:|---|
| Document extraction and OCR | 2 | 30 seconds | HTTP 503 with a retryable capacity error |
| Browser RPA | 2 | 5 seconds | Structured retryable failure; the browser is not launched |

Tune these only after measuring the target container CPU and memory:

```text
AGENTICORG_DOCUMENT_EXTRACTION_MAX_CONCURRENCY
AGENTICORG_DOCUMENT_EXTRACTION_QUEUE_TIMEOUT_SECONDS
AGENTICORG_RPA_MAX_CONCURRENCY
AGENTICORG_RPA_QUEUE_TIMEOUT_SECONDS
```

Readiness checks reuse a bounded Redis pool, probe DB and Redis concurrently,
and coalesce concurrent probes into a one-second cache. Liveness remains a
dependency-free process check.

The Docker image defaults to one Uvicorn worker. Operators may set
`WEB_CONCURRENCY` when the service has enough CPU and its total DB connection
budget has been calculated. Capacity gates are process-local, so effective
container concurrency is `WEB_CONCURRENCY` multiplied by each configured
limit. More workers are not automatically faster for a CPU-saturated OCR/RPA
container.

## Reproduce locally

Build and start a production-style API with isolated dependencies:

```powershell
docker build -t agenticorg-performance:local .
$env:AGENTICORG_PERF_IMAGE = "agenticorg-performance:local"
docker compose -p agenticorg_perf `
  -f docker-compose.yml `
  -f docker-compose.local-e2e.yml `
  -f docker-compose.simulation.yml `
  -f docker-compose.performance.yml `
  up -d --wait postgres redis minio mailpit api
docker compose -p agenticorg_perf `
  -f docker-compose.yml `
  -f docker-compose.local-e2e.yml `
  -f docker-compose.simulation.yml `
  -f docker-compose.performance.yml `
  port api 8000
```

Run the HTTP harness against the mapped API port:

```powershell
python tests/load/local_docker_http.py `
  --base-url http://127.0.0.1:<mapped-port> `
  --output codex-pytest-artifacts/local-docker-http.json
```

Run real OCR and Chromium stress inside the built container. The workload uses
synthetic local content and performs no external action:

```powershell
docker run --rm --network none `
  -e PYTHONPATH=/work `
  --mount "type=bind,source=$PWD,target=/work,readonly" `
  -w /work agenticorg-performance:local `
  python tests/load/local_docker_resource_stress.py
```

Remove the isolated stack when finished:

```powershell
docker compose -p agenticorg_perf `
  -f docker-compose.yml `
  -f docker-compose.local-e2e.yml `
  -f docker-compose.simulation.yml `
  -f docker-compose.performance.yml `
  down --volumes --remove-orphans
```

## Production release gate

Before raising production concurrency or instance limits, run a staging soak
with production-equivalent Cloud Run CPU, memory, DB pool, Redis tier, and
autoscaling settings. Include authenticated API mixes, tenant isolation,
large-document OCR, browser RPA, websocket/voice control traffic, and failure
injection. Record p50/p95/p99, error rate, queue wait, CPU, memory, DB pool
wait, Redis latency, and external-provider latency.

Do not load-test paid telephony, payment rails, merchant systems, or other
third-party services without written approval, dedicated test credentials,
spend caps, and provider-safe test destinations.
