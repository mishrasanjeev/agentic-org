# SPDX-License-Identifier: Apache-2.0
"""Run the reference agents for a governed case and persist what they hand off.

``investigate_case`` moves a case to ``in_progress``, runs the Business Onboarding Underwriter with
no database connection held, then saves the memo, policy result, ownership graph, screening
results, parties and the agent's case record and moves the case to ``awaiting_decision`` (or
``failed`` with the reason). ``dispose_screening_hits`` proposes a disposition for every hit that has
none. ``run_case_step`` is the workflow engine's ``case_agent`` step.

Everything here is gated per tenant by the ``governed_cases.enabled`` flag, off by default; a flag
that cannot be read counts as off. The provider tool gateway's grant check is supplied by
``CaseRuntime.authorizer_factory`` (PRD F-1 wires the run grant in).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from connectors.framework.verification_provider import VerificationProvider
from core.agents.business_underwriter import UnderwriterConfig, UnderwriterDependencies, run_underwriter
from core.agents.screening_disposition import DispositionConfig, DispositionDependencies, run_screening_disposition
from core.cases.decisions import DecisionVerifier, RequireDecisionGrant, record_decision
from core.cases.states import CaseError, CaseState
from core.cases.store import CASE_REF_RE, get_case, transition
from core.policy import EXAMPLES_DIR, Policy, PolicyLoadError, load_policies
from core.tool_gateway.provider_gateway import ToolAuthorizer

logger = structlog.get_logger()

FLAG_KEY = "governed_cases.enabled"
POLICY_BY_COUNTRY: dict[str, str] = {"US": "business_onboarding_us", "GB": "business_onboarding_uk"}
WORKFLOW_ACTOR = "workflow:business_onboarding"
_PLACEHOLDER = re.compile(r"^\$([a-z][a-z0-9_]*)$")

SessionFactory = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _default_session_factory(tenant_id: uuid.UUID) -> AbstractAsyncContextManager[AsyncSession]:
    from core.database import get_tenant_session

    return get_tenant_session(tenant_id)


def _default_provider(name: str) -> VerificationProvider:
    from connectors.providers.registry import ProviderRegistry

    return ProviderRegistry.create(name)


async def _default_flag(tenant_id: uuid.UUID) -> bool:
    from core.feature_flags import is_enabled

    return await is_enabled(FLAG_KEY, tenant_id=tenant_id, default=False)


def load_case_policies(directory: str = "") -> dict[str, Policy]:
    """Case policies by id. The shipped examples load only where the runtime is not strict."""
    from core.config import is_strict_runtime_env, settings

    return load_policies(
        Path(directory) if directory else EXAMPLES_DIR, require_production=is_strict_runtime_env(settings.env)
    )


def default_policy_id(jurisdiction: str) -> str:
    policy_id = POLICY_BY_COUNTRY.get(jurisdiction.split("-")[0])
    if policy_id is None:
        raise CaseError("policy_not_configured", jurisdiction, status=422)
    return policy_id


@dataclass
class CaseRuntime:
    provider_factory: Callable[[str], VerificationProvider] = _default_provider
    policies: Callable[[], Mapping[str, Policy]] = field(
        default=lambda: load_case_policies(_settings().case_policy_dir)
    )
    session_factory: SessionFactory = _default_session_factory
    flag: Callable[[uuid.UUID], Awaitable[bool]] = _default_flag
    authorizer_factory: Callable[[str, str], ToolAuthorizer | None] = lambda tenant_id, case_ref: None
    decision_verifier: DecisionVerifier = field(default_factory=RequireDecisionGrant)
    clock: Callable[[], datetime] = _utc_now
    llm_model: str = field(default_factory=lambda: _settings().case_llm_model)
    pseudonym_store: Any = None
    require_os_isolation: bool | None = None

    async def require_enabled(self, tenant_id: uuid.UUID) -> None:
        try:
            enabled = await self.flag(tenant_id)
        # enterprise-gate: broad-except-ok reason=flag-lookup-failure-fails-closed-as-disabled
        except Exception as exc:
            logger.warning("governed_cases_flag_lookup_failed", error=type(exc).__name__)
            enabled = False
        if not enabled:
            raise CaseError("governed_cases_disabled", status=404)


def _settings() -> Any:
    from core.config import settings

    return settings


def _tenant(tenant_id: str | uuid.UUID) -> uuid.UUID:
    try:
        return tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    except ValueError as exc:
        raise CaseError("tenant_invalid", status=401) from exc


def _run_id(tenant: uuid.UUID, case_ref: str, kind: str) -> str:
    """Server-generated and tenant-prefixed: it keys the run's pseudonym map."""
    return f"tenant:{tenant}:case:{case_ref}:{kind}:{uuid.uuid4().hex}"


