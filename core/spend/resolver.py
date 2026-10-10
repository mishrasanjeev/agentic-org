# SPDX-License-Identifier: Apache-2.0
"""Attribution resolution: which organisation node, labels and dimensions a usage record carries.

**Server-owned.** The inputs (``Hints``) come from context variables the
server binds (``core/spend/context.py``), the routing context and the
caller's identity, never from a request body: the labels a caller sends to
the agents run route are ignored here. The agent's configuration, the
registry, the source mappings and the organisation tree decide.

**Order** (first match wins; ``attribution_path`` records which rule):

* agent present: the agent's mapping; then, when the agent carries a legacy
  cost centre, the cost centre's mapping, else the node whose code is the
  cost centre's code, else **unattributed with ``unknown_label``** (the
  legacy label is kept as a fact, never skipped); then the application's
  mapping; else ``no_mapping``.
* workflow, no agent: the workflow's mapping, the application's mapping,
  the initiating user's department; else ``no_mapping``.
* anything else: the application's mapping, the initiating user's
  department; else ``no_mapping`` (``no_source`` for a system call with no
  user).

A user's department resolves through its mapping, else the node with its
code, else ``unknown_label``. A matched node that is inactive gives
``inactive_node``. A database failure gives ``resolver_failed``. Every
unattributed record is still written and counted by reason.

On the hook path an agent hint naming a missing, retired or deleted agent is
dropped (a workflow author cannot charge spend to a retired agent's cost
centre by naming it). Backfill hints come from signed gateway rows of real
runs, so an absent agent keeps its id with no attributes.

Every query carries the tenant predicate on every joined table, even where
row-level security already filters. Results are cached for 60 seconds per
key in this process and dropped on a local mapping or tree change; another
process sees a change within 60 seconds, and re-attribution fixes records
written in that window.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

import structlog
from sqlalchemy import and_, or_, select, text

from core.spend import vocab
from core.spend.errors import SpendError

logger = structlog.get_logger()

CACHE_TTL_SECONDS = 60.0
CACHE_MAX = 4096
ORIGINS = ("hook", "backfill", "reattribute")

_AGENT_SQL = text(
    """
    SELECT a.version, a.agent_type, a.status, a.cost_center_id, cc.code, cc.department_id, r.risk_tier, r.use_case
    FROM agents a
    LEFT JOIN cost_centers cc ON cc.id = a.cost_center_id AND cc.tenant_id = :tid
    LEFT JOIN agent_registry r ON r.agent_id = a.id AND r.tenant_id = :tid
    WHERE a.tenant_id = :tid AND a.id = :agent
    """
)
_USER_DEPARTMENT_SQL = text(
    """
    SELECT u.department_id, d.code
    FROM users u
    LEFT JOIN departments d ON d.id = u.department_id AND d.tenant_id = :tid
    WHERE u.id = :user AND u.tenant_id = :tid
    """
)
_NEVER_RUNS = frozenset({"deleted", "retired"})  # core/governance/agent_status.py:NEVER_RUNS


@dataclass(frozen=True)
class Hints:
    agent_id: str | None
    agent_version: str | None
    application: str
    default_use_case: str
    workflow_id: str | None
    workflow_run_id: str | None
    run_id: str | None
    initiating_user_id: str | None
    origin: str = "hook"  # "hook" | "backfill" | "reattribute"


@dataclass(frozen=True)
class Resolved:
    org_node_id: uuid.UUID | None
    business_unit_node_id: uuid.UUID | None
    attribution_path: str | None
    unattributed_reason: str | None
    product_line: str | None
    use_case: str
    agent_id: uuid.UUID | None
    agent_version: str | None
    risk_tier: str | None
    region: str | None
    environment: str
    initiating_user_id: uuid.UUID | None
    workflow_id: uuid.UUID | None
    run_id: str | None
    application: str


@dataclass(frozen=True)
class AgentFacts:
    version: str | None
    agent_type: str | None
    status: str | None
    cost_center_id: uuid.UUID | None
    cost_center_code: str | None
    risk_tier: str | None
    registry_use_case: str | None


@dataclass(frozen=True)
class MappingFacts:
    org_node_id: uuid.UUID | None
    product_line: str | None
    use_case: str | None


@dataclass(frozen=True)
class NodeFacts:
    id: uuid.UUID
    code: str
    kind: str
    active: bool


@dataclass(frozen=True)
class Facts:
    """Everything the decision reads, loaded from the tenant's tables."""

    agent_uuid: uuid.UUID | None = None
    agent: AgentFacts | None = None
    workflow_uuid: uuid.UUID | None = None
    user_uuid: uuid.UUID | None = None
    user_department_id: uuid.UUID | None = None
    user_department_code: str | None = None
    mappings: Mapping[tuple[str, str], MappingFacts] = field(default_factory=dict)
    nodes_by_id: Mapping[uuid.UUID, NodeFacts] = field(default_factory=dict)
    nodes_by_code: Mapping[str, NodeFacts] = field(default_factory=dict)
    region: str | None = None
    environment: str = ""


