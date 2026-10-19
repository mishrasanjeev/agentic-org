# Runtime Operations Runbook

Canonical end-to-end flow: [OACP end-user flow](end-user-flow.md). Launch closure source of truth: [OACP Runtime Launch Closure PRD](runtime-launch-closure-prd.md).

## Deploy Order For Strict OACP Verification

Strict artifact verification (`AGENTICORG_OACP_VERIFY_STRICT`, on by default in production and staging) refuses to cache any artifact Grantex cannot prove, and refuses to use cache rows that were never verified. Before the AgenticOrg release that carries it reaches production, these must be live on the Grantex side for the same environment:

1. The Grantex OACP signing release (ES256 detached-JWS artifacts, persisted and replayable, revocable).
2. A first OACP issuer key (`POST /v1/commerce/oacp/issuer-keys/rotate`), so `/.well-known/jwks.json` lists an `oacp-` key.
3. `OACP_STATUS_LIST_PUBLIC_ENABLED=true`, so `/status/oacp/<list>` is served; while it is off every artifact is refused `revocation_status_unavailable`.

Then deploy AgenticOrg and run `POST /api/v1/commerce/runtime/artifacts/refresh` for each live merchant and seller agent, so legacy rows are replaced by verified ones. If AgenticOrg must ship first, set `AGENTICORG_OACP_VERIFY_STRICT=false` for that deployment as a recorded operator decision and unset it once the three items are live and the refresh has run.

## Commerce Ledger, Countersignature And Witness

Deploy order: Grantex with `POST /v1/commerce/oacp/arbiter-decisions` and `POST
/v1/audit/witness-checkpoints` (migration `134_commerce_ledger.sql`) and the tenant in
`COMMERCE_C6Z_AUTHORITY_SERVICE_TENANTS`, then AgenticOrg. In strict runtimes the AgenticOrg
release refuses caller-asserted buyer authority (`AGENTICORG_ARBITER_REQUIRE_VERIFIED_AUTHORITY`)
and consumes only countersigned allows (`AGENTICORG_ARBITER_REQUIRE_GRANTEX_GRANT`); both are on
by default there. Witnessing (`AGENTICORG_AUDIT_WITNESS_ENABLED`) and retention
(`AGENTICORG_OACP_RETENTION_APPLY_ENABLED`) are off until switched on.

Checks after deploy:

1. A POS handoff without `decision_id` returns `grantex_countersignature.status: granted` (or
   `recorded` for a refusal); the repeat with `decision_id` creates the packet.
2. `GET /api/v1/commerce/runtime/ledger/decisions` lists the decisions with `grantex_grant_jti`.
3. `GET /api/v1/commerce/runtime/ledger/export > export.json` and `python
   scripts/verify_ledger.py export.json` exits 0.
4. With witnessing on, after the next sealing (every 5 minutes) `audit_chain_witness_checkpoints`
   has a `witnessed` row for the tenant and the export carries it.

Monitor `commerce_arbiter_countersign_unavailable`, `commerce_arbiter_grant_irregularity`,
`audit_chain_witness_unavailable`, `audit_chain_witness_irregularity`, `oacp_retention_applied`
and `oacp_retention_disposition_failed`.

## Smoke Test

```mermaid
flowchart TD
  config[Save merchant commerce config] --> packet[Create or sync onboarding packet]
  packet --> creds[Store Shopify credentials]
  creds --> sync[Run read-only sync]
  sync --> authority[Request Grantex authority]
  authority --> cache[Cache artifacts]
  cache --> ask[Ask buyer question]
  ask --> bridge[Check bridge matrix]
  bridge --> provider[Verify Plural/Pine capability]
  provider --> purchase[Prepare purchase handoff]
  purchase --> pos[Create Offline POS handoff packet]
  pos --> confirm[Run POS simulator or verified callback]
  confirm --> reconcile[Check buyer-safe reconciliation]
```

## Redacted Evidence Run

Local non-live closure evidence:

```bash
OACP_LAUNCH_WRITE_EVIDENCE=true python scripts/oacp_runtime_launch_check.py
```

Outputs:

- `docs/reports/oacp-runtime-launch-evidence.local.json`
- `docs/reports/oacp-runtime-launch-evidence.local.md`

Set `OACP_LAUNCH_EXTERNAL_CHECKS=true` only when Shopify, Grantex, and Plural/Pine credentials are configured for sandbox/live smoke. Missing credentials must be recorded as exact blockers, not converted into success.

## Production/Sandbox Smoke After Deploy Approval

