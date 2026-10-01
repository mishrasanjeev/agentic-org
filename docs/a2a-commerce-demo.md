# Independent buyer to seller A2A commerce demo

This is a runnable **local synthetic demo**, not a public merchant listing or
a completed purchase. It proves that a buyer agent running outside AgenticOrg
can discover an AgenticOrg seller, authenticate with merchant-scoped access,
read a catalog backed by scoped cache evidence, ask a product question, and
receive a safe refusal for a purchase request. No Shopify, Grantex, Plural,
payment, order, or merchant-private API is contacted by this demo.

## What happens

```text
Synthetic Local Store                 Independent Python buyer agent
  3 catalog fixtures                         separate process
         |                                        |
         |-- local connector evidence             |
         |-- local catalog cache record           |
         |-- scoped buyer credential ------------>|
         |                                        |-- GET public Agent Card
         |                                        |-- GET scoped extended Agent Card
         |<------------- A2A message:send --------|  Show me your catalogue
         |------------- sourced snapshots ------->|
         |<------------- A2A message:send --------|  Tell me about Canvas Tote
         |------------- price/stock snapshot ---->|
         |<------------- A2A message:send --------|  Buy two Canvas Tote now
         |------------- purchase refusal -------->|
         |-- revoke buyer credential              |
         |<------------- card request ------------|  HTTP 401
         |-- delete all synthetic rows            |
```

| Synthetic product | Price snapshot | Quantity snapshot |
| --- | ---: | ---: |
| Canvas Tote | INR 1299 | 7 |
| Ceramic Mug | INR 499 | 12 |
| Pocket Notebook | INR 249 | 20 |

These are local fixtures, not real inventory or an
offer. Answers disclose their synthetic source and freshness. A buyer cannot
promote a snapshot into a final price, hold, checkout, or payment.

## Run locally

Use a development API with the repository's migrations applied and local
PostgreSQL and Redis available. See [local setup](quickstart-local.md).
Set `AGENTICORG_ENV=development`, `AGENTICORG_DB_URL`, and
`AGENTICORG_REDIS_URL` for that local stack, then run from the repository root:

```powershell
python -m examples.a2a_commerce_demo.run_demo --base-url http://127.0.0.1:8000 --confirm-local-synthetic
```

The runner refuses non-loopback APIs, non-local databases, and production
environments. It creates randomly scoped synthetic seller/evidence/cache
rows and a one-hour buyer credential, calls the separate
[`buyer_agent.py`](../examples/a2a_commerce_demo/buyer_agent.py) process over
HTTP, revokes access, verifies HTTP 401, and removes those rows. It does not
print the credential. Do not point it at shared or production data.

You should see three `Seller (answered)` / `Seller (refused)` results, followed
by the revocation check. The refusal is expected: current external A2A access
is for non-binding seller questions only. There is **no paid receipt, order,
reservation, or provider mandate** in this demonstration.

## Replace fixtures with a merchant pilot

1. Have the merchant onboard its Seller Commerce Agent and authorize a
   read-only Shopify connection. Shopify remains product and stock truth.
2. Sync products and obtain scoped, verified Grantex OACP artifacts. Confirm
   the artifact and connector evidence share source references. Do not use the
   synthetic catalog record as production authority.
3. The tenant administrator issues a buyer-agent credential for one merchant
   and one seller. Store it in the external buyer's secret manager and use the
   A2A v1 HTTP+JSON Agent Card and `message:send` routes.
4. Validate buyer-safe answers, source and expiry, wrong-merchant refusal,
   revocation, and rate limiting before wider access.
5. For a purchase, use the separately authorized commerce preparation and
   provider/merchant confirmation flow. The buyer-facing A2A question route
   does not execute it. A real paid purchase additionally needs merchant and
   provider onboarding, human authorization, payment callbacks, order and
   inventory reconciliation, and approved channel rollout.

An external client need not run on AgenticOrg, but supporting A2A v1
HTTP+JSON alone does not grant access. It must be configured to discover the
seller endpoint and hold a merchant-issued credential. This is not automatic
ChatGPT, Claude, Gemini, Codex, or marketplace distribution. See the
[A2A interoperability guide](a2a-interoperability.md) for wire and security
details and the [OACP flow](oacp/end-user-flow.md) for system ownership.
