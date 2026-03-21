"""Prometheus metrics — all 13 from PRD."""

from prometheus_client import Counter, Gauge, Histogram

tasks_total = Counter(
    "agenticorg_tasks_total", "Total tasks", ["tenant", "domain", "agent_type", "status"]
)
task_latency = Histogram(
    "agenticorg_task_latency_seconds", "Task latency", ["tenant", "domain", "agent_type"]
)
hitl_rate = Gauge("agenticorg_hitl_rate", "HITL rate", ["tenant", "domain", "agent_type"])
confidence_avg = Gauge(
    "agenticorg_agent_confidence_avg", "Avg confidence", ["tenant", "agent_type"]
)
tool_error_rate = Gauge(
    "agenticorg_tool_error_rate", "Tool error rate", ["tenant", "tool_name", "error_code"]
)
stp_rate = Gauge("agenticorg_stp_rate", "STP rate", ["tenant", "domain"])
llm_tokens_total = Counter(
    "agenticorg_llm_tokens_total", "Total LLM tokens", ["tenant", "agent_type", "model"]
)
llm_cost_total = Counter(
    "agenticorg_llm_cost_usd_total", "Total LLM cost USD", ["tenant", "agent_type", "model"]
)
hitl_overdue = Gauge(
    "agenticorg_hitl_overdue_count", "Overdue HITL items", ["tenant", "assignee_role", "priority"]
)
circuit_breaker_state = Gauge(
    "agenticorg_circuit_breaker_state", "Circuit breaker state", ["tenant", "connector_name"]
)
shadow_accuracy = Gauge(
    "agenticorg_shadow_accuracy",
    "Shadow accuracy",
    ["tenant", "shadow_agent_id", "reference_agent_id"],
)
agent_replicas = Gauge("agenticorg_agent_replicas", "Agent replicas", ["tenant", "agent_type"])
agent_budget_pct = Gauge("agenticorg_agent_budget_pct", "Agent budget %", ["tenant", "agent_id"])
