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
| `buyer_authority_verified` | The buyer authority carries the server-side verification ref the local mandate verifier produced, naming the same payment mandate jti and `mandate_hash` as the mandate evidence ([Grantex mandates](mandates.md#grantex-mandates)); or verified authority is not required (`AGENTICORG_ARBITER_REQUIRE_VERIFIED_AUTHORITY`, on in strict runtimes). | `buyer_authority_caller_asserted`, `buyer_authority_ref_invalid` |
| `oacp_artifacts_valid` | For each required family (catalog snapshot, price, inventory, policy, mandate capability, protocol adapter; policy and mandate capability for `mandate_request`) an artifact in scope that the ES256 verifier accepted (`oacp-artifact-jws-v1;` ref with an `oacp-` key), fresh, unexpired, with a `fresh` revocation snapshot. | `artifact_missing:<family>`, `artifact_revoked:<family>`, `artifact_unverified:<family>`, `artifact_revocation_unknown:<family>`, `artifact_expired:<family>`, `artifact_stale:<family>` |
| `merchant_limits` | Merchant config `provider_policy.arbiter` sets `max_order_amount_minor_units`, `currency_allowlist`, `max_quantity_per_order`, and the order is within them. | `merchant_limits_missing`, `order_amount_missing`, `currency_not_allowed`, `quantity_cap_exceeded`, `order_amount_cap_exceeded` |
| `human_not_present_limits` | No verified presence (`presence_unverified`; caller-asserted authority is governed by `buyer_authority_verified`); or `human_present`; or `human_not_present` with the merchant's not-present limits (`provider_policy.arbiter.human_not_present.max_order_amount_minor_units`, optional `max_quantity_per_order`) configured and met and the intent mandate `max_amount` covering the total in its currency. | `presence_unknown`, `not_present_limits_missing`, `order_amount_missing`, `intent_cap_missing`, `intent_currency_mismatch`, `intent_cap_below_total`, `not_present_amount_cap_exceeded`, `not_present_quantity_cap_exceeded` |
| `afa_evidence_present` (`session_complete` only, last) | No UPI AutoPay instrument in the payload; or RBI AFA does not apply (a subsequent debit within the limit; never for `human_not_present`); or server-side AFA evidence is present ([Mandates](mandates.md#the-arbiter-rule-afa_evidence_present)). | `afa_input_missing`, `afa_evidence_missing` |
| `refund_within_captured` (`refund`, its only rule) | The refund is positive, in the order currency, and the captured amount covers it with every earlier refund that has not failed. | `refund_input_missing`, `refund_amount_invalid`, `refund_currency_mismatch`, `order_not_captured`, `refund_exceeds_captured` |

Buyer authority has two sources. On a UCP Complete with `mandate_request`, the server requests
the Intent, Cart and Payment mandates from Grantex and verifies them itself; only a verified,
linked and bound chain becomes the mandate, consent and passport evidence, with the
verification ref `oacp-mandate-v1;jti=<payment jti>;mandate_hash=<hex>;verified_at=<time>`
([Grantex mandates](mandates.md#what-the-arbiter-receives)). Everywhere else (the request's
`buyer_authority` object, the AP2 `checkout_mandate`) the references are caller-asserted: hashed
into the decision and the ledger, never treated as credentials, and a missing value refuses.

Rule `buyer_authority_verified` (D1, closed) passes only with a verification ref that parses and
names the decision's own mandate jti and hash. The server sets it; the request body cannot carry
one (`buyer_authority` refuses unknown members), and any other string refuses
`buyer_authority_ref_invalid`. Without one:

- strict runtimes (production, staging, preview and unrecognised `AGENTICORG_ENV` labels, the
  same detection as `AGENTICORG_OACP_VERIFY_STRICT`) refuse every purchase handoff, POS handoff,
  mandate request and UCP session complete with `failing_rule: buyer_authority_verified`
  (`reason: buyer_authority_caller_asserted`);
- local, dev, test and ci runtimes, or any runtime with
  `AGENTICORG_ARBITER_REQUIRE_VERIFIED_AUTHORITY=false`, pass the rule and record
  `buyer_authority_source: caller_asserted` in the decision (`arbiter_decision`, the stored
  `evidence` and the ledger row).

`AGENTICORG_ARBITER_REQUIRE_VERIFIED_AUTHORITY` set explicitly wins in either direction. The
rule is part of the decision id material, so the same request decided under the two settings
gets two different decision ids. The policy version is `commerce-arbiter-v4`:

| Version | Change |
| --- | --- |
| v2 | `buyer_authority_verified` (D1). |
| v3 | The `refund` action and the `afa_evidence_present` rule. |
| v4 | `buyer_authority_verified` checks the ref against the mandate evidence; `human_not_present_limits` after `merchant_limits` for every action but `refund`; no AFA-exempt path for `human_not_present`; the presence is part of the decision id material. |

A decision's stored evidence also carries `mandate_hashes` and `mandate_jtis` (`intent`, `cart`,
`payment`; null without verified mandates, never from a request body), `presence` and
`authority_verification_ref`; `mandate_ref` and `mandate_hash` are the payment mandate's jti and
the hex SHA-256 of its compact JWT. The ledger's `commerce.arbiter.decision` row seals
`decision.mandates {jtis, hashes, presence}`, and the executor copies the hashes into the
provider order metadata.

Merchant configuration (under `provider_policy`):

```json
{
  "arbiter": {
    "max_order_amount_minor_units": 500000,
    "currency_allowlist": ["INR"],
    "max_quantity_per_order": 5,
    "human_not_present": {"max_order_amount_minor_units": 100000, "max_quantity_per_order": 2},
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
   `decision_action_mismatch`, `decision_payload_mismatch`, `decision_expired`, and, with
   `AGENTICORG_ARBITER_REQUIRE_GRANTEX_GRANT` on, `grantex_grant_missing`
   ([Grantex Countersignature](#grantex-countersignature)).
5. With `escalate_to_human: true` and a `review_agent_id` of the tenant's agents, a refusal also
   queues a review item in `hitl_queue` (`trigger_type = commerce_arbiter_refusal`,
   `outcome_binding: false`) and the response carries `escalation.hitl_ref`. The response does not
   wait for it and the decision does not change. Off (the default), nothing touches
   `hitl_queue`.

UCP session complete uses the same guard with the `session_complete` action. A fresh decision is
sealed in its own transaction; after that commit it is offered to Grantex; then the allow is
consumed in a second transaction (the session must still be `ready_for_complete` at the decided
revision, else `decision_payload_mismatch`). So a strict runtime that requires the Grantex grant
can consume it. A refund of a captured order is decided with the `refund` action and
consumed by the same guard before the Plural executor calls Plural
([Plural executor, Refunds](plural-executor.md#refunds)). A mandate request route arrives with Phase 4 mandates; the arbiter
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
  `commerce.oacp.retention_disposition_decision.record`) in the same transaction. Review
  manifests have one documented exit, retention ([Retention](#retention)).
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


## Grantex Countersignature

AgenticOrg's arbiter is authoritative; Grantex only countersigns
(`core/commerce/arbiter_grantex.py`). After the decision's transaction has committed, the route
posts the decision to Grantex `POST /v1/commerce/oacp/arbiter-decisions` with the AgenticOrg
service token (`GRANTEX_COMMERCE_BASE_URL`, `GRANTEX_COMMERCE_INTERNAL_TOKEN`). No database
transaction is open during the call; the call is async over the DNS-pinning egress transport,
has a 5 s timeout, and is never retried.

- Body: `decision_id`, `tenant_id`, `merchant_id`, `seller_agent_id`, `buyer_agent_id`, `action`,
  `action_hash` (the decision's `payload_hash`), `outcome`, `rules_evaluated`, `failing_rule`
  (null for an allow, one of `rules_evaluated` for a refusal), `evidence` with all six keys
  (`mandate_ref`, `mandate_hash`, `consent_ref`, `consent_status`, `passport_ref`,
  `artifact_ids`; null when absent), `decided_at` (the decision time, so Grantex's 10 minute
  limit holds), and `policy_version`.
- A refusal is recorded by Grantex and gets no token (`grantex_countersignature.status:
  recorded`).
- An allow gets a `decision+jwt` grant in the machine profile. It is verified against the
  Grantex JWKS pinned in configuration (the Phase 1 trust fetcher and its caches): issuer,
  audience `urn:grantex:decision`, `approver_auth: policy_arbiter`, `action_hash ==
  payload_hash`, `decision_request == decision_id`, `sub == buyer_agent_id`, no `acr`,
  unexpired and at most 10 minutes after the decision. Its `jti` is then attached to the decision
  row (the one-time `grantex_grant_jti` transition) and sealed to the ledger
  (`commerce.arbiter.decision.countersigned`).
- A decision without a `buyer_agent_id` cannot be countersigned (Grantex binds the grant to the
  buyer agent): `status: skipped`, `reason: buyer_agent_missing`. UCP session-complete and refund
  decisions name one: the authenticated platform (`ucp_platform:` and 40 hex characters of the
  SHA-256 of its `UCP-Agent` profile URL) recorded on the checkout session at create time, and
  for a refund the buyer agent of the order's session.

Responses of purchase prepare, POS handoff and UCP Complete (in `ai.agenticorg.commerce`) carry
`grantex_countersignature`:
`{status, reason, jti, exp}` with `status` one of `granted`, `recorded`, `skipped`,
`unavailable`, `invalid`, `conflict`.

**Is the countersignature required for an allow to be consumable?** Yes, behind
`AGENTICORG_ARBITER_REQUIRE_GRANTEX_GRANT` (unset: on in strict runtimes, off in
local/dev/test/ci). On, `require_allow_decision` consumes only an allow with a verified grant
attached. An allow whose countersignature is missing (Grantex unreachable, not configured,
answered an error, or returned a grant that does not verify or does not bind the decision) stays
allow-pending: the executing call is refused `grantex_grant_missing` at once. Nothing waits for
Grantex and nothing retries; the caller asks for a new decision. Posting follows
`AGENTICORG_ARBITER_GRANTEX_POST_ENABLED` (unset: the same value as the grant flag), so a
staging deployment can post without yet requiring the grant.

## Ledger Routes

All under `/api/v1/commerce/runtime/ledger`, tenant admin only (`require_tenant_admin`), tenant
taken from the authenticated principal. Write routes declare `route_meta(audit_event=...)` and
record that event in the same transaction as the write; route enforcement checks it.

| Method and path | What it does | Ledger event |
| --- | --- | --- |
| `GET /events?after_seq&limit` | The tenant's ledger rows: sealed rows after `after_seq` in chain order, then unsealed rows. | read |
| `GET /decisions?action&outcome&limit` | Arbiter decisions, newest first. | read |
| `GET /decisions/{decision_id}` | One decision with the operator decisions bound to it. | read |
| `GET /decisions/{decision_id}/operator-decisions` | The operator decisions bound to a decision. | read |
| `POST /decisions/{decision_id}/operator-decisions` | Record an operator decision bound to the arbiter decision. | `commerce.runtime.ledger.operator_decision.create` (+ `commerce.oacp.operator_decision.record`) |
| `POST /manifests` | Store a review manifest (`oacp_audit_review_manifest_records`) over an OACP audit export bundle of this tenant. | `commerce.runtime.ledger.manifest.create` (+ `commerce.oacp.audit_review_manifest.record`) |
| `GET /manifests?merchant_id&retention_class` | Stored review manifests. | read |
| `GET /export?from_seq&to_seq` | The offline-verifiable export bundle ([Export Bundle](#export-bundle)). | read |
| `POST /retention/dispositions` | Record a retention disposition decision (`oacp_retention_disposition_decision_records`). Deletes nothing. | `commerce.runtime.ledger.retention.disposition.create` (+ `commerce.oacp.retention_disposition_decision.record`) |
| `GET /retention/dispositions?merchant_id` | Stored disposition decisions. | read |
| `GET /retention/due` | What the retention task would delete now. | read |

A body that names another tenant (`audit_export_bundle.tenant_id`,
`retention_disposition_dry_run.tenant_id`, `operator_review_packet.tenant_id`) is refused 403;
a decision id of another tenant is 404. A repeated record is 409 `record_append_only`.

### Operator decisions annotate; they never decide

An operator decision is stored in `oacp_operator_decision_records` with `review_packet_id` set to
the arbiter `decision_id` and `maintenance_plan_id` `arbiter_decision:<decision_id>`; the
reviewer is an opaque `operator_ref_` digest of the authenticated principal. It can never turn a
refusal into an allow or execute anything:

- the body is `{decision_kind, reason_codes}` with unknown members refused; there is no outcome,
  consume or execute member;
- only annotation kinds are accepted: `request_more_evidence`, `escalate_to_human_support`,
  `defer_until_freshness_update` (the cache-maintenance `approve_future_*` kinds and
  `block_unsafe_action` are refused);
- the stored record carries `allowed_to_execute: false`, `no_execution: true`, and the response
  `outcome_binding: false`;
- the arbiter decision row is append-only; `require_allow_decision` reads only that row, so a
  refused decision stays refused (`decision_not_allow`) whatever is recorded beside it.

## Export Bundle

`GET /api/v1/commerce/runtime/ledger/export` returns one JSON document (`format:
agenticorg-commerce-ledger-export-v1`, `core/commerce/ledger_export.py`). The chain links
every audit row of the tenant, so the export carries every sealed row of the range, commerce or
not (`commerce_ledger: true` marks commerce ledger rows); at most 5000 rows per export (page with
`from_seq`).

| Member | Content |
| --- | --- |
| `chain` | `version` (link version `1`), `genesis` (64 zeros), `link_hash` (the formula), `from_seq`, `to_seq`, `prev_link` (the link before `from_seq`; genesis when it is 1), `head` (the tenant's head when exported), `row_count`. |
| `rows[]` | Each sealed row as signed and sealed: `id`, the signed columns in canonical form (`tenant_id`, `event_type`, `actor_type`, `actor_id`, `agent_id`, `workflow_run_id`, `resource_type`, `resource_id`, `action`, `outcome`, `details`, `trace_id`, `created_at`), `signature`, `chain_seq`, `chain_prev`, `chain_hash`, `sealed_at`, `commerce_ledger`. |
| `anchors.audit_chain_anchor` | The head the last sealing stored (`audit_chain_anchors`). |
| `anchors.witness_checkpoints[]` | Every head Grantex witnessed (`status: witnessed`, `witness_id`, `countersignature`) or refused as a fork (`status: fork`, `fork_evidence`). |
| `signatures` | `algorithm: HMAC-SHA256`, `key_id` (a keyed digest naming the audit key; it reveals nothing about it), `signed_columns`, `offline_verifiable: false` and why. |
| `grantex` | The pinned Grantex `issuer` and, when witness countersignatures are present and the JWKS could be read, the `jwks` to verify them. |
| `unsealed_commerce_rows` | Commerce ledger rows not yet sealed (not in the export; they appear once the sealing task runs). |

`link_hash = sha256("1|" + prev_link + "|" + canonical_signed_columns + "|" + signature)`,
lowercase hex, where `canonical_signed_columns` is the JSON of the signed columns with sorted
keys and no spaces (`core/governance/audit_chain_link.py`).

**Signatures are HMAC.** The platform signs audit rows with HMAC-SHA256 under its secret
(`core/tool_gateway/audit_logger.py`), not with an asymmetric key, and the secret is never
exported. An offline verifier therefore recomputes and checks every chain link, the anchors and
the witness countersignatures, but cannot check row signatures; a holder of the secret can
(`--audit-secret-env`). The witness countersignatures are what make the chain verifiable by a
third party: Grantex signed each witnessed head with its published key.

## Offline Verifier

```bash
python scripts/verify_ledger.py export.json
python scripts/verify_ledger.py export.json --expected-head 1042:<64 hex>   # a head kept outside the platform
AUDIT_SECRET=... python scripts/verify_ledger.py export.json --audit-secret-env AUDIT_SECRET
```

No database, no network: the script loads only `core/governance/audit_chain_link.py` and
`core/commerce/oacp_jws.py` by path (no settings, no drivers). It walks `rows` in file order and
reports the first break as JSON:

| Break | Meaning |
| --- | --- |
| `sequence_gap` | A row is missing or rows are out of order. |
| `previous_link` | A row's `chain_prev` is not the previous link (inserted or swapped row). |
| `link_hash` | A row's content or signature changed after sealing. |
| `truncated` | An anchor (stored anchor, Grantex-witnessed head, `--expected-head`) is beyond the exported head: rows were cut from the end. |
| `anchor_mismatch` | A row at an anchor's sequence has another link hash. |
| `witness_countersignature` | A witness countersignature does not verify against the bundled Grantex JWKS or does not name the stored head. |
| `signature` | With `--audit-secret-env`, a row's HMAC does not verify. |

Exit code 0: verified; 1: a break (first break reported); 2: the file is not a readable export.
A partial range (`to_seq` below the head) is verified as far as it goes and noted. Someone who
rewrites both the rows and every anchor inside the file can only be caught by a head kept
outside it: a Grantex witness countersignature (verifiable with Grantex's published JWKS) or
`--expected-head` from a log kept outside the platform.

## Retention

Review manifests (`oacp_audit_review_manifest_records`) are append-only with one documented
exit. A manifest is deleted when a stored disposition decision makes it due:

- the disposition is `approve_future_retention_review` and its `retain_until` has passed;
- the manifest is in the disposition's tenant and merchant (and seller and buyer agent when the
  disposition names them), has its retention class, is not `legal_hold_candidate`, and its own
  `retain_until` has passed and is not later than the disposition's;
- no `approve_future_legal_hold_review` or `block_unsafe_disposition` decision exists for that
  tenant, merchant and retention class, and no `reject_disposition` or `defer_until_recheck`
  decision exists for the same source summary.

The database enforces the transition, not only the task:

1. The task calls `oacp_delete_due_manifest(disposition_id, manifest_id)`, a SECURITY DEFINER
   function owned by the migration role (`search_path` pinned), so the application role needs no
   DELETE grant on the table. Because the owner may bypass row-level security, the function binds
   the DELETE to the session's tenant (`agenticorg.tenant_id`) itself; a session bound to another
   tenant, or to none, deletes nothing. EXECUTE is revoked from PUBLIC and granted only to the
   application role `agenticorg_app` (where it exists), so no other database role can call it.
2. The function names the disposition in a transaction-local setting
   (`agenticorg.oacp_retention_disposition`) and deletes the row.
3. The table's trigger (`oacp_manifest_retention_guard`) re-checks the tenant and every condition
   above for that row and refuses anything else, as before. A plain DELETE, a DELETE naming a
   disposition that does not make the row due, and any UPDATE are refused.
4. Each deletion is recorded on the ledger in the same transaction
   (`commerce.oacp.retention.manifest_deleted`, with the manifest id, the disposition id, the
   retention class and `retain_until`), and each applied disposition once
   (`commerce.oacp.retention.disposition_applied`). Disposition decisions and ledger rows are
   never deleted.

The Celery task `core.tasks.commerce_ledger_tasks.apply_oacp_retention` runs daily at 04:30 on the
shared Celery app (beat entry `apply-oacp-retention`, queue `maintenance`) for every tenant, each
disposition in its own transaction, behind `AGENTICORG_OACP_RETENTION_APPLY_ENABLED` (off by
default). Nobody approves a run: the stored decisions are the authority, and nothing waits.

## Witness Checkpoints

After each sealing (`core.tasks.audit_chain_tasks.seal_audit_chains`), with
`AGENTICORG_AUDIT_WITNESS_ENABLED` on (off by default), the tenant's new head is posted to
Grantex `POST /v1/audit/witness-checkpoints` as `{source: "agenticorg", tenant_ref: <tenant id>,
chain: "audit_log", head_seq, head_hash, anchored_at}` (`core/governance/audit_witness.py`).
The answer's countersignature (`typ: audit-witness+jwt`) is verified against the pinned Grantex
JWKS and its claims held against the posted head, then stored in
`audit_chain_witness_checkpoints` (tenant-scoped, forced RLS, append-only) beside the anchor.

A `409 WITNESS_FORK` means Grantex already witnessed another hash at that sequence: the chain
disagrees with a head it published earlier. It is logged as an error
(`audit_chain_witness_irregularity`) and the fork evidence Grantex returned (the witnessed head
with its countersignature and the presented head) is stored in a `fork` row. A Grantex outage is
logged and the sealing stands; the next sealing posts its head.

## Flags

| Flag | Default | Effect |
| --- | --- | --- |
| `AGENTICORG_ARBITER_REQUIRE_VERIFIED_AUTHORITY` | unset: on in strict runtimes | Rule `buyer_authority_verified` refuses caller-asserted buyer authority. |
| `AGENTICORG_ARBITER_REQUIRE_GRANTEX_GRANT` | unset: on in strict runtimes | An allow is consumable only with a verified Grantex grant (`grantex_grant_missing`). |
| `AGENTICORG_ARBITER_GRANTEX_POST_ENABLED` | unset: follows the grant flag | Post every decision to Grantex. |
| `AGENTICORG_GRANTEX_MANDATES_ENABLED` | off | UCP Complete honours `mandate_request`: Grantex mandates requested and verified before the decision. |
| `AGENTICORG_AUDIT_WITNESS_ENABLED` | off | Post each sealed head to Grantex for witnessing. |
| `AGENTICORG_OACP_RETENTION_APPLY_ENABLED` | off | The daily retention task applies due dispositions. |
| `AGENTICORG_AUDIT_CHAIN_DEFAULT_ON` | off | Seal every tenant with a commerce merchant config. |
