# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Outbound buyer-agent request signatures with the tenant agent key (G48) and TAP body objects (G49).

The buyer connector (``connectors/commerce/grantex_commerce.py``) signs each
request under the Visa Trusted Agent Protocol profile of RFC 9421
(``grantex_agent_httpsig.sign_with_profile(profile="tap")``) when
``AGENTICORG_HTTPSIG_SIGN_OUTBOUND`` is on:

- ``tag``: ``agent-payer-auth`` for payment and checkout calls,
  ``agent-browser-auth`` for everything else (TAP-SPEC: browsing is product
  details, availability and final cost);
- covered: ``@method``, ``@authority``, ``@path`` and ``content-digest``
  (TAP's minimum is ``@authority`` and ``@path``; the body is covered too);
- ``created``, ``expires`` (8 minutes, TAP's maximum), ``keyid`` (the key's
  identifier in the TAP key store, or its RFC 7638 thumbprint when none is
  configured), ``alg`` and a fresh UUID ``nonce`` per request;
- optionally a second, Web Bot Auth signature (``sig2``, ``tag="web-bot-auth"``)
  with a ``Signature-Agent`` naming the agent's key directory, when the
  tenant configured one (draft-ietf-webbotauth-httpsig-protocol-00 section
  5.2). It carries the same nonce, which TAP requires to be equal in every
  signature of the message.

The tenant agent key. A buyer agent's private key is a JWK (Ed25519, P-256 or
RSA of at least 2048 bits, which signs ``rsa-pss-sha512``) sealed with the
tenant crypto helpers (``core.crypto.tenant_secrets``; ``seal_agent_signing_key``
produces the ciphertext) and stored as the ``agent_signing_key_encrypted``
credential of the tenant's ``grantex_commerce`` connector configuration, with
``agent_signing_keyid`` (the TAP key-store id) and, optionally,
``agent_signature_agent_url``. It is decrypted in a worker thread when the
connector first signs and kept only in memory; it is never logged or
returned (``AgentSigningKey`` hides it from ``repr``).

TAP body objects (G49): on a purchase-intent request with a granted, unexpired
Grantex consent, the body carries ``agenticPaymentContainer`` (and
``agenticConsumer`` when the caller holds the consumer's TAP id token) signed
with the same key (``kid`` = keyid) and the Grantex consent record object
``grantexConsentRecord``, all bound to the message-signature nonce. Their body
placement and signature canonicalization are not published by TAP; the
library's defaults (top-level members, RFC 8785 JCS) are used.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from grantex_agent_httpsig import (
    TAP_BROWSER_TAG,
    TAP_PAYER_TAG,
    AgentHttpSigError,
    HttpRequest,
    build_agentic_consumer,
    build_agentic_payment_container,
    build_grantex_consent_record,
    combine_signatures,
    jwk_thumbprint,
    private_jwk_from_key,
    sign_with_profile,
    with_tap_body_objects,
)

TAP_COMPONENTS = ("@method", "@authority", "@path", "content-digest")
WBA_COMPONENTS = ("@authority", "signature-agent")
KEY_CREDENTIAL = "agent_signing_key_encrypted"
KEYID_CREDENTIAL = "agent_signing_keyid"
SIGNATURE_AGENT_CREDENTIAL = "agent_signature_agent_url"


class AgentSigningError(ValueError):
    """The agent key or a body object could not be used; the request must not be sent."""


@dataclass(frozen=True)
class AgentSigningKey:
    private_jwk: Mapping[str, Any] = field(repr=False)
    keyid: str
    signature_agent_url: str | None = None


def _check_private_jwk(jwk: Any) -> None:
    from grantex_agent_httpsig._keys import private_key_object

    if not isinstance(jwk, Mapping):
        raise AgentSigningError("the agent signing key is not a JWK")
    try:
        private_key_object(jwk)
    except (AgentHttpSigError, KeyError, TypeError, ValueError) as exc:
        raise AgentSigningError("the agent signing key is not a usable private JWK") from exc


