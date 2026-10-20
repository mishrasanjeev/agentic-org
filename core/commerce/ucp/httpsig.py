# SPDX-License-Identifier: Apache-2.0
"""Outbound RFC 9421 HTTP message signatures and RFC 9530 Content-Digest for UCP.

UCP 2026-08-25 signatures (REST Request Signing; shared by 2026-04-08):
ES256 at minimum, ECDSA signatures in raw ``r||s`` form (64 bytes for P-256),
the algorithm is derived from the key (no ``alg`` parameter in
``Signature-Input``), and the covered components are ``@method``,
``@authority``, ``@path`` (``@query`` when there is one), ``content-digest``
and ``content-type`` with a body, and ``ucp-agent``, ``signature-agent`` and
``idempotency-key`` when those headers are present. Order webhooks MUST be
signed (UCP order capability, Events).

Signing never sees a private key: it goes through an injected
``MerchantSigner`` (the merchant key store provides one: ``key_id`` names the
merchant's active key, ``sign`` returns a raw ES256 signature). The same
signer produces the detached JWS of ``ap2.merchant_authorization``.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature


class MerchantSigner(Protocol):
    """Signs with a merchant's active ES256 key; never exposes the private key."""

    async def key_id(self, tenant_id: str, merchant_id: str, seller_agent_id: str) -> str | None:
        """The ``kid`` of the merchant's active signing key (as published in ``keys[]``), or None."""
        ...

    async def sign(self, tenant_id: str, merchant_id: str, seller_agent_id: str, data: bytes) -> bytes:
        """The raw ``r||s`` ES256 signature of ``data``."""
        ...


class SigningKeyUnavailableError(Exception):
    """The merchant has no active signing key: nothing that MUST be signed may be sent."""


DEFAULT_LABEL = "sig1"
_OPTIONAL_HEADERS = ("ucp-agent", "signature-agent", "idempotency-key")
_DEFAULT_PORTS = {"https": 443, "http": 80}


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def content_digest(body: bytes) -> str:
    """RFC 9530 ``Content-Digest`` over the raw body bytes."""
    return "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode("ascii") + ":"