# enterprise-gate: process-local-ok reason=attribution-cache-ttl-60s-dropped-on-local-writes-keeps-no-cross-tenant-data
_RESOLUTION_CACHE: dict[tuple[str, ...], tuple[float, Resolved]] = {}


def invalidate(tenant_id: uuid.UUID | str) -> None:
    """Drop the tenant's cached resolutions in this process."""
    key = str(tenant_id)
    for cached in [k for k in _RESOLUTION_CACHE if k[0] == key]:
        _RESOLUTION_CACHE.pop(cached, None)


# ---------------------------------------------------------------- pure parts


def as_uuid(value: Any) -> uuid.UUID | None:
    """``value`` as a UUID, or ``None`` when it is not one (``mcp_<hex>``, ``voice-agent``)."""
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value).strip())
    except (ValueError, AttributeError, TypeError):
        return None


def application_of(value: Any) -> str:
    """A known application name; anything else is ``system``."""
    name = str(value or "").strip().lower()
    return name if name in vocab.APPLICATIONS else "system"


def _code(value: Any) -> str | None:
    try:
        return vocab.norm_code(value)
    except SpendError:
        return None


def _bounded_label(value: Any) -> str:
    return vocab.label(value)[:64]


def environment_now() -> str:
    from core.config import normalize_env, settings

    return normalize_env(getattr(settings, "env", ""))[:32]


def _from_node(node: NodeFacts | None, path: str) -> tuple[uuid.UUID | None, str | None, str | None] | None:
    """``(node_id, path, reason)`` for a matched node, or ``None`` when nothing matched."""
    if node is None:
        return None
    if not node.active:
        return None, None, "inactive_node"
    return node.id, path, None


def decide(hints: Hints, facts: Facts) -> tuple[uuid.UUID | None, str | None, str | None, MappingFacts | None]:
    """``(org_node_id, attribution_path, unattributed_reason, label_mapping)``; pure.

    ``label_mapping`` is the agent's or the workflow's mapping, which gives
    the product line and use case before the application's.
    """
    mappings = facts.mappings
    application = application_of(hints.application)

    def node_of(source_type: str, ref: Any, path: str) -> tuple[uuid.UUID | None, str | None, str | None] | None:
        if ref is None:
            return None
        mapping = mappings.get((source_type, str(ref)))
        if mapping is None or mapping.org_node_id is None:
            return None
        node = facts.nodes_by_id.get(mapping.org_node_id)
        if node is None:
            return None
        return _from_node(node, path)

    def department() -> tuple[uuid.UUID | None, str | None, str | None] | None:
        if facts.user_department_id is None:
            return None
        found = node_of("department", facts.user_department_id, "department_mapping")
        if found is not None:
            return found
        code = _code(facts.user_department_code)
        node = facts.nodes_by_code.get(code) if code else None
        if node is None:
            return None, None, "unknown_label"
        return _from_node(node, "department_code")

    steps: list[Any]
    label_mapping: MappingFacts | None = None
    if facts.agent_uuid is not None:
        label_mapping = mappings.get(("agent", str(facts.agent_uuid)))
        agent = facts.agent

        def cost_centre() -> tuple[uuid.UUID | None, str | None, str | None] | None:
            if agent is None or agent.cost_center_id is None:
                return None
            found = node_of("cost_center", agent.cost_center_id, "cost_centre_mapping")
            if found is not None:
                return found
            code = _code(agent.cost_center_code)
            node = facts.nodes_by_code.get(code) if code else None
            if node is None:
                return None, None, "unknown_label"
            return _from_node(node, "cost_centre_code")

        steps = [
            lambda: node_of("agent", facts.agent_uuid, "agent_mapping"),
            cost_centre,
            lambda: node_of("application", application, "application_mapping"),
        ]
        fallback = "no_mapping"
    elif facts.workflow_uuid is not None:
        label_mapping = mappings.get(("workflow", str(facts.workflow_uuid)))
        steps = [
            lambda: node_of("workflow", facts.workflow_uuid, "workflow_mapping"),
            lambda: node_of("application", application, "application_mapping"),
            department,
        ]
        fallback = "no_mapping"
    else:
        steps = [lambda: node_of("application", application, "application_mapping"), department]
        fallback = "no_source" if application == "system" and facts.user_uuid is None else "no_mapping"
    for step in steps:
        found = step()
        if found is not None:
            node_id, path, reason = found
            return node_id, path, reason, label_mapping
    return None, None, fallback, label_mapping


