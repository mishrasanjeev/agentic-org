# AgenticOrg Local Docker Stress and Performance Report

Date: 2026-09-01

Scope: local workstation Docker only

Base commit: `2af012775be7ce3269c76340a93691eb59db1640`

Branch: `codex/stress-performance-20260901`

Implementation commit tested: `3fda4cbc747b6b5a96cb4885324065d04c760515`

## Executive result

The production API image, fresh migrations, dependency services, HTTP probes,
real Tesseract OCR, real Playwright Chromium, local SMTP capture, and the
signed voice runtime were exercised on Docker Desktop. The implementation now
bounds OCR and browser concurrency, sheds work with retryable errors rather
than spawning processes without limit, removes blocking SMTP/DNS work from the
async event loop, and coalesces readiness dependency probes.

The final Docker-network HTTP run completed 9,500 requests with zero client or
HTTP errors. The resource stress completed 12 OCR jobs and 8 Chromium jobs with
all jobs successful and a maximum of two expensive jobs active at once.

This is regression evidence from one workstation. It is not a production SLA,
Cloud Run capacity claim, or proof of external LLM, telephony, payment, or
merchant-system capacity.

## Exact-commit reproducibility rerun

After the fixes were committed, the complete local gate was repeated from a
new production image and a new isolated Compose project. The image was built
from implementation commit `3fda4cbc747b6b5a96cb4885324065d04c760515`
and had image ID
`sha256:5f764daf254180ca1136ad3ba09f289a4086225ce7132dadb8cd6df4e66969cd`.
PostgreSQL, Redis, MinIO, Mailpit, application data, and the voice-integration
database were fresh for this rerun.

### Repeated HTTP result

| Phase | Requests / concurrency | Throughput | p50 | p95 | p99 | Max | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|
| Liveness steady | 3,000 / 50 | 210.67 rps | 146.36 ms | 742.43 ms | 1,189.77 ms | 2,615.19 ms | 0 |
| Coalesced readiness | 1,500 / 50 | 188.84 rps | 160.57 ms | 773.18 ms | 1,311.33 ms | 2,779.75 ms | 0 |
| Liveness burst | 5,000 / 150 | 170.51 rps | 517.15 ms | 2,877.65 ms | 4,889.91 ms | 8,148.80 ms | 0 |

All 9,500 responses were HTTP 200. Readiness remained below the 1,000 ms
local p95 gate. The burst maximum shows substantial tail latency on this
shared workstation, so the result is a correctness and overload-safety gate,
not a latency objective.

### Repeated resource result

| Workload | Jobs | Limit / max active | Elapsed | Result |
|---|---:|---:|---:|---|
| Tesseract OCR | 12 | 2 / 2 | 72.815 s | 12 passed |
| Playwright Chromium | 8 | 2 / 2 | 4.508 s | 8 passed |

Sampled peak usage across the repeated resource run was 1,092.84% CPU,
319.8 MiB memory, and 164 PIDs. The CPU value is Docker's aggregate across
cores, not utilization of a single core. The gate held at two active jobs and
the container completed without an OOM or restart.

### Repeated dependency result

- PostgreSQL `pgbench`, scale 10, 20 clients, 4 threads, 15 seconds: 8,232
  transactions, zero failures, 36.380 ms average latency, and 546.07 TPS.
- Redis, 100,000 requests per operation with 50 clients: PING inline
  24,271.85 rps, PING multibulk 15,576.32 rps, SET 13,260.84 rps, and GET
  28,579.59 rps. Operation p50 was 0.455-1.087 ms.

These repeated dependency numbers are much lower than the quiet first-pass
baseline because unrelated Docker workloads were active. They are retained
as honest shared-host evidence. Both services remained correct and returned
zero benchmark failures; a quiet-host benchmark is still required for a
stable capacity baseline.

### Repeated end-to-end and regression gate

| Check | Repeated result |
|---|---|
| Production image build and `pip check` | Passed |
| Empty-database ORM bootstrap and Alembic migration to head | Passed twice, including dedicated voice database |
| HTTP stress | 9,500 passed, zero errors and zero non-200 responses |
| OCR, RPA, voice, and multichannel focused pack | 36 passed |
| Auth, email, sales, budget, and security pack | 302 passed; 42 existing test/deprecation warnings |
| Signed voice runtime with encrypted durable PostgreSQL state | 1 passed |
| Local Mailpit SMTP transport and capture | Passed; one local-only message captured |
| Tenant signup, JWT issuance, and authenticated agent listing | Passed; 20 seeded agents returned |
| Ruff on all changed Python files | Passed |
| Focused mypy check | Passed with third-party missing imports ignored |
| Compose rendering and `git diff --check` | Passed |
| API process stability | Zero restarts, no OOM, no traceback or HTTP 5xx log match |