async def seal_agent_signing_key(private_key: Any, tenant_id: uuid.UUID) -> str:
    """The tenant-sealed ciphertext of a private key (a cryptography key object or a private JWK)."""
    from core.crypto.tenant_secrets import encrypt_for_tenant

    jwk = private_key if isinstance(private_key, Mapping) else private_jwk_from_key(private_key)
    _check_private_jwk(jwk)
    return await encrypt_for_tenant(json.dumps(dict(jwk), separators=(",", ":")), tenant_id)


async def load_agent_signing_key(config: Mapping[str, Any]) -> AgentSigningKey | None:
    """The tenant agent key named by a connector configuration, or None when none is configured."""
    from core.crypto.tenant_secrets import decrypt_for_tenant

    ciphertext = str(config.get(KEY_CREDENTIAL) or "").strip()
    if not ciphertext:
        return None
    try:
        plaintext = await asyncio.to_thread(decrypt_for_tenant, ciphertext)
        jwk = json.loads(plaintext)
    # enterprise-gate: broad-except-ok reason=undecryptable-agent-key-refuses-signing-without-echoing-the-cause
    except Exception as exc:
        raise AgentSigningError("the agent signing key could not be decrypted") from exc
    _check_private_jwk(jwk)
    keyid = str(config.get(KEYID_CREDENTIAL) or "").strip() or jwk_thumbprint(jwk)
    agent_url = str(config.get(SIGNATURE_AGENT_CREDENTIAL) or "").strip() or None
    return AgentSigningKey(private_jwk=jwk, keyid=keyid, signature_agent_url=agent_url)


def tap_tag(purpose: str) -> str:
    """``agent-payer-auth`` for a payment or checkout call, else ``agent-browser-auth``."""
    return TAP_PAYER_TAG if purpose == "payment" else TAP_BROWSER_TAG


def new_nonce() -> str:
    # A UUID: a valid TAP nonce and, being 36 base64url characters, a valid Web Bot Auth one.
    return str(uuid.uuid4())


def sign_agent_request(
    *,
    method: str,
    url: str,
    body: bytes,
    headers: Mapping[str, str],
    key: AgentSigningKey,
    tag: str,
    nonce: str,
    created: int | None = None,
) -> dict[str, str]:
    """The fields to add to the request: Content-Digest, Signature-Input, Signature (and Signature-Agent)."""
    message = HttpRequest(method, url, list(headers.items()), body)
    try:
        tap = sign_with_profile(
            message,
            profile="tap",
            key=key.private_jwk,
            keyid=key.keyid,
            tag=tag,
            nonce=nonce,
            components=TAP_COMPONENTS,
            created=created,
            label="sig1",
        )
        if key.signature_agent_url is None:
            return dict(tap.headers)
        # The Web Bot Auth signature covers the same request plus Signature-Agent.
        wba = sign_with_profile(
            message,
            profile="web-bot-auth",
            key=key.private_jwk,
            nonce=nonce,
            components=WBA_COMPONENTS,
            created=created,
            label="sig2",
            signature_agent_url=key.signature_agent_url,
        )
        return combine_signatures(tap, wba)
    except AgentHttpSigError as exc:
        raise AgentSigningError(f"the request could not be signed: {exc}") from exc


def with_purchase_body_objects(
    body: Mapping[str, Any],
    *,
    key: AgentSigningKey,
    nonce: str,
    consent: Mapping[str, Any],
    consumer_id_token: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """``body`` with the TAP payment container, the optional consumer object and the Grantex consent record."""
    try:
        record = build_grantex_consent_record(consent, nonce=nonce, now=now)
        container = build_agentic_payment_container(key=key.private_jwk, kid=key.keyid, nonce=nonce)
        consumer = (
            build_agentic_consumer(key=key.private_jwk, kid=key.keyid, nonce=nonce, id_token=consumer_id_token)
            if consumer_id_token
            else None
        )
        return with_tap_body_objects(body, consumer=consumer, payment_container=container, consent_record=record)
    except AgentHttpSigError as exc:
        raise AgentSigningError(str(exc)) from exc
