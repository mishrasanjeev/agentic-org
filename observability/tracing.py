# SPDX-License-Identifier: Apache-2.0
"""OpenTelemetry tracing: wiring, correlation ids and the span helpers.

Off by default (``AGENTICORG_TRACING_ENABLED``). Off, every helper here is a
no-op that touches no tracer: ``span`` yields a non-recording span,
``current_trace_id`` is empty and audit rows fall back to the request id.
On, ``init_tracing_from_settings`` installs a provider that exports to the
OTLP endpoint and the call sites open these spans:

- ``agenticorg.http.request`` (SERVER) around every API request, continuing
  an incoming W3C trace context (``traceparent``);
- ``agenticorg.task.run`` (CONSUMER) around every Celery task, continuing
  the publisher's context carried in the task headers;
- ``agenticorg.agent.run`` and ``agenticorg.agent.resume`` (INTERNAL) around
  an agent graph's execution, carrying the routing decision's correlation id;
- ``agenticorg.agent.reason`` (CLIENT) around every model call, with the
  provider, the model and the token counts;
- ``agenticorg.tool.call`` (CLIENT) around every connector dispatch;
- ``agenticorg.knowledge.search`` (INTERNAL) around a knowledge search.

The model gateway's decision and every guardrail outcome are events on the
span in progress. While a span records, the log context carries its trace
id under ``trace_id`` and the signed audit rows the governance modules write
record it in their ``trace_id`` column, so one id links a request, its logs,
its spans and its audit trail.

A span names its tenant by ``tenant.ref``, a keyed reference, never by the
tenant identifier. Residency: with deployment-wide enforcement no exporter
is installed (spans stay in the process); with tenant-scoped enforcement a
span of a tenant that enforces, or whose enforcement was never read, is
withheld from export, as is a span that names no tenant while some tenant in
the process enforces. The original span catalogue (workflow, step, agent,
tool, hitl, auth, shadow) keeps its constructors below for callers that
manage span lifetimes themselves.
"""

from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

import structlog
from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import SpanKind, StatusCode

from core.config import external_keys, is_strict_runtime_env, settings

logger = structlog.get_logger()

PROTOCOLS: tuple[str, ...] = ("http/protobuf", "grpc")
TRACE_CONTEXT_HEADERS: tuple[str, ...] = ("traceparent", "tracestate")
LOG_KEY = "trace_id"
TENANT_REF_KEY = "tenant.ref"
WITHHELD_KEY = "export.withheld"
RUN_KEY = "run.id"
# The agent run in progress (its root span id): every span opened inside it
# carries it, so the run's spans can be told apart from a sibling run sharing
# the same trace (observability/timeline.py).
_run_scope: ContextVar[str | None] = ContextVar("agenticorg_run_scope", default=None)
AUDIT_TRACE_ID_WIDTH = 64
_TRACES_PATH = "/v1/traces"

_tracer: trace.Tracer | None = None
_provider: TracerProvider | None = None


class TracingError(RuntimeError):
    """Tracing is on and cannot start as configured."""


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def enabled() -> bool:
    """Whether a tracer is installed in this process."""
    return _tracer is not None


def init_tracing(
    service_name: str = "agenticorg-core",
    exporter: SpanExporter | None = None,
    *,
    sample_ratio: float = 1.0,
    environment: str = "",
    set_global: bool = False,
) -> trace.Tracer:
    """Install the tracer; a second call returns the one installed.

    ``exporter`` is a ``SpanExporter`` (the OTLP exporter in a deployment, an
    in-memory one in a test). Without one, spans are recorded and dropped.
    ``set_global`` also publishes the provider as the process-wide one so a
    library instrumentation would share it; tests leave it unset.
    """
    global _tracer, _provider
    if _tracer is not None:
        return _tracer
    attributes: dict[str, str] = {"service.name": service_name}
    if environment:
        attributes["deployment.environment"] = environment
    resource = Resource.create(attributes)
    if sample_ratio < 1.0:
        provider = TracerProvider(resource=resource, sampler=ParentBased(TraceIdRatioBased(sample_ratio)))
    else:
        provider = TracerProvider(resource=resource)
    if exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter))
    if set_global and not isinstance(trace.get_tracer_provider(), TracerProvider):
        trace.set_tracer_provider(provider)
    _provider = provider
    _tracer = provider.get_tracer(service_name)
    return _tracer


