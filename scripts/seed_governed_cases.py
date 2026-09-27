#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Sample governed cases for the local development stack (``make seed-cases``).

Needs ``make dev`` and ``make seed``. For the seeded development tenant it:

* turns the ``governed_cases.enabled`` flag on (idempotent);
* makes sure the tenant has the one active, shared agent per case role that every governed-case
  provider call is checked against (``business_underwriter`` and ``screening_disposition``), each
  with only its reference agent's read tools, registered with the stack's Grantex service and
  allowed the ``aml.cdd.onboarding`` purpose (idempotent);
* obtains a development root grant covering both agents' registered scopes, held in this process
  only, from which each agent run is delegated its own short-lived grant;
* submits one new case per mock provider fixture (default: a clean case, a missing-owner case, a
  probable false-positive screening hit, a true match and a thin file with no registry match);
* runs the Business Onboarding Underwriter on each against the mock provider service, then the
  Screening Disposition agent on every hit, exactly as the workflows do;
* writes a JSON summary with the new case references (``--output``, default standard output), which
  the browser suite reads.

Every run submits new cases, so a run never depends on what an earlier run or test did to its cases.
It refuses to run outside a development or test runtime, and refuses any Grantex service but the
stack's own (``GRANTEX_BASE_URL`` on this machine or the ``grantex`` compose service). Nothing here
approves, declines or reviews anything: the cases stop at ``awaiting_decision`` with proposed
dispositions for a human. The grant checks are not relaxed: a provider call the delegated grant does
not cover is refused and the case fails, as it would in production.

The root grant needs a *sandbox* Grantex developer key, which ``make seed-cases`` passes: a live
key's authorization request waits for the principal's passkey, which a seed cannot give.

    make seed-cases    # python -m scripts.seed_governed_cases --output ui/test-results/governed-cases-seed.json

The agents' prose comes from the model stub (``--llm-model``, default the stub's scripted model), so no
model credentials are needed; the deterministic documents are complete without prose.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from scripts.seed_dev import SAMPLE_AGENT_MODEL, SeedError, assert_development_runtime, seed_id

FLAG_KEY = "governed_cases.enabled"
SEED_ACTOR = "seed:governed_cases"
DEFAULT_FIXTURES: tuple[str, ...] = (
    "gb-clean-brightwater",
    "gb-missing-owner-marlpit",
    "us-false-positive-oakhollow",
    "gb-true-match-corvane",
    "us-thin-file-brambleway",
)
#: The purpose every sample case is submitted under, and the only one the case agents are allowed.
CASE_PURPOSE = "aml.cdd.onboarding"
CASE_AGENT_DOMAIN = "backoffice"
CASE_AGENT_VERSION = "1.0.0"
#: Hosts the seed may reach Grantex on: this machine, or the stack's ``grantex`` service.
LOCAL_GRANTEX_HOSTS = frozenset({"grantex", "localhost", "127.0.0.1", "::1"})
#: The Grantex agent that holds the development root grant, and the principal it is issued to.
ROOT_GRANT_HOLDER = "AgenticOrg development root grant"
ROOT_GRANT_PRINCIPAL = "agenticorg-dev-seed"
#: Long enough for one seed run; every grant delegated from it is shorter-lived still.
ROOT_GRANT_LIFETIME = "1h"


def parse_fixtures(value: str) -> tuple[str, ...]:
    keys = tuple(key.strip() for key in value.split(",") if key.strip())
    if not keys:
        raise SeedError("--fixtures names no fixture")
    return keys


def load_applications(keys: Sequence[str]) -> dict[str, dict[str, Any]]:
    """The fixture applications, failing before any write when a key is unknown."""
    from connectors.providers.mock.data import default_dataset  # noqa: PLC0415

    dataset = default_dataset()
    applications: dict[str, dict[str, Any]] = {}
    for key in keys:
        try:
            applications[key] = dict(dataset.business(key).application)
        except KeyError as exc:
            raise SeedError(f"unknown mock fixture {key!r}") from exc
    return applications


