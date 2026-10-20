# Mandates

Canonical end-to-end flow: [OACP end-user flow](end-user-flow.md).

This page covers the AgenticOrg side of buyer mandates:

- where mandate hashes are sealed and carried today;
- UPI AutoPay e-mandates on the payment capability;
- the RBI additional factor of authentication (AFA) as an out-of-band buyer step.

The Grantex-issued Intent, Cart and Payment mandates and their verification are described in
[Grantex mandates](#grantex-mandates). That section is completed with the AgenticOrg mandate
client.

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
journal and ledger row of the payment calls carries them too. All three fields are present
today with empty values; the mandate client fills them.

Until then, buyer authority is caller-asserted as before. This covers the vendor member
`ai.agenticorg.commerce.buyer_authority` and the AP2 `checkout_mandate` hash. In strict
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
(policy `commerce-arbiter-v3`):

| Case | Result |
| --- | --- |
| No `upi_autopay` instrument in the decision payload | Passes (`afa_not_applicable`) |
| A UPI AutoPay instrument but no AFA input, for example terms that cannot be built (wrong currency, above the mandate limit) | Refuses (`afa_input_missing`) |
| AFA required and no evidence ref | Refuses (`afa_evidence_missing`) |
| AFA not required (a subsequent debit within the limit) | Passes (`afa_not_required:<basis>`) |
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

To be completed with the AgenticOrg mandate client. Per the cross-repository contract, Grantex
issues Intent, Cart and Payment mandates as ES256 JWTs (`oacp-intent-mandate+jwt`,
`oacp-cart-mandate+jwt`, `oacp-payment-mandate+jwt`) on the OACP status list. AgenticOrg will:

- request them through the authority route;
- verify them locally against the pinned JWKS and status list;
- store each `jti` and the SHA-256 of the compact JWT in the ledger and as `mandate_hashes`;
- set the arbiter's server-side verification ref, which closes `buyer_authority_verified`
  (D1).

The seams are already in place: `ArbiterContext.mandate_hashes`, the stored evidence, the
consumed decision and the Plural order metadata.
