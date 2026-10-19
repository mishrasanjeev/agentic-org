# Commerce Arbiter And Ledger

Canonical end-to-end flow: [OACP end-user flow](end-user-flow.md).

Every commerce write leaves a signed, hash-chained ledger row, and every commitment-bound
action (purchase handoff, POS handoff, and later mandate request and session complete) is
decided by a deterministic arbiter before anything executes.

**No human legs.** The arbiter is a pure policy function. It seals `allow` or `refuse`; it never
waits for a person and never returns "pending". Human review is a merchant option that only
copies a refusal into the review queue; it never changes or delays an outcome.

## Model

| Part | Where | What it holds |
| --- | --- | --- |
| Ledger row | `audit_log`, `resource_type = commerce_ledger` (`core/commerce/ledger.py`) | Event name, actor, tenant, `details.ledger` = merchant, seller agent, buyer agent, `payload_hash`. Signed with the platform audit key; linked into the tenant hash chain by the sealing task. |
| Arbiter decision | `commerce_arbiter_decisions` (`core/models/commerce_arbiter.py`) | `decision_id`, tenant, merchant, seller, buyer, action, `payload_hash`, outcome, `rules_evaluated`, `failing_rule`, per-rule results, evidence (six keys), `evidence_hashes`, `policy_version`, `decided_at`, `expires_at`, `consumed_at`. |
| Arbiter | `core/commerce/arbiter.py` | `decide(action, context) -> Decision`. No clock reads (the decision time is an input), no I/O, no imports from the review queue. |
| Executor guard | `core/commerce/arbiter_store.py` `require_allow_decision` | Consumes an `allow` decision once, for the same tenant, action and payload hash, before its TTL. |

`payload_hash` is the lowercase hex SHA-256 of the RFC 8785 canonical JSON of the payload, the
same canonicalisation as the OACP artifact JWS profile. The payload itself is not stored in the
ledger; whoever holds it can prove which row it belongs to. For a decision, the payload is the
action's binding payload (`binding_payload`): the handoff or packet fields that matter
(product, variant, quantity, price, currency, idempotency key, packet id). Execution flags and
timestamps are excluded, so a decision taken at preparation still binds the identical payload
at execution, and a changed price, quantity or packet is refused.

Every commerce write route records its declared `route_meta(audit_event=...)` event in the same
database transaction as the write. Route enforcement (`api/route_enforcement.py`) checks this for
every non-read route under `/api/v1/commerce/`, `/api/v1/public/commerce/`,
`/api/v1/a2a/commerce/` and `POST /api/v1/a2a/message:send`: a write route that returns without
its event is a server error (`commerce_ledger_event_missing`) in enforce mode and a logged error
in log mode (`AGENTICORG_ROUTE_ENFORCEMENT_MODE`). A route that raises is not checked; refusals
that matter (artifact verification refusals, arbiter refusals) are written in their own
transaction. The public commerce router has read routes only.

## Rules

Evaluated in this fixed order. Every rule is evaluated and listed in `rules_evaluated`; the first
failure is `failing_rule` and makes the outcome `refuse`. A missing input refuses with the rule
that needed it.

| Rule | Allows when | Refusal reasons |
| --- | --- | --- |
| `mandate_present_unexpired` | Mandate reference and hash present, expiry after the decision time. Not evaluated for `mandate_request`. | `mandate_missing`, `mandate_expiry_missing`, `mandate_expired` |
| `consent_active` | Consent reference present, status `active` or `granted`. | `consent_missing`, `consent_<status>` |
| `passport_amount_cap` | Commerce Passport reference, cap, currency and expiry present; unexpired; same currency as the order; cap at least the order amount (minor units). | `passport_missing`, `passport_cap_missing`, `passport_expiry_missing`, `passport_expired`, `order_amount_missing`, `passport_currency_mismatch`, `passport_amount_cap_exceeded` |
| `buyer_authority_verified` | The buyer authority (mandate, consent, passport) carries a server-side verification ref, or verified authority is not required (`AGENTICORG_ARBITER_REQUIRE_VERIFIED_AUTHORITY`, on in strict runtimes). | `buyer_authority_caller_asserted` |
| `oacp_artifacts_valid` | For each required family (catalog snapshot, price, inventory, policy, mandate capability, protocol adapter; policy and mandate capability for `mandate_request`) an artifact in scope that the ES256 verifier accepted (`oacp-artifact-jws-v1;` ref with an `oacp-` key), fresh, unexpired, with a `fresh` revocation snapshot. | `artifact_missing:<family>`, `artifact_revoked:<family>`, `artifact_unverified:<family>`, `artifact_revocation_unknown:<family>`, `artifact_expired:<family>`, `artifact_stale:<family>` |
| `merchant_limits` | Merchant config `provider_policy.arbiter` sets `max_order_amount_minor_units`, `currency_allowlist`, `max_quantity_per_order`, and the order is within them. | `merchant_limits_missing`, `order_amount_missing`, `currency_not_allowed`, `quantity_cap_exceeded`, `order_amount_cap_exceeded` |

Mandate, consent and passport references arrive in the request's `buyer_authority` object. Until
Grantex-signed mandates and passports are verified server-side (Phase 4), they are
caller-asserted evidence: they are hashed into the decision and the ledger, never treated as
credentials, and a missing value refuses.

Fail closed until Phase 4: rule `buyer_authority_verified` needs a server-side verification ref
for that evidence. No code path produces one yet and the request body cannot carry one
(`buyer_authority` refuses unknown members), so:

