# SPDX-License-Identifier: Apache-2.0
"""Inbound RFC 9421 agent request signatures: the Visa TAP and Web Bot Auth profiles (G47, G49, G51).

``verify_agent_request_signature`` is a FastAPI dependency on the commerce API
surfaces: the public commerce JSON reads (``catalog.json`` and the product
``.json``, ``api/v1/commerce_public.py``) and the A2A routes
(``api/v1/a2a_interop.py``). ``observe_agent_request_signature`` verifies and
records the same way but never refuses, whatever the mode: it is on the public
pages crawlers and browsers read unsigned (the seller and product HTML pages,
``schema-org.jsonld``, ``sitemap.xml``, ``llms.txt``). Both verify with
``grantex_agent_httpsig.verify_with_profile``:

- ``tap`` (Visa Trusted Agent Protocol): ``tag`` ``agent-browser-auth`` or
  ``agent-payer-auth``, at least ``@authority`` and ``@path``, ``expires`` at
  most 8 minutes after ``created``, a ``nonce`` refused when seen in the last
  8 minutes, keys from the pinned TAP key store by ``keyid``;
- ``web-bot-auth`` (draft-ietf-webbotauth-httpsig-protocol-00 section 5):
  ``tag="web-bot-auth"``, ``@authority`` or ``@target-uri`` plus
  ``"signature-agent";key=...``, keys by (Signature-Agent URL, keyid) from an
  allow-listed directory (``auth/httpsig_keys.py``).

The library's key resolver and nonce store are synchronous, and a request
handler must not do sync I/O, so the key is fetched asynchronously before the
library runs (from the keyid and Signature-Agent the request names) and the
library's nonce check, which it makes only after the signature verified, is
recorded and then claimed atomically in Redis (``SET NX`` with an expiry of at
least the profile's replay window). The outcome is the library's.

``AGENTICORG_HTTPSIG_VERIFY_MODE``: ``log`` (default) verifies and records,
never refuses; ``enforce`` refuses an unsigned or invalid request with 401 and
an ``Accept-Signature`` field (RFC 9421 section 5.1) naming what to sign, and
answers 503 when the replay store cannot be reached. A verified identity is
recorded on the commerce ledger for write routes (``a2a.agent_signature``,
details only, no credential) and as a structured log for reads, and is left on
``request.state.agent_signature`` for the handler.

TAP body objects (G49): after a ``tap`` signature verifies, the
``agenticConsumer``, ``agenticPaymentContainer`` and ``grantexConsentRecord``
members of a JSON body are checked with ``verify_tap_body_objects`` (same
nonce, kid = keyid, the request's key). A consumer failure is soft; a payment
container that fails is ``usable_for_payment: false``. No covered route takes
a payment, so the result is recorded, not acted on.

Scheme agent ids (G51): a TAP keyid is recorded as ``tap_keyid``, never as a
scheme agent id. With ``AGENTICORG_HTTPSIG_REGISTRY_LOOKUP_ENABLED``, a
verified agent is looked up in the Grantex registry by the RFC 7638 thumbprint
of the key that verified it (``key_thumbprint``), whatever the profile, and the
DID and scheme agent ids the registry attests for that key are recorded.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import structlog
from fastapi import HTTPException, Request
from grantex_agent_httpsig import (
    SIGNATURE_AGENT_FIELD,
    TAP_BROWSER_TAG,
    TAP_NONCE_REPLAY_WINDOW_SECONDS,
    TAP_PAYER_TAG,
    WEB_BOT_AUTH_TAG,
    AgentHttpSigError,
    HttpRequest,
    InnerList,
    KeyReference,
    jwk_thumbprint,
    parse_dictionary,
    parse_signature_agent,
    verify_tap_body_objects,
    verify_with_profile,
)

from auth import httpsig_keys
from core.config import settings

logger = structlog.get_logger()

MODE_LOG = "log"
MODE_ENFORCE = "enforce"
LEDGER_EVENT = "a2a.agent_signature"
TAP_TAGS = (TAP_BROWSER_TAG, TAP_PAYER_TAG)
TAP_BODY_MEMBERS = ("agenticConsumer", "agenticPaymentContainer", "grantexConsentRecord")
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_MAX_SIGNED_BODY_BYTES = 1024 * 1024
# RFC 9421 section 5.1: what a refused client should sign.
ACCEPT_SIGNATURE = (
    f'tap=("@authority" "@path");tag="{TAP_BROWSER_TAG}", '
    f'wba=("@authority" "signature-agent";key="wba");tag="{WEB_BOT_AUTH_TAG}"'
)

Status = Literal["verified", "unsigned", "invalid", "unavailable"]


@dataclass(frozen=True)
class AgentSignatureIdentity:
    """Who signed a verified request. Public values only: no key material, no credential."""

    profile: str
    tag: str
    keyid: str
    alg: str | None
    label: str | None
    created: int | None
    expires: int | None
    signature_agent: str | None
    key_source: str
    #: The RFC 7638 thumbprint of the verifying key (the registry looks agents up by it).
    key_thumbprint: str | None = None
    #: The TAP key-store keyid: an identifier of the key, not a scheme agent id.
    tap_keyid: str | None = None
    agent_did: str | None = None
    scheme_agent_ids: tuple[Mapping[str, str], ...] = ()
    tap_body: Mapping[str, Any] | None = None

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["scheme_agent_ids"] = [dict(item) for item in self.scheme_agent_ids]
        return record


@dataclass(frozen=True)
class SignatureCheck:
    status: Status
    identity: AgentSignatureIdentity | None = None
    code: str | None = None
    reason: str | None = None
    profiles_seen: tuple[str, ...] = field(default=())


class _RecordedNonce:
    """The library's nonce store, recording the one claim it asks for (made only after the signature verified)."""

    def __init__(self) -> None:
        self.claim: tuple[str, str, int] | None = None

    def check_and_store(self, keyid: str, nonce: str, expires_at: int) -> bool:
        self.claim = (keyid, nonce, int(expires_at))
        return True