async def enable_flag(session: Any, tenant_id: uuid.UUID) -> None:
    from sqlalchemy import select  # noqa: PLC0415

    from core.models.feature_flag import FeatureFlag  # noqa: PLC0415

    row = (
        await session.execute(
            select(FeatureFlag).where(FeatureFlag.tenant_id == tenant_id, FeatureFlag.flag_key == FLAG_KEY)
        )
    ).scalar_one_or_none()
    if row is None:
        row = FeatureFlag(id=seed_id(f"flag:{FLAG_KEY}"), tenant_id=tenant_id, flag_key=FLAG_KEY)
        session.add(row)
    row.enabled, row.rollout_percentage = True, 100
    row.description = "Enabled by scripts/seed_governed_cases.py for the development tenant."
    await session.flush()


async def seed_cases(keys: Sequence[str], *, runtime: Any = None, llm_model: str = SAMPLE_AGENT_MODEL) -> dict[str, Any]:
    from core.cases.runtime import CaseRuntime, default_policy_id, dispose_screening_hits, investigate_case  # noqa: PLC0415
    from core.cases.states import CaseError  # noqa: PLC0415
    from core.cases.store import create_case  # noqa: PLC0415
    from core.config import settings  # noqa: PLC0415

    applications = load_applications(keys)
    runtime = runtime or CaseRuntime(llm_model=llm_model)
    tenant_id = seed_id("tenant")
    async with runtime.session_factory(tenant_id) as session:
        await enable_flag(session, tenant_id)

    summary: dict[str, Any] = {"tenant_id": str(tenant_id), "cases": {}}
    for key, application in applications.items():
        async with runtime.session_factory(tenant_id) as session:
            case = await create_case(
                session,
                tenant_id=tenant_id,
                application=application,
                purpose=CASE_PURPOSE,
                provider=settings.case_provider,
                policy_id=default_policy_id(str(application.get("jurisdiction") or "")),
                created_by=SEED_ACTOR,
                now=runtime.clock(),
            )
            case_ref = case.case_ref
        investigation = await investigate_case(tenant_id, case_ref, runtime=runtime, actor=SEED_ACTOR)
        entry: dict[str, Any] = {"case_ref": case_ref, "legal_name": application.get("legal_name"), **investigation}
        if investigation.get("state") == "awaiting_decision" and investigation.get("screening_hits"):
            try:
                disposed = await dispose_screening_hits(tenant_id, case_ref, runtime=runtime, actor=SEED_ACTOR)
            except CaseError as exc:
                entry["dispositions_refused"] = exc.reason
            else:
                entry["dispositions_proposed"] = disposed["proposed"]
                entry["disposition_outcomes"] = disposed["outcomes"]
        summary["cases"][key] = entry
    return summary


@dataclass(frozen=True)
class CaseAgentRole:
    agent_type: str
    name: str
    tools: tuple[str, ...]
    prompt_ref: str


def case_agent_roles() -> tuple[CaseAgentRole, ...]:
    """The two governed-case roles, each with exactly its reference agent's tool set."""
    from core.agents.business_underwriter import agent as underwriter  # noqa: PLC0415
    from core.agents.screening_disposition import agent as disposition  # noqa: PLC0415

    return (
        CaseAgentRole(
            underwriter.AGENT_NAME,
            "Business Onboarding Underwriter (development)",
            tuple(sorted(underwriter.TOOL_SET)),
            "core/agents/business_underwriter/prompts/narrative-1.0.0.txt",
        ),
        CaseAgentRole(
            disposition.AGENT_NAME,
            "Screening Disposition (development)",
            tuple(sorted(disposition.TOOL_SET)),
            "core/agents/screening_disposition/prompts/rationale-1.0.0.txt",
        ),
    )