def use_case_of(hints: Hints, facts: Facts, label_mapping: MappingFacts | None) -> str:
    """The first usable of: the agent's or workflow's mapping, the application's mapping, the registry,
    the agent type, the call site's default; else ``unattributed``."""
    app_mapping = facts.mappings.get(("application", application_of(hints.application)))
    agent = facts.agent
    for candidate in (
        label_mapping.use_case if label_mapping else None,
        app_mapping.use_case if app_mapping else None,
        agent.registry_use_case if agent else None,
        agent.agent_type if agent else None,
        hints.default_use_case,
    ):
        value = _bounded_label(candidate)
        if value:
            return value
    return "unattributed"


def product_line_of(hints: Hints, facts: Facts, label_mapping: MappingFacts | None) -> str | None:
    app_mapping = facts.mappings.get(("application", application_of(hints.application)))
    for candidate in (
        label_mapping.product_line if label_mapping else None,
        app_mapping.product_line if app_mapping else None,
    ):
        if candidate:
            return str(candidate)[:64]
    return None


def resolved_from(hints: Hints, facts: Facts, business_unit: uuid.UUID | None = None) -> Resolved:
    """The resolution of ``hints`` over loaded ``facts`` (pure; the business unit is looked up separately)."""
    node_id, path, reason, label_mapping = decide(hints, facts)
    agent = facts.agent
    risk = agent.risk_tier if agent and agent.risk_tier in vocab.RISK_TIERS else None
    version = hints.agent_version or (agent.version if agent else None)
    return Resolved(
        org_node_id=node_id,
        business_unit_node_id=business_unit if node_id is not None else None,
        attribution_path=path,
        unattributed_reason=reason,
        product_line=product_line_of(hints, facts, label_mapping),
        use_case=use_case_of(hints, facts, label_mapping),
        agent_id=facts.agent_uuid,
        agent_version=str(version)[:20] if version else None,
        risk_tier=risk,
        region=(facts.region or None),
        environment=facts.environment,
        initiating_user_id=facts.user_uuid,
        workflow_id=facts.workflow_uuid,
        run_id=str(hints.run_id)[:64] if hints.run_id else None,
        application=application_of(hints.application),
    )


def failed(hints: Hints, *, agent_uuid: uuid.UUID | None = None, user_uuid: uuid.UUID | None = None) -> Resolved:
    """The resolution of a record whose attribution could not be read: kept, ``resolver_failed``."""
    return Resolved(
        org_node_id=None,
        business_unit_node_id=None,
        attribution_path=None,
        unattributed_reason="resolver_failed",
        product_line=None,
        use_case=_bounded_label(hints.default_use_case) or "unattributed",
        agent_id=agent_uuid if hints.origin == "backfill" else None,
        agent_version=str(hints.agent_version)[:20] if hints.agent_version else None,
        risk_tier=None,
        region=None,
        environment=environment_now(),
        initiating_user_id=user_uuid,
        workflow_id=as_uuid(hints.workflow_id),
        run_id=str(hints.run_id)[:64] if hints.run_id else None,
        application=application_of(hints.application),
    )


# ---------------------------------------------------------------- loaders


