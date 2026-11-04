## Inspect run timelines and workload

Run timelines live in **Insights**: the **Runs** explorer (`/dashboard/insights/explore/runs`) lists recorded runs, and each run's page shows its span waterfall, the first failure and its records. The administrator-only **Monitoring** screen (`/dashboard/monitoring`, which replaces the Observability screen; the old address redirects there) has Workload, which summarizes review deadlines and selected tenant outcomes. These are read-only views. Where the deployment turns on synthetic checks (`AGENTICORG_SYNTHETIC_CHECKS_ENABLED`, off by default), Monitoring has a **Checks** tab that lists the tenant's scheduled probes with their latest results and lets an administrator add or run one. Where the debugging console is on (`AGENTICORG_RUNTIME_DEBUG_CONSOLE_ENABLED`, off by default), an administrator can open the debugger on a run's Insights page (for a run recorded while it was on) to read its checkpoints one step at a time. They help an operator investigate; they do not replace the audit log, provider billing, a system-of-record check or an incident process.

```flow
Open Insights | Sign in as an authorized administrator and select the intended company.
Check recording state | Read the tracing/timeline status before interpreting an empty run list.
Find a run | Filter the Runs explorer by agent or status, then open one run to inspect parent/child spans and events.
Compare workload | On Monitoring, review pending/overdue reviews and the one-hour run, model-call and guardrail summaries.
Follow the correlation | Use run/correlation identifiers to inspect audit details and the downstream system separately.
```

## Traces and timelines

The Runs explorer pages through the period's runs and filters by agent, status and other columns. A run's page displays its stored spans in relative time order, including duration, parent relationship, status and available attributes/events such as model decisions, tool outcomes, guardrail outcomes and exceptions. Data is limited to what that deployment records; missing spans do not prove that no work occurred.

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
