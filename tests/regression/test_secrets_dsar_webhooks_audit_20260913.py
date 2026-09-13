"""Regression tests for the 2026-09-13 enterprise sweep — findings 4-7.

4. OIDC ``client_secret`` is sealed with ``core.crypto.encrypt_for_tenant``
   on write (``client_secret_enc``), plaintext is never persisted, legacy
   plaintext rows still load with a warning, GET never returns the secret.
5. DSAR requests are persisted rows with honest statuses; ``erase`` is
   tenant-admin gated; a real-Postgres replay proves erasure + access.
6. SendGrid Event Webhook signatures are verified as ECDSA P-256 over
   ``timestamp + payload`` (base64 DER), fail-closed.
7. AA consent handles live in Redis (tenant-scoped, TTL); the provider
   callback returns 404 for unknown handles; the request body is typed.
"""

from __future__ import annotations

import base64
import inspect
import json
import os
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# 4. OIDC client_secret at rest
# ---------------------------------------------------------------------------


class TestOidcClientSecretSealed:
    @pytest.mark.asyncio
    async def test_upsert_encrypts_and_drops_plaintext(self, monkeypatch):
        from api.v1 import sso

        encrypt = AsyncMock(return_value="env:v1:sealed")
        with patch("core.crypto.encrypt_for_tenant", encrypt):
            sealed = await sso._seal_client_secret(
                {"issuer": "https://idp.example", "client_id": "cid", "client_secret": "shhh"},
                uuid.uuid4(),
            )
        assert "client_secret" not in sealed
        assert sealed["client_secret_enc"] == "env:v1:sealed"
        assert sealed["client_id"] == "cid"
        encrypt.assert_awaited_once()
        assert encrypt.await_args.args[0] == "shhh"

    @pytest.mark.asyncio
    async def test_upsert_without_secret_keeps_existing_sealed_secret(self):
        from api.v1.sso import SSOConfigIn, upsert_config

        tid = str(uuid.uuid4())
        existing = SimpleNamespace(
            id=uuid.uuid4(),
            provider_key="okta",
            provider_type="oidc",
            display_name="Okta",
            enabled=True,
            jit_provisioning=True,
            default_role="analyst",
            allowed_domains=[],
            config={"issuer": "https://idp.example", "client_id": "cid", "client_secret_enc": "env:v1:old"},
        )
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=existing)))
        session.add = MagicMock()
        session.refresh = AsyncMock()
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        body = SSOConfigIn(
            provider_key="okta",
            display_name="Okta",
            config={
                "issuer": "https://idp.example",
                "client_id": "cid",
                "redirect_uri": "https://app.example/cb",
            },
        )
        with (
            patch("api.v1.sso.get_tenant_session", factory),
            patch("api.v1.sso.OIDCProvider", MagicMock()),
        ):
            out = await upsert_config(body=body, tenant_id=tid)
        assert existing.config["client_secret_enc"] == "env:v1:old"
        assert "client_secret" not in existing.config
        assert not hasattr(out, "config")

    def test_get_response_model_never_carries_config(self):
        from api.v1.sso import SSOConfigOut

        assert "config" not in SSOConfigOut.model_fields
        assert "client_secret" not in SSOConfigOut.model_fields

    def test_provider_decrypts_sealed_secret(self):
        from auth.sso import oidc

        with patch("core.crypto.decrypt_for_tenant", return_value="plain-secret") as dec:
            provider = oidc.OIDCProvider(
                "okta",
                {
                    "issuer": "https://idp.example",
                    "client_id": "cid",
                    "client_secret_enc": "env:v1:sealed",
                    "redirect_uri": "https://app.example/cb",
                },
            )
        assert provider.client_secret == "plain-secret"
        dec.assert_called_once_with("env:v1:sealed")

    def test_legacy_plaintext_row_still_loads_with_warning(self):
        from auth.sso import oidc

        with patch.object(oidc.logger, "warning") as warn:
            secret = oidc.resolve_client_secret({"client_secret": "legacy"})
        assert secret == "legacy"
        warn.assert_called_once()
        assert warn.call_args.args[0] == "sso_client_secret_plaintext_legacy_row"

    def test_no_secret_configured_is_empty_string(self):
        from auth.sso.oidc import resolve_client_secret

        assert resolve_client_secret({}) == ""