The email transport used Mailpit on the workstation and did not deliver
externally. The signed voice test mocked provider transport and did not place
a paid call. The browser used synthetic in-memory HTML and made no external
navigation. No new runtime defect reproduced during this exact-commit rerun,
so no additional code change was made after the performance fixes.

## Test host and isolation

- Docker Desktop allocation: 16 CPUs and 31.08 GiB memory.
- AgenticOrg used an isolated Compose project with PostgreSQL, Redis, MinIO,
  Mailpit, and a production Dockerfile API image.
- Application migrations ran against fresh dedicated databases.
- HTTP was measured both through the Windows mapped port and on the Docker
  network. The Docker-network result is the final regression gate.
- Other unrelated Docker stacks were active on the shared workstation during
  parts of the run. Host-port tail latency was therefore noisy and is recorded
  as diagnostic evidence, not a capacity ceiling.
- No production endpoint, paid phone number, payment rail, merchant system, or
  external write target was used.

## Baseline findings

### HTTP before hardening

The initial one-worker production image had no HTTP errors in the baseline,
but burst tail latency was high:

| Phase | Requests / concurrency | Throughput | p50 | p95 | p99 | Errors |
|---|---:|---:|---:|---:|---:|---:|
| Liveness steady | 3,000 / 50 | 199.01 rps | 144.84 ms | 796.45 ms | 1,295.61 ms | 0 |
| DB/Redis readiness | 1,500 / 50 | 164.17 rps | 268.17 ms | 539.40 ms | 706.44 ms | 0 |
| Liveness burst | 5,000 / 150 | 281.80 rps | 278.22 ms | 1,603.28 ms | 4,678.10 ms | 0 |

Readiness created a Redis client for every request and performed DB and Redis
checks serially. Concurrent probes could amplify load on both dependencies.

### Unbounded expensive work

| Workload | Jobs | Elapsed | Sampled peak CPU | Sampled peak memory | Peak PIDs |
|---|---:|---:|---:|---:|---:|
| OCR, unbounded | 12 | 7.99 s | 1,853.73% | about 702 MiB | 72 |
| Chromium, unbounded | 8 | 0.927 s | 746.22% | about 601.4 MiB | 552 |

The short elapsed time hid unsafe resource fan-out. Multiple simultaneous OCR
uploads or RPA jobs could exhaust a smaller Cloud Run container.

### Dependency headroom

The isolated dependency services were not the first bottleneck:

- PostgreSQL `pgbench`, scale 10, 20 clients, 4 threads, 15 seconds:
  49,858 transactions, zero failures, 6.000 ms average latency, 3,320.35 TPS.
- Redis, 100,000 requests, 50 clients: PING 109,289.62 rps, SET 80,775.45
  rps, GET 63,856.96 rps; p50 was 0.215-0.327 ms.

These numbers describe the local containers only and do not project managed
database or network performance.

## Fixes implemented

1. Added reusable async admission control with bounded concurrency and queue
   deadlines.
2. Applied the gate to document extraction/OCR. Saturation returns HTTP 503,
   a stable `document_extraction_capacity_exhausted` code, `retryable: true`,
   and `Retry-After` without starting another extraction.
3. Applied the gate to browser RPA. Saturation returns a structured retryable
   `rpa_capacity_exhausted` result without launching Chromium.
4. Added environment-validated OCR and RPA capacity settings. Defaults are two
   active jobs per API worker process; one worker is the image default.
5. Reused a bounded Redis pool for health checks, added dependency timeouts,
   ran DB and Redis probes concurrently, and coalesced concurrent readiness
   probes into a one-second cache.
6. Closed shared health resources during application shutdown.
7. Moved synchronous email delivery and recipient-domain validation off the
   FastAPI event loop for signup, password reset, invitation, demo, sales, and
   budget-notification paths.
8. Corrected sales email state so a transport refusal is not marked `sent`.
9. Removed the hard-coded single worker flag from the Docker image. One worker
   remains the default, while an explicitly sized deployment can use
   `WEB_CONCURRENCY`.
10. Added a production-style performance Compose override and reproducible
    HTTP/resource stress harnesses.
