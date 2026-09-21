# Metrics, alerts and the dashboard

> **Status: not yet carrying data.** The endpoint, the instruments and the alert definitions are
> in place; the collector that would scrape them is not deployed, so no sample has yet travelled
> the path from a process to a notification. Every alert described below is defined and tested,
> and none of them can fire yet. See [What is not here yet](#what-is-not-here-yet).

AgenticOrg defines Prometheus instruments throughout `core/` and `api/`. Until this change
nothing read them: there was no endpoint, so every counter lived and died inside a container
(FINDINGS A-56). This page describes the read path and the alerts built on it.

## The endpoint

Every service - the API, the Celery worker, the beat scheduler - serves the registry at
`GET /metrics` on `METRICS_PORT` (default `9090`).

It is deliberately **not** on the port the service serves traffic on. Cloud Run routes only
`$PORT`, so a second port has no external surface: the collector that runs beside the process
reaches it over the loopback interface, there is no credential to manage and no route that a
careless entry in the authentication middleware's public-path list could expose. The process
refuses to start the listener if `METRICS_PORT` is set to the routed port, and logs the refusal;
it never takes the service down for it, because telemetry that cannot start must not stop the API
from serving.

### Multiple processes and multiple instances

The Celery worker runs Celery's prefork pool: tasks execute in forked children, and a counter a
child increments is invisible to the parent that serves the endpoint. The worker therefore sets
`PROMETHEUS_MULTIPROC_DIR`, every process writes its samples there, the exporter merges them, and
a child's gauges are dropped when Celery retires it. The API runs one uvicorn process per
container today and does not need this, but the same switch is wired in, so adding `--workers`
later cannot silently start under-reporting instead.

Nothing aggregates across instances in the process. Instances come and go with autoscaling; each
exports its own counters, the collector attaches the instance identity, and aggregation happens in
the query.

## The alerts

`monitoring/prometheus/agenticorg-alerts.yml` holds the six alerts PRD §10 asks for plus one
companion. `infra/terraform/monitoring/alerts.tf` reads *that file* to create the Cloud Monitoring
policies, so there is one definition rather than two that drift.

Two conventions are enforced by `scripts/check_alert_rules.py` in CI:

1. **Never read a raw counter.** Instances scale to zero, so a counter's value is an accident of
   which instances happen to be alive. Counters are read through `rate()` or `increase()` over a
   window, summed across instances. Gauges are levels and may be read directly.
2. **Every alert has a `for`.** An alert that fires on a single evaluation fires on a deploy.

`promtool check rules` and `promtool test rules` run in CI over committed fixtures. The fixtures
are not decoration: two expressions in this file were wrong when first written and the fixtures
are what showed it - a single cap exhaustion whose `rate()` window had closed before the `for`
elapsed, so it never fired, and an alert whose selector matched nothing in exactly the case it
existed to detect (see below).

### denial-rate spike

`agenticorg_grant_enforcement_denials_total`. Authorization fails closed, so denials are normal; a
step change is not. Usually a grant, a scope or a policy changed and work that used to be allowed
is now refused. Start with what changed, not with the caller.

### decision-dwell collapse

`agenticorg_case_decision_dwell_seconds{dwell_source="server"}`. This is the rubber-stamping
alert, and the label is the whole point. The console also measures dwell
(`agenticorg_case_console_dwell_seconds`), but that is render-to-submit in a browser: advisory
telemetry that constrains nobody. The authoritative figure is measured by the issuer's own
approval page and returned on the decision request; AgenticOrg records it once, when the decision
is recorded and the grants are consumed. The issuer is never polled for it.

If more than half of approvals are being submitted inside fifteen seconds, over six hours, with
enough decisions for that to mean something, the memo is not being read.

### authoritative dwell missing

The companion. Cases are being decided and no issuer-measured dwell is arriving, which means the
alert above has gone blind - it cannot detect this itself, because no samples produce no ratio.
"Dwell collapsed" and "we stopped receiving dwell" are different incidents and the silent one is
the dangerous one. The expression ends in `or vector(0)` on purpose: in the worst case the series
does not exist at all, and without it an empty selector would make this alert quietly evaluate to
nothing, which is the failure it exists to catch.

### spend-cap exhaustion

`agenticorg_budget_cap_events_total{outcome="exhausted"}`. Budget evaluation crossed a cap rather
than a warning point; work under that budget is refused until the cap is raised or the period
rolls over.

### provider error rate

`agenticorg_provider_calls_total`. More than one call in ten to a verification provider failing,
with a floor so a single failure in an idle period does not fire it. Cases stall on failed
provider calls.

### dead-letter growth

`agenticorg_case_push_dead_letters_total` and `agenticorg_case_push_dead_letter_backlog`. Both
halves matter: the rate says events are being dead-lettered now, the gauge says a backlog is
sitting unreplayed even when nothing new has failed. Events are kept for replay, but nobody
downstream sees the case until they are.

### chain-verification failure

`agenticorg_chain_verifications_total{outcome="failed"}`. A stored passage or a promotion-history
chain no longer matches the digest recorded for it. This is never transient: one occurrence is the
incident. The content is refused rather than shown, so nothing unverified reaches a reviewer, but
the stored copy needs investigating.

## Keeping the alerts honest

`observability/alert_contract.py` declares which instruments each alert may read.
`tests/unit/observability/test_alert_instruments.py` asserts that each one is registered **and**
that somewhere in the codebase a value is actually recorded on it. That check reads the syntax
tree rather than the text, so a mention left behind in a comment does not count as a writer -
which is exactly what a half-finished removal looks like.

The check earns its place: `agenticorg_agent_budget_pct` was defined in `observability/metrics.py`,
read by a live threshold rule in `observability/alerting.py` and plotted on the Grafana dashboard,
and nothing had ever given it a value. The rule and the panel were removed along with the gauge -
an alert on an instrument like that never fires and looks exactly like a healthy system. (Its
labels were `tenant` and `agent_id`, which is per-tenant, per-agent cardinality; if it is ever
written, it needs different labels.)

## What is not here yet

**PRD §10 is achievable, not met.** The full path - process, endpoint, collector, managed
Prometheus, policy, notification - has never carried a single sample. Nothing here should be
relied on until it has.

What remains: the Managed Service for Prometheus sidecar on each Cloud Run service, with the
service-spec deploy that multiple containers require and the worker's in-memory volume for
`PROMETHEUS_MULTIPROC_DIR`; one real `terraform apply` of the policies (the only thing that would
have caught a `duration` in Prometheus's `15m` form rather than the protobuf `900s` the API
demands, which `terraform validate` cannot see); and one alert driven end to end, from a real
metric to a real notification.
