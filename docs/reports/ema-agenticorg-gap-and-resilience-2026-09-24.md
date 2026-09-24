# AgenticOrg vs Ema: evidence, product gaps, and resilience work

Date: 2026-09-24. Scope: Ema's public site and v2 docs versus AgenticOrg
`origin/main` at `46e8fe6c0b6f33279e5246e28239fbd7d3a18cf2`. This is not a
head-to-head benchmark or an audit of Ema's private deployment. "Implemented"
means present in source, not that every tenant has credentials or that a
production SLO has been measured. "Claimed" means Ema says it, not that an
independent test has verified it.

## Verdict

AgenticOrg cannot honestly be called universally superior. It has valuable
code-visible breadth, an inspectable Apache-2.0 codebase, Docker/Cloud Run
deployment paths, India-oriented finance/commerce adapters, and explicit
tenant-governed external actions. Ema's published product has a more coherent
AI-employee builder/integration/evaluation story and claims broader prebuilt
integration and model coverage. Neither public source establishes a matched
cost, quality, uptime, security, or throughput win. Ema advertises on-prem and
air-gapped delivery, while its v2 admin docs describe a managed cloud product;
the available deployment mode must be confirmed in a sales/technical evaluation.

## Evidence used

- Ema [homepage](https://www.ema.ai/) (250+ integration, 100+ model,
  enterprise scale, deployment and security claims).
