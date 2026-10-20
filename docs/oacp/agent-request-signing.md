# Agent request signing (RFC 9421, Visa TAP, Web Bot Auth)

AgenticOrg verifies the HTTP message signatures of agents that call its public commerce reads and
its A2A routes, and signs the requests its own buyer agent sends. Both directions use RFC 9421
HTTP Message Signatures under two public profiles:

- **Visa Trusted Agent Protocol (TAP)**: `tag` is `agent-browser-auth` (browsing: product details,
  availability, final cost) or `agent-payer-auth` (payment and checkout); at least `@authority`
  and `@path` are covered; `expires` is at most 8 minutes after `created`; a `nonce` is required,
  equal in every signature of the message, and refused when it was seen in the last 8 minutes;
  `keyid` names the key in the TAP key store.
- **Web Bot Auth** (draft-ietf-webbotauth-httpsig-protocol-00 section 5): `tag="web-bot-auth"`,
  `@authority` (or `@target-uri`) plus `"signature-agent";key="<label>"`, `created` and `expires`
  (at most 24 hours), `keyid` is the key's RFC 7638 thumbprint, and keys are looked up by the
  (Signature-Agent URL, keyid) pair (section 5.4).

The implementation is the `grantex-agent-httpsig` library (version 0.2.0, `verify_with_profile`,
`sign_with_profile`, `verify_tap_body_objects`, `verify_directory_response`).

> **Dependency.** The library is not published to PyPI (its package is marked
> `Private :: Do Not Upload`). `requirements.txt` and `pyproject.toml` install it as a direct
> reference pinned to a full commit sha of the public Grantex repository:
>
> ```
> grantex-agent-httpsig @ git+https://github.com/mishrasanjeev/grantex@<40-character sha>#subdirectory=packages/agent-httpsig-py
> ```
>
> A branch or tag is never used (`tests/regression/test_agent_httpsig_pin_20261020.py` refuses
> one), `[tool.hatch.metadata] allow-direct-references` is on, every image that installs the
> Python dependencies has `git`, and `config/pip-audit-exceptions.toml` carries a reviewed
> `[[skip]]` for it (pip-audit cannot look it up on PyPI). The commit must exist on GitHub before
> CI can install it. The sha is the Grantex Phase 5 branch head for now and moves to the Grantex
> `main` merge commit once Phase 5 lands there.

## Inbound verification (`auth/httpsig.py`)

`verify_agent_request_signature` is a FastAPI dependency on:

- every route under `/api/v1/public/commerce/` (`api/v1/commerce_public.py`, all reads), and
- `POST /api/v1/a2a/message:send` and `GET /api/v1/a2a/extendedAgentCard`
  (`api/v1/a2a_interop.py`).

The tenant-admin buyer-access routes (`/api/v1/a2a/commerce/buyer-access`) are operated by people
through the dashboard, not by agents, and are not covered.

For each request:

1. The `Signature-Input` field is parsed. A signature tagged `agent-browser-auth` or
   `agent-payer-auth` is verified under the TAP profile, one tagged `web-bot-auth` under the Web
   Bot Auth profile (TAP first when both are present). A request with no such signature is
   **unsigned** (a signature with another tag is not a trusted-agent signature, TAP-SPEC step 1).
2. The key is resolved asynchronously (see below) before the library runs: the library's key
   resolver and nonce store are synchronous, and a request handler does no sync I/O.
3. `verify_with_profile` checks the covered components, the window, the authority, the algorithm,
   the signature and `content-digest` when covered.
4. The nonce the library asks to store (only after the signature verified) is claimed atomically in
   Redis: `SET httpsig:nonce:<sha256(profile, keyid or Signature-Agent URL and keyid, nonce)> NX EX
   <ttl>`, with the TTL at least the profile's replay window (8 minutes for TAP). A second use is
   `nonce_replayed`.
5. For a TAP signature, the TAP body objects of a JSON body are checked (below).
6. With `AGENTICORG_HTTPSIG_REGISTRY_LOOKUP_ENABLED`, the agent is looked up in the Grantex
   registry for its DID and scheme agent ids (below).

The `@authority` a signature must cover is the request's `Host`. When
`AGENTICORG_HTTPSIG_AUTHORITIES` lists authorities, a `Host` outside the list is refused
(`authority_not_accepted`); set it in every deployment behind a proxy.

