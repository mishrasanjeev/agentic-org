# SPDX-License-Identifier: Apache-2.0
"""Audio BYTEA registration, vault rotation, and retained KMS key references."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from core.crypto import rewrap, verify_all
from core.speech import audio_crypto
from core.speech.audio import SpeechError

TENANT = UUID("11111111-1111-1111-1111-111111111111")
LABEL = "speech_recordings.content"
DOTTED = "core.models.speech_recording:SpeechRecording:content"
KEK = "projects/test/locations/global/keyRings/audio/cryptoKeys/retained"
ENVELOPE = "env1:" + json.dumps({"version": 1, "kek": KEK, "wrapped_dek": "synthetic"})


@pytest.mark.parametrize("container", (bytes, bytearray, memoryview))
@pytest.mark.parametrize(
    ("ciphertext", "expected"),
    (("agko_vold$synthetic", verify_all.KeyRef("vault", "old")), (ENVELOPE, verify_all.KeyRef("envelope", KEK))),
)
def test_bytea_parser_preserves_vault_and_env1_key_references(container, ciphertext, expected):
    value = container(ciphertext.encode())
    assert verify_all.parse_encrypted_container(value) == expected
    assert verify_all.parse_encrypted_container({"_encrypted": value}) == expected


def test_audio_column_is_registered_resolvable_and_retirement_sees_kms():
    assert dict(verify_all._SCANNERS)[LABEL] == DOTTED
    model = verify_all._resolve_scanner_model(DOTTED, strict=True)
    assert model.content.type.python_type is bytes
    refs = {}
    verify_all._collect_key_refs(refs, LABEL, [b"agko_vold$synthetic", ENVELOPE.encode()])
    assert refs == {LABEL: {verify_all.KeyRef("vault", "old"), verify_all.KeyRef("envelope", KEK)}}


@pytest.mark.asyncio
async def test_bytea_kms_reference_blocks_key_retirement(monkeypatch):
    monkeypatch.setattr(
        verify_all,
        "scan_encrypted_columns_all_scopes",
        AsyncMock(return_value={LABEL: {verify_all.parse_encrypted_container(ENVELOPE.encode())}}),
    )
    with pytest.raises(verify_all.KeyStillReferencedError) as error:
        await verify_all.assert_key_unreferenced(KEK)
    assert error.value.locations == {LABEL: {KEK}}


@pytest.mark.parametrize("container", (bytes, bytearray, memoryview))
def test_bytea_rotation_extract_and_wrap_preserve_bytes(container):
    ciphertext = "agko_vold$synthetic"
    assert rewrap._extract_ciphertext(LABEL, container(ciphertext.encode())) == ciphertext
    assert rewrap._wrap_ciphertext_for_column(LABEL, ciphertext) == ciphertext.encode()
    assert isinstance(rewrap._wrap_ciphertext_for_column(LABEL, ciphertext), bytes)


@pytest.mark.parametrize("value", (b"\xffRIFF", "agko_vold$synthetic", 123))
def test_invalid_bytea_values_abort_instead_of_being_silently_skipped(value):
    with pytest.raises(rewrap._RewrapRowError):
        rewrap._extract_ciphertext(LABEL, value)


@pytest.mark.parametrize("ciphertext", (ENVELOPE, ENVELOPE.removeprefix("env1:")))
def test_kms_envelopes_never_enter_vault_rotation(ciphertext):
    assert rewrap._stamp_kid(ciphertext) is None


@pytest.mark.parametrize(
    "ciphertext",
    (
        "env1:not-json",
        "env1:{}",
        'env1:{"wrapped_dek":"synthetic"}',
        'env1:{"kek":"","wrapped_dek":"synthetic"}',
        'env1:{"kek":"synthetic-kek"}',
        'env1:{"kek":"synthetic-kek","wrapped_dek":null}',
        'env1:{"kek":"synthetic-kek","wrapped_dek":""}',
        'env1:{"kek":"synthetic-kek","wrapped_dek":123}',
        "env1:[]",
        'env1:"agko_vold$synthetic"',
    ),
)
def test_malformed_env1_aborts_instead_of_becoming_legacy_vault(ciphertext):
    with pytest.raises(rewrap._RewrapRowError, match="Malformed KMS envelope"):
        rewrap._stamp_kid(ciphertext)
    with pytest.raises(ValueError, match="Malformed KMS envelope"):
        verify_all._collect_key_refs({}, LABEL, [ciphertext.encode()])


@pytest.mark.parametrize("container", (bytes, bytearray, memoryview))
def test_non_utf8_envelope_blocks_key_scan_even_inside_jsonb(container):
    raw = container(b"env1:\xff\xfe")
    for value in (raw, {"_encrypted": raw}):
        with pytest.raises(ValueError, match="not UTF-8 ciphertext"):
            verify_all._collect_key_refs({}, LABEL, [value])


@pytest.mark.asyncio
async def test_bytea_update_binds_bytes_and_refuses_json_or_text():
    session = AsyncMock()
    await rewrap._update_row(session, LABEL, TENANT, b"agko_vnew$synthetic")
    statement, params = session.execute.call_args.args
    assert "content = :v" in str(statement)
    assert "jsonb" not in str(statement)
    assert params["v"] == b"agko_vnew$synthetic"
    session.execute.reset_mock()
    for value in ("agko_vnew$synthetic", {"_encrypted": "agko_vnew$synthetic"}):
        with pytest.raises(rewrap._RewrapRowError, match="requires bytes"):
            await rewrap._update_row(session, LABEL, TENANT, value)
    session.execute.assert_not_called()


def _rotation_session(monkeypatch, values):
    scope = verify_all.TenantCompanyScope(TENANT)
    session = MagicMock()
    updates = []

    async def execute(statement, params):
        result = MagicMock()
        if str(statement).startswith("UPDATE"):
            assert isinstance(params["v"], bytes)
            values[UUID(params["id"])] = params["v"]
            updates.append(params["v"])
            result.rowcount = 1
        else:
            result.all.return_value = sorted(values.items())
        return result

    session.execute = execute

    @asynccontextmanager
    async def scoped(tenant_id, company_id=None):
        assert tenant_id == TENANT
        assert company_id is None
        yield session

    monkeypatch.setattr(rewrap, "_SCANNERS", [(LABEL, DOTTED)])
    monkeypatch.setattr(rewrap, "_build_scope_plan", AsyncMock(return_value={LABEL: ([scope], False)}))
    monkeypatch.setattr(rewrap, "get_tenant_session", scoped)
    return updates


@pytest.mark.asyncio
async def test_rotation_loop_reencrypts_real_audio_as_bytes_and_leaves_kms_untouched(monkeypatch):
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "old:synthetic-old-audio-key")
    monkeypatch.setattr(audio_crypto, "resolve_tenant_kek", AsyncMock(return_value=""))
    audio = b"RIFF\x00\xffWAVEsynthetic-private-audio"
    old = await audio_crypto.encrypt_audio(TENANT, audio)
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "new:synthetic-new-audio-key,old:synthetic-old-audio-key")
    active = await audio_crypto.encrypt_audio(TENANT, audio)
    old_id, kms_id, active_id = (UUID(int=n) for n in (1, 2, 3))
    values = {old_id: old, kms_id: ENVELOPE.encode(), active_id: active}
    updates = _rotation_session(monkeypatch, values)
    assert await rewrap.run(only_column=LABEL, only_kid=None, batch_size=100, dry_run=False) == 0
    assert len(updates) == 1
    assert values[old_id].startswith(b"agko_vnew$")
    assert values[kms_id] == ENVELOPE.encode()
    assert values[active_id] == active
    assert await audio_crypto.decrypt_audio(TENANT, values[old_id]) == audio
    with pytest.raises(SpeechError):
        await audio_crypto.decrypt_audio(UUID(int=9), values[old_id])
    assert await rewrap.verify(LABEL) == 0
    assert await rewrap.run(only_column=LABEL, only_kid=None, batch_size=100, dry_run=False) == 0
    assert len(updates) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", (b"RIFFlegacy-raw-audio", b"\xff\x00legacy", b"env1:not-json"))
async def test_rotation_refuses_raw_or_corrupt_audio_without_writing(monkeypatch, raw):
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "new:synthetic-new-audio-key")
    values = {UUID(int=1): raw}
    updates = _rotation_session(monkeypatch, values)
    assert await rewrap.run(only_column=LABEL, only_kid=None, batch_size=100, dry_run=False) == 1
    assert updates == []
    assert values[UUID(int=1)] == raw