1. Health and auth login.
2. Merchant config saved and read back without secret values.
3. Shopify connector status and read-only sync.
4. Shopify webhook signed test marks source stale/refresh-required.
5. Grantex authority request returns 11 artifacts or an exact refusal.
6. `POST /artifacts/refresh` for the smoke merchant returns `cached`; artifact cache count, `verification.mode` `strict` with `verification.refused` empty, each record's `verifier_result_ref` starting `oacp-artifact-jws-v1;`, and a buyer answer with source/freshness labels.
7. Public catalog page, product page, catalog JSON, Schema.org JSON-LD, sitemap, and llms.txt when merchant publishing is enabled.
8. Protocol adapter endpoints and MCP seller fact read.
9. A2A agent card and OpenAPI bridge schema.
10. WhatsApp and Telegram signed webhook smokes when credentials are present.
11. Pine/Plural capability verifier and purchase prepare handoff/blocker.
12. Offline POS fake packet 404, unsigned callback rejected/downgraded, signed safe-mode callback accepted.

## Monitor

- Merchant config readiness by tenant, merchant, and seller agent.
- Shopify sync failures.
- Grantex authority status and refusal codes.
- Cache freshness distribution.
- `oacp_artifact_verification_irregularity` warnings and `artifact_verification_refused` 422s by refusal code (Grantex JWKS or status list unreachable shows as `issuer_key_untrusted` / `revocation_status_unavailable`).
- Buyer answer source labels.
- Bridge route errors.
- Provider capability status.
- Purchase-preparation blockers.
- Offline POS handoff packet creation rate.
- POS confirmation status mix.
- Reconciliation outcomes that require inventory or artifact refresh.

## Rollback

1. Disable buyer surfaces for affected merchant.
2. Disable merchant public publishing in merchant commerce config, or set `OACP_PUBLIC_CATALOG_PLATFORM_DISABLED=true` for a platform-wide stop.
3. Mark affected cache records stale.
   If strict OACP verification refuses every Grantex issuance because Grantex is not yet issuing ES256-signed artifacts, the recorded operator decision is `AGENTICORG_OACP_VERIFY_STRICT=false` for that deployment until Grantex deploys; artifacts are then cached without a `verifier_result_ref` and every failure is logged as an irregularity. Set it back (unset) once `verification.refused` is empty.
4. Stop Shopify sync jobs.
5. Ask Grantex to remove tenant allowlist or rotate token if needed.
6. Disable Offline POS handoff creation for affected merchant if POS evidence is stale or callback verification fails.
7. Commerce arbiter and ledger (recorded operator decisions, set per deployment and unset once
   the cause is fixed): Grantex unreachable or misconfigured, so every executing call is refused
   `grantex_grant_missing`: `AGENTICORG_ARBITER_REQUIRE_GRANTEX_GRANT=false` (posting continues
   while `AGENTICORG_ARBITER_GRANTEX_POST_ENABLED` is unset or true). Caller-asserted buyer
   authority must be accepted before Phase 4: `AGENTICORG_ARBITER_REQUIRE_VERIFIED_AUTHORITY=false`.
   Witness posting misbehaving: `AGENTICORG_AUDIT_WITNESS_ENABLED=false` (sealing is unaffected).
   Retention deleting unexpectedly: `AGENTICORG_OACP_RETENTION_APPLY_ENABLED=false`; deletions
   already made are on the ledger (`commerce.oacp.retention.manifest_deleted`).
8. Re-run smoke before re-enabling.

## Merchant Config Smoke

1. Open `/dashboard/commerce-runtime` as an `admin` or `merchant` role user.
2. Save config for the merchant with source connector `Shopify`, buyer channels, provider-owned payment config, and optional Offline POS store.
3. Load config and verify the same tenant, merchant, seller agent, source connector, channel, provider, and POS refs return without secret values.
4. Check readiness. Shopify should be `runtime_ready`. WooCommerce, ERP, PIM, OMS, WMS, and custom API should stay `configured_pending_adapter` until real adapters exist.
5. Enable public publishing only for merchants that have source evidence and approved public surfaces.

## Offline POS Smoke

1. Prepare a purchase from fresh OACP cache records.
2. Create an Offline POS handoff packet.
3. Confirm simulator `accepted`; verify buyer wording says staff/payment confirmation is still required.
4. Submit fake or missing packet ids to the POS routes; they must return safe `404` or auth errors, not `500`.
5. Submit `payment_confirmed` without `X-AgenticOrg-POS-Signature` when `OFFLINE_POS_WEBHOOK_SECRET` is configured; it must return a safe authorization failure.
6. Submit `payment_confirmed` with `X-AgenticOrg-POS-Timestamp` and `X-AgenticOrg-POS-Signature: sha256=<hmac>` over `<timestamp>.<raw body>` plus a non-sensitive evidence ref; it may reconcile as confirmed, but must still report `allowed_to_execute=false`.