def verify_mode() -> str:
    mode = str(settings.httpsig_verify_mode or MODE_LOG).strip().lower()
    if mode not in (MODE_LOG, MODE_ENFORCE):
        logger.warning("httpsig_verify_mode_unknown", mode=mode[:20])
        return MODE_LOG
    return mode


def _normalise_authority(value: str) -> str:
    host = value.strip().lower()
    for default_port in (":443", ":80"):
        if host.endswith(default_port):
            return host[: -len(default_port)]
    return host


def expected_authority(request: Request) -> str | None:
    """The @authority a signature must cover: the Host header, which must be configured when a list is set."""
    host = _normalise_authority(request.headers.get("host") or "")
    if not host:
        return None
    configured = {_normalise_authority(a) for a in str(settings.httpsig_authorities or "").split(",") if a.strip()}
    if configured and host not in configured:
        return None
    return host


def _origin_form(request: Request) -> str:
    raw_path = request.scope.get("raw_path")
    path = raw_path.decode("latin-1") if isinstance(raw_path, bytes) and raw_path else request.url.path
    query = request.scope.get("query_string") or b""
    return path + ("?" + query.decode("latin-1") if query else "")


def _candidates(headers: list[tuple[str, str]]) -> dict[str, tuple[str, InnerList]] | None:
    """The first signature per profile in Signature-Input, by tag; None when the field does not parse."""
    values = [v for k, v in headers if k.lower() == "signature-input"]
    if not values:
        return {}
    try:
        inputs = parse_dictionary(", ".join(values))
    except AgentHttpSigError:
        return None
    out: dict[str, tuple[str, InnerList]] = {}
    for label, member in inputs.items():
        if not isinstance(member, InnerList):
            continue
        tag = member.params.get("tag")
        profile = "tap" if tag in TAP_TAGS else "web-bot-auth" if tag == WEB_BOT_AUTH_TAG else None
        if profile is not None and profile not in out:
            out[profile] = (label, member)
    return out


async def _prefetch_key(
    profile: str, member: InnerList, headers: list[tuple[str, str]], now: int
) -> tuple[httpsig_keys.ResolvedKey | None, str | None]:
    keyid = member.params.get("keyid")
    if not isinstance(keyid, str) or not keyid:
        return None, None
    if profile == "tap":
        return await httpsig_keys.resolve_tap_key(keyid), None
    agent_label = next(
        (str(c.params.get("key")) for c in member.items if c.value == SIGNATURE_AGENT_FIELD and "key" in c.params),
        None,
    )
    agent_values = [v for k, v in headers if k.lower() == SIGNATURE_AGENT_FIELD]
    if agent_label is None or not agent_values:
        return None, None
    try:
        agent = parse_signature_agent(", ".join(agent_values)).get(agent_label)
    except AgentHttpSigError:
        return None, None
    if agent is None:
        return None, None
    resolved = await httpsig_keys.resolve_signature_agent_key(agent.url, agent.type, keyid, now=now)
    return resolved, agent.url


