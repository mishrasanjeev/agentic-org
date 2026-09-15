#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Seed the local development stack (``make seed``).

Creates, or brings back to the seeded state, one development tenant:

* tenant ``acme-underwriting-dev``;
* the users of the development OIDC provider (``tools/oidc_stub/config.dev.json``),
  matched by email: Approver A (underwriter and approver) and Approver B;
* an OIDC sign-in configuration ``dev-oidc`` pointing at that provider's public
  client, stored disabled because the API only accepts HTTPS issuers on
  public hosts;
* two sample agents in shadow mode with no tools authorised;
* a two-step sequential approval policy (both steps for the ``domain_lead``
  role the seeded users hold). It does not require two different people: the
  approval flow does not enforce distinct approvers across steps yet.

Every row has a fixed id derived from its seed key, so running the seed again
changes nothing (and restores seeded fields someone edited). All names are
invented and all addresses use ``example.com``. Refuses to run unless
``AGENTICORG_ENV`` is a development or test runtime and no other common
environment variable names a production-like runtime; refuses a database that
is not on this machine or the local stack (loopback or the ``postgres`` compose
service) unless ``AGENTICORG_SEED_ALLOW_REMOTE_DB=1``; and fails without
writing anything if a seeded name is already taken by a row the seed did not
create.

    AGENTICORG_ENV=development AGENTICORG_DB_URL=postgresql+asyncpg://... python -m scripts.seed_dev

Users get no password unless ``AGENTICORG_SEED_PASSWORD`` (12 characters or
more) is set, in which case both users can also sign in with email and that
password.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
OIDC_STUB_CONFIG = REPO_ROOT / "tools" / "oidc_stub" / "config.dev.json"
SEED_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://example.com/agenticorg/dev-seed")
RELAXED_ENVS = frozenset({"development", "dev", "local", "test", "ci"})
PRODUCTION_MARKERS = frozenset({"production", "prod", "staging", "stage", "uat", "preprod", "live"})
ENV_VARIABLES = ("AGENTICORG_ENV", "APP_ENV", "ENVIRONMENT", "ENV", "NODE_ENV")
LOCAL_DB_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "postgres"})
ALLOW_REMOTE_DB_ENV = "AGENTICORG_SEED_ALLOW_REMOTE_DB"
MIN_PASSWORD_LENGTH = 12

TENANT_SLUG = "acme-underwriting-dev"
TENANT_NAME = "Acme Underwriting (development)"
SSO_PROVIDER_KEY = "dev-oidc"
SSO_CLIENT_ID = "agenticorg-dev-public"
OIDC_ISSUER = "http://127.0.0.1:9400"
APPROVAL_POLICY_NAME = "two-step-dev"
APPROVAL_STEP_ROLE = "domain_lead"
# Seed keys of rows earlier versions of this script created; removed when found.
RETIRED_POLICY_KEY = "approval-policy:four-eyes"


class SeedError(RuntimeError):
    """The seed cannot be applied safely; nothing was committed."""


def seed_id(key: str) -> uuid.UUID:
    """Stable id for a seeded row."""
    return uuid.uuid5(SEED_NAMESPACE, key)


@dataclass(frozen=True)
class SeedUser:
    key: str
    sub: str
    email: str
    name: str
    role: str
    domain: str


@dataclass(frozen=True)
class SeedAgent:
    key: str
    name: str
    agent_type: str
    domain: str
    confidence_floor: Decimal


# Approver A underwrites and approves; Approver B is the second approver.
USER_ROLES: Mapping[str, tuple[str, str]] = {
    "dev-approver-a": ("domain_lead", "backoffice"),
    "dev-approver-b": ("domain_lead", "backoffice"),
}

AGENTS: Sequence[SeedAgent] = (
    SeedAgent("agent:risk-sentinel", "Risk Sentinel (development)", "risk_sentinel", "backoffice", Decimal("0.950")),
    SeedAgent("agent:compliance-guard", "Compliance Guard (development)", "compliance_guard", "ops", Decimal("0.950")),
)


def assert_development_runtime(environ: Mapping[str, str]) -> None:
    for name in ENV_VARIABLES:
        value = environ.get(name, "").strip().lower()
        if value in PRODUCTION_MARKERS:
            raise SeedError(f"refusing to seed: {name}={value} indicates a production-like runtime")
    runtime = environ.get("AGENTICORG_ENV", "").strip().lower()
    if runtime not in RELAXED_ENVS:
        raise SeedError(
            f"refusing to seed: AGENTICORG_ENV must be one of {', '.join(sorted(RELAXED_ENVS))} "
            f"(got {runtime or 'nothing'})"
        )


