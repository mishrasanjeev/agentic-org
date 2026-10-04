"""Prometheus metrics — safe for multi-tenant scale.

Design rules (gap analysis #14):
  - NO raw tenant IDs, agent IDs, or tool names as metric labels.
    Those are high-cardinality identifiers that explode Prometheus
    storage and leak customer info into the observability plane.
  - Use low-cardinality dimensions only: domain, status, model, role,
    priority, connector_name (capped set of 54).
  - High-cardinality identifiers belong in structured logs and traces
    (structlog + OpenTelemetry), not metrics.
"""

from prometheus_client import Counter, Gauge, Histogram

# ── Task execution ──────────────────────────────────────────────────

tasks_total = Counter(
    "agenticorg_tasks_total",
    "Total tasks executed",
    ["domain", "agent_type", "status"],
)
task_latency = Histogram(
    "agenticorg_task_latency_seconds",
    "Task execution latency",
    ["domain", "agent_type"],
)

# ── HITL ────────────────────────────────────────────────────────────

hitl_rate = Gauge(
    "agenticorg_hitl_rate",
    "HITL intervention rate",
    ["domain", "agent_type"],
)
hitl_overdue = Gauge(
    "agenticorg_hitl_overdue_count",
    "Overdue HITL items",
    ["assignee_role", "priority"],
)

# ── Agent quality ───────────────────────────────────────────────────

confidence_avg = Gauge(
    "agenticorg_agent_confidence_avg",
    "Average confidence score",
    ["agent_type"],
)
tool_success_rate = Gauge(
    "agenticorg_tool_success_rate",
    "Tool success rate observed during agent runs",
    ["agent_type"],
)
shadow_accuracy = Gauge(
    "agenticorg_shadow_accuracy",
    "Shadow comparison accuracy",
    ["domain"],
)

# ── Connectors / tools ─────────────────────────────────────────────

tool_error_rate = Gauge(
    "agenticorg_tool_error_rate",
    "Tool error rate",
    ["connector_name", "error_code"],
)
circuit_breaker_state = Gauge(
    "agenticorg_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open)",
    ["connector_name"],
)

# ── Grant enforcement ───────────────────────────────────────────────
# One series per (mode, reason): mode is ``warn`` (the call was allowed and
# would have been denied) or ``deny`` (the call was refused); reason is the
# fixed denial vocabulary in auth/grant_enforcement.py. Grant ids, tools and
# tenants go to the structured log event, never to labels.

grant_enforcement_denials_total = Counter(
    "agenticorg_grant_enforcement_denials_total",
    "Agent tool calls denied, or that would be denied in warn mode, by grant enforcement",
    ["mode", "reason"],
)
operator_override_blocks_total = Counter(
    "agenticorg_operator_override_blocks_total",
    "Model, agent, workflow and tool calls refused by an operator override",
    ["target_kind", "mode"],
)
model_gateway_decisions_total = Counter(
    "agenticorg_model_gateway_decisions_total",
    "Model gateway decisions by outcome (applied, passthrough, refused)",
    ["outcome"],
)
model_gateway_limit_outcomes_total = Counter(
    "agenticorg_model_gateway_limit_outcomes_total",
    "Model gateway per-model limit checks by limit (concurrency, rate) and outcome (allowed, rejected, unavailable)",
    ["limit", "outcome"],
)

guardrail_outcomes_total = Counter(
    "agenticorg_guardrail_outcomes_total",
    "Guardrail outcomes by stage, detector, action and mode (flag_only, enforced)",
    ["stage", "detector", "action", "mode"],
)

# ── Tamper-evident audit (core/governance/audit_chain.py) ──────────

audit_chain_links_total = Counter(
    "agenticorg_audit_chain_links_total",
    "Audit rows sealed into a tenant's hash chain",
)

audit_chain_verifications_total = Counter(
    "agenticorg_audit_chain_verifications_total",
    "Audit chain verifications by result (empty, verified, broken, error)",
    ["result"],
)

