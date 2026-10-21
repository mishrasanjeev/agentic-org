# OACP Demo Merchant: Nilgiri Tea House (Synthetic)

One clearly labelled demo merchant, the invented tea retailer "Nilgiri Tea House", for showing
OACP end to end in a running environment without a real store.
`scripts/seed_oacp_demo_merchant.py` creates it, keeps it converged and tears it down. Everything
it creates is invented.

| What | Value |
| --- | --- |
| Tenant | "Nilgiri Tea House (OACP demo)", slug `nilgiri-tea-house-demo`, id `13bd6961-b1e4-56ca-b242-91fbdfe1b1b0` (fixed, so Grantex can be allowlisted before the first run), marked `settings.oacp_demo_merchant.synthetic = true` |
| Admin user | `demo-admin@demo.agenticorg.example` (reserved `.example` domain); a password only from `AGENTICORG_OACP_DEMO_ADMIN_PASSWORD`, never printed |
| Merchant | `nilgiri_tea_house`, seller agent `seller_agent_nilgiri_tea`, display name "Nilgiri Tea House (synthetic demo)" |
| Catalog | 10 teas and tea accessories, 11 variants, prices in INR, stock snapshots (one tea out of stock), every description starting "Synthetic demo product"; connector source `synthetic_demo`, never Shopify |
| Store | "Nilgiri Tea House, Ooty (synthetic demo)", invented street address, hours in `Asia/Kolkata` for every day |
| Policies | returns summary in the brand profile; privacy, terms and returns links and product images under `https://demo.agenticorg.example/` (nothing is hosted there) |
| Public host | `nilgiritea.agenticorg.ai`, UCP vendor namespace `ai.agenticorg.nilgiritea`, attested by DNS TXT |
| Payment | no payment executor on the merchant config, so no checkout can charge whatever `PLURAL_PINE_LIVE_EXECUTION_ENABLED` says |

The synthetic source is accepted only while `AGENTICORG_OACP_DEMO_MERCHANT_ENABLED=true`. A
Grantex artifact naming it is cached only for a tenant carrying the demo marker; any other tenant
gets `422 synthetic_demo_artifact_refused`. With the flag off every path accepts Shopify read-only
sources only, as before. Design: `core/commerce/oacp_demo_merchant.py`.

## Prerequisites

1. **AgenticOrg** release containing the demo source deployed (the owner-approved deploy runbook).
2. **Grantex**:
   - the AgenticOrg authority service token (already configured);
   - the demo tenant id in `COMMERCE_C6Z_AUTHORITY_SERVICE_TENANTS`; without it the seed stops with
     exit code 3 and prints the id;
   - a Grantex release that issues artifacts for the `synthetic_demo` connector source of the demo
     tenant. A Grantex that accepts Shopify sources only refuses with
     `unsupported_or_enabling_authority_request`; the seed then stops with exit code 4 and nothing
     is cached. The seed never presents the synthetic catalog as Shopify;
   - for the public UCP profile: `OACP_PUBLIC_DISCOVERY_ISSUANCE_ENABLED=true`.
3. **Secret Manager** (optional, for signing in to the console as the demo admin):

   ```
   openssl rand -base64 32 | tr -d '\n' | gcloud secrets create oacp-demo-admin-password \
     --project=perfect-period-305406 --replication-policy=automatic --data-file=-
   ```

   Read it only when needed: `gcloud secrets versions access latest --secret=oacp-demo-admin-password`.

## Flags

Set on **both** `agenticorg-api` and `agenticorg-worker` (and on the seed job):