def validate_settings() -> tuple[str, float, str]:
    """Check the tracing settings and return the protocol, the sample ratio and the endpoint.

    Raises ``TracingError`` for an unknown protocol, a sample ratio outside 0
    to 1, or a strict runtime with tracing on and no OTLP endpoint (spans with
    nowhere to go would be a silent gap in the evidence trail). The worker
    process refuses to start on it, as the API's lifespan does.
    """
    protocol = str(settings.tracing_protocol or "").strip().lower()
    if protocol not in PROTOCOLS:
        raise TracingError(f"AGENTICORG_TRACING_PROTOCOL must be one of {', '.join(PROTOCOLS)}, not {protocol!r}")
    ratio = settings.tracing_sample_ratio
    if isinstance(ratio, bool) or not isinstance(ratio, int | float) or not math.isfinite(ratio) or not 0 <= ratio <= 1:
        raise TracingError("AGENTICORG_TRACING_SAMPLE_RATIO must be a number from 0 to 1")
    endpoint = str(external_keys.otel_exporter_otlp_endpoint or "").strip()
    if not endpoint and is_strict_runtime_env(settings.env):
        raise TracingError("tracing is on and OTEL_EXPORTER_OTLP_ENDPOINT is not set: spans would have nowhere to go")
    return protocol, float(ratio), endpoint


def init_tracing_from_settings() -> bool:
    """Install the tracer the settings describe; False when tracing is off.

    Residency: with deployment-wide enforcement no exporter is installed (the
    collector is an external destination with no attestation path), and the
    spans stay in the process; with tenant-scoped enforcement the exporter
    withholds the spans ``residency_exporter`` describes.
    """
    if not settings.tracing_enabled:
        return False
    if enabled():
        return True
    protocol, ratio, endpoint = validate_settings()
    exporter: SpanExporter | None = None
    if settings.residency_enforce:
        logger.warning("tracing_export_withheld_residency", env=settings.env)
    elif endpoint:
        exporter = ResidencyExporter(otlp_exporter(protocol, endpoint))
    else:
        logger.warning("tracing_without_exporter", env=settings.env)
    init_tracing(
        external_keys.otel_service_name or "agenticorg-core",
        exporter,
        sample_ratio=float(ratio),
        environment=settings.env,
        set_global=True,
    )
    if settings.tracing_timeline_enabled:
        # The console's run timelines (observability/timeline.py): off by default.
        from observability.timeline import install as install_timeline

        install_timeline()
    logger.info(
        "tracing_started",
        protocol=protocol,
        exporter=exporter is not None,
        sample_ratio=ratio,
        timeline=bool(settings.tracing_timeline_enabled),
    )
    return True


class ResidencyExporter(SpanExporter):
    """Hands spans to the OTLP exporter, withholding the ones residency keeps in the platform.

    A span marked withheld at creation (its tenant enforces residency, or its
    enforcement was never read in this process) never leaves, nor does a span
    that names no tenant while some tenant in the process enforces: the rule
    the LangSmith redaction hook applies (observability/trace_redaction.py).
    """

    def __init__(self, inner: SpanExporter) -> None:
        self.inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        kept = [span for span in spans if exportable(span)]
        if not kept:
            return SpanExportResult.SUCCESS
        return self.inner.export(kept)

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.inner.force_flush(timeout_millis)


def exportable(span: ReadableSpan) -> bool:
    """Whether residency lets ``span`` leave the platform."""
    from core.governance import residency

    attributes = span.attributes or {}
    if attributes.get(WITHHELD_KEY):
        return False
    if TENANT_REF_KEY not in attributes and residency.any_tenant_enforcing():
        return False
    return True


def tenant_ref(tenant_id: Any) -> str:
    """A keyed, bounded reference to a tenant for telemetry: the same tenant always maps to it, nobody maps it back."""
    digest = hmac.new(settings.secret_key.encode(), b"tenant-ref:" + str(tenant_id).encode(), hashlib.sha256)
    return digest.hexdigest()[:16]