async def _region(session: Any, tenant_id: uuid.UUID) -> str | None:
    from core.config import settings
    from core.governance.residency import normalise_region
    from core.models.governance_config import GovernanceConfig

    rows = (
        await session.execute(select(GovernanceConfig.data_region).where(GovernanceConfig.tenant_id == tenant_id))
    ).all()
    raw = rows[0][0] if rows and rows[0][0] else getattr(settings, "data_region", "")
    return normalise_region(raw)[:8] or None


async def _agent(session: Any, tenant_id: uuid.UUID, agent_uuid: uuid.UUID) -> AgentFacts | None:
    rows = (await session.execute(_AGENT_SQL, {"tid": tenant_id, "agent": agent_uuid})).all()
    if not rows:
        return None
    row = rows[0]
    return AgentFacts(
        version=str(row[0]) if row[0] is not None else None,
        agent_type=str(row[1]) if row[1] is not None else None,
        status=str(row[2]) if row[2] is not None else None,
        cost_center_id=as_uuid(row[3]),
        cost_center_code=str(row[4]) if row[4] is not None else None,
        risk_tier=str(row[6]) if row[6] is not None else None,
        registry_use_case=str(row[7]) if row[7] is not None else None,
    )


async def _user_department(
    session: Any, tenant_id: uuid.UUID, user_uuid: uuid.UUID
) -> tuple[uuid.UUID | None, str | None]:
    rows = (await session.execute(_USER_DEPARTMENT_SQL, {"tid": tenant_id, "user": user_uuid})).all()
    if not rows:
        return None, None
    return as_uuid(rows[0][0]), (str(rows[0][1]) if rows[0][1] is not None else None)


async def _mappings(
    session: Any, tenant_id: uuid.UUID, sources: list[tuple[str, str]]
) -> dict[tuple[str, str], MappingFacts]:
    from core.models.spend import SpendSourceMapping as M

    if not sources:
        return {}
    statement = select(M.source_type, M.source_ref, M.org_node_id, M.product_line, M.use_case).where(
        M.tenant_id == tenant_id,
        M.active.is_(True),
        or_(*(and_(M.source_type == kind, M.source_ref == ref) for kind, ref in sources)),
    )
    rows = (await session.execute(statement)).all()
    return {(r[0], r[1]): MappingFacts(org_node_id=r[2], product_line=r[3], use_case=r[4]) for r in rows}


async def _nodes(
    session: Any, tenant_id: uuid.UUID, ids: set[uuid.UUID], codes: set[str]
) -> tuple[dict[uuid.UUID, NodeFacts], dict[str, NodeFacts]]:
    from core.models.spend import SpendOrgNode as N

    if not ids and not codes:
        return {}, {}
    conditions = []
    if ids:
        conditions.append(N.id.in_(sorted(ids, key=str)))
    if codes:
        conditions.append(N.code.in_(sorted(codes)))
    rows = (
        await session.execute(select(N.id, N.code, N.kind, N.active).where(N.tenant_id == tenant_id, or_(*conditions)))
    ).all()
    found = [NodeFacts(id=r[0], code=r[1], kind=r[2], active=bool(r[3])) for r in rows]
    return {n.id: n for n in found}, {n.code: n for n in found}


async def _initiator(session: Any, tenant_id: uuid.UUID, workflow_run_uuid: uuid.UUID) -> uuid.UUID | None:
    from core.models.workflow import WorkflowRun
    from workflows.run_sync import workflow_run_initiator

    run = (
        (
            await session.execute(
                select(WorkflowRun).where(WorkflowRun.id == workflow_run_uuid, WorkflowRun.tenant_id == tenant_id)
            )
        )
        .scalars()
        .first()
    )
    return workflow_run_initiator(run) if run is not None else None


async def initiating_user(session: Any, tenant_id: uuid.UUID, hints: Hints) -> uuid.UUID | None:
    """The hint's user id when it is one; else the recorded initiator of the workflow run; else ``None``."""
    user = as_uuid(hints.initiating_user_id)
    if user is not None:
        return user
    run = as_uuid(hints.workflow_run_id)
    if run is None:
        return None
    return await _initiator(session, tenant_id, run)