def assert_local_database(db_url: str, environ: Mapping[str, str]) -> None:
    """Refuse a database outside this machine or the local stack unless explicitly allowed."""
    if environ.get(ALLOW_REMOTE_DB_ENV, "").strip() == "1":
        return
    try:
        parts = urlsplit(db_url)
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError for a malformed port
    except ValueError as exc:
        raise SeedError(f"refusing to seed: AGENTICORG_DB_URL cannot be parsed ({exc})") from exc
    if parts.query or parts.fragment:
        raise SeedError(
            "refusing to seed: AGENTICORG_DB_URL carries query parameters, which can select another host; "
            f"set {ALLOW_REMOTE_DB_ENV}=1 if that is intended"
        )
    if host not in LOCAL_DB_HOSTS:
        raise SeedError(
            f"refusing to seed: database host {host or '(none)'!r} is not local "
            f"({', '.join(sorted(LOCAL_DB_HOSTS))}); set {ALLOW_REMOTE_DB_ENV}=1 to seed it anyway"
        )


def load_users(path: Path = OIDC_STUB_CONFIG) -> list[SeedUser]:
    """The development provider's users, with the roles the seed gives them."""
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SeedError(f"cannot read the OIDC stub config {path}: {exc}") from exc
    by_sub = {entry.get("sub"): entry for entry in config.get("users", []) if isinstance(entry, dict)}
    users = []
    for sub, (role, domain) in USER_ROLES.items():
        entry = by_sub.get(sub)
        if entry is None:
            raise SeedError(f"the OIDC stub config has no user with sub {sub!r}")
        email = str(entry.get("email", ""))
        if not email.endswith("@example.com"):
            raise SeedError(f"seeded user {sub!r} must use an example.com address")
        users.append(SeedUser(f"user:{sub}", sub, email, str(entry.get("name", sub)), role, domain))
    return users


def _password_hash(environ: Mapping[str, str]) -> str | None:
    password = environ.get("AGENTICORG_SEED_PASSWORD", "")
    if not password:
        return None
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SeedError(f"AGENTICORG_SEED_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters")
    import bcrypt  # noqa: PLC0415 - only needed when a password is requested

    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