def _tenant_attributes(tenant: Any) -> dict[str, Any]:
    """What a span records for its tenant: the reference, and the withholding mark when residency keeps it home."""
    if tenant is None or tenant == "":
        return {}
    from core.governance import residency

    attributes: dict[str, Any] = {TENANT_REF_KEY: tenant_ref(tenant)}
    if residency.enforcement_known(tenant) is not False:
        attributes[WITHHELD_KEY] = "residency"
    return attributes


def otlp_exporter(protocol: str, endpoint: str) -> SpanExporter:
    """The OTLP span exporter for ``protocol``; an http endpoint gains the traces path when it names none.

    Headers for the collector (an authorisation token, say) come from the
    exporter's own ``OTEL_EXPORTER_OTLP_HEADERS`` environment variable and are
    never a setting of this platform.
    """
    if protocol == "grpc":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter as GrpcExporter

        return GrpcExporter(endpoint=endpoint, insecure=endpoint.startswith("http://"))
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as HttpExporter

    return HttpExporter(endpoint=traces_endpoint(endpoint))


def traces_endpoint(endpoint: str) -> str:
    """The http/protobuf traces URL for a collector base URL (``/v1/traces`` appended once)."""
    base = endpoint.strip().rstrip("/")
    return base if base.endswith(_TRACES_PATH) else base + _TRACES_PATH


def add_span_processor(processor: Any) -> bool:
    """Attach a span processor to the installed provider; False while tracing is off."""
    if _provider is None:
        return False
    _provider.add_span_processor(processor)
    return True


def flush() -> None:
    """Hand every finished span to the exporter now."""
    if _provider is not None:
        _provider.force_flush()


def shutdown_tracing() -> None:
    """Flush and drop the installed tracer; the helpers are no-ops again."""
    global _tracer, _provider
    provider, _tracer, _provider = _provider, None, None
    if provider is not None:
        provider.shutdown()


def get_tracer() -> trace.Tracer:
    """The installed tracer, or one that records nothing while tracing is off."""
    return _tracer if _tracer is not None else trace.NoOpTracer()


# ---------------------------------------------------------------------------
# Spans, events and correlation ids
# ---------------------------------------------------------------------------


def _attributes(values: Mapping[str, Any]) -> dict[str, Any]:
    """Span attributes as the SDK takes them: missing values dropped, everything else a primitive or its text."""
    clean: dict[str, Any] = {}
    for key, value in values.items():
        if value is None or (isinstance(value, str) and value == ""):
            continue
        clean[key] = value if isinstance(value, bool | int | float | str) else str(value)
    return clean


def _trace_id_of(current: trace.Span) -> str:
    context = current.get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else ""


def _bind_log_context(current: trace.Span) -> dict[str, Any] | None:
    trace_id = _trace_id_of(current)
    if not trace_id or structlog.contextvars.get_contextvars().get(LOG_KEY) == trace_id:
        return None
    return structlog.contextvars.bind_contextvars(**{LOG_KEY: trace_id})


def _unbind_log_context(tokens: dict[str, Any] | None) -> None:
    if not tokens:
        return
    try:
        structlog.contextvars.reset_contextvars(**tokens)
    except ValueError:
        # The binding was made in another context (a thread hand-off): drop the key instead.
        structlog.contextvars.unbind_contextvars(LOG_KEY)


@contextmanager
def span(
    name: str,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
    parent: Mapping[str, str] | None = None,
    tenant: Any = None,
    **attributes: Any,
) -> Iterator[trace.Span]:
    """A span named ``name`` around the block, current for its duration.

    While tracing is off this yields a non-recording span and touches
    nothing. ``parent`` carries W3C trace-context headers (``traceparent``)
    to continue a trace that started in another process. ``tenant`` is the
    tenant the span belongs to: the span records a keyed reference to it,
    never the identifier, and is withheld from export when residency keeps
    that tenant's data in the platform. While the span is current the log
    context carries its trace id under ``trace_id``.
    """
    tracer = _tracer
    if tracer is None:
        yield trace.INVALID_SPAN
        return
    context = propagate.extract(dict(parent)) if parent else None
    recorded = {**_attributes(attributes), **_tenant_attributes(tenant)}
    run_scope = _run_scope.get()
    if run_scope:
        recorded[RUN_KEY] = run_scope
    with tracer.start_as_current_span(name, context=context, kind=kind, attributes=recorded) as current:
        tokens = _bind_log_context(current)
        try:
            yield current
        finally:
            _unbind_log_context(tokens)


