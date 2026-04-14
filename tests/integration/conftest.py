"""Integration test fixtures — async test client, database setup, JWT tokens."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# ---------------------------------------------------------------------------
# Environment defaults (explicit opt-in only)
# ---------------------------------------------------------------------------
DB_URL = os.getenv("AGENTICORG_DB_URL")
REDIS_URL = os.getenv("AGENTICORG_REDIS_URL")

# Keep only non-routing test defaults here. DB/Redis env should stay explicit
# so importing this conftest does not accidentally make unrelated tests think
# integration infrastructure is configured.
os.environ.setdefault("AGENTICORG_SECRET_KEY", "integration-test-secret-key-32chars")
os.environ.setdefault("AGENTICORG_ENV", "test")

# ---------------------------------------------------------------------------
# RSA key pair for test JWT signing
# ---------------------------------------------------------------------------
_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public_key = _private_key.public_key()

_private_pem = _private_key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
_public_numbers = _public_key.public_numbers()


def _int_to_base64url(n: int) -> str:
    """Convert an integer to an unpadded base64url string (for JWK)."""
    import base64

    byte_length = (n.bit_length() + 7) // 8
    raw = n.to_bytes(byte_length, byteorder="big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


TEST_KID = "test-kid-001"
TEST_JWKS = {
    "keys": [
        {
            "kty": "RSA",
            "kid": TEST_KID,
            "use": "sig",
            "alg": "RS256",
            "n": _int_to_base64url(_public_numbers.n),
            "e": _int_to_base64url(_public_numbers.e),
        }
    ]
}


# ---------------------------------------------------------------------------
# Test tenant / user identifiers
# ---------------------------------------------------------------------------
TEST_TENANT_ID = str(uuid.uuid4())
TEST_USER_SUB = f"user|{uuid.uuid4().hex[:12]}"
TEST_AGENT_ID = str(uuid.uuid4())


def _make_jwt(
    tenant_id: str = TEST_TENANT_ID,
    scopes: list[str] | None = None,
    agent_id: str = TEST_AGENT_ID,
    sub: str = TEST_USER_SUB,
    expires_in: int = 3600,
) -> str:
    """Mint a signed RS256 JWT for integration tests."""
    now = int(time.time())
    claims = {
        "sub": sub,
        "iss": "agenticorg-test-issuer",
        "aud": "agenticorg-tool-gateway",
        "iat": now,
        "exp": now + expires_in,
        "agenticorg:tenant_id": tenant_id,
        "agenticorg:agent_id": agent_id,
        "grantex:scopes": scopes or ["agenticorg:admin"],
    }
    return jwt.encode(claims, _private_pem, algorithm="RS256", headers={"kid": TEST_KID})


# ---------------------------------------------------------------------------
# Database engine / session for tests
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def db_engine() -> AsyncEngine:
    if not DB_URL:
        pytest.skip("integration tests require AGENTICORG_DB_URL")
    return create_async_engine(DB_URL, echo=False, pool_size=5, max_overflow=2)


@pytest_asyncio.fixture(scope="session")
async def _setup_schema(db_engine: AsyncEngine) -> None:
    """Create the schema once per test session."""
    import core.models  # noqa: F401 — registers all ORM models
    from core.models.base import BaseModel as ORMBase

    async with db_engine.begin() as conn:
        await conn.run_sync(ORMBase.metadata.create_all)


@pytest_asyncio.fixture
async def db_session(
    db_engine: AsyncEngine, _setup_schema: None
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a per-test database session that rolls back after each test."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        async with session.begin():
            yield session
            # Rollback so each test starts clean
            await session.rollback()


# ---------------------------------------------------------------------------
# Monkey-patch auth middleware to accept our test JWTs
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _patch_jwt_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the JWKS fetch with our local test JWKS and
    override the token validator so it trusts our test RSA key.
    """
    import auth.jwt as auth_jwt_module

    async def _fake_fetch_jwks():
        return TEST_JWKS

    monkeypatch.setattr(auth_jwt_module, "_fetch_jwks", _fake_fetch_jwks)
    # Clear rate limiter state between tests. REQ-04 moved auth failure
    # tracking to core.auth_state; only the in-memory fallback dicts remain.
    from core.auth_state import _mem_blocked, _mem_failures

    _mem_failures.clear()
    _mem_blocked.clear()
    import auth.grantex_middleware as gx_mw

    # Override _is_grantex_token to always return False in tests
    # so RS256 test tokens go through the legacy path (which is patched above)
    monkeypatch.setattr(gx_mw, "_is_grantex_token", lambda token: False)
    # Also override issuer validation so the test issuer is accepted
    monkeypatch.setattr(
        auth_jwt_module,
        "settings",
        type(auth_jwt_module.settings)(
            **{
                **{
                    field: getattr(auth_jwt_module.settings, field)
                    for field in auth_jwt_module.settings.model_fields
                },
                "jwt_issuer": "agenticorg-test-issuer",
                "jwt_public_key_url": "https://test.example.com/.well-known/jwks.json",
            }
        ),
    )


