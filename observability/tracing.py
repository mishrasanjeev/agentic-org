"""OpenTelemetry tracing setup with all 7 span types.

Span catalogue
--------------
1. agenticorg.workflow.run   — SERVER  — top-level workflow execution
2. agenticorg.step.execute   — INTERNAL — individual step within a workflow
3. agenticorg.agent.reason   — INTERNAL — LLM reasoning cycle
4. agenticorg.tool.call      — CLIENT  — outbound connector / tool invocation
5. agenticorg.hitl.create    — INTERNAL — human-in-the-loop review creation
6. agenticorg.auth.validate  — INTERNAL — JWT / auth validation
7. agenticorg.shadow.compare — INTERNAL — shadow-mode quality comparison
"""
from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, StatusCode

_tracer: trace.Tracer | None = None


def init_tracing(
    service_name: str = "agenticorg-core",
    exporter=None,
) -> trace.Tracer:
    """Initialise the global tracer with an optional exporter.

    Parameters
    ----------
    service_name:
        OpenTelemetry service name.
    exporter:
        A ``SpanExporter`` instance (e.g. ``OTLPSpanExporter``).  When *None*,
        the provider is still configured so spans are recorded in-process (useful
        for tests).

    Returns
    -------
    trace.Tracer
        The configured tracer instance.
    """
    global _tracer
    provider = TracerProvider()
    if exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(service_name)
    return _tracer


def get_tracer() -> trace.Tracer:
    """Return the global tracer, initialising lazily if needed."""
    if not _tracer:
        init_tracing()
    return _tracer  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. agenticorg.workflow.run — SERVER
# ---------------------------------------------------------------------------
def start_workflow_span(
    run_id: str,
    name: str,
    tenant_id: str,
    trigger_type: str = "manual",
    *,
    workflow_version: str = "1",
    priority: str = "normal",
    parent_run_id: str = "",
    initiator_user_id: str = "",
    dag_hash: str = "",
):
    """Create the top-level span for a workflow execution."""
    return get_tracer().start_span(
        "agenticorg.workflow.run",
        kind=SpanKind.SERVER,
        attributes={
            "workflow.run.id": run_id,
            "workflow.name": name,
            "workflow.version": workflow_version,
            "tenant.id": tenant_id,
            "trigger.type": trigger_type,
            "workflow.priority": priority,
            "workflow.parent_run_id": parent_run_id,
            "workflow.initiator_user_id": initiator_user_id,
            "workflow.dag_hash": dag_hash,
        },
    )


# ---------------------------------------------------------------------------
# 2. agenticorg.step.execute — INTERNAL
# ---------------------------------------------------------------------------
def start_step_span(
    step_id: str,
    step_type: str,
    run_id: str,
    agent_id: str,
    *,
    step_index: int = 0,
    retry_number: int = 0,
    timeout_ms: int = 30_000,
    depends_on: str = "",
    tenant_id: str = "",
):
    """Create a span for an individual workflow step."""
    return get_tracer().start_span(
        "agenticorg.step.execute",
        kind=SpanKind.INTERNAL,
        attributes={
            "step.id": step_id,
            "step.type": step_type,
            "step.index": step_index,
            "workflow.run.id": run_id,
            "agent.id": agent_id,
            "step.retry_number": retry_number,
            "step.timeout_ms": timeout_ms,
            "step.depends_on": depends_on,
            "tenant.id": tenant_id,
        },
    )


# ---------------------------------------------------------------------------
# 3. agenticorg.agent.reason — INTERNAL
# ---------------------------------------------------------------------------
def start_agent_span(
    agent_id: str,
    agent_type: str,
    domain: str,
    model: str,
    *,
    tenant_id: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    temperature: float = 0.2,
    confidence_threshold: float = 0.88,
    max_retries: int = 3,
    reasoning_strategy: str = "chain-of-thought",
):
    """Create a span for an agent reasoning cycle."""
    return get_tracer().start_span(
        "agenticorg.agent.reason",
        kind=SpanKind.INTERNAL,
        attributes={
            "agent.id": agent_id,
            "agent.type": agent_type,
            "domain": domain,
            "llm.model": model,
            "llm.temperature": temperature,
            "llm.prompt_tokens": prompt_tokens,
            "llm.completion_tokens": completion_tokens,
            "agent.confidence_threshold": confidence_threshold,
            "agent.max_retries": max_retries,
            "agent.reasoning_strategy": reasoning_strategy,
            "tenant.id": tenant_id,
        },
    )