@dataclass
class SpanHandle:
    """A span whose lifetime a caller manages across callbacks (``start`` and ``end``)."""

    manager: Any
    span: trace.Span
    scope_token: Token[str | None] | None = None

    def set(self, **attributes: Any) -> None:
        if self.span.is_recording():
            self.span.set_attributes(_attributes(attributes))

    def error(self, error: BaseException) -> None:
        if self.span.is_recording():
            record_span_error(self.span, error)

    def end(self) -> None:
        manager, self.manager = self.manager, None
        if manager is not None:
            manager.__exit__(None, None, None)
        token, self.scope_token = self.scope_token, None
        if token is not None:
            try:
                _run_scope.reset(token)
            except ValueError:
                _run_scope.set(None)

    @property
    def run_id(self) -> str:
        """The span's id as the run scope records it (empty while tracing is off)."""
        context = self.span.get_span_context()
        return format(context.span_id, "016x") if context.is_valid else ""


def start(
    name: str,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
    parent: Mapping[str, str] | None = None,
    tenant: Any = None,
    root: bool = False,
    **attributes: Any,
) -> SpanHandle:
    """Open ``span`` without a ``with`` block; the caller ends it through the handle.

    ``root`` makes the span an agent run's root: it and every span opened
    while it is current carry ``run.id``, its own span id, until ``end``.
    """
    manager = span(name, kind=kind, parent=parent, tenant=tenant, **attributes)
    handle = SpanHandle(manager, manager.__enter__())
    if root and handle.span.is_recording():
        run_id = handle.run_id
        handle.span.set_attribute(RUN_KEY, run_id)
        handle.scope_token = _run_scope.set(run_id)
    return handle


def add_event(name: str, **attributes: Any) -> None:
    """An event on the span in progress; nothing while no span records."""
    if _tracer is None:
        return
    current = trace.get_current_span()
    if current.is_recording():
        current.add_event(name, _attributes(attributes))


def set_attributes(**attributes: Any) -> None:
    """Attributes on the span in progress; nothing while no span records."""
    if _tracer is None:
        return
    current = trace.get_current_span()
    if current.is_recording():
        current.set_attributes(_attributes(attributes))


def current_trace_id() -> str:
    """The trace id of the span in progress (32 hex characters), or empty when none is."""
    if _tracer is None:
        return ""
    return _trace_id_of(trace.get_current_span())


def _request_id() -> str:
    try:
        return str(structlog.contextvars.get_contextvars().get("request_id") or "")
    # enterprise-gate: broad-except-ok reason=a-missing-log-context-degrades-to-an-empty-trace-id
    except Exception:
        return ""


def audit_trace_id(fallback: str | None = None) -> str:
    """What a signed audit row records as ``trace_id``: the trace in progress, else ``fallback``, else the request id.

    The column holds 64 characters: a trace id always fits, a longer
    correlation id is cut to the width.
    """
    trace_id = current_trace_id()
    if trace_id:
        return trace_id
    return str(fallback or _request_id()).strip()[:AUDIT_TRACE_ID_WIDTH]


def inject_headers(carrier: dict[str, Any]) -> None:
    """Write the trace context in progress into ``carrier`` as W3C headers; nothing while tracing is off."""
    if _tracer is None:
        return
    propagate.inject(carrier)


def trace_headers(headers: Mapping[Any, Any] | None) -> dict[str, str]:
    """The W3C trace-context entries of ``headers`` (any case, text or bytes), ready for ``span(parent=...)``."""
    found: dict[str, str] = {}
    for key, value in (headers or {}).items():
        name = key.decode("latin-1") if isinstance(key, bytes) else str(key)
        name = name.lower()
        if name in TRACE_CONTEXT_HEADERS and value:
            found[name] = value.decode("latin-1") if isinstance(value, bytes) else str(value)
    return found


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


def record_span_error(span: trace.Span, error: BaseException) -> None:
    """Mark a span as errored with the exception details."""
    span.set_status(StatusCode.ERROR, str(error))
    span.record_exception(error)


def record_span_ok(span: trace.Span) -> None:
    """Mark a span as successfully completed."""
    span.set_status(StatusCode.OK)