def assert_local_grantex(environ: Mapping[str, str]) -> None:
    """Refuse any Grantex service but the stack's own, before anything is registered or written.

    The platform's Grantex client falls back to the hosted issuer when ``GRANTEX_BASE_URL`` is
    unset, and this seed registers agents and obtains a root grant, so an unnamed or remote issuer
    is refused rather than defaulted.
    """
    base_url = environ.get("GRANTEX_BASE_URL", "").strip()
    if not base_url:
        raise SeedError("GRANTEX_BASE_URL is not set: the seed only uses the stack's own Grantex service")
    try:
        parts = urlsplit(base_url)
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError for a malformed port
    except ValueError as exc:
        raise SeedError(f"GRANTEX_BASE_URL cannot be parsed ({exc})") from exc
    if parts.scheme not in ("http", "https") or parts.query or parts.fragment or host not in LOCAL_GRANTEX_HOSTS:
        raise SeedError(
            f"GRANTEX_BASE_URL must name the stack's own Grantex service "
            f"({', '.join(sorted(LOCAL_GRANTEX_HOSTS))}), not {host or base_url!r}"
        )
    if not environ.get("GRANTEX_API_KEY", "").strip():
        raise SeedError("GRANTEX_API_KEY is not set: `make seed-cases` passes the stack's development sandbox key")


def current_registration(
    client: Any, grantex_config: Mapping[str, Any], tools: Sequence[str], stored_tools: Sequence[str]
) -> dict[str, Any] | None:
    """The stored Grantex registration when Grantex still holds it unchanged, else ``None``.

    ``None`` means register again: nothing is stored, the agent's tools changed, the scopes differ,
    or the auth service no longer knows the agent (its database was reset). Blocking: call it off
    the event loop.
    """
    grantex_agent_id = str(grantex_config.get("grantex_agent_id") or "")
    scopes = grantex_config.get("grantex_scopes")
    if not grantex_agent_id or not isinstance(scopes, list) or not scopes or list(stored_tools) != list(tools):
        return None
    try:
        registration = client.agents.get(grantex_agent_id)
    # enterprise-gate: broad-except-ok reason=only-a-404-means-register-again-every-other-failure-stops-the-seed
    except Exception as exc:
        if getattr(exc, "status_code", None) == 404:
            return None
        # An unreadable registration is not evidence that it is gone; registering a second agent
        # would leave the first one registered with nothing tracking it. Stop instead.
        raise SeedError(f"cannot read the Grantex registration {grantex_agent_id}: {type(exc).__name__}") from exc
    registered = getattr(registration, "scopes", None)
    if not isinstance(registered, list | tuple) or sorted(registered) != sorted(scopes):
        return None
    return {
        "grantex_agent_id": grantex_agent_id,
        "grantex_did": str(grantex_config.get("grantex_did") or ""),
        "grantex_scopes": list(scopes),
    }


def issue_root_grant(client: Any, scopes: Sequence[str]) -> str:
    """A development root grant covering ``scopes``, held by the seed's own Grantex agent.

    Only a sandbox developer's authorization request is approved without the principal's passkey;
    with any other key the request stays pending and the seed stops. Blocking: call it off the
    event loop.
    """
    from grantex import AuthorizeParams, ExchangeTokenParams  # noqa: PLC0415

    wanted = list(dict.fromkeys(scopes))
    holder = next(
        (
            agent
            for agent in client.agents.list().agents
            if agent.name == ROOT_GRANT_HOLDER and set(wanted) <= set(agent.scopes or ())
        ),
        None,
    )
    if holder is None:
        holder = client.agents.register(
            name=ROOT_GRANT_HOLDER,
            scopes=wanted,
            description="Holds the development root grant the governed-case seed delegates from.",
        )
    request = client.authorize(
        AuthorizeParams(agent_id=holder.id, user_id=ROOT_GRANT_PRINCIPAL, scopes=wanted, expires_in=ROOT_GRANT_LIFETIME)
    )
    code = getattr(request, "code", None)
    if not code:
        raise SeedError(
            "Grantex did not approve the development root grant: GRANTEX_API_KEY must be the stack's "
            "sandbox developer key (`make seed-cases` passes it); a live key waits for the principal's passkey"
        )
    token = getattr(client.tokens.exchange(ExchangeTokenParams(code=code, agent_id=holder.id)), "grant_token", "")
    if not isinstance(token, str) or not token:
        raise SeedError("Grantex returned no root grant token")
    return token


