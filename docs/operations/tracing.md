# Distributed tracing and correlation ids

> **Status: wired, off by default, no collector deployed.** The tracer, the spans at every
> call site and the correlation ids are in place and tested. Nothing exports anywhere until a
> deployment sets `AGENTICORG_TRACING_ENABLED=true` and names an OTLP endpoint, and no
> environment of this platform has done that yet. See [What is not here yet](#what-is-not-here-yet).

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

## Tests

`tests/unit/observability/test_tracing.py` covers: the helpers as no-ops while tracing is off;
spans with their attributes, events, nested trace ids, the log-context binding and error status
while it is on; the settings validation and the strict-runtime refusal; the request middleware
continuing an incoming `traceparent` and stamping the response status; the task headers carrying
the trace context and the worker continuing it; the tool-call span's outcome; the runner's span
on a blocked resume; and the audit rows' trace id with and without a trace in progress.

## What is not here yet

- **No collector.** No environment names an OTLP endpoint; the switch stays off everywhere. The
  deployment reference records how to turn it on when a collector exists.
- **No dashboards or trace-based alerts.** Latency and error alerts still come from the
  Prometheus instruments (`docs/operations/metrics.md`).
- **No automatic HTTP client instrumentation.** A connector's outbound HTTP calls are inside the
  `agenticorg.tool.call` span but not spans of their own.
- **Workflow steps** are not yet spans; the original span catalogue in
  `observability/tracing.py` (workflow, step, hitl, auth, shadow) keeps its constructors for that
  work.