### Modes

| `AGENTICORG_HTTPSIG_VERIFY_MODE` | Unsigned or invalid | Verified | Redis unreachable (signed request) |
| --- | --- | --- | --- |
| `log` (default) | Proceeds; structured log `agent_request_signature` with the status and reason | Proceeds; logged; ledgered on write routes | Proceeds; logged as `unavailable` |
| `enforce` | `401` with `Accept-Signature`; a refused write is ledgered (`outcome: refused`) | Proceeds; logged; ledgered on write routes | `503` with `Retry-After` |

An unknown value is treated as `log` and logged. The default `log` changes no response: before
this change the routes read no signature; now they read it and write a log line (and, for a
verified write, one ledger row).

`enforce` applies to every request on the covered routes, including the public seller pages,
sitemap and `llms.txt` that search engines and browsers read without signing. Turn it on only
where every expected caller is a signing agent.

The 401 body is `{"detail": {"error": "agent_signature_required" | "agent_signature_invalid",
"reason": "<library reason>"}}`. RFC 9421 defines no `WWW-Authenticate` scheme for signatures; its
section 5.1 `Accept-Signature` field tells the client what to sign:

```
Accept-Signature: tap=("@authority" "@path");tag="agent-browser-auth", wba=("@authority" "signature-agent";key="wba");tag="web-bot-auth"
```

Reasons come from the library: `signature_missing`, `signature_not_found`, `signature_malformed`,
`tag_not_accepted`, `covered_components_mismatch`, `signature_params_mismatch`, `nonce_missing`,
`nonce_mismatch`, `window_too_long`, `created_in_future`, `expired`, `authority_mismatch`,
`signature_agent_missing`, `signature_agent_unsupported`, `key_unknown`, `key_mismatch`,
`algorithm_mismatch`, `signature_mismatch`, `content_digest_malformed`, `content_digest_mismatch`,
`nonce_replayed`; and from AgenticOrg: `authority_not_accepted`, `nonce_store_unavailable`.

### Key resolution (`auth/httpsig_keys.py`)

Every fetch is async, after the async egress URL check, over the DNS-pinning transport
(`build_pinned_async_transport`), with no redirects, a 5 second timeout and a size cap. Answers
are cached per process for 300 seconds, failures for 60 seconds (negative cache), so a forged
`keyid` cannot force a fetch per request.

| Signature | Key source | Configuration |
| --- | --- | --- |
| TAP | The TAP key store. TAP-SPEC names a `GET /keys` operation keyed by `keyID` but not its URL. | `AGENTICORG_HTTPSIG_TAP_KEY_STORE_URLS`: https URL templates holding `{keyid}`, tried in order. The answer is a JWK, or a JWK Set whose member's `kid` is the keyid. |
| Web Bot Auth, `type=directory` | `<origin>/.well-known/http-message-signatures-directory` (draft-meunier-webbotauth-httpsig-directory-00), media type `application/http-message-signatures-directory+json`, 200 only. Only keys with a valid directory response signature (`tag="http-message-signatures-directory"`, section 5.2) are used. | `AGENTICORG_HTTPSIG_SIGNATURE_AGENT_ORIGINS`: the https origins that may be fetched. Any other origin is never contacted (`key_unknown`). |
| Web Bot Auth, Grantex registry directory | The same path on the Grantex origin (`GRANTEX_BASE_URL`). The registry does not hold agents' private keys, so it signs the directory once with its platform key (`tag="grantex-registry-directory"`, `"@authority";req` and `content-digest`). AgenticOrg verifies that countersignature against the Grantex platform JWKS (the pinned JWKS the OACP artifact verifier uses) and then trusts the listed keys. | Add the Grantex origin to `AGENTICORG_HTTPSIG_SIGNATURE_AGENT_ORIGINS`. |

`Signature-Agent` members of `type=jwks_uri` or `type=cimd` are recognised but have no configured
key source: such a request is `key_unknown`. Supported algorithms: `ed25519`,
`ecdsa-p256-sha256` and `rsa-pss-sha512` (RSA keys of at least 2048 bits). TAP's sample
`rsa-pss-sha256` name is not an RFC 9421 algorithm and is refused.