# ---------------------------------------------------------------------------
# 5. DSAR — real Postgres replay (skipped without AGENTICORG_DB_URL)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not os.getenv("AGENTICORG_DB_URL"), reason="requires Postgres (AGENTICORG_DB_URL)")
@pytest.mark.asyncio
async def test_dsar_erase_and_access_replay_against_postgres():
    from sqlalchemy import select

    from audit.dsar import DSARHandler
    from core.database import async_session_factory, engine, get_tenant_session
    from core.models.audit import AuditLog
    from core.models.dsar import DSARRequestRecord
    from core.models.tenant import Tenant
    from core.models.user import User

    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: DSARRequestRecord.__table__.create(sync_conn, checkfirst=True))

    tid = uuid.uuid4()
    other_tid = uuid.uuid4()
    subject = f"subject-{uuid.uuid4().hex[:8]}@example.com"
    async with async_session_factory() as session:
        for t in (tid, other_tid):
            session.add(Tenant(id=t, name=f"dsar-{t}", slug=f"dsar-{t}", settings={}))
        await session.flush()
        session.add(User(id=uuid.uuid4(), tenant_id=tid, email=subject, name="Subject", role="analyst"))
        # Same e-mail in another tenant must be untouched.
        session.add(User(id=uuid.uuid4(), tenant_id=other_tid, email=subject, name="Other", role="analyst"))
        for t in (tid, other_tid):
            session.add(
                AuditLog(
                    tenant_id=t, event_type="x", actor_type="user", actor_id=subject,
                    action="did", outcome="success", details={},
                )
            )
        await session.commit()

    handler = DSARHandler()
    async with get_tenant_session(tid) as session:
        record = await handler.submit(
            session, tenant_id=tid, request_type="access", subject_email=subject, requested_by="admin@x"
        )
        record = await handler.process(session, record)
        assert record.status == "completed"
        assert record.result["totals"] == {"users": 1, "audit_log": 1, "agent_feedback": 0}
        assert record.result["users"][0]["email"] == subject

        record = await handler.submit(
            session, tenant_id=tid, request_type="erase", subject_email=subject, requested_by="admin@x"
        )
        record = await handler.process(session, record)
        assert record.status == "completed"
        assert record.result["users_anonymised"] == 1
        assert record.result["audit_log_pseudonymised"] == 1
        await session.commit()

        stored = (
            await session.execute(select(DSARRequestRecord).where(DSARRequestRecord.id == record.id))
        ).scalar_one()
        assert stored.status == "completed" and stored.completed_at is not None

    async with async_session_factory() as session:
        erased = (await session.execute(select(User).where(User.tenant_id == tid))).scalar_one()
        assert erased.email != subject and erased.email.startswith("erased:")
        assert erased.name is None and erased.password_hash is None
        assert erased.status == "inactive" and erased.sessions_invalid_before is not None
        untouched = (await session.execute(select(User).where(User.tenant_id == other_tid))).scalar_one()
        assert untouched.email == subject and untouched.name == "Other"
        other_audit = (await session.execute(select(AuditLog).where(AuditLog.tenant_id == other_tid))).scalar_one()
        assert other_audit.actor_id == subject


def test_dsar_handler_no_longer_fabricates_processing_or_deadlines():
    from audit import dsar

    src = inspect.getsource(dsar)
    assert '"status": "processing"' not in src
    assert "deadline_days" not in src


# ---------------------------------------------------------------------------
# 6. SendGrid ECDSA signature
# ---------------------------------------------------------------------------


@pytest.fixture
def sendgrid_keypair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_b64 = base64.b64encode(
        private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).decode()
    return private_key, public_b64