| Flag | Value | Why |
| --- | --- | --- |
| `AGENTICORG_OACP_DEMO_MERCHANT_ENABLED` | `true` | Accept the synthetic demo source |
| `AGENTICORG_UCP_PROFILE_ENABLED` | `true` | UCP signing key, profile, catalog and location reads |
| `AGENTICORG_UCP_CHECKOUT_ENABLED` | `true` | UCP checkout, cart and order routes |
| `AGENTICORG_ACP_FEED_ENABLED` | `true` | ACP product feed export |
| `AGENTICORG_ACP_CHECKOUT_ENABLED` | `true` | ACP checkout sessions |
| `AGENTICORG_OACP_ON_DEMAND_REFRESH_ENABLED` | `true` | Reads renew lapsed short-lived artifacts on demand (needs Redis, already used by the API) |
| `AGENTICORG_OACP_REFRESH_WAIT_MS` | `3000` (default) | Longest a read waits for a refresh |
| `AGENTICORG_OACP_REFRESH_SWEEP_ENABLED` | unset (off) | Optional 5-minute sweep of recently read scopes; not needed for the demo |
| `AGENTICORG_UCP_VERSIONS` | `2026-04-08` (default) | Release served at `/.well-known/ucp` |
| `AGENTICORG_OACP_VERIFY_STRICT` | unset (on in production) | Artifacts are verified before caching |
| `AGENTICORG_ARBITER_REQUIRE_VERIFIED_AUTHORITY` | unset (on) | Caller-asserted buyer authority is refused |
| `AGENTICORG_ARBITER_REQUIRE_GRANTEX_GRANT` | unset (on) | An allow needs a Grantex countersignature |
| `AGENTICORG_ROUTE_ENFORCEMENT_MODE` | `enforce` (default) | Every commerce write records its ledger event |
| `AGENTICORG_HTTPSIG_VERIFY_MODE` | `log` (default) | Agent signatures recorded, never refused |
| `AGENTICORG_AUDIT_CHAIN_DEFAULT_ON` | `true` (optional) | Seals the ledger of every tenant with a commerce merchant config, so the demo's export verifies offline |
| `PLURAL_PINE_LIVE_EXECUTION_ENABLED` | unset or `false` | No money moves; the seed job refuses to run with it on |

Flags already on stay on. Settings are read at start-up, so a change needs a new revision. The API
has no host allowlist: a request for `nilgiritea.agenticorg.ai` reaches the same middleware as any
other host, `/.well-known/ucp` and the `/ucp` catalog and location reads are public, and the UCP
and ACP checkout routes take the merchant-issued API key.

## Seed Job (one-time setup)

The seed needs the same runtime environment as the API (database URL, tenant KEK, Grantex base
URL and service token). Create the job from the deployed API image and copy the API's environment:

```
PROJECT=perfect-period-305406; REGION=asia-southeast1
SHA=<the deployed commit>   # from https://app.agenticorg.ai/api/v1/health
IMAGE=asia-south1-docker.pkg.dev/$PROJECT/agenticorg/agenticorg:$SHA
# List the API's variables, secret references, service account and network (names only):
gcloud run services describe agenticorg-api --project=$PROJECT --region=$REGION \
  --format='yaml(spec.template.spec.serviceAccountName,spec.template.spec.containers[0].env,spec.template.metadata.annotations)'
gcloud run jobs create agenticorg-oacp-demo-seed --project=$PROJECT --region=$REGION \
  --image=$IMAGE --command=python --args=scripts/seed_oacp_demo_merchant.py \
  --service-account=<the API service account> \
  --set-env-vars=<the API's plain variables>,AGENTICORG_OACP_DEMO_MERCHANT_ENABLED=true,AGENTICORG_UCP_PROFILE_ENABLED=true \
  --set-secrets=<the API's secret references>,AGENTICORG_OACP_DEMO_ADMIN_PASSWORD=oacp-demo-admin-password:latest \
  --vpc-connector=<as on the API> --max-retries=0 --task-timeout=90m
```

Leave out the `AGENTICORG_OACP_DEMO_ADMIN_PASSWORD` reference to create the admin without a
password (no console sign-in). After a new deploy, point the job at the new image with
`gcloud run jobs update agenticorg-oacp-demo-seed --image=...`.

## Run Order

1. Deploy, set the flags above on the API and worker, add the tenant id to the Grantex allowlist.
2. **Dry run** (the job's default arguments; reads only):

   ```
   gcloud run jobs execute agenticorg-oacp-demo-seed --project=$PROJECT --region=$REGION --wait
   ```

   Read the one-line JSON summary in the execution's logs (`summary.actions` says what an apply
   would do):

   ```
   gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="agenticorg-oacp-demo-seed"' \
     --project=$PROJECT --freshness=1h --format='value(textPayload)' | grep '"summary"'
   ```

3. **Apply**:

   ```
   gcloud run jobs execute agenticorg-oacp-demo-seed --project=$PROJECT --region=$REGION --wait \
     --args=scripts/seed_oacp_demo_merchant.py,--apply
   ```

   The summary names the tenant, merchant and seller agent, the merchant signing key id, the
   artifacts issued and verified, what is usable now and what is enabled. Rerunning converges:
   nothing is created twice, and artifacts are requested only when a family has no usable one.

