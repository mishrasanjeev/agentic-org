"""OpenTelemetry tracing setup with all 7 span types."""
from __future__ import annotations
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_tracer: trace.Tracer | None = None

def init_tracing(service_name: str = "agentflow-core"):
    global _tracer
    provider = TracerProvider()
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(service_name)

def get_tracer() -> trace.Tracer:
    if not _tracer:
        init_tracing()
    return _tracer  # type: ignore

def start_workflow_span(run_id, name, tenant_id, trigger_type="manual"):
    return get_tracer().start_span("agentflow.workflow.run", attributes={"workflow.run.id": run_id, "workflow.name": name, "tenant.id": tenant_id, "trigger.type": trigger_type})

def start_step_span(step_id, step_type, run_id, agent_id):
    return get_tracer().start_span("agentflow.step.execute", attributes={"step.id": step_id, "step.type": step_type, "workflow.run.id": run_id, "agent.id": agent_id})

def start_agent_span(agent_id, agent_type, domain, model):
    return get_tracer().start_span("agentflow.agent.reason", attributes={"agent.id": agent_id, "agent.type": agent_type, "domain": domain, "llm.model": model})

def start_tool_span(tool_name, connector_id, category):
    return get_tracer().start_span("agentflow.tool.call", attributes={"tool.name": tool_name, "connector.id": connector_id, "connector.category": category})