def _tap_body_result(body: bytes, *, nonce: str, keyid: str, jwk: Mapping[str, Any]) -> dict[str, Any] | None:
    if not body:
        return None
    try:
        document = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(document, Mapping) or not any(name in document for name in TAP_BODY_MEMBERS):
        return None
    checked = verify_tap_body_objects(
        document, nonce=nonce, keyid=keyid, resolve_key=lambda kid: jwk if kid == keyid else None
    )
    return {
        "consumer_present": checked.consumer.present,
        "consumer_verified": checked.consumer.verified,
        "consumer_problems": list(checked.consumer.problems),
        "payment_container_present": checked.payment_container.present,
        "payment_container_verified": checked.payment_container.verified,
        "payment_container_problems": list(checked.payment_container.problems),
        "usable_for_payment": checked.usable_for_payment,
        "consent_record_present": checked.consent_record_present,
        "consent_record_nonce_matches": checked.consent_record_nonce_matches,
        "consent_id": checked.consent_id,
    }


def _thumbprint(jwk: Mapping[str, Any]) -> str | None:
    try:
        return jwk_thumbprint(jwk)
    except (AgentHttpSigError, KeyError, TypeError, ValueError):
        return None


async def _registry_identity(thumbprint: str | None) -> tuple[str | None, tuple[Mapping[str, str], ...]]:
    # Scheme agent ids come only from the registry's attestations for this key; a TAP keyid is
    # never taken to be one.
    if not settings.httpsig_registry_lookup_enabled or thumbprint is None:
        return None, ()
    agent = await httpsig_keys.lookup_registry_agent([("key_thumbprint", thumbprint)])
    if agent is None:
        return None, ()
    return agent.agent_did, agent.scheme_agent_ids


async def check_request_signature(request: Request, *, body: bytes, now: int | None = None) -> SignatureCheck:
    """Verify the request's TAP or Web Bot Auth signature (TAP first when both are present)."""
    current = int(time.time()) if now is None else int(now)
    headers = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in request.headers.raw]
    candidates = _candidates(headers)
    if candidates is None:
        return SignatureCheck("invalid", code="request_signature_invalid", reason="signature_malformed")
    if not candidates:
        has_signature = any(k.lower() == "signature-input" for k, _ in headers)
        return SignatureCheck("unsigned", reason="signature_not_found" if has_signature else "signature_missing")
    authority = expected_authority(request)
    if authority is None:
        return SignatureCheck(
            "invalid",
            code="request_signature_invalid",
            reason="authority_not_accepted",
            profiles_seen=tuple(candidates),
        )
    outcome = SignatureCheck("invalid", code="request_signature_invalid", reason="signature_not_found")
    for profile in ("tap", "web-bot-auth"):
        if profile not in candidates:
            continue
        label, member = candidates[profile]
        resolved, agent_url = await _prefetch_key(profile, member, headers, current)
        recorder = _RecordedNonce()
        signed_keyid = member.params.get("keyid")

        def resolve(
            ref: KeyReference, _resolved: Any = resolved, _url: Any = agent_url, _keyid: Any = signed_keyid
        ) -> Any:
            if _resolved is None or ref.keyid != _keyid:
                return None
            if ref.signature_agent is not None and ref.signature_agent.url != _url:
                return None
            return _resolved.jwk

        try:
            result = verify_with_profile(
                HttpRequest(request.method, _origin_form(request), headers, body),
                profile=profile,
                resolve_key=resolve,
                expected_authority=authority,
                nonce_store=recorder,
                now=current,
                label=label,
            )
        except AgentHttpSigError as exc:
            outcome = SignatureCheck("invalid", code="request_signature_invalid", reason=str(exc)[:80])
            continue
        if not result.ok:
            outcome = SignatureCheck("invalid", code=result.code, reason=result.reason, profiles_seen=tuple(candidates))
            continue
        if recorder.claim is not None:
            scope, nonce, until = recorder.claim
            if profile == "tap":
                until = max(until, current + TAP_NONCE_REPLAY_WINDOW_SECONDS)
            claimed = await httpsig_keys.claim_nonce(f"{profile} {scope}", nonce, until, now=current)
            if claimed is None:
                return SignatureCheck(
                    "unavailable",
                    code="request_signature_unverifiable",
                    reason="nonce_store_unavailable",
                    profiles_seen=tuple(candidates),
                )
            if not claimed:
                outcome = SignatureCheck(
                    "invalid",
                    code="request_signature_invalid",
                    reason="nonce_replayed",
                    profiles_seen=tuple(candidates),
                )
                continue
        if resolved is None or result.keyid is None or result.tag is None:  # the library verified with our key
            continue
        tap_body = (
            _tap_body_result(body, nonce=result.nonce, keyid=result.keyid, jwk=resolved.jwk)
            if profile == "tap" and result.nonce
            else None
        )
        thumbprint = _thumbprint(resolved.jwk)
        agent_did, scheme_ids = await _registry_identity(thumbprint)
        identity = AgentSignatureIdentity(
            profile=profile,
            tag=result.tag,
            keyid=result.keyid,
            alg=result.alg,
            label=result.label,
            created=result.created,
            expires=result.expires,
            signature_agent=result.signature_agent.url if result.signature_agent else None,
            key_source=resolved.source,
            key_thumbprint=thumbprint,
            tap_keyid=result.keyid if profile == "tap" else None,
            agent_did=agent_did,
            scheme_agent_ids=scheme_ids,
            tap_body=tap_body,
        )
        return SignatureCheck("verified", identity=identity, profiles_seen=tuple(candidates))
    return outcome