# ---------------------------------------------------------------------------
# 4. agenticorg.tool.call — CLIENT
# ---------------------------------------------------------------------------
def start_tool_span(
    tool_name: str,
    connector_id: str,
    category: str,
    *,
    tenant_id: str = "",
    agent_id: str = "",
    tool_version: str = "1",
    http_method: str = "",
    http_url: str = "",
    timeout_ms: int = 10_000,
    retry_policy: str = "exponential",
    idempotency_key: str = "",
):
    """Create a span for an outbound tool / connector call."""
    return get_tracer().start_span(
        "agenticorg.tool.call",
        kind=SpanKind.CLIENT,
        attributes={
            "tool.name": tool_name,
            "tool.version": tool_version,
            "connector.id": connector_id,
            "connector.category": category,
            "agent.id": agent_id,
            "http.method": http_method,
            "http.url": http_url,
            "tool.timeout_ms": timeout_ms,
            "tool.retry_policy": retry_policy,
            "tool.idempotency_key": idempotency_key,
            "tenant.id": tenant_id,
        },
    )


# ---------------------------------------------------------------------------
# 5. agenticorg.hitl.create — INTERNAL
# ---------------------------------------------------------------------------
def start_hitl_span(
    hitl_id: str,
    run_id: str,
    agent_id: str,
    reason: str,
    *,
    tenant_id: str = "",
    assignee_role: str = "",
    priority: str = "normal",
    confidence_score: float = 0.0,
    threshold_value: float = 0.0,
    timeout_hours: float = 24.0,
    escalation_chain: str = "",
):
    """Create a span for a HITL review item creation."""
    return get_tracer().start_span(
        "agenticorg.hitl.create",
        kind=SpanKind.INTERNAL,
        attributes={
            "hitl.id": hitl_id,
            "hitl.reason": reason,
            "hitl.assignee_role": assignee_role,
            "hitl.priority": priority,
            "hitl.confidence_score": confidence_score,
            "hitl.threshold_value": threshold_value,
            "hitl.timeout_hours": timeout_hours,
            "hitl.escalation_chain": escalation_chain,
            "workflow.run.id": run_id,
            "agent.id": agent_id,
            "tenant.id": tenant_id,
        },
    )


# ---------------------------------------------------------------------------
# 6. agenticorg.auth.validate — INTERNAL
# ---------------------------------------------------------------------------
def start_auth_span(
    user_id: str,
    tenant_id: str,
    method: str = "jwt",
    *,
    token_issuer: str = "",
    token_subject: str = "",
    scopes: str = "",
    ip_address: str = "",
    user_agent: str = "",
    mfa_verified: bool = False,
    auth_provider: str = "grantex",
):
    """Create a span for an authentication / authorisation validation."""
    return get_tracer().start_span(
        "agenticorg.auth.validate",
        kind=SpanKind.INTERNAL,
        attributes={
            "auth.user_id": user_id,
            "auth.method": method,
            "auth.token_issuer": token_issuer,
            "auth.token_subject": token_subject,
            "auth.scopes": scopes,
            "auth.ip_address": ip_address,
            "auth.user_agent": user_agent,
            "auth.mfa_verified": mfa_verified,
            "auth.provider": auth_provider,
            "tenant.id": tenant_id,
        },
    )


# ---------------------------------------------------------------------------
# 7. agenticorg.shadow.compare — INTERNAL
# ---------------------------------------------------------------------------
def start_shadow_span(
    shadow_agent_id: str,
    reference_agent_id: str,
    run_id: str,
    *,
    tenant_id: str = "",
    comparison_id: str = "",
    shadow_model: str = "",
    reference_model: str = "",
    quality_gates: str = "",
    traffic_pct: float = 0.0,
    accuracy_floor: float = 0.90,
):
    """Create a span for a shadow-mode quality comparison."""
    return get_tracer().start_span(
        "agenticorg.shadow.compare",
        kind=SpanKind.INTERNAL,
        attributes={
            "shadow.agent_id": shadow_agent_id,
            "shadow.reference_agent_id": reference_agent_id,
            "shadow.comparison_id": comparison_id,
            "shadow.model": shadow_model,
            "shadow.reference_model": reference_model,
            "shadow.quality_gates": quality_gates,
            "shadow.traffic_pct": traffic_pct,
            "shadow.accuracy_floor": accuracy_floor,
            "workflow.run.id": run_id,
            "tenant.id": tenant_id,
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def record_span_error(span: trace.Span, error: Exception) -> None:
    """Mark a span as errored with the exception details."""
    span.set_status(StatusCode.ERROR, str(error))
    span.record_exception(error)


def record_span_ok(span: trace.Span) -> None:
    """Mark a span as successfully completed."""
    span.set_status(StatusCode.OK)