### What is recorded

A verified identity is `request.state.agent_signature` for the handler and:

- on a write route (`POST /api/v1/a2a/message:send`), a commerce ledger row
  `a2a.agent_signature` (`outcome: success`, actor and buyer/merchant scope from the request) whose
  `details.agent_signature` holds the profile, tag, keyid, algorithm, label, `created`, `expires`,
  Signature-Agent URL, key source, registry agent DID and scheme agent ids, and the TAP body
  object results. The route's own `a2a.message.send` row is unchanged.
- on a read, the structured log `agent_request_signature` with the same fields.

No key material, nonce or credential is recorded. A refused write in `enforce` mode is ledgered
with `outcome: refused` and the reason.

### TAP body objects (G49, inbound)

After a TAP signature verifies, a JSON body's top-level `agenticConsumer`,
`agenticPaymentContainer` and `grantexConsentRecord` members are checked with
`verify_tap_body_objects`: each object's `nonce` must equal the message-signature nonce and its
`kid` the signature's `keyid`, and its signature must verify with the request's key. A consumer
object that fails is a soft failure (the content "may be inaccurate"); a payment container that
fails is `usable_for_payment: false` and must not be used for payment. No covered route takes a
payment, so the result is recorded, not acted on. The A2A `message:send` body accepts the three
members for this reason. TAP does not publish the objects' body placement or signature
canonicalization; the library's defaults (top-level members, RFC 8785 JCS) are used.

### Scheme agent ids (G51)

With `AGENTICORG_HTTPSIG_REGISTRY_LOOKUP_ENABLED=true` a verified agent is looked up in the Grantex
registry, unauthenticated (the registry answers while `REGISTRY_PUBLIC_ENDPOINTS_ENABLED` is on;
Grantex `REGISTRY_SCHEME_AGENT_IDS_ENABLED` must be on for scheme ids):

- a TAP agent by `GET /v1/registry/agents?scheme=visa_tap&scheme_agent_id=<keyid>`: TAP keys an
  agent by the `keyid` of its key store, so that keyid is its Visa TAP scheme agent id;
- an agent whose key came from the Grantex registry directory by
  `GET /v1/registry/agents?key_thumbprint=<keyid>`.

The agent DID and the `scheme_agent_ids` of its counted attestations are recorded with the
identity. Answers are cached (300 seconds, failures 60 seconds); a failed lookup records nothing
and never refuses the request.

