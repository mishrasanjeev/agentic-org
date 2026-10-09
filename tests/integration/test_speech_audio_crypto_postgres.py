# SPDX-License-Identifier: Apache-2.0
"""Real PostgreSQL BYTEA scanner/rotation semantics in a rolled-back private schema."""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Column, LargeBinary, MetaData, Table, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema

from core.crypto import envelope, rewrap, verify_all
from core.speech import audio_crypto
from core.speech.audio import SpeechError

DB_URL = os.getenv("AGENTICORG_DB_URL", "")
pytestmark = pytest.mark.skipif(not DB_URL, reason="Requires local PostgreSQL")
LABEL = "speech_recordings.content"
DOTTED = "core.models.speech_recording:SpeechRecording:content"


@pytest.mark.asyncio
async def test_postgres_bytea_audio_rotation_and_key_retirement_are_tenant_scoped(monkeypatch):
    engine = create_async_engine(DB_URL, poolclass=NullPool)
    schema = "speech_audio_crypto_" + uuid.uuid4().hex
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    audio_a, audio_b = b"RIFF\x00\xffWAVEsynthetic-audio-a", b"RIFF\x01\xfeWAVEsynthetic-audio-b"
    scopes = [verify_all.TenantCompanyScope(tenant_a), verify_all.TenantCompanyScope(tenant_b)]
    kek = "projects/test/locations/global/keyRings/audio/cryptoKeys/retained"
    wrapping_key = Fernet(Fernet.generate_key())
    monkeypatch.setattr(envelope, "_wrap_dek", lambda _kek, dek: wrapping_key.encrypt(dek))
    monkeypatch.setattr(envelope, "_unwrap_dek", lambda _kek, wrapped: wrapping_key.decrypt(wrapped))
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "old:synthetic-old-audio-key")
    resolver = AsyncMock(return_value="")
    monkeypatch.setattr(audio_crypto, "resolve_tenant_kek", resolver)
    ciphertext_a = await audio_crypto.encrypt_audio(tenant_a, audio_a)
    ciphertext_b = await audio_crypto.encrypt_audio(tenant_b, audio_b)
    resolver.return_value = kek
    kms_audio = await audio_crypto.encrypt_audio(tenant_a, audio_a)
    resolver.return_value = ""
    rows = [
        {"id": uuid.uuid4(), "tenant_id": tenant_a, "content": ciphertext_a},
        {"id": uuid.uuid4(), "tenant_id": tenant_b, "content": ciphertext_b},
        {"id": uuid.uuid4(), "tenant_id": tenant_a, "content": kms_audio},
    ]
    table = Table(
        "speech_recordings",
        MetaData(),
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("tenant_id", UUID(as_uuid=True), nullable=False),
        Column("content", LargeBinary, nullable=False),
    )
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(CreateSchema(schema))
                await connection.execute(text("SELECT set_config('search_path', :schema, true)"), {"schema": schema})
                await connection.run_sync(table.metadata.create_all)
                await connection.execute(table.insert(), rows)

                @asynccontextmanager
                async def scoped(tenant_id, company_id=None):
                    assert tenant_id in (tenant_a, tenant_b)
                    assert company_id is None
                    yield connection

                for module in (rewrap, verify_all):
                    monkeypatch.setattr(module, "_SCANNERS", [(LABEL, DOTTED)])
                    monkeypatch.setattr(module, "discover_tenant_company_scopes", AsyncMock(return_value=scopes))
                    monkeypatch.setattr(module, "get_tenant_session", scoped)
                refs = await verify_all.scan_encrypted_columns_all_scopes()
                assert refs[LABEL] == {verify_all.KeyRef("vault", "old"), verify_all.KeyRef("envelope", kek)}
                with pytest.raises(verify_all.KeyStillReferencedError):
                    await verify_all.assert_key_unreferenced("old")
                only_a = await verify_all._fetch_column(connection, DOTTED, scope=scopes[0], strict=True)
                assert set(only_a) == {ciphertext_a, kms_audio}

                monkeypatch.setenv(
                    "AGENTICORG_VAULT_KEYRING", "new:synthetic-new-audio-key,old:synthetic-old-audio-key"
                )
                assert await rewrap.run(only_column=LABEL, only_kid="old", batch_size=1, dry_run=False) == 0
                read_stored = text("SELECT id, content, pg_typeof(content)::text FROM speech_recordings")
                stored = (await connection.execute(read_stored)).all()
                assert all(storage_type == "bytea" and isinstance(value, bytes) for _, value, storage_type in stored)
                by_id = {row_id: value for row_id, value, _storage_type in stored}
                assert by_id[rows[2]["id"]] == kms_audio
                assert await audio_crypto.decrypt_audio(tenant_a, by_id[rows[2]["id"]]) == audio_a
                with pytest.raises(SpeechError):
                    await audio_crypto.decrypt_audio(tenant_b, by_id[rows[2]["id"]])
                for row, audio, other_tenant in ((rows[0], audio_a, tenant_b), (rows[1], audio_b, tenant_a)):
                    value = by_id[row["id"]]
                    assert value.startswith(b"agko_vnew$")
                    assert audio not in value
                    assert await audio_crypto.decrypt_audio(row["tenant_id"], value) == audio
                    with pytest.raises(SpeechError):
                        await audio_crypto.decrypt_audio(other_tenant, value)
                await verify_all.assert_key_unreferenced("old")
                with pytest.raises(verify_all.KeyStillReferencedError):
                    await verify_all.assert_key_unreferenced(kek)
                monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "new:synthetic-new-audio-key")
                assert await audio_crypto.decrypt_audio(tenant_a, by_id[rows[0]["id"]]) == audio_a
                assert await rewrap.verify(LABEL) == 0
                assert await rewrap.run(only_column=LABEL, only_kid=None, batch_size=1, dry_run=False) == 0
                assert (await connection.execute(read_stored)).all() == stored

                # A damaged envelope must block retirement and vault rotation,
                # never disappear from the scanner or be rewritten as vault data.
                damaged = b'env1:{"kek":"synthetic-kek","wrapped_dek":null}'
                await connection.execute(
                    table.update().where(table.c.id == rows[2]["id"]).values(content=damaged)
                )
                before = (await connection.execute(read_stored)).all()
                with pytest.raises(ValueError, match="Malformed KMS envelope"):
                    await verify_all.assert_key_unreferenced("old")
                assert await rewrap.verify(LABEL) == 1
                assert await rewrap.run(only_column=LABEL, only_kid=None, batch_size=1, dry_run=False) == 1
                assert (await connection.execute(read_stored)).all() == before
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