- strict runtimes (production, staging, preview and unrecognised `AGENTICORG_ENV` labels, the
  same detection as `AGENTICORG_OACP_VERIFY_STRICT`) refuse every purchase handoff, POS handoff
  and mandate request with `failing_rule: buyer_authority_verified`
  (`reason: buyer_authority_caller_asserted`);
- local, dev, test and ci runtimes, or any runtime with
  `AGENTICORG_ARBITER_REQUIRE_VERIFIED_AUTHORITY=false`, pass the rule and record
  `buyer_authority_source: caller_asserted` in the decision (`arbiter_decision`, the stored
  `evidence` and the ledger row).

`AGENTICORG_ARBITER_REQUIRE_VERIFIED_AUTHORITY` set explicitly wins in either direction. The
rule is part of the decision id material, so the same request decided under the two settings
gets two different decision ids. The policy version is `commerce-arbiter-v2`.

Merchant configuration (under `provider_policy`):

```json
{
  "arbiter": {
    "max_order_amount_minor_units": 500000,
    "currency_allowlist": ["INR"],
    "max_quantity_per_order": 5,
    "escalate_to_human": false,
    "review_agent_id": null
  }
}
```

## Decision Lifecycle

1. A preparation call (purchase prepare, POS handoff) builds the binding payload and calls
   `decide`. The decision row and its ledger row (`commerce.arbiter.decision`) are written in the
   request's transaction, with the route's own ledger row.
2. A refusal is returned as a blocker in the runtime's blocker shape (`code: arbiter_refused`,
   `failing_rule`, `reason`, `decision_id`, `owner`, `action`, `required_config`,
   `unblock_command`, `doc_reference`).
3. An allow is returned with its `decision_id` and `payload_hash`. It expires after
   `DECISION_TTL` (10 minutes).
4. The executing call presents `decision_id`. The route decides again on the current state and,
   only if that is also `allow`, the guard consumes the presented decision in one conditional
   UPDATE (`commerce.arbiter.decision.consumed` on the ledger). Of two concurrent executors one
   wins; the other is refused `decision_consumed`. Other refusals: `decision_id_missing`,
   `decision_not_found` (including another tenant's id), `decision_not_allow`,
   `decision_action_mismatch`, `decision_payload_mismatch`, `decision_expired`.
5. With `escalate_to_human: true` and a `review_agent_id` of the tenant's agents, a refusal also
   queues a review item in `hitl_queue` (`trigger_type = commerce_arbiter_refusal`,
   `outcome_binding: false`) and the response carries `escalation.hitl_ref`. The response does not
   wait for it and the decision does not change. Off (the default), nothing touches
   `hitl_queue`.

Session complete arrives with the checkout sessions of Phase 3; it uses the same guard with the
`session_complete` action. A mandate request route arrives with Phase 4 mandates; the arbiter
already defines the `mandate_request` rule set.

## Append-Only Storage: Choice And Alternative

Chosen: **mirror every write into the chained `audit_log` and make the tables append-only.**

- `commerce_arbiter_decisions` refuses UPDATE and DELETE (trigger
  `commerce_arbiter_decisions_guard`) except two one-time transitions: an `allow` row consumed
  (`consumed_at` filled, nothing else changed) and the Grantex countersignature reference
  attached (`grantex_grant_jti`, `grantex_recorded_at`).
- `oacp_operator_decision_records`, `oacp_audit_review_manifest_records` and
  `oacp_retention_disposition_decision_records` refuse every UPDATE and DELETE (trigger
  `oacp_record_reject_mutation`). Their repositories write a record once
  (`record_append_only` on a repeat) and mirror it into the ledger
  (`commerce.oacp.operator_decision.record`, `commerce.oacp.audit_review_manifest.record`,
  `commerce.oacp.retention_disposition_decision.record`) in the same transaction. A future
  retention deletion needs a new, documented transition in the trigger.
- `audit_log` keeps its append-only trigger with the sealing transition. Migration
  `v6z88_commerce_ledger` reinstalls it: a database bootstrapped from an empty schema is stamped
  past the baseline that created the trigger, so only `init_db` used to install it there.

Alternative not taken: `prev_hash`/`row_hash` columns on each table. The audit chain already
links, anchors and verifies every tenant's rows (`core/governance/audit_chain.py`); a second
chain per table would duplicate the sealing, anchoring and verification code and still need the
append-only trigger to stop edits between seals.

The chain seals behind `AGENTICORG_AUDIT_CHAIN_ENABLED`. `AGENTICORG_AUDIT_CHAIN_DEFAULT_ON`
(off by default) also seals every tenant that has a commerce merchant config while the global
flag is off.

## Seams For The Next Work

- Ledger read and export: `core.commerce.ledger.list_commerce_events` (sealed rows in chain
  order, then unsealed rows) and `ledger_view` (one row in exportable shape).
- Operator decisions bound to arbiter decisions: `core.commerce.arbiter_store.get_decision`
  reads a decision within the tenant.
- Grantex countersignature: `Decision.to_public()` and the stored `evidence` carry the CONTRACT
  section 7 body; the row reserves `grantex_grant_jti` and `grantex_recorded_at` for the
  one-time attach transition.
- Chain-head witness posting: `core.governance.audit_chain.status(tenant_id)` returns the head
  (`seq`, `hash`) to post.