def _authority(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    port = parts.port
    if port is not None and port != _DEFAULT_PORTS.get(parts.scheme.lower()):
        return f"{host}:{port}"
    return host


def _derived(name: str, method: str, url: str) -> str:
    parts = urlsplit(url)
    if name == "@method":
        return method.upper()
    if name == "@authority":
        return _authority(url)
    if name == "@path":
        return parts.path or "/"
    if name == "@query":
        return "?" + parts.query
    raise ValueError(f"unsupported derived component {name}")


def covered_components(*, url: str, headers: Mapping[str, str], has_body: bool) -> list[str]:
    """The UCP covered-component set for a request (RFC 9421 component identifiers, in order)."""
    lowered = {k.lower() for k in headers}
    components = ["@method", "@authority", "@path"]
    if urlsplit(url).query:
        components.append("@query")
    if has_body:
        components.extend(["content-digest", "content-type"])
    components.extend(name for name in _OPTIONAL_HEADERS if name in lowered)
    return components


def _signature_params(components: Sequence[str], *, created: int, keyid: str) -> str:
    inner = " ".join(f'"{name}"' for name in components)
    return f'({inner});created={created};keyid="{keyid}"'


def signature_base(
    *, method: str, url: str, headers: Mapping[str, str], components: Sequence[str], params: str
) -> bytes:
    """RFC 9421 section 2.5 signature base."""
    lowered = {k.lower(): str(v).strip() for k, v in headers.items()}
    lines = []
    for name in components:
        if name.startswith("@"):
            value = _derived(name, method, url)
        else:
            if name not in lowered:
                raise ValueError(f"covered header {name} is missing")
            value = lowered[name]
        lines.append(f'"{name}": {value}')
    lines.append(f'"@signature-params": {params}')
    return "\n".join(lines).encode("utf-8")


async def sign_request(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    signer: MerchantSigner,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str,
    created: int,
    label: str = DEFAULT_LABEL,
) -> dict[str, str]:
    """The headers to send: the input headers plus ``Content-Digest``, ``Signature-Input`` and ``Signature``."""
    out = {str(k): str(v) for k, v in headers.items()}
    if body is not None:
        out["Content-Digest"] = content_digest(body)
    components = covered_components(url=url, headers=out, has_body=body is not None)
    kid = await signer.key_id(tenant_id, merchant_id, seller_agent_id)
    if not kid:
        raise SigningKeyUnavailableError(merchant_id)
    params = _signature_params(components, created=created, keyid=kid)
    base = signature_base(method=method, url=url, headers=out, components=components, params=params)
    raw = await signer.sign(tenant_id, merchant_id, seller_agent_id, base)
    out["Signature-Input"] = f"{label}={params}"
    out["Signature"] = f"{label}=:{base64.b64encode(raw).decode('ascii')}:"
    return out


def parse_signature_input(value: str) -> tuple[str, list[str], dict[str, str]]:
    """``label``, covered components and parameters of a single-signature ``Signature-Input``."""
    label, _, rest = value.partition("=")
    inner, _, param_text = rest.partition(")")
    components = [item.strip('"') for item in inner.lstrip("(").split() if item]
    params: dict[str, str] = {}
    for part in param_text.split(";"):
        if "=" in part:
            key, _, raw = part.partition("=")
            params[key.strip()] = raw.strip().strip('"')
    return label.strip(), components, params


def verify_request_signature(
    *, method: str, url: str, headers: Mapping[str, str], body: bytes | None, public_key: ec.EllipticCurvePublicKey
) -> bool:
    """Verify an ES256 RFC 9421 signature made by ``sign_request`` (used by tests and receivers)."""
    lowered = {k.lower(): v for k, v in headers.items()}
    if body is not None and lowered.get("content-digest") != content_digest(body):
        return False
    label, components, params = parse_signature_input(lowered.get("signature-input", ""))
    signature_value = lowered.get("signature", "")
    prefix = f"{label}=:"
    if not signature_value.startswith(prefix) or not signature_value.endswith(":"):
        return False
    raw = base64.b64decode(signature_value[len(prefix) : -1])
    param_text = _signature_params(components, created=int(params["created"]), keyid=params["keyid"])
    base = signature_base(method=method, url=url, headers=headers, components=components, params=param_text)
    return verify_es256(public_key, base, raw)


class LocalEs256Signer:
    """A ``MerchantSigner`` over an in-memory P-256 key (tests and local development only)."""

    def __init__(self, private_key: ec.EllipticCurvePrivateKey, kid: str) -> None:
        self._key = private_key
        self._kid = kid

    async def key_id(self, _tenant_id: str, _merchant_id: str, _seller_agent_id: str) -> str | None:
        return self._kid

    async def sign(self, _tenant_id: str, _merchant_id: str, _seller_agent_id: str, data: bytes) -> bytes:
        der = self._key.sign(data, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def verify_es256(public_key: ec.EllipticCurvePublicKey, data: bytes, raw: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature

    if len(raw) != 64:
        return False
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    try:
        public_key.verify(der, data, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return False
    return True


async def detached_jws(
    payload: Any, *, signer: MerchantSigner, tenant_id: str, merchant_id: str, seller_agent_id: str
) -> str:
    """RFC 7515 Appendix F detached JWS (ES256) over the RFC 8785 canonical JSON of ``payload``.

    The shape of ``ap2.merchant_authorization``: ``<b64url header>..<b64url signature>``
    with header ``{alg, kid}``.
    """
    from core.commerce.oacp_jws import canonicalize_bytes

    kid = await signer.key_id(tenant_id, merchant_id, seller_agent_id)
    if not kid:
        raise SigningKeyUnavailableError(merchant_id)
    header = b64url(json.dumps({"alg": "ES256", "kid": kid}, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header}.{b64url(canonicalize_bytes(payload))}".encode("ascii")
    raw = await signer.sign(tenant_id, merchant_id, seller_agent_id, signing_input)
    return f"{header}..{b64url(raw)}"


class MerchantKeyStoreSigner:
    """``MerchantSigner`` over the merchant key store (``core.commerce.merchant_keys``): the active P-256 key."""

    async def key_id(self, tenant_id: str, merchant_id: str, seller_agent_id: str) -> str | None:
        from core.commerce.merchant_keys import MerchantKeyError, load_active_signing_key

        try:
            key = await load_active_signing_key(
                tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=seller_agent_id
            )
        except MerchantKeyError:
            return None
        return key.kid

    async def sign(self, tenant_id: str, merchant_id: str, seller_agent_id: str, data: bytes) -> bytes:
        from core.commerce.merchant_keys import MerchantKeyError, load_active_signing_key

        try:
            key = await load_active_signing_key(
                tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=seller_agent_id
            )
        except MerchantKeyError as exc:
            raise SigningKeyUnavailableError(merchant_id) from exc
        return key.sign(data)
