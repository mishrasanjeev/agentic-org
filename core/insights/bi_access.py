# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Per-tenant BI database access: read-only SQL logins for a tenant's BI tool.

A tenant administrator creates a login for a BI tool (Metabase and the like)
that connects to the read replica. Migration ``v6z108_insights_bi`` ties each
login to its tenant by name (``insights_bi_logins``) and lets it read the
views in schema ``insights_bi`` only (no table at all), its own tenant's rows
only, whatever it sets ``agenticorg.tenant_id`` to, and only on a standby
unless ``insights_bi_settings.allow_primary`` is on.

The application never holds CREATEROLE: it calls
``public.insights_bi_create_login`` and ``public.insights_bi_revoke_login``
(SECURITY DEFINER, EXECUTE only) inside the caller's tenant session. The
password is generated here, shown to the administrator once and never stored;
the database receives only its SCRAM-SHA-256 verifier (RFC 5802/7677), the
same secret PostgreSQL itself keeps, so the plaintext never reaches the
database, its statement log or ``audit_log``.

Off unless ``AGENTICORG_INSIGHTS_BI_ENABLED`` is on and the replica's host and
database are set (``bi_configured``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import string
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from core.config import settings

# The views a BI login may read (schema insights_bi), as created by v6z108.
BI_SCHEMA = "insights_bi"
BI_VIEWS: tuple[str, ...] = ("run_facts", "daily", "spend_daily", "agents", "approvals")
BI_PORT = 5432
# Cloud SQL's server certificate names the instance, not the address a BI tool
# dials, so ``verify-full`` would fail; ``verify-ca`` checks the chain against
# the replica's server CA (docs/runbooks/insights-bi-access.md). Channel binding
# (SCRAM-SHA-256-PLUS) ties the password exchange to that TLS session.
BI_SSLMODE = "verify-ca"
BI_CHANNEL_BINDING = "require"

# The login names the database functions accept.
ROLE_NAME_RE = re.compile(r"^bi_[a-z0-9_]{8,48}$")
_SUFFIX_ALPHABET = string.ascii_lowercase + string.digits
SCRAM_ITERATIONS = 4096
SCRAM_SALT_BYTES = 16

# SQLSTATEs the v6z108 functions raise for the outcomes the API maps.
SQLSTATE_LIMIT_REACHED = "BI409"
SQLSTATE_NOT_FOUND = "BI404"
# Schema public is writable by PUBLIC or the BI group: creation is refused.
SQLSTATE_UNAVAILABLE = "BI503"


class BiLoginLimitError(Exception):
    """The tenant already has the maximum number of active BI logins."""


class BiLoginNotFoundError(Exception):
    """No such BI login for this tenant."""


class BiAccessUnavailableError(Exception):
    """The database refuses to create BI logins in its current state (schema public is writable)."""


class BiAccessError(Exception):
    """A database refusal other than the mapped ones; carries the SQLSTATE only, never the statement."""


@dataclass(frozen=True)
class CreatedLogin:
    login_id: uuid.UUID
    role_name: str
    password: str
    created_at: datetime | None

    def __repr__(self) -> str:  # never print the password
        return f"CreatedLogin(login_id={self.login_id!s}, role_name={self.role_name!r}, password=<redacted>)"


def bi_configured() -> bool:
    """BI access is on and the replica's host and database are set."""
    return bool(
        settings.insights_bi_enabled and settings.insights_bi_host.strip() and settings.insights_bi_database.strip()
    )


def max_logins() -> int:
    return max(1, min(int(settings.insights_bi_max_logins_per_tenant), 50))


def connection_details() -> dict[str, Any]:
    """What a BI tool needs besides the login: host, port, database, TLS settings and the views."""
    return {
        "host": settings.insights_bi_host.strip(),
        "port": BI_PORT,
        "database": settings.insights_bi_database.strip(),
        "sslmode": BI_SSLMODE,
        "channel_binding": BI_CHANNEL_BINDING,
        "schema": BI_SCHEMA,
        "views": [f"{BI_SCHEMA}.{view}" for view in BI_VIEWS],
    }


def connection_string(role_name: str, password: str) -> str:
    details = connection_details()
    return (
        f"postgresql://{quote(role_name, safe='')}:{quote(password, safe='')}@{details['host']}:{details['port']}/"
        f"{quote(details['database'], safe='')}?sslmode={BI_SSLMODE}&channel_binding={BI_CHANNEL_BINDING}"
    )


def new_role_name(tenant_id: uuid.UUID) -> str:
    """``bi_<first 8 hex of the tenant id>_<10 random lowercase letters and digits>``."""
    suffix = "".join(secrets.choice(_SUFFIX_ALPHABET) for _ in range(10))
    name = f"bi_{tenant_id.hex[:8]}_{suffix}"
    if not ROLE_NAME_RE.fullmatch(name):  # a tenant id is a UUID, so this cannot happen
        raise ValueError("generated BI login name does not match the accepted pattern")
    return name


def new_password() -> str:
    """32 URL-safe characters (192 random bits)."""
    return secrets.token_urlsafe(24)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def scram_keys(password: str, salt: bytes, iterations: int) -> tuple[bytes, bytes, bytes]:
    """``(ClientKey, StoredKey, ServerKey)`` for ``password`` (RFC 5802 section 3)."""
    if not password or not password.isascii() or not password.isprintable():
        # Printable ASCII is unchanged by SASLprep, so the server derives the same keys.
        raise ValueError("BI passwords are printable ASCII")
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    return client_key, stored_key, server_key


def scram_verifier(password: str, *, salt: bytes | None = None, iterations: int = SCRAM_ITERATIONS) -> str:
    """PostgreSQL's SCRAM-SHA-256 secret: ``SCRAM-SHA-256$<iter>:<salt>$<StoredKey>:<ServerKey>`` (base64)."""
    if iterations < 4096:
        raise ValueError("at least 4096 iterations")
    salt = salt if salt is not None else secrets.token_bytes(SCRAM_SALT_BYTES)
    _client_key, stored_key, server_key = scram_keys(password, salt, iterations)
    return f"SCRAM-SHA-256${iterations}:{_b64(salt)}${_b64(stored_key)}:{_b64(server_key)}"


def _sqlstate(exc: DBAPIError) -> str | None:
    orig = getattr(exc, "orig", None)
    for attr in ("sqlstate", "pgcode"):
        code = getattr(orig, attr, None)
        if isinstance(code, str) and code:
            return code
    inner = getattr(orig, "__cause__", None)
    code = getattr(inner, "sqlstate", None)
    return code if isinstance(code, str) else None


async def list_logins(session: Any, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    """The tenant's logins, newest first (the tenant session's RLS also scopes them)."""
    from core.models.insights import InsightsBiLogin

    rows = (
        await session.execute(
            select(InsightsBiLogin)
            .where(InsightsBiLogin.tenant_id == tenant_id)
            .order_by(InsightsBiLogin.created_at.desc())
        )
    ).scalars()
    return [
        {
            "role_name": row.role_name,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "created_by": str(row.created_by) if row.created_by else None,
            "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        }
        for row in rows
    ]


async def create_login(session: Any, tenant_id: uuid.UUID, *, created_by: uuid.UUID | None, limit: int) -> CreatedLogin:
    """Create a login for ``tenant_id`` through ``public.insights_bi_create_login``.

    Raises :class:`BiLoginLimitError` at the limit and :class:`BiAccessUnavailableError`
    while schema public is writable by PUBLIC or the BI group. The statement carries the
    verifier, never the password; a refusal is re-raised without the statement.
    """
    role_name = new_role_name(tenant_id)
    password = new_password()
    params = {
        "tenant": tenant_id,
        "role": role_name,
        "verifier": scram_verifier(password),
        "created_by": created_by,
        "max": int(limit),
    }
    try:
        login_id = (
            await session.execute(
                text("SELECT public.insights_bi_create_login(:tenant, :role, :verifier, :created_by, :max)"),
                params,
            )
        ).scalar_one()
    except DBAPIError as exc:
        code = _sqlstate(exc)
        if code == SQLSTATE_LIMIT_REACHED:
            raise BiLoginLimitError() from None
        if code == SQLSTATE_UNAVAILABLE:
            raise BiAccessUnavailableError() from None
        raise BiAccessError(code or "unknown") from None
    created_at = (
        await session.execute(text("SELECT created_at FROM insights_bi_logins WHERE id = :id"), {"id": login_id})
    ).scalar_one_or_none()
    return CreatedLogin(login_id=login_id, role_name=role_name, password=password, created_at=created_at)


async def revoke_login(session: Any, tenant_id: uuid.UUID, role_name: str) -> str:
    """Revoke the tenant's login ``role_name``: ``dropped``, or ``nologin`` when the role had to stay."""
    if not ROLE_NAME_RE.fullmatch(role_name or ""):
        raise BiLoginNotFoundError()
    try:
        result = (
            await session.execute(
                text("SELECT public.insights_bi_revoke_login(:tenant, :role)"),
                {"tenant": tenant_id, "role": role_name},
            )
        ).scalar_one()
    except DBAPIError as exc:
        code = _sqlstate(exc)
        if code == SQLSTATE_NOT_FOUND:
            raise BiLoginNotFoundError() from None
        raise BiAccessError(code or "unknown") from None
    return str(result)
