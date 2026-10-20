# Mandates

Canonical end-to-end flow: [OACP end-user flow](end-user-flow.md).

This page covers both sides of buyer mandates:

- the Intent, Cart and Payment mandates Grantex issues and AgenticOrg requests and verifies
  ([Grantex mandates](#grantex-mandates));
- where mandate hashes are sealed and carried;
- UPI AutoPay e-mandates on the payment capability;
- the RBI additional factor of authentication (AFA) as an out-of-band buyer step.

**No human legs.** AFA is the buyer's own step at their UPI app. AgenticOrg returns it as an
action and refuses completion until server-side evidence exists. It never waits for it.

## Mandate Hashes On Decisions And Provider Orders

Every arbiter decision carries `mandate_hashes: {intent, cart, payment}` in its stored evidence
(`commerce_arbiter_decisions.evidence`). Each value is the lowercase hex SHA-256 of a compact
mandate JWT, or `null`.

The arbiter input `ArbiterContext.mandate_hashes` is set only server-side; no request body can
supply it. A value that is not 64 lowercase hex characters is dropped. A consumed decision
hands the hashes to the executor (`ConsumedDecision.mandate_hashes`).

The Plural executor puts them in the Plural order's `merchant_metadata` as
`oacp_intent_mandate_hash`, `oacp_cart_mandate_hash` and `oacp_payment_mandate_hash`. Every
journal and ledger row of the payment calls carries them too. They are filled when the
completion ran on server-verified mandates ([Grantex mandates](#grantex-mandates)); otherwise
they are empty strings.

Without verified mandates the buyer authority is caller-asserted: the vendor member
`ai.agenticorg.commerce.buyer_authority` or the AP2 `checkout_mandate` hash. In strict
runtimes the rule `buyer_authority_verified` refuses it
([Commerce arbiter](arbiter-ledger.md#rules)).

## UPI AutoPay On The Payment Capability

A merchant offers UPI AutoPay with these two settings together:

- `upi_autopay` in `provider_policy.ucp.mandate_capability.instrument_types`;
- a complete `mandate_capability.upi_autopay` block.

```json
{
  "mandate_capability": {
    "rails": ["card", "upi"],
    "max_amount_minor_units": 500000,
    "currency": "INR",
    "instrument_types": ["card", "upi_autopay"],
    "upi_autopay": {
      "amount_rule": "max",
      "max_amount_minor_units": 500000,
      "frequency": "monthly",
      "validity_days": 365,
      "purpose": "general",
      "authorization_url": "https://payments.merchant.example/upi/mandates/{mandate_reference}"
    }
  }
}
```

| Field | Meaning | Source |
| --- | --- | --- |
| `amount_rule` | `fixed` (the mandate amount is the first debit) or `max` (a per-debit maximum) | RBI e-mandate framework 2026, s.4(c) |
| `max_amount_minor_units` | The mandate limit in paise | NPCI-UNCONFIRMED: the UPI maximum is not public, so it is configuration |
| `frequency` | `one_time`, `daily`, `weekly`, `fortnightly`, `monthly`, `bimonthly`, `quarterly`, `half_yearly`, `yearly` | NPCI AutoPay (2020); the API enum strings are NPCI-UNCONFIRMED and mapped by the PSP |
| `validity_days` | Mandate validity from the session date (1 to 3650) | RBI s.4(b) requires a validity period; the UPI maximum is NPCI-UNCONFIRMED |
| `purpose` | `general`, `insurance_premium`, `mutual_fund_subscription`, `credit_card_bill` | RBI s.8(b) |
| `authorization_url` | The merchant's or PSP's https page where the buyer registers the mandate with the UPI PIN; `{mandate_reference}` is substituted | Merchant |

A capability with any value missing or invalid is not offered: the `upi_autopay` instrument type
is dropped (fail closed). The handler config carries the block as
`mandate_capability.upi_autopay`. The instrument a platform sends is a `upi_autopay` instrument
on the Plural handler, with a `plural_pine_token` credential (the PSP's token for the payer's
UPI handle).

## RBI AFA: When It Applies

`core/commerce/india_rails.py`, from the RBI Master Direction "Digital Payments - E-mandate
Framework, 2026" (21 April 2026):

| Payment | AFA |
| --- | --- |
| Mandate registration, and the first debit combined with it (s.4(a), s.5(a)) | Always |
| A subsequent debit up to INR 15,000 (s.8(a)) | Not required |
| A subsequent debit up to INR 1,00,000 for insurance premiums, mutual fund subscriptions, credit-card bills (s.8(b)) | Not required |
| A subsequent debit above those amounts | Required |
| Unknown amount, currency or debit kind | Required (fail closed) |

The amounts are settings in minor units:

- `AGENTICORG_RBI_EMANDATE_AFA_LIMIT_MINOR_UNITS` (default `1500000`)
- `AGENTICORG_RBI_EMANDATE_AFA_HIGHER_LIMIT_MINOR_UNITS` (default `10000000`)

A later RBI revision is therefore a configuration change. A UCP checkout is always a
registration plus first debit, so AFA always applies there. The subsequent-debit rows apply to
recurring charges against an existing mandate.

## The Arbiter Rule `afa_evidence_present`

AFA is enforced in the arbiter, not in the handler. It is the last rule of `session_complete`
(policy `commerce-arbiter-v4`):

| Case | Result |
| --- | --- |
| No `upi_autopay` instrument in the decision payload | Passes (`afa_not_applicable`) |
| A UPI AutoPay instrument but no AFA input, for example terms that cannot be built (wrong currency, above the mandate limit) | Refuses (`afa_input_missing`) |
| AFA required and no evidence ref | Refuses (`afa_evidence_missing`) |
| AFA not required (a subsequent debit within the limit) | Passes (`afa_not_required:<basis>`), except for a `human_not_present` purchase, which has no AFA-exempt path and needs the evidence |
| Evidence ref present | Passes; the ref's SHA-256 joins `evidence_hashes`, so the allowed decision differs from the refused one |

The evidence ref is read from `commerce_afa_evidence`, never from the request. It exists only
after a verified Plural event (below).

## The Out-Of-Band Step

When the arbiter refuses with `afa_evidence_present`, the session leaves `ready_for_complete`
and the response carries the step. The refused decision is in
`ai.agenticorg.commerce.decision`. AgenticOrg records `requested` in `commerce_afa_evidence`
and ledgers `commerce.ucp.payment.afa_requested`.

- **UCP 2026-08-25, with the vendor extension `ai.agenticorg.payment.upi_mandate` negotiated.**
  The extension extends checkout and is advertised only to merchants offering UPI AutoPay. Its
  schema URL is on agenticorg.ai. The session becomes `incomplete` and carries the Action
  `ai.agenticorg.payment.upi_mandate_authorization`:
  `{id, config: {payment_instrument_id, url, mandate_reference, mandate: {amount_rule, amount,
  currency, frequency, start_date, end_date, purpose}}}`. It also carries a `recoverable` error
  `mandate_authorization_required` whose `path` is
  `$.actions['ai.agenticorg.payment.upi_mandate_authorization'][0]` (UCP overview, Actions).
  The Action id is stable for the same mandate reference and terms.
- **Otherwise (2026-04-08, or the extension not negotiated).** The session becomes
  `requires_escalation` with `continue_url` set to the authorization URL and a
  `requires_buyer_input` error `mandate_authorization_required`. This is the core UCP
  mechanism 2026-04-08 uses for 3DS.
- **ACP.** `india_rails.acp_afa_out_of_band(...)` returns the ACP mapping for the ACP binding to
  adopt. That is a `requires_escalation` session with `continue_url`, plus a `MessageError`
  `approval_required` with `resolution: requires_buyer_input`. It also names the ACP extension
  `ai.agenticorg.upi_mandate` and its `upi_mandate` member: action id, instrument, mandate
  reference and terms. The constants are `ACP_AFA_OUT_OF_BAND` and `ACP_UPI_MANDATE_EXTENSION`.
  ACP 2026-04-17 has a closed `interventions.supported` list without UPI.

The mandate reference is `aom-` plus 40 hex characters, derived from the tenant, session and
instrument. The buyer opens the URL, authorises the mandate with the UPI PIN, and the PSP
reports the outcome through Plural.

The merchant's integration relays that event to
`POST /api/v1/commerce/runtime/ucp/plural/events`. The event is verified with Plural's
signature ([Plural executor](plural-executor.md#webhooks)) and then recorded:

- **Authorised outcome** (`ACTIVE`, `AUTHORIZED` or `APPROVED`): an `authorized` row with the
  evidence ref `afa:plural_pine:<digest>`. The session returns to `ready_for_complete`.
- **Failed outcome:** a `failed` row; the session stays blocked.

The next Complete gets a fresh decision, which now passes `afa_evidence_present`, and the
executor charges through the mandate.

A forged event changes nothing (`signature_invalid`). Neither does an event for an unknown
reference. The buyer's PSP is responsible for the 24-hour pre-debit notice and the opt-out
(RBI s.6). Neither maps to checkout.

## Grantex Mandates

Cross-repository contract section 8. Grantex issues the mandates; AgenticOrg requests them for
one checkout, verifies them itself, and only then lets the arbiter treat the buyer authority as
verified. This closes decision D1 (`buyer_authority_verified`).

### What Grantex Issues

Three compact JWTs signed ES256 (P-256) with the Grantex OACP issuer key (an `oacp-` kid
published in `/.well-known/jwks.json`). Each names its entry on the OACP Token Status List.

| Mandate | `typ` | Carries |
| --- | --- | --- |
| Intent | `oacp-intent-mandate+jwt` | `intent {description_hash, max_amount {value, currency}, merchants, expires_at, constraints}` and `cnf.jwk` when the buyer agent has a key |
| Cart | `oacp-cart-mandate+jwt` | `cart {checkout_hash, checkout_id, line_items [{id, quantity, digest}], total}`, `intent_mandate_jti`, and for `human_present` a `webauthn` evidence whose challenge is `checkout_hash` |
| Payment | `oacp-payment-mandate+jwt` | `payment {transaction_id = checkout_hash, amount, currency, payee {id}, rail, token_binding {type, id}, cart_mandate_jti}` |

Common claims: `iss`, `sub` (the passport subject), `aud` (= `merchant_id`), `iat`, `exp`
(at most 600 s after `iat`, never past the passport or the parent), `jti`, `mandate_version`
`oacp.mandate.v1`, `tenant_id`, `merchant_id`, `buyer_agent_id`, `presence`
(`human_present` or `human_not_present`), `amr`, `passport_ref`, `consent_ref`,
`status.status_list {idx, uri}`. The claims use the AP2 v0.2 member names where AP2 has one
(`checkout_hash`, `transaction_id`, constraints, `cnf`, `payee`); the Grantex guide
`docs/guides/oacp/mandates-decision.mdx` holds the mapping and why plain JWTs were chosen over
SD-JWT VC.

Grantex refuses to issue, and records the refusal, when the passport cap is exceeded
(`passport_cap_exceeded`), the consent is not active (`consent_not_active`), the chain does not
link (`mandate_chain_mismatch`, `intent_cap_exceeded`), a parent expired or was revoked or
suspended (`parent_mandate_expired`, `parent_mandate_revoked`, `parent_mandate_suspended`), a
`human_present` cart has no valid passkey evidence (`passkey_evidence_required`,
`passkey_evidence_invalid`, `passkey_evidence_replayed`), or the request carries card data or an
unknown rail (`card_data_refused`, `payment_rail_unsupported`, `token_binding_invalid`).

### How AgenticOrg Requests Them

A UCP Complete opts in with the vendor member `ai.agenticorg.commerce.mandate_request`:

```json
{
  "payment": {"instruments": [{"id": "instr_1", "handler_id": "plural_tokenized", "type": "card",
    "credential": {"type": "plural_pine_token", "token": "tok_example_1"}}]},
  "ai.agenticorg.commerce": {
    "mandate_request": {
      "passport_jwt": "<the checkout Commerce Passport the buyer granted>",
      "presence": "human_present",
      "description": "A tote bag for the office, delivered to Pune",
      "max_amount_minor_units": 50000,
      "webauthn_assertion": {"id": "...", "rawId": "...", "type": "public-key",
        "response": {"clientDataJSON": "...", "authenticatorData": "...", "signature": "..."}}
    }
  }
}
```

- `passport_jwt`: the checkout Commerce Passport (Grantex verifies it in payment-affecting mode
  and the consent behind it).
- `presence`: `human_present` needs `webauthn_assertion`; `human_not_present` refuses one.
- `max_amount_minor_units`: the intent cap; defaults to the session total.
- `webauthn_assertion`: the buyer's `AuthenticationResponseJSON` over `checkout_hash`, signed by
  the buyer's authenticator on the platform. AgenticOrg passes it to Grantex unchanged; it never
  creates, stores or alters one.

The merchant names its Grantex commerce tenant and merchant in
`provider_policy.ucp.grantex_mandates` (`{tenant_id, merchant_id, rail?, token_type?}`; rail
`plural` and token type `plural_pine_token` by default). The flag
`AGENTICORG_GRANTEX_MANDATES_ENABLED` (default off) turns the request on; while it is off, or the
merchant names no Grantex merchant, the outcome is `unavailable` and the authority stays
caller-asserted.

The checkout object the cart mandate binds is derived by the server from the session
(`completion.mandate_checkout`): `{id, revision, currency, merchant {id}, line_items [{id, item
{id, price}, quantity}], totals [{type: total, amount}]}`. Its RFC 8785 SHA-256, unpadded
base64url, is `checkout_hash`. Every checkout response of a merchant with `grantex_mandates`,
while `ready_for_complete`, names both under `ai.agenticorg.commerce.mandate_checkout`, so the
platform knows the challenge to sign. A changed session has a new revision and a new hash.

`core/commerce/mandates_client.py` then calls, in order and before the decision's transaction:

1. `POST /v1/commerce/oacp/mandates/intent` `{tenant_id, passport_jwt, description, max_amount,
   presence}`;
2. `POST /v1/commerce/oacp/mandates/cart` `{tenant_id, passport_jwt, intent_mandate, checkout,
   presence, webauthn_assertion?}`;
3. `POST /v1/commerce/oacp/mandates/payment` `{tenant_id, passport_jwt, cart_mandate, rail,
   token_binding}`. The token binding is `{type, id: "sha256:<credential digest>"}`: a digest of
   the payment credential, never the token.

Each call uses the AgenticOrg service token (`GRANTEX_COMMERCE_BASE_URL`,
`GRANTEX_COMMERCE_INTERNAL_TOKEN`; Grantex gives each route its own scope), async httpx over the
DNS-pinning egress transport after the async egress validator, a 5 s timeout, no redirect, no
retry. A refusal stops the chain and its Grantex code is recorded (`refused`); an unreachable
Grantex is `unavailable`. Nothing waits.

### How AgenticOrg Verifies Them

`core/commerce/oacp_mandates.py`, against the issuer, JWKS and status-list origins pinned in
configuration (the Phase 1 trust fetcher, `core/commerce/oacp_grantex_trust.py`), never a URL
taken from a mandate. Stop at the first failure, with the shared refusal code:

| Step | Check | Refusal |
| --- | --- | --- |
| 1 | Three canonical base64url parts | `mandate_malformed` |
| 2 | Header is a JSON object with no repeated member; `alg` ES256; exactly `alg`, `kid`, `typ`; the expected `typ` | `mandate_malformed`, `mandate_algorithm_unsupported`, `mandate_type_mismatch` |
| 3 | An `oacp-` kid published exactly once as a usable P-256 key (one JWKS refresh for an unknown kid) | `issuer_key_untrusted` |
| 4 | ES256 signature (64-byte `r || s`) | `mandate_signature_invalid` |
| 5 | Claims are a JSON object with no repeated member at any depth | `mandate_malformed` |
| 6 | `iss` is the pinned issuer | `mandate_issuer_mismatch` |
| 7 | Common and per-type claims | `mandate_claims_invalid` |
| 8 | `iat` within 60 s skew, unexpired, lifetime at most 600 s | `mandate_not_yet_valid`, `mandate_expired`, `mandate_lifetime_exceeded` |
| 9 | `aud` is the merchant's Grantex merchant | `mandate_audience_mismatch` |
| 10 | Token Status List from the pinned origin (https), verified as `statuslist+jwt` | `revocation_status_unavailable` (fail closed), `mandate_revoked`, `mandate_suspended` |

Then the chain (`mandate_chain_mismatch`): the parent jtis link, `transaction_id ==
checkout_hash`, the payment amount and currency equal the cart total, the cart total is within
the intent cap and currency, the cart's merchant is in the intent's merchants, a child never
outlives its parent, a `human_present` intent has a `human_present` cart, and all three share
tenant, merchant, subject, buyer agent, consent and issuer. Last, the binding to this checkout
(`mandate_binding_mismatch`): the Grantex tenant the merchant configured, the `checkout_hash` the
server computed, the session total and currency. The session is checked again inside the
decision's transaction; a revision that moved since the request refuses the chain.

Every case of the shared vectors passes with the exact code: `tests/fixtures/oacp/oacp-mandates-v1.json`
(31 cases, 5 chains) and `oacp-mandates-v1-extra.json` (repeated member names), in
`tests/unit/test_oacp_mandates_vectors.py`. Grantex's `POST /v1/commerce/oacp/mandates/verify` is
used only as a cross-check in the live integration test, never at runtime.

### What The Arbiter Receives

Only a verified, linked and bound chain reaches the arbiter as authority
(`completion.arbiter_context(verified_mandates=...)`):

| Arbiter input | From the chain |
| --- | --- |
| mandate `ref`, `hash`, `expires_at` | the payment mandate jti, its `mandate_hash`, the earliest `exp` of the three |
| consent `ref`, `status` | `consent_ref`, `active` (Grantex issued under granted consent; the status list says VALID) |
| passport `ref`, cap, currency, expiry | `passport_ref`, the intent `max_amount` (never above the passport cap), the chain expiry |
| `authority_verification_ref` | `oacp-mandate-v1;jti=<payment jti>;mandate_hash=<hex>;verified_at=<time>` |
| `mandate_hashes`, `mandate_jtis` | the three hashes and jtis |
| `presence`, intent cap | for `human_not_present_limits` |

`buyer_authority_verified` passes only when the ref parses and names the same jti and hash as
the mandate evidence; any other string refuses `buyer_authority_ref_invalid`. No request member
can set any of these: the body's `buyer_authority` is ignored when a verified chain exists.

The decision row stores `mandate_ref` (payment jti), `mandate_hash` (hex SHA-256 of the compact
payment JWT), `mandate_hashes`, `mandate_jtis`, `presence` and the verification ref; the ledger
seals them in `commerce.arbiter.decision` (`decision.mandates`) and in
`commerce.ucp.checkout.mandates` (status, code, jtis, hashes, presence). The Grantex
countersignature body carries the payment mandate's `mandate_ref` and `mandate_hash`. The Plural
order's `merchant_metadata` carries the three hashes, and so does every payment journal row.

### Not-Present Purchases

With `presence: human_not_present` the arbiter applies the stricter rule
`human_not_present_limits` (policy `commerce-arbiter-v4`): the merchant's not-present limits
must be configured and met, and the intent cap must cover the total.

```json
{"provider_policy": {"arbiter": {
  "max_order_amount_minor_units": 100000, "currency_allowlist": ["INR"], "max_quantity_per_order": 5,
  "human_not_present": {"max_order_amount_minor_units": 20000, "max_quantity_per_order": 2}
}}}
```

A UPI AutoPay instrument on a not-present purchase has no AFA-exempt path. There is no human
step: the rule refuses, and the refusal is sealed with `failing_rule: human_not_present_limits`.

### Residuals

- A mandate lives at most 600 s and never outlives its checkout passport; longer autonomous
  windows need a longer-lived consent model on the Grantex side.
- The buyer does not sign the mandate itself: Grantex attests the consent and the passkey.
- Only the UCP Complete path requests mandates. Purchase, POS and mandate-request handoffs remain
  caller-asserted and are refused in strict runtimes.
- Mandate revocation is read from the status list at decision time; a mandate revoked after the
  decision does not reverse an execution already made.