4. **Public host `nilgiritea.agenticorg.ai`** (UCP profile, catalog and locations, UCP REST and ACP
   checkout). These routes find the merchant by the `Host` header, so the host must reach
   `agenticorg-api` directly with the header preserved, not through `agenticorg-ui` (its nginx
   rewrites `Host` and has no `/.well-known/ucp` or `/ucp` proxy).

   a. Map the host straight to the API service:

      ```
      gcloud beta run domain-mappings create --service=agenticorg-api \
        --domain=nilgiritea.agenticorg.ai --project=$PROJECT --region=$REGION
      ```

   b. At Namecheap (Domain List, `agenticorg.ai`, Advanced DNS) add a **CNAME Record**: host
      `nilgiritea`, value `ghs.googlehosted.com.`. Cloud Run issues the managed certificate once it
      resolves; `gcloud beta run domain-mappings describe --domain=nilgiritea.agenticorg.ai
      --project=$PROJECT --region=$REGION` then shows `CertificateProvisioned` true.

   c. Request the attestation challenge. The seed prints the exact record and changes nothing else:

      ```
      gcloud run jobs execute agenticorg-oacp-demo-seed --project=$PROJECT --region=$REGION --wait \
        --args=scripts/seed_oacp_demo_merchant.py,--apply,--serving-host=nilgiritea.agenticorg.ai
      ```

      The summary's `dns_record_to_create` holds `name` (`_agenticorg-ucp.nilgiritea.agenticorg.ai`),
      `type` (`TXT`), `value` (`agenticorg-ucp-verify=...`) and `host_in_zone`
      (`_agenticorg-ucp.nilgiritea`). Rerunning the same command reprints the same challenge. The
      seed never creates or invents DNS.

   d. At Namecheap add a **TXT Record**: host `_agenticorg-ucp.nilgiritea`, value the printed
      `agenticorg-ucp-verify=...` string. Check it: `dig +short TXT _agenticorg-ucp.nilgiritea.agenticorg.ai`.

   e. After propagation, complete the attestation and publish (a separate run):

      ```
      gcloud run jobs execute agenticorg-oacp-demo-seed --project=$PROJECT --region=$REGION --wait \
        --args=scripts/seed_oacp_demo_merchant.py,--apply,--verify-host
      ```

      It looks the TXT record up, records the attestation, publishes the profile under
      `ai.agenticorg.nilgiritea` and requests new artifacts carrying the public-discovery opt-in. If
      the record is not visible yet, the summary says `not verified (dns_txt_record_not_found)`:
      rerun later. The profile is served once Grantex issues an enabled `public_discovery_state`
      artifact.