async def investigate_case(
    tenant_id: str | uuid.UUID, case_ref: str, *, runtime: CaseRuntime, actor: str
) -> dict[str, Any]:
    tenant = _tenant(tenant_id)
    await runtime.require_enabled(tenant)
    async with runtime.session_factory(tenant) as session:
        case = await get_case(session, tenant, case_ref, for_update=True)
        await transition(
            session, case, CaseState.IN_PROGRESS, actor=actor, reason="investigation_started", now=runtime.clock()
        )
        started_version = case.version
        application, provider_name, policy_id = dict(case.application), case.provider, case.policy_id

    outcome = None
    failure = ""
    try:
        policy = runtime.policies().get(policy_id)
    except PolicyLoadError as exc:
        logger.error("governed_case_policies_refused", reason=exc.reason.value)
        policy = None
    if policy is None:
        failure = "policy_not_configured"
    else:
        try:
            provider = runtime.provider_factory(provider_name)
        # enterprise-gate: broad-except-ok reason=provider-construction-failure-fails-the-case-closed
        except Exception as exc:
            logger.error("governed_case_provider_unavailable", case_ref=case_ref, error=type(exc).__name__)
            failure = "provider_unavailable"
        else:
            outcome = await _run_underwriter_safely(
                tenant_id=str(tenant),
                case_id=case_ref,
                run_id=_run_id(tenant, case_ref, "underwriter"),
                application=application,
                config=UnderwriterConfig(
                    policy=policy, llm_model=runtime.llm_model, require_os_isolation=runtime.require_os_isolation
                ),
                deps=UnderwriterDependencies(
                    provider=provider,
                    authorizer=runtime.authorizer_factory(str(tenant), case_ref),
                    clock=runtime.clock,
                    pseudonym_store=runtime.pseudonym_store,
                ),
            )
            failure = (
                "investigation_error"
                if outcome is None
                else ("" if outcome.status == "completed" else outcome.failure_reason)
            )

    async with runtime.session_factory(tenant) as session:
        case = await get_case(session, tenant, case_ref, for_update=True)
        if case.version != started_version:
            # The case changed while the agent ran (for example it was withdrawn): keep what happened, drop the result.
            logger.warning("governed_case_result_discarded", case_ref=case_ref, reason="case_version_conflict")
            raise CaseError("case_version_conflict", f"expected {started_version}, found {case.version}")
        if outcome is not None:
            case.agent_records = [*(case.agent_records or []), outcome.case_record()]
        if failure or outcome is None or outcome.memo is None:
            case.failure_reason = (failure or "investigation_failed")[:128]
            await transition(
                session, case, CaseState.FAILED, actor=actor, reason=case.failure_reason, now=runtime.clock()
            )
            return {"case_ref": case_ref, "state": case.state, "failure_reason": case.failure_reason}
        memo = outcome.memo
        case.failure_reason = None
        case.subject = memo["subject"]
        case.memo = memo
        case.policy_result = memo["policy_result"]
        case.ownership_graph = outcome.ownership_graph
        case.screening_results = outcome.screening_results
        case.parties = outcome.parties
        case.screening_dispositions = []
        await transition(
            session, case, CaseState.AWAITING_DECISION, actor=actor, reason="memo_ready", now=runtime.clock()
        )
        return {
            "case_ref": case_ref,
            "state": case.state,
            "recommendation": memo["recommendation"]["proposed"],
            "tier": memo["policy_result"]["tier"],
            "screening_hits": sum(len(r["hits"]) for r in outcome.screening_results),
            "missing_items": [item["item"] for item in memo["missing_items"]],
        }


async def _run_underwriter_safely(**kwargs: Any) -> Any:
    try:
        return await run_underwriter(**kwargs)
    # enterprise-gate: broad-except-ok reason=unexpected-agent-error-fails-the-case-closed
    except Exception as exc:
        logger.error("governed_case_investigation_error", case_id=kwargs.get("case_id"), error=type(exc).__name__)
        return None


def _associated_names(case_application: Mapping[str, Any], parties: list[dict[str, Any]], party_name: str) -> list[str]:
    names = [str(case_application.get("legal_name") or "")]
    names += [str(p.get("name") or "") for p in parties]
    return sorted({name for name in names if name and name != party_name})


