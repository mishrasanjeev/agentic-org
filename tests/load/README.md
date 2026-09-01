# Load test harness

This directory holds repeatable local Docker stress tools and the staging-only
Locust connector test.

## Local Docker runtime tests

`local_docker_http.py` exercises liveness, dependency readiness, and a burst
phase. It emits JSON and returns non-zero for client errors, non-200 responses,
unhealthy DB/Redis state in an HTTP 200 readiness body, or a readiness p95
above the configured limit.

```powershell
python tests/load/local_docker_http.py `
  --base-url http://127.0.0.1:<mapped-port> `
  --output codex-pytest-artifacts/local-docker-http.json
```

`local_docker_resource_stress.py` runs real Tesseract OCR and Playwright
through the same production capacity gates used by knowledge ingestion and RPA
Chromium against synthetic local data. It checks that expensive work stays
inside configured concurrency bounds without contacting an external site.

```powershell
docker run --rm --network none `
  -e PYTHONPATH=/work `
  --mount "type=bind,source=$PWD,target=/work,readonly" `
  -w /work agenticorg-performance:local `
  python tests/load/local_docker_resource_stress.py
```

See [Performance and Load Testing](../../docs/PERFORMANCE.md) for the complete
production-style Compose command and interpretation rules.

## Connector rate-limit test

## Why Locust

- Open source (MIT) — fits the user instruction to avoid proprietary tools
- Python-based, no extra runtime
- Easy to mix request shapes per "user" without learning a DSL

## Files

- `locustfile_connectors.py` — verifies the per-connector rate limiter
  in `core.tool_gateway.rate_limiter.RateLimiter` actually clamps at
  the published RPM. Run against a staging API (never production).

### Running locally against a dev API

```
pip install locust
export AGENTICORG_TOKEN="$(cat ~/.agenticorg/local-token)"
export AGENTICORG_TEST_TENANT_ID="00000000-0000-0000-0000-000000000001"
locust -f tests/load/locustfile_connectors.py \
  --host http://localhost:8000 \
  --users 50 --spawn-rate 5 --run-time 2m \
  --headless --csv tests/load/results/local
```

### Running in CI

Locust is *not* part of the default `pytest` run because it needs a
live API to talk to. The intended CI flow is:

1. A nightly GitHub Actions workflow spins up a single staging API
   pod with synthetic credentials.
2. It runs `locust ... --headless` for a fixed duration.
3. The CSV in `tests/load/results/` is uploaded as a build artifact
   and compared against the previous night's baseline (delta > 5%
   on p95 fails the build).

This nightly job is not yet wired — it's a P2 item tracked in
`docs/ENTERPRISE_V4_8_0_SUMMARY.md`.

### Interpreting the output

| Connector | Published RPM | Expected 429 rate when running at 50% | When running at 110% |
|-----------|---------------|--------------------------------------|----------------------|
| hubspot   | 6000          | 0%                                   | ~9%                  |
| salesforce| 1500          | 0%                                   | ~9%                  |
| stripe    | 6000          | 0%                                   | ~9%                  |
| github    | 5000          | 0%                                   | ~9%                  |
| gstn      | 100           | 0%                                   | ~9%                  |

`gstn` is the strictest — useful as a canary because the limit is so
low that even a small bug in the bucket math will be obvious.