def _platform_grantex_client() -> Any:
    """The client the token pool and the grant check use too, so every call acts as one developer."""
    from core.langgraph.grantex_auth import get_grantex_client  # noqa: PLC0415

    try:
        return get_grantex_client()
    except ValueError as exc:
        raise SeedError(str(exc)) from exc


def _tenant_session(tenant_id: uuid.UUID) -> Any:
    from core.database import get_tenant_session  # noqa: PLC0415

    return get_tenant_session(tenant_id)


async def _claim_case_agent(session: Any, tenant_id: uuid.UUID, role: CaseAgentRole) -> Any:
    """The seeded agent for ``role``, or ``None``; ``SeedError`` when another agent is in the way.

    The case runtime needs exactly one active, shared, tenant-wide agent per role, so an agent the
    seed did not create that is one - or that has the seeded name - stops the seed before anything
    is registered or written.
    """
    from sqlalchemy import and_, or_, select  # noqa: PLC0415

    from core.models.agent import Agent  # noqa: PLC0415

    agent_id = seed_id(f"agent:case:{role.agent_type}")
    active_shared = and_(
        Agent.status == "active",
        Agent.visibility == "tenant",
        Agent.owner_user_id.is_(None),
        Agent.company_id.is_(None),
    )
    same_name = and_(Agent.employee_name == role.name, Agent.version == CASE_AGENT_VERSION)
    other = (
        (
            await session.execute(
                select(Agent.id).where(
                    Agent.tenant_id == tenant_id,
                    Agent.agent_type == role.agent_type,
                    Agent.id != agent_id,
                    or_(active_shared, same_name),
                )
            )
        )
        .scalars()
        .first()
    )
    if other is not None:
        raise SeedError(
            f"{role.agent_type} agent {other}, which this seed did not create, is active and shared or has the "
            "seeded name; the case runtime needs exactly one active shared agent per role"
        )
    return await session.get(Agent, agent_id)


def _write_case_agent(
    session: Any, row: Any, tenant_id: uuid.UUID, role: CaseAgentRole, registration: Mapping[str, Any]
) -> Any:
    from core.models.agent import Agent  # noqa: PLC0415

    if row is None:
        row = Agent(id=seed_id(f"agent:case:{role.agent_type}"), tenant_id=tenant_id)
        session.add(row)
    row.name = row.employee_name = role.name
    row.agent_type, row.domain, row.version = role.agent_type, CASE_AGENT_DOMAIN, CASE_AGENT_VERSION
    row.description = f"Development governed-case agent ({role.agent_type})."
    row.system_prompt_ref, row.hitl_condition = role.prompt_ref, "always"
    # The one active, shared, tenant-wide agent the case runtime selects for this role.
    row.status, row.visibility, row.owner_user_id, row.company_id = "active", "tenant", None, None
    # Only the provider read tools this role's reference agent calls.
    row.authorized_tools, row.is_builtin = list(role.tools), False
    row.llm_model, row.llm_provider = SAMPLE_AGENT_MODEL, None
    # Replaced whole, so no legacy grant token survives in it.
    grantex = {
        "grantex_agent_id": registration["grantex_agent_id"],
        "grantex_did": str(registration.get("grantex_did") or ""),
        "grantex_scopes": list(registration["grantex_scopes"]),
        "case_purposes": [CASE_PURPOSE],
    }
    row.config = {**dict(row.config or {}), "grantex": grantex}
    return row