5. **Freshness while it is used.** Inventory artifacts live 60 s, prices 5 min and public
   discovery 15 min. With `AGENTICORG_OACP_ON_DEMAND_REFRESH_ENABLED=true` the first read that
   finds one lapsed renews the demo scope (one Grantex issuance at most every 30 s, the read waits
   at most 3 s; the synthetic catalog evidence is re-read first when it is older than 12 minutes),
   so nothing has to run before a showcase and nothing is issued while nobody uses the demo. A
   one-off `--refresh-artifacts` run is still available. See the
   [artifact cache guide](../oacp/artifact-cache-guide.md#on-demand-freshness).

6. **Sign in** (with the password secret): `https://app.agenticorg.ai` as
   `demo-admin@demo.agenticorg.example`, then create the API keys a demo platform needs (scopes
   `ucp:read`, `ucp:write`, `acp:read`, `acp:write`) in the console.

## What Works Where

| Surface | Before the host is published | On `https://nilgiritea.agenticorg.ai` once published |
| --- | --- | --- |
| Buyer Q&A, products, protocol adapter payloads, purchase preparation (arbiter), ledger export | yes (tenant API key or admin session, on `app.agenticorg.ai`) | yes |
| ACP product feed (`/api/v1/commerce/runtime/acp/feed`) | yes, while the artifacts are fresh | yes |
| MCP `seller.*` tools (Q&A bridges) | yes (`AGENTICORG_API_KEY`) | yes |
| `/.well-known/ucp`, `/ucp/catalog/*`, `/ucp/locations/*` | 404 | yes |
| UCP checkout and cart, ACP checkout sessions, MCP UCP checkout tools | 404 (merchant found by `Host`) | yes; Complete never charges (no executor) |

## Verification

With a demo-tenant API key or admin session token in `$TOKEN`:

```
API=https://app.agenticorg.ai/api/v1/commerce/runtime
M='merchant_id=nilgiri_tea_house&seller_agent_id=seller_agent_nilgiri_tea'
curl -s -H "Authorization: Bearer $TOKEN" "$API/merchant-configs/nilgiri_tea_house?seller_agent_id=seller_agent_nilgiri_tea"
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -X POST "$API/buyer-sessions/ask" \
  -d '{"merchant_id":"nilgiri_tea_house","seller_agent_id":"seller_agent_nilgiri_tea","question":"Do you have masala chai?"}'
curl -s -H "Authorization: Bearer $TOKEN" "$API/acp/feed?$M&format=json"   # 409 once inventory lapses
curl -s -H "Authorization: Bearer $TOKEN" "$API/protocol-adapters?$M"
curl -s -H "Authorization: Bearer $TOKEN" "$API/ledger/export"
```

The answer's `source_label` reads "Source: synthetic local demo catalog; not externally verified".
On the published host (`$KEY` is a demo-tenant API key with the `ucp:*` and `acp:*` scopes; a UCP
checkout fetches the platform profile named in `UCP-Agent`, so use your demo platform's real
profile URL):

```
H=https://nilgiritea.agenticorg.ai
curl -s $H/.well-known/ucp
curl -s -X POST $H/ucp/catalog/search -H 'Content-Type: application/json' \
  -d '{"query":"masala","context":{"currency":"INR"}}'
curl -s -X POST $H/ucp/locations/search -H 'Content-Type: application/json' -d '{}'
curl -s -X POST $H/ucp/checkout-sessions -H "X-API-Key: $KEY" -H "Idempotency-Key: $(uuidgen)" \
  -H 'UCP-Agent: profile="https://<your demo platform>/.well-known/ucp"' -H 'Content-Type: application/json' \
  -d '{"line_items":[{"item":{"id":"NTH-MASALA-250"},"quantity":1}]}'
```

## Teardown

```
gcloud run jobs execute agenticorg-oacp-demo-seed --project=$PROJECT --region=$REGION --wait \
  --args=scripts/seed_oacp_demo_merchant.py,--teardown            # lists what would be deleted
gcloud run jobs execute agenticorg-oacp-demo-seed --project=$PROJECT --region=$REGION --wait \
  --args=scripts/seed_oacp_demo_merchant.py,--teardown,--apply
```

It deletes the demo tenant's merchant config, onboarding packet, catalog evidence, artifact cache,
POS packets, checkout sessions, carts, orders and their webhooks; removes the serving-host index;
deactivates the admin user; and records `commerce.runtime.demo_merchant.teardown`. The append-only
ledger (audit log, arbiter decisions, OACP decision records, payment records) is evidence and is
kept. Then remove the tenant id from the Grantex allowlist, delete the Namecheap TXT and CNAME
records and the domain mapping (`gcloud beta run domain-mappings delete
--domain=nilgiritea.agenticorg.ai --project=$PROJECT --region=$REGION`), and unset
`AGENTICORG_OACP_DEMO_MERCHANT_ENABLED` if no demo is planned. Issued artifacts expire within 24
hours. A later `--apply` re-creates the demo on the same tenant.

## Exit Codes

| Code | Meaning |
| --- | --- |
| 0 | Done (or the dry-run plan was printed) |
| 1 | Failed (Grantex unreachable, an artifact refused at caching, ...): the summary says which |
| 2 | Refused before any write: flag off, missing settings, Plural live on in the job, a short admin password, `--verify-host` without a challenge, or a tenant id, slug or admin email the seed did not create |
| 3 | Grantex does not allow the demo tenant: add the printed id to `COMMERCE_C6Z_AUTHORITY_SERVICE_TENANTS` |
| 4 | Grantex does not accept the synthetic demo source yet |

## Cost and Safety

- No cost beyond normal: one short job execution, a few hundred rows, one Grantex request per
  refresh, and refreshes only while someone reads (at most one issuance of 11 artifacts every 30 s
  per merchant scope, none while idle; the scope's cache keeps at most 6 records per type), one
  domain mapping.
- Synthetic only: invented names, an invented address, `.example` addresses and URLs, no real
  people, no secrets in the output (the admin password never leaves Secret Manager and the job's
  environment).
- Plural live execution stays off; the demo merchant names no executor, so even with it on no demo
  checkout can complete a payment.