async def load_facts(session: Any, tenant_id: uuid.UUID, hints: Hints, *, user_uuid: uuid.UUID | None) -> Facts:
    """Read what the decision needs: the region, the agent, the mappings, the user's department, the nodes."""
    region = await _region(session, tenant_id)
    agent_uuid = as_uuid(hints.agent_id)
    agent: AgentFacts | None = None
    if agent_uuid is not None:
        agent = await _agent(session, tenant_id, agent_uuid)
        if hints.origin != "backfill" and (agent is None or (agent.status or "") in _NEVER_RUNS):
            agent_uuid, agent = None, None
    workflow_uuid = as_uuid(hints.workflow_id)
    department_id: uuid.UUID | None = None
    department_code: str | None = None
    if agent_uuid is None and user_uuid is not None:
        department_id, department_code = await _user_department(session, tenant_id, user_uuid)
    sources: list[tuple[str, str]] = [("application", application_of(hints.application))]
    if agent_uuid is not None:
        sources.append(("agent", str(agent_uuid)))
        if agent is not None and agent.cost_center_id is not None:
            sources.append(("cost_center", str(agent.cost_center_id)))
    elif workflow_uuid is not None:
        sources.append(("workflow", str(workflow_uuid)))
    if department_id is not None:
        sources.append(("department", str(department_id)))
    mappings = await _mappings(session, tenant_id, sources)
    codes = {c for c in (_code(agent.cost_center_code) if agent else None, _code(department_code)) if c}
    ids = {m.org_node_id for m in mappings.values() if m.org_node_id is not None}
    by_id, by_code = await _nodes(session, tenant_id, ids, codes)
    return Facts(
        agent_uuid=agent_uuid,
        agent=agent,
        workflow_uuid=workflow_uuid,
        user_uuid=user_uuid,
        user_department_id=department_id,
        user_department_code=department_code,
        mappings=mappings,
        nodes_by_id=by_id,
        nodes_by_code=by_code,
        region=region,
        environment=environment_now(),
    )


def _cache_key(tenant_id: uuid.UUID, hints: Hints, user_uuid: uuid.UUID | None) -> tuple[str, ...]:
    agent = as_uuid(hints.agent_id)
    return (
        str(tenant_id),
        str(agent or ""),
        application_of(hints.application),
        str(as_uuid(hints.workflow_id) or ""),
        "" if agent is not None else str(user_uuid or ""),
        _bounded_label(hints.default_use_case),
        hints.origin,
    )


def _per_event(resolved: Resolved, hints: Hints, user_uuid: uuid.UUID | None) -> Resolved:
    """Apply what varies per event over a cached resolution: the run, the hint's version, the user."""
    return replace(
        resolved,
        run_id=str(hints.run_id)[:64] if hints.run_id else None,
        agent_version=str(hints.agent_version)[:20] if hints.agent_version else resolved.agent_version,
        initiating_user_id=user_uuid,
    )


async def resolve(session: Any, tenant_id: uuid.UUID, hints: Hints, *, now: float | None = None) -> Resolved:
    """The attribution of ``hints`` for ``tenant_id``; never raises (a failure is ``resolver_failed``).

    The reads run in a savepoint, so a failed read leaves the caller's
    transaction usable and the record is still written.
    """
    from core.spend import org

    clock_now = time.monotonic() if now is None else now
    user_uuid = as_uuid(hints.initiating_user_id)
    try:
        async with session.begin_nested():
            user_uuid = await initiating_user(session, tenant_id, hints)
            key = _cache_key(tenant_id, hints, user_uuid)
            hit = _RESOLUTION_CACHE.get(key)
            if hit is not None and clock_now - hit[0] < CACHE_TTL_SECONDS:
                return _per_event(hit[1], hints, user_uuid)
            facts = await load_facts(session, tenant_id, hints, user_uuid=user_uuid)
            node_id, _path, _reason, _labels = decide(hints, facts)
            business_unit = await org.business_unit_of(session, tenant_id, node_id) if node_id else None
            resolved = resolved_from(hints, facts, business_unit)
    # enterprise-gate: broad-except-ok reason=attribution-failure-records-the-usage-as-unattributed-and-logs
    except Exception as exc:
        logger.warning("spend_attribution_failed", error_type=type(exc).__name__, origin=hints.origin)
        return failed(hints, agent_uuid=as_uuid(hints.agent_id), user_uuid=user_uuid)
    if len(_RESOLUTION_CACHE) >= CACHE_MAX:
        _RESOLUTION_CACHE.clear()
    _RESOLUTION_CACHE[key] = (clock_now, resolved)
    return _per_event(resolved, hints, user_uuid)
