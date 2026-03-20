"""Prometheus metrics — all 13 from PRD."""
from prometheus_client import Counter, Gauge, Histogram, REGISTRY

tasks_total = Counter("agentflow_tasks_total", "Total tasks", ["tenant", "domain", "agent_type", "status"])
task_latency = Histogram("agentflow_task_latency_seconds", "Task latency", ["tenant", "domain", "agent_type"])
hitl_rate = Gauge("agentflow_hitl_rate", "HITL rate", ["tenant", "domain", "agent_type"])
confidence_avg = Gauge("agentflow_agent_confidence_avg", "Avg confidence", ["tenant", "agent_type"])
tool_error_rate = Gauge("agentflow_tool_error_rate", "Tool error rate", ["tenant", "tool_name", "error_code"])
stp_rate = Gauge("agentflow_stp_rate", "STP rate", ["tenant", "domain"])
llm_tokens_total = Counter("agentflow_llm_tokens_total", "Total LLM tokens", ["tenant", "agent_type", "model"])
llm_cost_total = Counter("agentflow_llm_cost_usd_total", "Total LLM cost USD", ["tenant", "agent_type", "model"])
hitl_overdue = Gauge("agentflow_hitl_overdue_count", "Overdue HITL items", ["tenant", "assignee_role", "priority"])
circuit_breaker_state = Gauge("agentflow_circuit_breaker_state", "Circuit breaker state", ["tenant", "connector_name"])
shadow_accuracy = Gauge("agentflow_shadow_accuracy", "Shadow accuracy", ["tenant", "shadow_agent_id", "reference_agent_id"])
agent_replicas = Gauge("agentflow_agent_replicas", "Agent replicas", ["tenant", "agent_type"])
agent_budget_pct = Gauge("agentflow_agent_budget_pct", "Agent budget %", ["tenant", "agent_id"])