def _refuse(check: SignatureCheck) -> HTTPException:
    if check.status == "unavailable":
        return HTTPException(
            status_code=503,
            detail={"error": "agent_signature_unverifiable", "reason": check.reason},
            headers={"Retry-After": "5"},
        )
    error = "agent_signature_required" if check.status == "unsigned" else "agent_signature_invalid"
    return HTTPException(
        status_code=401,
        detail={"error": error, "reason": check.reason or check.code},
        headers={"Accept-Signature": ACCEPT_SIGNATURE},
    )


async def _ledger(request: Request, check: SignatureCheck, *, outcome: str) -> None:
    from core.commerce.ledger import record_commerce_event_in_own_transaction

    state = request.state
    signature: dict[str, Any] = (
        check.identity.to_record()
        if check.identity is not None
        else {"status": check.status, "code": check.code, "reason": check.reason}
    )
    await record_commerce_event_in_own_transaction(
        tenant_id=state.tenant_id,
        event=LEDGER_EVENT,
        outcome=outcome,
        merchant_id=getattr(state, "buyer_merchant_id", None),
        seller_agent_id=getattr(state, "buyer_seller_agent_id", None),
        buyer_agent_id=getattr(state, "buyer_agent_id", None),
        payload={"method": request.method, "path": request.url.path, "agent_signature": signature},
        details={"agent_signature": signature},
    )


async def verify_agent_request_signature(request: Request) -> AgentSignatureIdentity | None:
    """FastAPI dependency for commerce API surfaces: verify, record, and in enforce mode refuse."""
    return await _verify_and_record(request, enforceable=True)


async def observe_agent_request_signature(request: Request) -> AgentSignatureIdentity | None:
    """FastAPI dependency for public pages: verify and record, never refuse (enforce mode does not apply)."""
    return await _verify_and_record(request, enforceable=False)


async def _verify_and_record(request: Request, *, enforceable: bool) -> AgentSignatureIdentity | None:
    mode = verify_mode() if enforceable else MODE_LOG
    body = b""
    if request.method not in _READ_METHODS:
        body = await request.body()
        if len(body) > _MAX_SIGNED_BODY_BYTES:
            body = b""
    try:
        check = await check_request_signature(request, body=body)
    except httpsig_keys.KeyFetchError as exc:  # defensive: resolvers catch their own failures
        check = SignatureCheck("unavailable", code="request_signature_unverifiable", reason=str(exc)[:80])
    request.state.agent_signature = check.identity
    is_write = request.method not in _READ_METHODS
    tenant_known = bool(getattr(request.state, "tenant_id", None))
    log_fields = {
        "path": request.url.path,
        "method": request.method,
        "mode": mode,
        "enforceable": enforceable,
        "status": check.status,
        "code": check.code,
        "reason": check.reason,
    }
    if check.identity is not None:
        logger.info("agent_request_signature", **log_fields, agent_signature=check.identity.to_record())
        if is_write and tenant_known:
            try:
                await _ledger(request, check, outcome="success")
            # enterprise-gate: broad-except-ok reason=ledger-write-failure-refuses-in-enforce-logs-in-log-mode
            except Exception:
                logger.exception("agent_request_signature_ledger_failed")
                if mode == MODE_ENFORCE:
                    raise HTTPException(status_code=503, detail="Agent signature could not be recorded") from None
        return check.identity
    logger.info("agent_request_signature", **log_fields)
    if mode != MODE_ENFORCE:
        return None
    if is_write and tenant_known and check.status != "unavailable":
        try:
            await _ledger(request, check, outcome="refused")
        # enterprise-gate: broad-except-ok reason=refusal-stands-when-its-ledger-row-cannot-be-written
        except Exception:
            logger.exception("agent_request_signature_ledger_failed")
    raise _refuse(check)


__all__ = [
    "ACCEPT_SIGNATURE",
    "AgentSignatureIdentity",
    "LEDGER_EVENT",
    "SignatureCheck",
    "check_request_signature",
    "expected_authority",
    "observe_agent_request_signature",
    "verify_agent_request_signature",
    "verify_mode",
]