# ── Synthetic checks (observability/synthetic.py) ──────────────────

synthetic_checks_total = Counter(
    "agenticorg_synthetic_checks_total",
    "Synthetic check runs by kind (model, knowledge, guardrail, audit_chain) and result (ok, failed, error)",
    ["kind", "result"],
)

# ── Model calls (agent path and direct router) ─────────────────────

model_calls_total = Counter(
    "agenticorg_model_calls_total",
    "Model calls by provider, model and outcome (completed, failed)",
    ["provider", "model", "outcome"],
)
model_call_latency_seconds = Histogram(
    "agenticorg_model_call_latency_seconds",
    "Model call latency by provider and model",
    ["provider", "model"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0),
)
model_call_tokens_total = Counter(
    "agenticorg_model_call_tokens_total",
    "Tokens by provider, model and direction (input, output, total)",
    ["provider", "model", "direction"],
)
model_call_cost_usd_total = Counter(
    "agenticorg_model_call_cost_usd_total",
    "Model call cost in USD by provider and model",
    ["provider", "model"],
)
model_call_output_tokens_per_second = Histogram(
    "agenticorg_model_call_output_tokens_per_second",
    "Output tokens per second of model calls by provider and model",
    ["provider", "model"],
    buckets=(5, 10, 20, 40, 80, 160, 320),
)
model_call_errors_total = Counter(
    "agenticorg_model_call_errors_total",
    "Failed model calls by provider, model and error type",
    ["provider", "model", "error_type"],
)
model_fallbacks_total = Counter(
    "agenticorg_model_fallbacks_total",
    "Model calls answered by a fallback model, by provider, from and to model",
    ["provider", "from_model", "to_model"],
)
model_admission_wait_seconds = Histogram(
    "agenticorg_model_admission_wait_seconds",
    "Time a model call waited at admission under the per-model limits",
    ["provider", "model"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
)
residency_refusals_total = Counter(
    "agenticorg_residency_refusals_total",
    "Providers refused by residency enforcement, by reason",
    ["reason"],
)
grant_enforcement_mode_fallbacks_total = Counter(
    "agenticorg_grant_enforcement_mode_fallbacks_total",
    "Runs whose grants.enforce_closed mode could not be read from the flag store",
    ["outcome"],
)

# ── LLM cost ────────────────────────────────────────────────────────

llm_tokens_total = Counter(
    "agenticorg_llm_tokens_total",
    "Total LLM tokens consumed",
    ["model"],
)
llm_cost_total = Counter(
    "agenticorg_llm_cost_usd_total",
    "Total LLM cost in USD",
    ["model"],
)

# ── STP / automation rate ───────────────────────────────────────────

stp_rate = Gauge(
    "agenticorg_stp_rate",
    "Straight-through processing rate",
    ["domain"],
)

# ── Scaling ───────────────────────��─────────────────────────────────

agent_replicas = Gauge(
    "agenticorg_agent_replicas",
    "Agent replicas running",
    ["agent_type"],
)
# ── Tamper-evident chains and spend caps (PRD §10 alerts) ───────────

chain_verifications_total = Counter(
    "agenticorg_chain_verifications_total",
    "Verifications of a tamper-evident chain, by chain and outcome. A failure means stored "
    "evidence no longer matches the digest recorded for it: it is never a transient error.",
    ["chain", "outcome"],
)
budget_cap_events_total = Counter(
    "agenticorg_budget_cap_events_total",
    "Spend caps crossed, by outcome: warned (past the configured warning point) or exhausted "
    "(at or past the cap itself, where work starts being refused).",
    ["outcome"],
)

# ── HITL conditions ─────────────────────────────────────────────────

hitl_condition_parse_failures_total = Counter(
    "agenticorg_hitl_condition_parse_failures_total",
    "HITL conditions outside the supported grammar, by stage, reason code and outcome",
    ["stage", "reason", "outcome"],
)
