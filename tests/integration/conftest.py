"""Integration test fixtures — async test client, database setup, JWT tokens."""
from __future__ import annotations

import os
import time
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# ---------------------------------------------------------------------------
# Environment defaults (CI service containers or local dev)
# ---------------------------------------------------------------------------
DB_URL = os.getenv(
    "AGENTFLOW_DB_URL",
    "postgresql+asyncpg://test:test@localhost:5432/test",
)
REDIS_URL = os.getenv(
    "AGENTFLOW_REDIS_URL",
    "redis://localhost:6379/0",
)

# Ensure the application picks up the test DB/Redis URLs and a valid secret key
os.environ.setdefault("AGENTFLOW_DB_URL", DB_URL)
os.environ.setdefault("AGENTFLOW_REDIS_URL", REDIS_URL)
os.environ.setdefault("AGENTFLOW_SECRET_KEY", "integration-test-secret-key-32chars")
os.environ.setdefault("AGENTFLOW_ENV", "test")

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
        "iss": "agentflow-test-issuer",
        "aud": "agentflow-tool-gateway",
        "iat": now,
        "exp": now + expires_in,
        "agentflow:tenant_id": tenant_id,
        "agentflow:agent_id": agent_id,
        "grantex:scopes": scopes or ["agentflow:admin"],
    }
    return jwt.encode(claims, _private_pem, algorithm="RS256", headers={"kid": TEST_KID})


# ---------------------------------------------------------------------------
# Database engine / session for tests
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def db_engine() -> AsyncEngine:
    return create_async_engine(DB_URL, echo=False, pool_size=5, max_overflow=2)


@pytest_asyncio.fixture(scope="session")
async def _setup_schema(db_engine: AsyncEngine) -> None:
    """Create the schema once per test session.

    We import Base from the application so that the same ORM metadata is used.
    If the application models rely on ``CREATE TABLE``, we run
    ``metadata.create_all``. As a fallback we at least verify connectivity.
    """
    try:
        from core.database import Base

        async with db_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception:
        # If models aren't complete enough for DDL, just verify connectivity
        async with db_engine.begin() as conn:
            await conn.execute(text("SELECT 1"))


@pytest_asyncio.fixture
async def db_session(
    db_engine: AsyncEngine, _setup_schema: None
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a per-test database session that rolls back after each test."""
    session_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
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
    # Also override issuer validation so the test issuer is accepted
    monkeypatch.setattr(auth_jwt_module, "settings", type(auth_jwt_module.settings)(
        **{
            **{
                field: getattr(auth_jwt_module.settings, field)
                for field in auth_jwt_module.settings.model_fields
            },
            "jwt_issuer": "agentflow-test-issuer",
        }
    ))


# ---------------------------------------------------------------------------
# Async HTTP test client
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Yield an ``httpx.AsyncClient`` wired to the FastAPI app."""
    from api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as ac:
        yield ac


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
