## Inspect run timelines and workload

The administrator-only **Observability** screen (`/dashboard/observability`) has two views: Traces, which lists recent instrumented runs and a span waterfall for one run, and Workload, which summarizes review deadlines and selected tenant outcomes. These are read-only views. They help an operator investigate; they do not replace the audit log, provider billing, a system-of-record check or an incident process.

```flow
Open Observability | Sign in as an authorized administrator and select the intended company.
Check recording state | Read the tracing/timeline status before interpreting an empty run list.
Find a run | Filter recent runs by agent, then open one run to inspect parent/child spans and events.
Compare workload | Review pending/overdue reviews and the one-hour run, model-call and guardrail summaries.
Follow the correlation | Use run/correlation identifiers to inspect audit details and the downstream system separately.
```

## Traces and timelines

The list requests up to 50 recent stored runs and can filter by agent ID. A run detail displays its stored spans in relative time order, including duration, parent relationship, status and available attributes/events such as model decisions, tool outcomes, guardrail outcomes and exceptions. Data is limited to what that deployment records; missing spans do not prove that no work occurred.

Before a platform owner changes `AGENTICORG_TRACING_ENABLED` or `AGENTICORG_TRACING_TIMELINE_ENABLED`, agree on an approved OTLP endpoint and protocol, sampling, data classification, storage and retention. Both controls default to off; strict production-like environments require a valid exporter endpoint when tracing is enabled. Confirm the effective retention setting with the platform owner before relying on historical timelines. Never send sensitive payloads to an unapproved telemetry collector.

## Workload view

Workload refreshes about every 15 seconds. Its run, model-call and guardrail summaries cover the most recent hour. Review counts and deadlines come from the tenant's pending human-review queue. Each panel can report unavailable data independently; a dash or an error means unknown/unavailable, not zero. Model-call summaries depend on model-gateway record capture, and guardrail block/transform counts depend on persisted guardrail audit outcomes.

Use the panel's stated time window and generated time when comparing it with another report. This is not a durable event stream, full audit export, cross-tenant dashboard or service-level guarantee. Do not use it as the sole evidence for a financial, compliance or customer-impact decision.

## A practical investigation

1. Capture the run ID, correlation ID, company, agent and approximate time.
2. Check whether tracing and timeline storage were enabled during that interval.
3. Follow span status, duration and error events; identify the earliest failing dependency.
4. Compare guardrail and model-gateway decisions with the audit view.
5. Confirm any external action with its authoritative system (for example, the provider or connected business application).
6. Record the safe error code, deployed commit and remediation owner; do not attach credentials or full customer payloads.

If the view is empty, first verify permissions, selected company, feature configuration, retention window and whether the run path emits timeline spans. See [Audit and monitoring](/docs/audit-and-monitoring), [Model Gateway](/docs/model-gateway), [Guardrails](/docs/guardrails) and [Security and data](/docs/security-and-data).