async def seed(db_url: str, environ: Mapping[str, str]) -> dict[str, Any]:
    """Apply the seed in one transaction and return what it contains."""
    assert_development_runtime(environ)
    assert_local_database(db_url, environ)
    users = load_users()
    password_hash = _password_hash(environ)

    from sqlalchemy import select, text  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: PLC0415
    from sqlalchemy.pool import NullPool  # noqa: PLC0415

    import core.models  # noqa: F401, PLC0415 - registers every mapped class
    from core.models.agent import Agent  # noqa: PLC0415
    from core.models.approval_policy import ApprovalPolicy, ApprovalStep  # noqa: PLC0415
    from core.models.sso_config import SSOConfig  # noqa: PLC0415
    from core.models.tenant import Tenant  # noqa: PLC0415
    from core.models.user import User  # noqa: PLC0415

    tenant_id = seed_id("tenant")
    engine = create_async_engine(db_url, poolclass=NullPool)
    summary: dict[str, Any] = {"tenant": TENANT_SLUG, "tenant_id": str(tenant_id)}
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
            await session.execute(
                text("SELECT set_config('agenticorg.tenant_id', :tid, true)"), {"tid": str(tenant_id)}
            )
            await session.execute(text("SELECT set_config('agenticorg.company_id', '', true)"))

            async def claim(model: Any, row_id: uuid.UUID, conflict: Any, what: str) -> Any:
                """The seeded row, or None when absent; SeedError when ``conflict`` finds someone else's row."""
                row = await session.get(model, row_id)
                if row is None:
                    other = (await session.execute(select(model).where(conflict))).scalars().first()
                    if other is not None:
                        raise SeedError(f"{what} already exists with id {other.id} that this seed did not create")
                return row

            tenant = await claim(Tenant, tenant_id, Tenant.slug == TENANT_SLUG, f"tenant {TENANT_SLUG!r}")
            if tenant is None:
                tenant = Tenant(id=tenant_id)
                session.add(tenant)
            tenant.name, tenant.slug, tenant.plan, tenant.data_region = TENANT_NAME, TENANT_SLUG, "enterprise", "EU"
            tenant.settings = {"seeded_by": "scripts/seed_dev.py"}
            await session.flush()

            summary["users"] = []
            for seed_user in users:
                user_id = seed_id(seed_user.key)
                conflict = (User.tenant_id == tenant_id) & (User.email == seed_user.email)
                user = await claim(User, user_id, conflict, f"user {seed_user.email}")
                if user is None:
                    user = User(id=user_id, tenant_id=tenant_id)
                    session.add(user)
                user.email, user.name = seed_user.email, seed_user.name
                user.role, user.domain, user.status, user.mfa_enabled = seed_user.role, seed_user.domain, "active", False
                if password_hash is not None:
                    user.password_hash = password_hash
                summary["users"].append({"email": seed_user.email, "oidc_sub": seed_user.sub, "role": seed_user.role})

            sso_id = seed_id("sso:dev-oidc")
            conflict = (SSOConfig.tenant_id == tenant_id) & (SSOConfig.provider_key == SSO_PROVIDER_KEY)
            sso = await claim(SSOConfig, sso_id, conflict, f"SSO provider {SSO_PROVIDER_KEY!r}")
            if sso is None:
                sso = SSOConfig(id=sso_id, tenant_id=tenant_id, provider_key=SSO_PROVIDER_KEY)
                session.add(sso)
            sso.provider_type, sso.display_name = "oidc", "Development identity provider"
            sso.config = {
                "issuer": OIDC_ISSUER,
                "client_id": SSO_CLIENT_ID,
                "scopes": ["openid", "profile", "email"],
                "redirect_uri": f"http://127.0.0.1:3000/api/v1/auth/sso/{SSO_PROVIDER_KEY}/callback",
            }
            sso.enabled, sso.jit_provisioning, sso.default_role = False, False, "analyst"
            sso.allowed_domains = ["example.com"]

            summary["agents"] = []
            for seed_agent in AGENTS:
                agent_id = seed_id(seed_agent.key)
                conflict = (
                    (Agent.tenant_id == tenant_id)
                    & (Agent.agent_type == seed_agent.agent_type)
                    & (Agent.employee_name == seed_agent.name)
                    & (Agent.version == "1.0.0")
                )
                agent = await claim(Agent, agent_id, conflict, f"agent {seed_agent.name!r}")
                if agent is None:
                    agent = Agent(id=agent_id, tenant_id=tenant_id)
                    session.add(agent)
                agent.name = agent.employee_name = seed_agent.name
                agent.agent_type, agent.domain, agent.version = seed_agent.agent_type, seed_agent.domain, "1.0.0"
                agent.description = f"Development sample agent ({seed_agent.agent_type})."
                agent.system_prompt_ref = f"prompts/{seed_agent.agent_type}.prompt.txt"
                agent.confidence_floor = seed_agent.confidence_floor
                agent.hitl_condition = f"confidence < {seed_agent.confidence_floor}"
                # Shadow mode and no authorised tools: a sample agent can never act on its own.
                agent.status, agent.authorized_tools, agent.is_builtin = "shadow", [], False
                summary["agents"].append(seed_agent.name)

            # An earlier version seeded a policy that claimed two different
            # approvers; nothing enforced that, so it is removed (only by its
            # seed-owned id) rather than left to mislead.
            retired = await session.get(ApprovalPolicy, seed_id(RETIRED_POLICY_KEY))
            if retired is not None:
                await session.delete(retired)
                await session.flush()

            policy_id = seed_id("approval-policy:two-step")
            conflict = (ApprovalPolicy.tenant_id == tenant_id) & (ApprovalPolicy.name == APPROVAL_POLICY_NAME)
            policy = await claim(ApprovalPolicy, policy_id, conflict, f"approval policy {APPROVAL_POLICY_NAME!r}")
            if policy is None:
                policy = ApprovalPolicy(id=policy_id, tenant_id=tenant_id, name=APPROVAL_POLICY_NAME)
                session.add(policy)
            policy.description = "Development two-step sequential approval, both steps for domain leads."
            policy.is_active = True
            await session.flush()
            for sequence in (1, 2):
                step_id = seed_id(f"approval-step:two-step:{sequence}")
                step = await session.get(ApprovalStep, step_id)
                if step is None:
                    step = ApprovalStep(id=step_id, policy_id=policy_id, sequence=sequence)
                    session.add(step)
                step.approver_role, step.quorum_required, step.quorum_total = APPROVAL_STEP_ROLE, 1, 1
                step.mode, step.step_metadata = "sequential", {}
            summary["approval_policy"] = APPROVAL_POLICY_NAME
    finally:
        await engine.dispose()
    return summary


def main() -> int:
    db_url = os.environ.get("AGENTICORG_DB_URL", "")
    try:
        if not db_url:
            raise SeedError("AGENTICORG_DB_URL is not set")
        summary = asyncio.run(seed(db_url, os.environ))
    except SeedError as exc:
        print(f"seed_dev: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