def _sign(private_key, timestamp: str, payload: bytes) -> str:
    der = private_key.sign(timestamp.encode() + payload, ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(der).decode()


class TestSendGridEcdsa:
    def test_valid_signature_accepted(self, sendgrid_keypair):
        from api.v1.webhooks import _verify_sendgrid_signature

        private_key, public_b64 = sendgrid_keypair
        payload = json.dumps([{"email": "a@b.c", "event": "delivered"}]).encode()
        ts = str(int(time.time()))
        assert _verify_sendgrid_signature(payload, _sign(private_key, ts, payload), ts, public_b64) is True

    def test_tampered_payload_or_timestamp_rejected(self, sendgrid_keypair):
        from api.v1.webhooks import _verify_sendgrid_signature

        private_key, public_b64 = sendgrid_keypair
        payload = b'[{"email":"a@b.c","event":"delivered"}]'
        ts = "1700000000"
        sig = _sign(private_key, ts, payload)
        assert _verify_sendgrid_signature(payload + b" ", sig, ts, public_b64) is False
        assert _verify_sendgrid_signature(payload, sig, "1700000001", public_b64) is False

    def test_wrong_key_rejected(self, sendgrid_keypair):
        from api.v1.webhooks import _verify_sendgrid_signature

        private_key, _ = sendgrid_keypair
        other = ec.generate_private_key(ec.SECP256R1())
        other_b64 = base64.b64encode(
            other.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        ).decode()
        payload = b"[]"
        assert _verify_sendgrid_signature(payload, _sign(private_key, "1", payload), "1", other_b64) is False

    def test_hmac_style_signature_is_rejected(self, sendgrid_keypair):
        """Pre-fix the check was HMAC-SHA256(key, ts+payload) hex — never what SendGrid sends."""
        import hashlib
        import hmac

        from api.v1.webhooks import _verify_sendgrid_signature

        _, public_b64 = sendgrid_keypair
        payload = b"[]"
        fake = hmac.new(public_b64.encode(), b"1" + payload, hashlib.sha256).hexdigest()
        assert _verify_sendgrid_signature(payload, fake, "1", public_b64) is False

    def test_malformed_inputs_and_missing_key_fail_closed(self, sendgrid_keypair, monkeypatch):
        from api.v1.webhooks import _verify_sendgrid_signature

        _, public_b64 = sendgrid_keypair
        assert _verify_sendgrid_signature(b"[]", "not-base64!!", "1", public_b64) is False
        assert _verify_sendgrid_signature(b"[]", "", "1", public_b64) is False
        assert _verify_sendgrid_signature(b"[]", "AAAA", "", public_b64) is False
        assert _verify_sendgrid_signature(b"[]", "AAAA", "1", "bm90LWEta2V5") is False
        monkeypatch.delenv("SENDGRID_WEBHOOK_KEY", raising=False)
        monkeypatch.delenv("AGENTICORG_WEBHOOK_ALLOW_UNSIGNED", raising=False)
        assert _verify_sendgrid_signature(b"[]", "AAAA", "1") is False


# ---------------------------------------------------------------------------
# 7. AA consent durability
# ---------------------------------------------------------------------------


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, int | None]] = {}

    async def get(self, key):
        item = self.store.get(key)
        return item[0] if item else None

    async def set(self, key, value, ex=None):
        self.store[key] = (value, ex)