AgenticOrg serves no `/.well-known/agent-registry.json` (that document is the Grantex registry's).
Its own scheme agent ids, when `AGENTICORG_A2A_SCHEME_AGENT_IDS` holds a JSON list of
`{"scheme": "visa_tap" | "mastercard_agent_pay", "agent_id": "..."}`, are published in the A2A
agent card (`/.well-known/agent-card.json` and the seller-specific extended card) as an A2A
`AgentExtension`:

```json
"capabilities": {
  "streaming": false, "pushNotifications": false, "extendedAgentCard": true,
  "extensions": [{
    "uri": "https://agenticorg.ai/a2a/extensions/scheme-agent-ids/v1",
    "description": "Payment-network scheme agent ids of this agent, as attested in the Grantex registry.",
    "required": false,
    "params": {"scheme_agent_ids": [{"scheme": "visa_tap", "agent_id": "tap-agent-0001"}]}
  }]
}
```

A malformed setting publishes nothing.

## Outbound signing (G48, `core/commerce/agent_request_signing.py`)

With `AGENTICORG_HTTPSIG_SIGN_OUTBOUND=true`, the Grantex Commerce buyer connector
(`connectors/commerce/grantex_commerce.py`), the client AgenticOrg's buyer agent uses to reach
merchant catalogs, carts, payment intents and checkouts, signs every request with the tenant
agent key under the TAP profile:

- `tag`: `agent-payer-auth` for `payment_create_intent` and `checkout_create`,
  `agent-browser-auth` for every other call;
- covered: `@method`, `@authority`, `@path`, `content-digest`; `created`, `expires` 8 minutes
  later, `keyid`, `alg` and a fresh UUID `nonce` per request; label `sig1`;
- when the tenant configured `agent_signature_agent_url`, a second Web Bot Auth signature `sig2`
  with `Signature-Agent: sig2="<url>"`, carrying the same nonce (TAP requires one nonce across the
  message's signatures; a UUID is also a valid Web Bot Auth nonce). The URL is the origin of a key
  directory that lists the agent's public key.

The bearer credential is sent as before. Off (the default), requests are byte-for-byte as before.

### The tenant agent key

The buyer agent's private key is a JWK (Ed25519, P-256, or RSA of at least 2048 bits) sealed with
the tenant crypto helpers and stored in the tenant's `grantex_commerce` connector configuration:

| Credential | Meaning |
| --- | --- |
| `agent_signing_key_encrypted` | `seal_agent_signing_key(private_key, tenant_id)`: `encrypt_for_tenant` of the private JWK (BYOK envelope when the tenant has a KEK). |
| `agent_signing_keyid` | The key's identifier in the TAP key store. Default: its RFC 7638 thumbprint. |
| `agent_signature_agent_url` | Optional: the https origin of the agent's Web Bot Auth key directory. |

The key is decrypted in a worker thread the first time the connector signs and kept only in
memory; `AgentSigningKey` hides it from `repr`, and nothing logs it. With signing on and no key
(or a key that does not decrypt), the connector refuses the call
(`agent_request_signing_failed`) rather than send it unsigned. Register the public key with the
TAP key store (and the agent's directory) before turning signing on.

### TAP body objects and the consent record (G49, outbound)

On `payment_create_intent` and `checkout_create`, a caller may pass `grantex_consent` (the Grantex
commerce consent record: `id`, `merchantId`, `agentId`, `passportType`, `status`,
`approvedScopes`, `maxAmount`, `currency`, `consentTextVersion`, `presentedPayloadHash`,
`approvedAt`, `expiresAt`) and `tap_consumer_id_token` (the consumer's TAP id token, a compact
JWT). They are never forwarded as tool arguments. With signing on and a consent present, the JSON
body carries, bound to the message-signature nonce and signed with the agent key (`kid` = keyid):

- `agenticPaymentContainer` (TAP payment container: `nonce`, `kid`, `alg`, `signature`; AgenticOrg
  holds no card data, so it carries no payment payload);
- `agenticConsumer` when an id token was given;
- `grantexConsentRecord` (`build_grantex_consent_record`): only for a granted, unexpired consent
  with approved scopes; anything else refuses the call.

With signing off the two inputs are dropped.

## Configuration summary

| Variable | Default | Effect |
| --- | --- | --- |
| `AGENTICORG_HTTPSIG_VERIFY_MODE` | `log` | `enforce` refuses unsigned or invalid requests on the covered routes. |
| `AGENTICORG_HTTPSIG_AUTHORITIES` | empty (the `Host` header) | Authorities a signature may cover. |
| `AGENTICORG_HTTPSIG_TAP_KEY_STORE_URLS` | empty (no TAP key resolves) | TAP key-store URL templates with `{keyid}`. |
| `AGENTICORG_HTTPSIG_SIGNATURE_AGENT_ORIGINS` | empty (no Web Bot Auth key resolves) | Directory origins that may be fetched. |
| `AGENTICORG_HTTPSIG_REGISTRY_LOOKUP_ENABLED` | `false` | Grantex registry lookup of verified agents. |
| `AGENTICORG_HTTPSIG_SIGN_OUTBOUND` | `false` | Sign the buyer connector's requests. |
| `AGENTICORG_A2A_SCHEME_AGENT_IDS` | empty | AgenticOrg's scheme agent ids in the A2A agent card. |

Rollout: [launch and rollback runbook](explainers/launch-rollback-runbook.md).

## Scheme token binding (G52)

The payment capability's scheme token binding (`{scheme, token_ref, agent_id, merchant_scope,
limits}`) is documented with the [purchase and mandate handoff](purchase-mandate-handoff.md#scheme-token-binding).

## Not implemented

- `Signature-Agent` types `jwks_uri` and `cimd` (no key source configured for them).
- Requesting a fresh signature with `403` or `429` and `Accept-Signature` (Web Bot Auth section
  5.3); a refused request gets `401`.
- Acting on TAP payment containers: no covered route takes a payment.
- Mastercard Agent Pay wire details are not public; only the `mastercard_agent_pay` scheme label of
  the Grantex registry is recognised.
