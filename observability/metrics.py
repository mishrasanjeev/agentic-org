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
agent_budget_pct = Gauge(
    "agenticorg_agent_budget_pct",
    "Agent budget utilization percentage",
    ["agent_type"],
)