async def dispose_screening_hits(
    tenant_id: str | uuid.UUID, case_ref: str, *, runtime: CaseRuntime, actor: str, hit_ids: list[str] | None = None
) -> dict[str, Any]:
    tenant = _tenant(tenant_id)
    await runtime.require_enabled(tenant)
    async with runtime.session_factory(tenant) as session:
        case = await get_case(session, tenant, case_ref)
        if case.state != CaseState.AWAITING_DECISION:
            raise CaseError("transition_not_allowed", f"dispositions need awaiting_decision, case is {case.state}")
        version = case.version
        results, parties, application = list(case.screening_results), list(case.parties), dict(case.application)
        existing = {d["hit_id"] for d in case.screening_dispositions or []}
        provider_name = case.provider

    provider = runtime.provider_factory(provider_name)
    proposed: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    failures: list[str] = []
    for result in results:
        party = next((p for p in parties if p.get("name") == result["subject"]["name"]), None)
        for hit in result["hits"]:
            if hit["hit_id"] in existing or (hit_ids is not None and hit["hit_id"] not in hit_ids) or party is None:
                continue
            outcome = await run_screening_disposition(
                tenant_id=str(tenant),
                case_id=case_ref,
                screening_result=result,
                hit_id=hit["hit_id"],
                subject=party,
                associated_entities=_associated_names(application, parties, str(party.get("name"))),
                config=DispositionConfig(llm_model=runtime.llm_model),
                deps=DispositionDependencies(
                    provider=provider,
                    authorizer=runtime.authorizer_factory(str(tenant), case_ref),
                    clock=runtime.clock,
                    pseudonym_store=runtime.pseudonym_store,
                ),
                run_id=_run_id(tenant, case_ref, "disposition"),
            )
            records.append(outcome.case_record())
            if outcome.disposition is None:
                failures.append(outcome.failure_reason)
            else:
                proposed.append(outcome.disposition)

    async with runtime.session_factory(tenant) as session:
        case = await get_case(session, tenant, case_ref, for_update=True)
        if case.version != version:
            raise CaseError("case_version_conflict", f"expected {version}, found {case.version}")
        case.screening_dispositions = [*(case.screening_dispositions or []), *proposed]
        case.agent_records = [*(case.agent_records or []), *records]
        case.version = case.version + 1
        case.updated_at = runtime.clock()
    return {
        "case_ref": case_ref,
        "proposed": len(proposed),
        "failed": sorted(set(failures)),
        "outcomes": sorted(d["proposed_outcome"] for d in proposed),
    }


async def decide_case(
    tenant_id: str | uuid.UUID, case_ref: str, *, runtime: CaseRuntime, actor: str, outcome: str, grants: list[str]
) -> dict[str, Any]:
    tenant = _tenant(tenant_id)
    await runtime.require_enabled(tenant)
    async with runtime.session_factory(tenant) as session:
        case = await get_case(session, tenant, case_ref, for_update=True)
        await record_decision(
            session,
            case,
            outcome=outcome,
            grants=grants,
            verifier=runtime.decision_verifier,
            actor=actor,
            now=runtime.clock(),
        )
        return {"case_ref": case_ref, "state": case.state}


# --- workflow step ------------------------------------------------------------------------------

_ACTIONS = ("investigate", "dispose_screening_hits", "record_decision")


def _resolve(value: Any, state: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        match = _PLACEHOLDER.match(value)
        if match:
            for scope in (state.get("trigger_payload") or {}, state.get("context") or {}):
                if isinstance(scope, Mapping) and match.group(1) in scope:
                    return scope[match.group(1)]
            return None
    return value


def _hitl_decision(state: Mapping[str, Any], step_id: str | None) -> str | None:
    if not step_id:
        return None
    result = (state.get("step_results") or {}).get(step_id) or {}
    output = result.get("output") if isinstance(result, Mapping) else None
    if isinstance(output, Mapping):
        decision = output.get("decision") or (output.get("hitl_decision") or {}).get("decision")
        return str(decision) if decision else None
    return None


async def run_case_step(
    step: Mapping[str, Any], state: Mapping[str, Any], *, runtime: CaseRuntime | None = None
) -> dict[str, Any]:
    """Execute a ``case_agent`` workflow step. Refusals are failed step results with a reason code."""
    runtime = runtime or CaseRuntime()
    step_id = str(step.get("id", ""))
    action = step.get("action")
    tenant_id = str(state.get("tenant_id") or "")
    case_ref = _resolve(step.get("case_ref", "$case_ref"), state)

    def failed(reason: str) -> dict[str, Any]:
        logger.warning("case_agent_step_failed", step_id=step_id, action=action, reason=reason)
        return {
            "step_id": step_id,
            "type": "case_agent",
            "status": "failed",
            "error": reason,
            "output": {"reason": reason},
        }

    if action not in _ACTIONS:
        return failed("case_action_unknown")
    if not isinstance(case_ref, str) or not CASE_REF_RE.match(case_ref):
        return failed("case_ref_invalid")
    try:
        if action == "investigate":
            output = await investigate_case(tenant_id, case_ref, runtime=runtime, actor=WORKFLOW_ACTOR)
            if output.get("state") == CaseState.FAILED:
                return {**failed(str(output.get("failure_reason") or "investigation_failed")), "output": output}
        elif action == "dispose_screening_hits":
            output = await dispose_screening_hits(tenant_id, case_ref, runtime=runtime, actor=WORKFLOW_ACTOR)
        else:
            decision = _hitl_decision(state, step.get("decision_step"))
            if decision not in ("approve", "decline"):
                return failed("decision_not_recorded")
            grants = _resolve(step.get("decision_grants", "$decision_grants"), state)
            output = await decide_case(
                tenant_id, case_ref, runtime=runtime, actor=WORKFLOW_ACTOR, outcome=decision,
                grants=[str(g) for g in grants] if isinstance(grants, list) else [],
            )  # fmt: skip
    except CaseError as exc:
        return failed(exc.reason)
    return {"step_id": step_id, "type": "case_agent", "status": "completed", "output": output, "confidence": 1.0}