- Ema [Integrations Hub](https://builder.ema.ai/builder/v2/integrations-data/integrations-hub),
  [data connectors](https://builder.ema.ai/builder/v2/integrations-data/data-connectors),
  [testing/operations](https://builder.ema.ai/builder/v2/testing-operations),
  [debug logs](https://builder.ema.ai/builder/v2/testing-operations/debug-logs),
  [EmaFusion](https://builder.ema.ai/builder/v2/emafusion/how-it-works), and
  [administration](https://builder.ema.ai/builder/v2/administration).
- AgenticOrg `README.md`, `docs/PRODUCT_STATUS.md`, `docs/PERFORMANCE.md`,
  `docs/reports/agenticorg-local-docker-stress-performance-2026-09-01.md`,
  `core/llm/router.py`, `core/live_feed.py`, `api/websocket/feed.py`,
  `ui/src/lib/websocket.ts`, `docs/BACKUP_AND_DR.md`, and
  `infra/terraform/multi_region/README.md`.

## Product comparison and delivery gaps

| Area | AgenticOrg evidence | Ema public evidence | Gap / next acceptance test |
| --- | --- | --- | --- |
| Agent builder and lifecycle | Tenant-created agents, workflow runs, schedules, approvals, UI, SDK (`docs/PRODUCT_STATUS.md`). | Plain-language AI employee builder, typed workflow nodes, versioning, HITL and publishing. | **P1:** run the same 10 real HR/IT/finance tasks with two new users on both products; measure time to first safe run, publish/rollback, error recovery, and task success. UI parity is unmeasured. |
| Integrations | Native registry and optional gateways, with tenant credentials and scopes. Registry entries are not active connections. | Homepage claims 250+ integrations; docs show install, credential, function execution, MCP and audit surfaces. | **P0:** publish a machine-generated integration readiness matrix: configured, authenticated, scoped, contract-tested, last successful sync, error budget. Verify top 20 customer integrations end to end; do not equate names with working installs. |
| Knowledge freshness | OCR/search and connector-specific paths (`docs/PRODUCT_STATUS.md`). | Docs describe scheduled/incremental background document sync with retries. | **P1:** common connector sync contract with checkpoint, deletion, ACL propagation, lag/last-success, backfill and dead-letter tests; measure freshness for each supported source. |
| Models | Gemini/Claude/OpenAI support and primary/fallback in `core/llm/router.py`; local/test modes. | EmaFusion docs describe task routing and model controls; homepage claims 100+ models and cost savings. | **P1:** provider-health-aware routing with bounded deadlines, safe fallback by error class, per-tenant budget reservation and a measured eval/cost corpus. No claim of cheaper or more accurate until matched tests. |
| Evaluation and debugging | Evaluation API/tests, audit and observability hooks; exact experience varies by path. | Docs expose dataset evaluations and per-step prompts, tools, routing and errors. | **P1:** one versioned release gate showing dataset score, latency, cost, failure class, tool side effects and rollback comparison per agent version. Check PII-safe trace retention. |
| Channels and voice | Web, MCP/A2A, signed Twilio STT/TTS, provider-gated other channels. | Website mentions Teams, Slack and voice; v2 docs describe channel configuration. | **P1:** real credentialed channel certification matrix and callbacks; test consent, delivery, duplicate/replay, failover and quality. Do not call a source adapter an approved marketplace integration. |
| Governance | Tenant/role boundaries, scoped tools, approval and audit paths. | Ema docs describe RBAC, per-integration controls, PII governance and audit. | **P0:** prove policy coverage with cross-tenant adversarial tests for each side-effecting tool and connector, plus revocation-on-running-session tests. Certification claims require independent evidence. |
| Commerce/India differentiation | Shopify/OACP and Plural/Pine capability verification are bounded, with provider and merchant systems authoritative. | Ema's public pages center HR/IT/finance; no comparable OACP flow is established from those pages. | **P1:** use this as a differentiated vertical only after merchant/provider-approved end-to-end purchase evidence, not as proof that generic agent features are superior. |
| Deployment and resilience | Docker and Cloud Run tooling; one local Docker stress report. DR Terraform is a commented-out GKE scaffold. | Homepage markets on-prem/air-gap and scale; v2 admin docs say managed cloud only. | **P0:** document which Ema deployment offer is actually purchasable; for AgenticOrg, run a Cloud Run-equivalent soak, DB/Redis outage injection and measured restore before promising availability/RPO/RTO. |

## Scale and resilience findings

These are prioritized by failure impact, not by how easy they are to document.

1. **P0, fixed in this change:** `api/websocket/feed.py` sent to tenant sockets
   sequentially without a deadline. One stalled client could block peers and
   a Redis listener. Bounded parallel batches and a per-socket deadline now
   isolate it; tests cover slow and 100-client fanout. This is not a production
   throughput benchmark.
2. **P0, fixed in this change:** `core/live_feed.py` ended its Redis Pub/Sub
   listener permanently after a transient exception. It now reconnects with
   bounded backoff; a disconnect/reconnect test exercises delivery recovery.
   Pub/Sub remains at-most-once; clients must use the durable sequence API.
3. **P0, fixed in this change:** `ui/src/lib/websocket.ts` fetched only one
   catch-up page and could deliver a newer live sequence before missed events.
   It now buffers gaps and paginates, resets the cursor on tenant switches,
   and caps pending memory. Browser tests cover these paths. If durable event
   history is missing/pruned, an explicit gap/reload UX is still needed.
4. **P0, corrected documentation:** `docs/BACKUP_AND_DR.md` previously
   described a live cross-region replica, verified RPO/RTO and quarterly drills
   not supported by checked-in evidence. The multi-region Terraform README
   explicitly calls itself a disabled scaffold; two referenced restore scripts
   and the drill directory are absent. The DR page now states targets and
   verification gates instead of claiming active controls.
5. **P0, open:** Redis Pub/Sub opens a subscription per active tenant in
   `core/live_feed.py`. Connection count grows with active tenants; test and
   redesign (shared multiplexed subscriber, streams, or partitioned broker)
   before raising tenant/socket limits. Measure connection count, reconnect
   storm, lag and catch-up load at target scale.
6. **P0, open:** feed append takes a per-tenant advisory lock and computes
   `max(sequence)+1` in `core/live_feed.py`. This serializes a hot tenant and
   can become a DB bottleneck. Benchmark 1/10/100 hot tenants; consider a
   per-tenant sequence row or DB sequence allocator while preserving order.
7. **P0, open:** `_add_connection` holds the global `_connections_lock`
   across Redis subscription setup in `api/websocket/feed.py`. An unhealthy
   broker can stall unrelated tenants' connection management. Move network
   setup outside the global lock with a per-tenant single-flight state.
8. **P0, open:** existing WebSocket sessions authenticate at handshake, not
   periodically (`api/websocket/feed.py`). A revoked session may remain on an
   established feed until disconnect. Add bounded periodic revalidation or
   centrally distributed revocation and test kill-switch latency.
9. **P1, open:** the Sep 1 Docker report shows 9,500 local HTTP requests
   without errors but burst p95 around 2.9 s and p99 around 4.9 s, on a shared
   workstation. It does not exercise production Cloud Run autoscaling, real
   providers, multi-tenant skew, broker outages, or long-duration soak.
10. **P1, open:** OCR/RPA gates are per process (`docs/PERFORMANCE.md`). Total
    concurrency rises with Uvicorn workers and Cloud Run instances; add a
    distributed admission budget if paid/browser-intensive load grows. Size
    Cloud SQL pool from instances x workers x (pool+overflow), with headroom.
11. **P1, open:** model fallback in `core/llm/router.py` catches broad
    exceptions and has no visible end-to-end request deadline at this layer.
    Separate transient transport failures from bad credentials/policy/budget,
    avoid runaway latency, and prove fallback via fault injection.
12. **P1, open:** a persisted feed event followed by Redis publish failure
    reaches local sockets but not other pods until catch-up. Add a durable
    publish outbox or scheduled replay if live cross-pod latency has an SLO;
    test broker outage/recovery and duplicate suppression.

## Execution order and gates

1. Land the feed fixes and browser/server regression tests here. No production
   rollout is implied by this report.
2. Resolve P0 connection single-flight and session revocation in separate
   small runtime PRs; use Redis-offline and cross-tenant fault injection.
3. Build a reproducible workload covering hot tenants, 10k concurrent idle
   sockets, burst events, 100+ tenants, 24-hour soak, DB/Redis restarts,
   OCR/RPA saturation and LLM 429/timeout. Record p50/p95/p99, delivery gaps,
   connection counts, lag, DB pool wait, cost and data loss. Use approved test
   systems only; no paid calls or merchant/provider load without approval.
4. Verify actual backup/PITR/replica configuration and run an isolated restore
   drill. Change external RTO/RPO claims only after measured evidence.
5. Select three buyer-valued workflows and run a blinded AgenticOrg/Ema trial
   with identical tasks, datasets, models where possible, human reviewers,
   acceptance criteria and full cost. Do not publish a superiority claim from
   vendor marketing or source inspection alone.

**Release gate:** no severity-1 security or data-loss findings; all tenant
isolation and reconnection tests green; measured soak and restore meet agreed
SLO/RPO/RTO; rollback and owner are documented. Failures block the specific
release, not unrelated engineering work.
