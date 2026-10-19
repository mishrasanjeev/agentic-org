# Runtime Operations Runbook

Canonical end-to-end flow: [OACP end-user flow](end-user-flow.md). Launch closure source of truth: [OACP Runtime Launch Closure PRD](runtime-launch-closure-prd.md).

## Deploy Order For Strict OACP Verification

Strict artifact verification (`AGENTICORG_OACP_VERIFY_STRICT`, on by default in production and staging) refuses to cache any artifact Grantex cannot prove, and refuses to use cache rows that were never verified. Before the AgenticOrg release that carries it reaches production, these must be live on the Grantex side for the same environment:

1. The Grantex OACP signing release (ES256 detached-JWS artifacts, persisted and replayable, revocable).
2. A first OACP issuer key (`POST /v1/commerce/oacp/issuer-keys/rotate`), so `/.well-known/jwks.json` lists an `oacp-` key.
3. `OACP_STATUS_LIST_PUBLIC_ENABLED=true`, so `/status/oacp/<list>` is served; while it is off every artifact is refused `revocation_status_unavailable`.

Then deploy AgenticOrg and run `POST /api/v1/commerce/runtime/artifacts/refresh` for each live merchant and seller agent, so legacy rows are replaced by verified ones. If AgenticOrg must ship first, set `AGENTICORG_OACP_VERIFY_STRICT=false` for that deployment as a recorded operator decision and unset it once the three items are live and the refresh has run.

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

The local artifacts are signed with an ES256 key generated for the run (kid `oacp-launchcheck-<random>`, never written to disk) and each one goes through `OacpGrantexArtifactVerifier`, the verifier the cache intake uses: detached JWS, JWKS key, scope, TTL and Token Status List. The JWKS and a status list with every entry VALID are served in process from the synthetic `https://launch-check.issuer.example` origin. `artifact_verifier_summary` reports what the verifier returned (`checked`, `valid`, `invalid`, `status_list_checked`, `refusal_codes`) and is labelled `verification_mode: local_throwaway_es256_key`, `grantex_issuer: false`: it proves the verifier path, not Grantex issuance or a Grantex status list. A non-zero `invalid` count makes the script exit non-zero after printing the summary.

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
7. Re-run smoke before re-enabling.

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
