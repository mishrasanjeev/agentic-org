# SPDX-License-Identifier: Apache-2.0
"""Speech schema exemptions and key-reference coverage must remain safe."""

from __future__ import annotations

import importlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import InvalidToken

from core.crypto import rewrap, verify_all


@pytest.mark.parametrize(
    "revision",
    ("v6_z69_speech_recordings", "v6_z70_speech_summaries", "v6_z71_speech_live_sessions"),
)
def test_speech_schema_exemptions_are_additive_only(revision, monkeypatch):
    module = importlib.import_module("migrations.versions." + revision)
    operations = MagicMock()
    monkeypatch.setattr(module, "op", operations)
    module.upgrade()
    assert operations.mock_calls
    for call in operations.mock_calls:
        assert call[0] == "execute", "Schema-only exemption cannot introduce data operations"
        sql = " ".join(call.args[0].split()).upper()
        assert sql.startswith(
            (
                "CREATE TABLE IF NOT EXISTS",
                "CREATE INDEX IF NOT EXISTS",
                "CREATE POLICY",
                "DROP POLICY IF EXISTS",
                "ALTER TABLE",
            )
        )
        if sql.startswith("ALTER TABLE"):
            assert any(
                clause in sql
                for clause in (" ADD COLUMN IF NOT EXISTS ", " ENABLE ROW LEVEL SECURITY", " FORCE ROW LEVEL SECURITY")
            )


@pytest.mark.parametrize(
    "label",
    (
        "speech_recordings.transcript_encrypted",
        "speech_recordings.summary_encrypted",
        "speech_live_sessions.turns_encrypted",
        "personalisation_profiles.attributes",
        "lineage_sync_sources.token",
    ),
)
def test_new_encrypted_columns_are_registered_and_resolvable(label):
    scanners = dict(verify_all._SCANNERS)
    model = verify_all._resolve_scanner_model(scanners[label], strict=True)
    assert getattr(model, label.split(".")[1]) is not None


@pytest.mark.parametrize("wrapped", (False, True))
@pytest.mark.parametrize("serialized", (False, True))
def test_key_reference_collection_preserves_envelope_and_vault_shapes(wrapped, serialized):
    kek = "projects/local/locations/global/keyRings/test/cryptoKeys/retained"
    envelope = {"kek": kek, "wrapped_dek": "synthetic", "version": 1}
    value = "env1:" + json.dumps(envelope) if wrapped else envelope
    if wrapped:
        value = {"_encrypted": value}
    if serialized:
        value = json.dumps(value)
    refs = {}
    verify_all._collect_key_refs(refs, "speech_recordings.transcript_encrypted", [value])
    assert refs == {"speech_recordings.transcript_encrypted": {verify_all.KeyRef("envelope", kek)}}
    verify_all._collect_key_refs(refs, "lineage_sync_sources.token", ["agko_vretained$synthetic"])
    assert refs["lineage_sync_sources.token"] == {verify_all.KeyRef("vault", "retained")}


@pytest.mark.parametrize("label", sorted(rewrap._JSONB_ENCRYPTED_COLUMNS))
@pytest.mark.asyncio
async def test_jsonb_rotation_preserves_container_and_uses_jsonb_bind(label, monkeypatch):
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "old:local-test-old-key")
    original = rewrap.encrypt_credential("synthetic local secret")
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "new:local-test-new-key,old:local-test-old-key")
    extracted = rewrap._extract_ciphertext(label, {"_encrypted": original})
    replacement = rewrap._wrap_ciphertext_for_column(
        label, rewrap.encrypt_credential(rewrap.decrypt_credential(extracted))
    )
    assert rewrap.decrypt_credential(replacement["_encrypted"]) == "synthetic local secret"
    assert replacement["_encrypted"].startswith("agko_vnew$")
    session = AsyncMock()
    await rewrap._update_row(session, label, "11111111-1111-1111-1111-111111111111", replacement)
    statement, parameters = session.execute.call_args.args
    assert "CAST(:v AS jsonb)" in str(statement)
    assert json.loads(parameters["v"]) == replacement


def test_vault_rotation_never_decrypts_or_rewrites_envelope_as_vault():
    value = {"_encrypted": "env1:" + json.dumps({"kek": "synthetic-kek", "wrapped_dek": "synthetic"})}
    ciphertext = rewrap._extract_ciphertext("speech_live_sessions.turns_encrypted", value)
    with pytest.raises(InvalidToken, match="(?i)(decrypt|invalid|fernet)"):
        rewrap.decrypt_credential(ciphertext)