class TestAAConsentDurability:
    @pytest.mark.asyncio
    async def test_redis_store_round_trip_is_tenant_scoped_with_ttl(self):
        from connectors.finance.aa_consent import CONSENT_RECORD_TTL_SECONDS, RedisConsentStore
        from connectors.finance.aa_consent_types import ConsentStatus

        redis = _FakeRedis()
        store_a = RedisConsentStore(redis, "tenant-a")
        store_b = RedisConsentStore(redis, "tenant-b")
        await store_a.put("h1", {"consent_handle": "h1", "status": ConsentStatus.PENDING, "consent_id": ""})
        assert (await store_a.get("h1"))["status"] is ConsentStatus.PENDING
        assert await store_b.get("h1") is None
        assert await RedisConsentStore.tenant_for_handle(redis, "h1") == "tenant-a"
        assert await RedisConsentStore.tenant_for_handle(redis, "nope") is None
        assert all(ttl == CONSENT_RECORD_TTL_SECONDS for _, ttl in redis.store.values())

        await store_a.put("h1", {"consent_handle": "h1", "status": ConsentStatus.APPROVED, "consent_id": "c1"})
        assert await store_a.handles_for_consent_id("c1") == ["h1"]

    @pytest.mark.asyncio
    async def test_callback_survives_process_restart(self):
        """Handle created by one manager instance is found by a fresh one (other replica)."""
        from connectors.finance.aa_consent import AAConsentManager, RedisConsentStore
        from connectors.finance.aa_consent_types import ConsentStatus

        redis = _FakeRedis()
        first = AAConsentManager(store=RedisConsentStore(redis, "t1"))
        await first._consents.put("h9", {"consent_handle": "h9", "status": ConsentStatus.PENDING, "consent_id": ""})
        second = AAConsentManager(store=RedisConsentStore(redis, "t1"))
        result = await second.handle_consent_callback("h9", ConsentStatus.APPROVED, "cid-9")
        assert result["status"] == "APPROVED"
        assert (await second.get_consent_status("h9"))["consent_id"] == "cid-9"

    @pytest.mark.asyncio
    async def test_callback_unknown_handle_returns_404_not_ok(self):
        from api.v1 import aa_callback

        redis = _FakeRedis()
        body = json.dumps(
            {"consent_handle": "ghost", "consent_status": "APPROVED", "timestamp": "2026-09-13T00:00:00Z"}
        ).encode()
        request = SimpleNamespace(body=AsyncMock(return_value=body), headers={})
        with (
            patch("api.v1.aa_callback.verify_aa_callback", AsyncMock(return_value="ok")),
            patch("api.v1.aa_callback._get_redis", MagicMock(return_value=SimpleNamespace(close=AsyncMock()))),
            patch("core.async_redis.get_async_redis", AsyncMock(return_value=redis)),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await aa_callback.consent_callback(request)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_callback_known_handle_updates_tenant_record(self):
        from api.v1 import aa_callback
        from connectors.finance.aa_consent import RedisConsentStore
        from connectors.finance.aa_consent_types import ConsentStatus

        redis = _FakeRedis()
        store = RedisConsentStore(redis, "tenant-7")
        await store.put("h7", {"consent_handle": "h7", "status": ConsentStatus.PENDING, "consent_id": ""})
        body = json.dumps(
            {
                "consent_handle": "h7",
                "consent_id": "c7",
                "consent_status": "APPROVED",
                "timestamp": "2026-09-13T00:00:00Z",
            }
        ).encode()
        request = SimpleNamespace(body=AsyncMock(return_value=body), headers={})
        with (
            patch("api.v1.aa_callback.verify_aa_callback", AsyncMock(return_value="ok")),
            patch("api.v1.aa_callback._get_redis", MagicMock(return_value=SimpleNamespace(close=AsyncMock()))),
            patch("core.async_redis.get_async_redis", AsyncMock(return_value=redis)),
        ):
            resp = await aa_callback.consent_callback(request)
        assert resp == {"status": "ok", "consent_handle": "h7"}
        assert (await store.get("h7"))["consent_id"] == "c7"
        assert (await store.get("h7"))["status"] is ConsentStatus.APPROVED

    @pytest.mark.asyncio
    async def test_consent_store_unavailable_is_503(self):
        from api.v1 import aa_callback

        with patch("core.async_redis.get_async_redis", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc_info:
                await aa_callback._get_consent_manager("t")
        assert exc_info.value.status_code == 503

    def test_create_consent_request_body_is_typed(self):
        from api.v1.aa_callback import create_consent_request
        from connectors.finance.aa_consent_types import ConsentRequest

        params = inspect.signature(create_consent_request).parameters
        assert params["params"].annotation in (ConsentRequest, "ConsentRequest")
        assert "_consent_managers" not in inspect.getsource(inspect.getmodule(create_consent_request))