# ---------------------------------------------------------------------------
# Async HTTP test client
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Yield an ``httpx.AsyncClient`` wired to the FastAPI app.

    We replace the module-level engine in core.database with a NullPool engine
    so that asyncpg connections are never reused across event-loop boundaries
    (the root cause of "Future attached to a different loop" errors when
    BaseHTTPMiddleware spawns internal tasks).
    """
    if not DB_URL:
        pytest.skip("integration tests require AGENTICORG_DB_URL")

    from sqlalchemy.pool import NullPool

    import core.database as db_mod
    from api.main import app

    # Replace the app's engine with one that creates fresh connections each time
    test_engine = create_async_engine(DB_URL, echo=False, poolclass=NullPool)
    original_engine = db_mod.engine
    original_factory = db_mod.async_session_factory
    db_mod.engine = test_engine
    db_mod.async_session_factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    # Create tables on the test engine (the app will use this engine).
    # Import all model modules so they register with BaseModel.metadata,
    # then run create_all on the correct declarative base.
    import core.models  # noqa: F401 — registers all ORM models
    from core.models.base import BaseModel as ORMBase

    async with test_engine.begin() as conn:
        await conn.run_sync(ORMBase.metadata.create_all)

    # Seed the test tenant so FK constraints are satisfied (idempotent)
    from sqlalchemy import text as sa_text

    async with test_engine.begin() as conn:
        await conn.execute(sa_text(
            "INSERT INTO tenants (id, name, slug, plan, data_region, settings) "
            "VALUES (:id, :name, :slug, :plan, :region, :settings) "
            "ON CONFLICT (id) DO NOTHING"
        ), {
            "id": TEST_TENANT_ID, "name": "test-tenant",
            "slug": "test-tenant", "plan": "enterprise",
            "region": "IN", "settings": "{}",
        })

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as ac:
        yield ac

    # Restore originals and dispose test engine
    await test_engine.dispose()
    db_mod.engine = original_engine
    db_mod.async_session_factory = original_factory


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Return Authorization headers with a valid test JWT."""
    token = _make_jwt()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def make_auth_headers():
    """Factory fixture — mint headers with custom claims."""

    def _factory(
        tenant_id: str = TEST_TENANT_ID,
        scopes: list[str] | None = None,
        agent_id: str = TEST_AGENT_ID,
    ) -> dict[str, str]:
        token = _make_jwt(tenant_id=tenant_id, scopes=scopes, agent_id=agent_id)
        return {"Authorization": f"Bearer {token}"}

    return _factory


@pytest.fixture
def tenant_id() -> str:
    return TEST_TENANT_ID


@pytest.fixture
def agent_id() -> str:
    return TEST_AGENT_ID