async def prepare_case_agents(
    tenant_id: uuid.UUID,
    *,
    grantex: Any = None,
    register: Callable[..., dict[str, Any] | None] | None = None,
    session_factory: Callable[[uuid.UUID], Any] | None = None,
) -> dict[str, str]:
    """Give each case role its agent, registered and allowed the case purpose, and a root grant.

    The root grant is set for this process only (``external_keys.grantex_root_grant_token``, which
    ``auth.token_pool`` delegates each run's grant from); it is never written, printed or
    returned. Returns the agent id per role.
    """
    from auth.grantex_registration import register_agent  # noqa: PLC0415
    from core.config import external_keys  # noqa: PLC0415

    client = grantex if grantex is not None else _platform_grantex_client()
    register = register or register_agent
    sessions = session_factory or _tenant_session
    roles = case_agent_roles()

    stored: dict[str, tuple[dict[str, Any], list[str]]] = {}
    async with sessions(tenant_id) as session:
        for role in roles:
            row = await _claim_case_agent(session, tenant_id, role)
            config = dict((row.config or {}).get("grantex") or {}) if row is not None else {}
            stored[role.agent_type] = (config, list(row.authorized_tools or []) if row is not None else [])

    # Grantex is called with no database transaction open.
    registrations: dict[str, dict[str, Any]] = {}
    for role in roles:
        config, stored_tools = stored[role.agent_type]
        registration = await asyncio.to_thread(current_registration, client, config, role.tools, stored_tools)
        if registration is None:
            registration = await asyncio.to_thread(
                register,
                name=role.name,
                agent_type=role.agent_type,
                domain=CASE_AGENT_DOMAIN,
                authorized_tools=list(role.tools),
            )
        if not registration or not registration.get("grantex_agent_id") or not registration.get("grantex_scopes"):
            raise SeedError(
                f"could not register the {role.agent_type} agent with the stack's Grantex service (see the log "
                "above); `make dev` starts it with the development sandbox key"
            )
        registrations[role.agent_type] = registration

    agents: dict[str, str] = {}
    async with sessions(tenant_id) as session:
        for role in roles:
            row = await _claim_case_agent(session, tenant_id, role)
            row = _write_case_agent(session, row, tenant_id, role, registrations[role.agent_type])
            agents[role.agent_type] = str(row.id)
        await session.flush()

    scopes = [scope for role in roles for scope in registrations[role.agent_type]["grantex_scopes"]]
    external_keys.grantex_root_grant_token = await asyncio.to_thread(issue_root_grant, client, scopes)
    return agents


async def seed_all(keys: Sequence[str], *, llm_model: str = SAMPLE_AGENT_MODEL) -> dict[str, Any]:
    """The case agents and their root grant, then the sample cases investigated with them."""
    agents = await prepare_case_agents(seed_id("tenant"))
    summary = await seed_cases(keys, llm_model=llm_model)
    summary["case_agents"] = agents
    return summary


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    parser = argparse.ArgumentParser(description="Submit and investigate sample governed cases (development only).")
    parser.add_argument("--fixtures", default=",".join(DEFAULT_FIXTURES), help="comma-separated mock fixture keys")
    parser.add_argument("--llm-model", default=SAMPLE_AGENT_MODEL, help="model for the agents' prose")
    parser.add_argument("--output", default="", help="write the JSON summary here instead of standard output")
    args = parser.parse_args(argv)
    try:
        assert_development_runtime(environ)
        # The Grantex client reads the process environment, which is ``environ`` here.
        assert_local_grantex(environ)
        summary = asyncio.run(seed_all(parse_fixtures(args.fixtures), llm_model=args.llm_model))
    except SeedError as exc:
        print(f"seed_governed_cases: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(summary, indent=2, sort_keys=True)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{text}\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
