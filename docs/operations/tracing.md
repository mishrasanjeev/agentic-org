# Distributed tracing and correlation ids

> **Status: wired, off by default, no collector deployed.** The tracer, the spans at every
> call site, the correlation ids, the run timelines and the console page are in place and
> tested. Nothing exports anywhere until a deployment sets `AGENTICORG_TRACING_ENABLED=true` and
> names an OTLP endpoint, and nothing is stored for the console until it also sets
> `AGENTICORG_TRACING_TIMELINE_ENABLED=true`; no environment of this platform has done either
> yet. See [What is not here yet](#what-is-not-here-yet).

AgenticOrg emits OpenTelemetry spans around the operations an operator or an auditor needs to
follow end to end: the API request, the task a worker picks up, the agent run inside it, every
model call and tool call that run makes, and every knowledge search. One trace id links the
request, its log lines, its spans and the signed audit rows written along the way.

## The switch

| Setting | Default | Meaning |
| --- | --- | --- |
| `AGENTICORG_TRACING_ENABLED` | `false` | Install a tracer at startup and open spans at the call sites. Off, every helper is a no-op that touches no tracer. |
| `AGENTICORG_TRACING_PROTOCOL` | `http/protobuf` | How spans travel to the collector: `http/protobuf` or `grpc`. |
| `AGENTICORG_TRACING_SAMPLE_RATIO` | `1.0` | The share of new traces recorded (parent-based: a sampled request keeps all its children). |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty | The collector. For `http/protobuf` the `/v1/traces` path is appended when the URL names none. |
| `OTEL_SERVICE_NAME` | `agenticorg-core` | The `service.name` resource attribute; `deployment.environment` carries `AGENTICORG_ENV`. |

Collector credentials (an authorisation header, say) belong in `OTEL_EXPORTER_OTLP_HEADERS`,
which the exporter reads itself; they are never a setting of this platform and never logged.

**A strict runtime refuses to start with tracing on and no endpoint.** Spans that have nowhere
to go would be a silent gap in the evidence trail, so the API's lifespan and every worker process
stop with a `TracingError` instead. A relaxed runtime (local, test) records the spans without an
exporter and logs `tracing_without_exporter`.

The tracer is installed in the API's lifespan (`api/main.py`) and in every Celery worker process
(`worker_process_init` in `core/tasks/celery_app.py`), and shut down, flushing what is pending, at
the matching shutdown.

## The spans

| Span | Kind | Opened by | Attributes |
| --- | --- | --- | --- |
| `agenticorg.http.request` | SERVER | the request-id middleware, around every API request | `http.request.method`, `url.path`, `request.id`, `http.response.status_code` |
| `agenticorg.task.run` | CONSUMER | the worker, around every Celery task | `task.name`, `task.id`, `request.id`, `task.state` |
| `agenticorg.agent.run` / `agenticorg.agent.resume` | INTERNAL | the runner, around an agent graph's execution | `tenant.ref`, `agent.id`, `agent.type`, `domain`, `gateway.correlation_id`, `gateway.gated`, `llm.provider`, `llm.model`, `agent.run.status`, `agent.run.error_code`, `llm.tokens` |
| `agenticorg.agent.reason` | CLIENT | the reasoning node, around every model call | `tenant.ref`, `llm.provider`, `llm.model`, `agent.id`, `gateway.correlation_id`, `gateway.admission_wait_ms`, `llm.input_tokens`, `llm.output_tokens`, `llm.latency_ms` |
| `agenticorg.tool.call` | CLIENT | the connector dispatch boundary, around every tool call | `tenant.ref`, `tool.name`, `connector.id`, `agent.id`, `tool.outcome` (`ok`, `guardrail_blocked`, `operator_override`, `action_contained`, `error`) |
| `agenticorg.knowledge.search` | INTERNAL | the knowledge search route | `tenant.ref`, `search.top_k`, `search.results`, `search.withheld` |

Two governance decisions are recorded as events on whichever span is in progress rather than as
spans of their own: `model_gateway.decision` (the correlation id, use case, policies, provider,
model and reason the gateway chose) and `guardrail.outcome` (the correlation id, stage, rule,
detector, action, whether it applied and the mode).

A span records identifiers, counts and outcomes, never prompts, model answers, tool arguments
or retrieved text. The content a run handles stays in the platform's own stores under its
tenant's residency and retention rules.

### Tenant references and residency

A span never carries a tenant identifier. `tenant.ref` is a keyed reference (an HMAC of the
identifier under the platform's audit key, cut to sixteen characters): the same tenant always
maps to the same reference, so a collector can group a tenant's spans, and nobody can map a
reference back to the tenant.

Residency follows the rule the LangSmith redaction hook applies (`observability/trace_redaction.py`):

- with deployment-wide enforcement (`AGENTICORG_RESIDENCY_ENFORCE`) no exporter is installed at
  all; the collector is an external destination with no attestation path, so spans stay in the
  process (`tracing_export_withheld_residency` is logged at startup);
- with tenant-scoped enforcement, a span of a tenant that enforces residency, or whose
  enforcement was never read in this process, is marked at creation and withheld from export;
  a span that names no tenant (the HTTP request, the task) is withheld while some tenant in the
  process enforces.

The withholding happens in the exporter wrapper before anything leaves the process; the spans
still exist in the process for the platform's own use.

## Correlation ids

Three ids travel together:

- **`request_id`** (`X-Request-ID`, or one minted by the middleware) is the id a client sees in
  the response and in every log line of the request; a worker binds the publisher's request id
  for the task it runs (`core/tasks/celery_app.py`).
- **`trace_id`** is the OpenTelemetry trace. While a span records, the log context carries it
  under `trace_id`, so a log line names both ids. An incoming `traceparent` header continues a
  trace that started in another service; the API never starts a new one when a caller supplied
  one. A task published while a span is in progress carries `traceparent` in its message headers
  and the worker's task span continues that trace.
- **The model gateway's correlation id** names one routing decision and is on the run's spans,
  the model-call records and the guardrail outcomes of that run.

Signed audit rows record the trace in their `trace_id` column: the rows the model gateway,
operator overrides, residency attestations and guardrails write take
`observability.tracing.audit_trace_id()`, which is the trace in progress, else the correlation
id the writer already holds, else the request id. The column is part of the signed payload, so
the link between an audit row and its trace is tamper-evident like the rest of the row.

## Reading a trace

With a collector attached, a single agent run reads as one tree:

```
agenticorg.http.request  POST /api/v1/agents/{id}/run           request.id=...
  agenticorg.agent.run   agent.id=... gateway.correlation_id=... agent.run.status=completed
    model_gateway.decision (event)      provider=... model=... reason=...
    agenticorg.agent.reason             llm.input_tokens=... llm.output_tokens=...
      guardrail.outcome (event)         stage=input detector=... action=flag
    agenticorg.tool.call                tool.name=... tool.outcome=ok
    agenticorg.agent.reason             ...
```

The audit rows of that run carry the same trace id, and `GET /api/v1/audit` returns it with each
row, so an auditor can go from a row to the trace and back.

## Run timelines and the console

A collector shows a trace; the console needs the waterfall of one run without one. With
`AGENTICORG_TRACING_TIMELINE_ENABLED=true` (off by default, and nothing while tracing itself is
off) a span processor keeps the finished spans that describe a run (the run, each model call,
tool call and knowledge search; never the HTTP request or the task span) in memory per run, and
the runner stores that run's spans in the tenant-scoped `run_spans` table when the run ends, on
the run's own event loop through the run's own tenant session. A run is named by its root span
(`run.id`, which the runner binds and every span opened inside the run carries), not by the
trace: two runs that share one trace (parallel agents under one task, a request continuing a
caller's `traceparent`) never mix, and a span opened outside any run is never kept. A run
nothing stores ages out of memory after thirty minutes, and the buffer never holds more than
twenty thousand spans.

| Setting | Default | Meaning |
| --- | --- | --- |
| `AGENTICORG_TRACING_TIMELINE_ENABLED` | `false` | Keep each run's spans and store them when the run ends. |
| `AGENTICORG_TRACING_TIMELINE_RETENTION_DAYS` | `30` | The daily task `core.tasks.timeline_tasks.prune_run_spans` drops older rows, deleted tenants included. |

A stored row holds the span's identifiers, kind, status, timings, the attributes listed above
and the governance events; an exception event keeps only the exception's type. Nothing stored
is a prompt, an answer, a tool argument or a retrieved text.

The endpoints (admin only):

| Endpoint | Returns |
| --- | --- |
| `GET /api/v1/observability/runs?agent_id=&limit=` | the newest stored runs, one entry per run (status, duration, provider, model, tokens, correlation id, trace id), and `enabled`, whether runs are being recorded at all |
| `GET /api/v1/observability/runs/{run_id}` | every stored span of one run (named by its root span id) with its offset from the run's start and its duration: the waterfall |
| `GET /api/v1/observability/workload` | the tenant's pending reviews with the soonest deadline and the overdue count, and the last hour's run, model-call and guardrail outcomes; each part reports its own `error` when it cannot be read |

The endpoints are tenant administrators' reads, and the console page is shown to administrators
only. Everything answered is the tenant's own: the task queues are shared by every tenant, so
their depths are not part of a tenant's workload.

The run response of `POST /api/v1/agents/{id}/run` carries `trace_id` when tracing is on, so a
client can open the run's waterfall directly.

The console page `/dashboard/observability` (administrators) has two views: **Traces**, the
stored runs and the waterfall of the selected one (model, tool and retrieval spans with their
durations, the model gateway's decision and every guardrail outcome as events on the span they
belong to), and **Workload**, the reviews with a countdown to the soonest deadline and the last
hour's outcomes, refreshed every fifteen seconds.

## Streaming latency

Two timings a call's total duration does not show (`observability/streaming.py`).

**Time to first token.** Behind `AGENTICORG_MODEL_STREAM_TIMING_ENABLED` (off by default). On,
the reasoning node reads each model answer as a stream, notes when the first chunk carrying text
or the start of a tool call arrives, and puts the chunks back together into the same message a
plain call returns (content, tool calls and token usage included). The time is observed in
`agenticorg_model_first_token_seconds{provider,model}` and set on the model call's span as
`llm.first_token_ms`. A model that does not stream yields its whole answer as one chunk, so its
first-token time equals its duration. A stream that fails raises exactly as a failed call does,
and one that yields nothing falls back to the plain call. Off, the call is made as before and no
first-token time is reported. The direct router (`core/llm/router.py`) is not timed this way.

**Task queue wait.** Always on. A published background task is stamped with its publish time
(not when the publisher asked for a later start with an eta or a countdown); when a worker
starts it, the wait is observed in `agenticorg_task_queue_wait_seconds{queue}` and set on the
task's span as `task.queue_wait_ms`. Together with the model admission wait
(`agenticorg_model_admission_wait_seconds`) this covers the time work spends waiting rather
than running. A wait that is negative or longer than a day is discarded as a clock problem.

Both are durations with provider, model or queue labels only; neither carries content or a
tenant.

## Tests

`tests/unit/observability/test_timeline.py` covers the processor keeping only the catalogue
spans, the cap and the age-out, the stored row (parent, status, offsets, the exception event
reduced to its type), storing at the end of a run and never raising, the reads and the prune.
`tests/unit/observability/test_observability_api.py` covers the admin-only routes, the
`enabled` flag, the 404 for an unknown trace and the workload parts reported on their own.

`tests/unit/observability/test_streaming.py` covers the first-token timing (off by default,
reassembly of text, tool calls and token usage, a model that does not stream, an empty and a
failing stream), its metric and the task queue wait (the stamp, scheduled tasks, unusable stamps,
the metric and the Celery signals).

`tests/unit/observability/test_tracing.py` covers: the helpers as no-ops while tracing is off;
spans with their attributes, events, nested trace ids, the log-context binding and error status
while it is on; the settings validation and the strict-runtime refusal; the request middleware
continuing an incoming `traceparent` and stamping the response status; the task headers carrying
the trace context and the worker continuing it; the tool-call span's outcome; the runner's span
on a blocked resume; and the audit rows' trace id with and without a trace in progress.

## What is not here yet

- **No collector.** No environment names an OTLP endpoint; the switch stays off everywhere. The
  deployment reference records how to turn it on when a collector exists.
- **No trace-based alerts.** Latency and error alerts still come from the Prometheus
  instruments (`docs/operations/metrics.md`); the console shows the picture, it does not page.
- **No automatic HTTP client instrumentation.** A connector's outbound HTTP calls are inside the
  `agenticorg.tool.call` span but not spans of their own.
- **No platform-wide view.** Queue depths and other deployment-wide signals need an operator
  surface outside tenant scope; the tenant console shows only the tenant's own workload.
- **Workflow steps** are not yet spans; the original span catalogue in
  `observability/tracing.py` (workflow, step, hitl, auth, shadow) keeps its constructors for that
  work.
