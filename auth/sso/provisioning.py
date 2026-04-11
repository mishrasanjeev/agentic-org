"""JIT (just-in-time) user provisioning for SSO logins.

When a federated user authenticates for the first time, we create a
local User record so that all our RBAC / audit / feature-flag logic
continues to work uniformly for SSO and password users alike.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select

from core.database import async_session_factory
from core.models.sso_config import SSOConfig
from core.models.user import User

logger = structlog.get_logger()


async def jit_provision_user(
    tenant_id: uuid.UUID,
    provider_key: str,
    claims: dict,
) -> User:
    """Return an existing User or create a new one from IdP claims.

    Raises ValueError if the email domain is not allowed by the SSOConfig,
    or if JIT provisioning is disabled and the user doesn't already exist.
    """
    email = claims.get("email", "").lower().strip()
    if not email:
        raise ValueError("OIDC claims missing email")

    name = claims.get("name") or claims.get("preferred_username") or email.split("@")[0]

    async with async_session_factory() as session:
        # Look up existing user first
        result = await session.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.email == email,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            logger.info(
                "sso_existing_user",
                tenant_id=str(tenant_id),
                email=email,
                provider=provider_key,
            )
            return existing

        # Pull the SSO config for provisioning policy
        result = await session.execute(
            select(SSOConfig).where(
                SSOConfig.tenant_id == tenant_id,
                SSOConfig.provider_key == provider_key,
            )
        )
        config = result.scalar_one_or_none()
        if config is None:
            raise ValueError(f"SSO provider {provider_key!r} not configured")

        if not config.jit_provisioning:
            raise ValueError(
                f"User {email} does not exist and JIT provisioning is disabled"
            )

        if config.allowed_domains:
            domain = email.split("@", 1)[-1]
            if domain not in config.allowed_domains:
                raise ValueError(
                    f"Email domain {domain!r} not allowed for provider {provider_key!r}"
                )

        # Create the user
        user = User(
            tenant_id=tenant_id,
            email=email,
            name=name,
            role=config.default_role,
            status="active",
            password_hash=None,  # SSO-only users have no local password
            mfa_enabled=False,  # MFA is enforced by the IdP
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        logger.info(
            "sso_jit_provisioned",
            tenant_id=str(tenant_id),
            user_id=str(user.id),
            email=email,
            provider=provider_key,
            role=user.role,
        )
        return user