11. Replaced stale, unverified GKE/Grafana capacity statements with measured
    local evidence and an explicit production staging gate.

## Final stress result

### HTTP through the Docker network

| Phase | Requests / concurrency | Throughput | p50 | p95 | p99 | Errors |
|---|---:|---:|---:|---:|---:|---:|
| Liveness steady | 3,000 / 50 | 128.77 rps | 241.33 ms | 1,189.14 ms | 1,806.43 ms | 0 |
| Coalesced readiness | 1,500 / 50 | 198.77 rps | 163.59 ms | 713.18 ms | 1,203.52 ms | 0 |
| Liveness burst | 5,000 / 150 | 220.28 rps | 400.02 ms | 2,154.47 ms | 3,809.16 ms | 0 |

The run passed the local readiness p95 threshold of 1,000 ms. Overall HTTP
throughput was lower than the earlier quiet-host baseline because unrelated
containers were active during the final run. The safety conclusion is based on
zero errors and bounded resources, not a claim that this noisy run is faster in
every percentile.

A simultaneous repeat through Docker Desktop's Windows host-port forwarding
did not pass: 2 of 5,000 burst requests ended in client-side
`RemoteProtocolError`, and readiness p95 was 1,739.13 ms. At that point the
workstation was also running unrelated application/model test stacks. The same
API and workload passed on the Docker network with zero errors. This isolates
the observed failure to the contended host/port-forwarding path but does not
erase it; a quiet-host repeat remains a release-engineering follow-up.

### Bounded OCR and Chromium

| Workload | Jobs | Limit / max active | Elapsed | Result |
|---|---:|---:|---:|---|
| Tesseract OCR | 12 | 2 / 2 | 70.281 s | 12 passed |
| Playwright Chromium | 8 | 2 / 2 | 5.617 s | 8 passed |

Across the bounded run, sampled peak memory was about 305.1 MiB and peak PIDs
were 170. During the OCR phase, sampled memory stayed at or below about 171.6
MiB and PIDs at or below 28. Compared with unbounded execution, this reduced
sampled OCR memory by about 75% and browser peak PIDs by about 69%.

Queueing intentionally increases completion time. With the API defaults, work
that cannot enter within its deadline is rejected for retry instead of waiting
indefinitely or exhausting the container.

## End-to-end verification

| Check | Result |
|---|---|
| Production Docker image build and dependency check | Passed |
| Fresh migration from empty PostgreSQL database to head | Passed |
| API liveness and DB/Redis readiness | Passed |
| Tenant signup, JWT issuance, and authenticated agent listing | Passed |
| HTTP stress, 9,500 requests | Passed, zero errors |
| Runtime/voice/RPA/OCR regression pack | 36 passed |
| Auth, email, demo, and negative-case unit pack | 177 passed |
| Signed voice runtime with durable PostgreSQL state | 1 passed |
| Real synthetic-image Tesseract OCR | Passed |
| Real synthetic-page Playwright Chromium | Passed |
| Local email through Mailpit SMTP capture | Passed |
| Compose configuration rendering | Passed |
| Ruff and focused type/regression checks | Passed |

The voice integration used signed synthetic events and local persistence. No
paid call was placed. Email was captured by Mailpit and never left the
workstation. Browser RPA used `page.set_content` with synthetic HTML and no
external navigation.

## Remaining limits and next production gate

- Run a dedicated, quiet-host repeat before using workstation numbers as a
  long-term regression baseline.
- Run a production-equivalent staging soak with the actual Cloud Run CPU,
  memory, instance concurrency, autoscaling limits, Cloud SQL pool budget, and
  Redis tier.
- Add authenticated mixed-route load with realistic tenant cardinality and
  payload sizes. The public health test does not represent the whole API.
- Measure long scanned PDFs and multilingual OCR separately; the current
  resource test uses one-page synthetic PNGs.
- Measure websocket audio streaming and STT/TTS providers with approved test
  credentials and spend caps. The local run proves control/persistence paths,
  not external provider throughput.
- Exercise real RPA targets only in an approved sandbox with deterministic
  test accounts. Browser resource safety is proven here; third-party site
  capacity is not.
- Add queue-wait and rejection counters to production metrics before changing
  the default OCR/RPA limits.

## Reproduction commands

The supported commands are maintained in [Performance and Load
Testing](../PERFORMANCE.md) and [the load-test README](../../tests/load/README.md).
