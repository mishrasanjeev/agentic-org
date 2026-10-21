# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Per-merchant ES256 UCP signing keys (G19)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import uuid
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from core.commerce import merchant_keys
from core.commerce.merchant_keys import (
    KEY_STATUS_ACTIVE,
    KEY_STATUS_RETIRED,
    MerchantKeyError,
    active_key_record,
    add_active_key,
    jwk_thumbprint,
    merchant_public_jwks,
    public_key_records_view,
    sign_with_merchant_key,
    signing_key_from_record,
)
from core.commerce.oacp_jws import es256_verify, p256_public_key_from_jwk
from tests.ucp_fixtures import (
    MERCHANT_ID,
    NOW,
    SELLER_AGENT_ID,
    TENANT_ID,
    fake_tenant_encryption,
    key_record,
    merchant_row,
    published_state,
)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def test_key_record_holds_a_public_jwk_and_only_an_encrypted_private_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tenant_encryption(monkeypatch)
    record = key_record()
    jwk = record["public_jwk"]
    assert jwk["kty"] == "EC" and jwk["crv"] == "P-256" and jwk["alg"] == "ES256" and jwk["use"] == "sig"
    assert record["kid"] == jwk["kid"] == jwk_thumbprint(jwk)
    assert "d" not in jwk
    assert record["private_key_ciphertext"].startswith("test-envelope:")
    assert "PRIVATE KEY" not in record["private_key_ciphertext"]
    assert record["status"] == KEY_STATUS_ACTIVE


def test_kid_is_the_rfc_7638_thumbprint() -> None:
    jwk = {
        "kty": "EC",
        "crv": "P-256",
        "x": "f83OJ3D2xF1Bg8vub9tLe1gHMzV76e8Tus9uPHvRVEU",
        "y": "x_FEzRu9m36HLN_tue659LNpXW6pCyStikYjKIWI5a0",
    }
    canonical = json.dumps({"crv": jwk["crv"], "kty": "EC", "x": jwk["x"], "y": jwk["y"]}, separators=(",", ":"))
    assert jwk_thumbprint(jwk) == _b64(hashlib.sha256(canonical.encode()).digest())


def test_private_key_goes_through_the_tenant_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    async def resolve(tenant_uuid: uuid.UUID) -> str:
        calls["tenant"] = tenant_uuid
        return "projects/example/locations/global/keyRings/r/cryptoKeys/k"

    def encrypt(plaintext: str, kek: str) -> str:
        calls["kek"] = kek
        calls["plaintext_is_pem"] = plaintext.startswith("-----BEGIN PRIVATE KEY-----")
        return "env1:sealed"

    monkeypatch.setattr("core.crypto.tenant_secrets.resolve_tenant_kek", resolve)
    monkeypatch.setattr("core.crypto.tenant_secrets.encrypt_with_kek", encrypt)
    record = asyncio.run(merchant_keys.new_merchant_key_record(TENANT_ID, now=NOW))
    assert record["private_key_ciphertext"] == "env1:sealed"
    assert calls == {
        "tenant": uuid.UUID(TENANT_ID),
        "kek": "projects/example/locations/global/keyRings/r/cryptoKeys/k",
        "plaintext_is_pem": True,
    }


def test_signature_is_raw_r_s_and_verifies_against_the_published_jwk(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tenant_encryption(monkeypatch)
    record = key_record()
    key = asyncio.run(signing_key_from_record(record))
    signing_input = b'"@method": POST\n"@authority": platform.example\n"@path": /webhooks/ucp/orders'
    signature = key.sign(signing_input)
    assert len(signature) == 64
    public = p256_public_key_from_jwk(record["public_jwk"])
    assert isinstance(public, ec.EllipticCurvePublicKey)
    assert es256_verify(public, signing_input, signature)
    assert not es256_verify(public, signing_input + b"x", signature)
    assert repr(key) == f"MerchantSigningKey(kid={record['kid']!r})"


def test_sign_with_merchant_key_loads_the_active_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tenant_encryption(monkeypatch)
    old = key_record()
    new = key_record(NOW + timedelta(days=1))
    keys = add_active_key([old], new, now=NOW + timedelta(days=1), grace_days=7)
    row = merchant_row(published_state(keys))

    async def load_row(**scope: str):
        assert scope == {"tenant_id": TENANT_ID, "merchant_id": MERCHANT_ID, "seller_agent_id": SELLER_AGENT_ID}
        return row

    monkeypatch.setattr("core.commerce.ucp.store.load_merchant_config_row", load_row)
    kid, signature = asyncio.run(
        sign_with_merchant_key(
            tenant_id=TENANT_ID, merchant_id=MERCHANT_ID, seller_agent_id=SELLER_AGENT_ID, signing_input=b"base"
        )
    )
    assert kid == new["kid"]
    assert es256_verify(p256_public_key_from_jwk(new["public_jwk"]), b"base", signature)


def test_missing_key_and_tampered_ciphertext_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tenant_encryption(monkeypatch)

    async def load_row(**_scope: str):
        return merchant_row(published_state([]))

    monkeypatch.setattr("core.commerce.ucp.store.load_merchant_config_row", load_row)
    with pytest.raises(MerchantKeyError):
        asyncio.run(
            sign_with_merchant_key(
                tenant_id=TENANT_ID, merchant_id=MERCHANT_ID, seller_agent_id=SELLER_AGENT_ID, signing_input=b"x"
            )
        )
    record = key_record()
    other = key_record()
    swapped = {**record, "private_key_ciphertext": other["private_key_ciphertext"]}
    with pytest.raises(MerchantKeyError):
        asyncio.run(signing_key_from_record(swapped))
    with pytest.raises(MerchantKeyError):
        asyncio.run(signing_key_from_record({**record, "private_key_ciphertext": "test-envelope:garbage"}))


def test_rotation_retires_with_a_grace_window_of_at_least_seven_days(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tenant_encryption(monkeypatch)
    first = key_record()
    second = key_record()
    rotated = add_active_key([first], second, now=NOW, grace_days=1)
    assert [item["status"] for item in rotated] == [KEY_STATUS_RETIRED, KEY_STATUS_ACTIVE]
    assert rotated[0]["retire_until"] == "2026-10-27T09:00:00Z"  # grace floor of 7 days
    assert active_key_record(rotated)["kid"] == second["kid"]
    assert [jwk["kid"] for jwk in merchant_public_jwks(rotated, now=NOW + timedelta(days=6))] == [
        second["kid"],
        first["kid"],
    ]
    assert [jwk["kid"] for jwk in merchant_public_jwks(rotated, now=NOW + timedelta(days=8))] == [second["kid"]]
    third = key_record()
    again = add_active_key(rotated, third, now=NOW + timedelta(days=8), grace_days=7)
    assert [item["kid"] for item in again] == [second["kid"], third["kid"]]


def test_public_views_never_carry_private_material(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tenant_encryption(monkeypatch)
    record = key_record()
    poisoned = {**record, "public_jwk": {**record["public_jwk"], "d": "secret"}}
    assert merchant_public_jwks([poisoned], now=NOW) == []
    for jwk in merchant_public_jwks([record], now=NOW):
        assert set(jwk) == {"kty", "crv", "x", "y", "kid", "use", "alg"}
    assert all("private_key_ciphertext" not in item for item in public_key_records_view([record]))
