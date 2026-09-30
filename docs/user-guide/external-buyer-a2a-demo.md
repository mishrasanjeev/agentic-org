## See an outside buyer talk to a seller

This walkthrough uses a synthetic local merchant. The buyer is a standalone
Python process, not an AgenticOrg buyer agent. It speaks the A2A v1 HTTP+JSON
message protocol to an AgenticOrg Seller Commerce Agent. A real external
client needs merchant-issued access; knowing an Agent Card URL is not enough.

```flow
Create a local seller | Three synthetic products and their price/inventory snapshots are placed in a development database.
Add source evidence | A local catalog cache record is linked to the exact connector evidence reference.
Approve one buyer | A tenant/merchant/seller/buyer-scoped credential is issued to a separate process.
Ask over A2A | The buyer discovers the card and sends catalog and product questions over HTTP.
Respect the boundary | A purchase request is refused; no order, payment or hold is created.
Revoke and clean up | The buyer receives HTTP 401 after revocation and temporary rows are removed.
```

## What the buyer sees

| Synthetic product | Price snapshot | Quantity snapshot |
| --- | ---: | ---: |
| Canvas Tote | INR 1299 | 7 |
| Ceramic Mug | INR 499 | 12 |
| Pocket Notebook | INR 249 | 20 |

The buyer asks to see this catalogue, then asks
about the tote. The seller gives a price and inventory **snapshot** with a
freshness label and says that final price needs merchant confirmation. These
are local teaching fixtures, not merchant offers.

When the buyer asks to buy two totes, the seller refuses transaction
execution. That is the correct result for this A2A question route. An answer
cannot reserve stock, collect money, or create a confirmed order. For a real
purchase, the merchant system and authorized fintech/payment rail must
confirm their own outcomes through separate approved integration steps.

## Try it on a development stack

Apply local migrations and start the development API with local PostgreSQL
and Redis. From the repository root, with local `AGENTICORG_ENV`,
`AGENTICORG_DB_URL`, and `AGENTICORG_REDIS_URL` configured, run:

```powershell
python -m examples.a2a_commerce_demo.run_demo `
  --base-url http://127.0.0.1:8000 `
  --confirm-local-synthetic
```

The runner rejects production or non-local targets. It keeps the buyer token
out of output, revokes it after the questions, and removes its synthetic
seller, evidence, and cache records. Read the [full local demo guide](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/a2a-commerce-demo.md)
for setup and expected results.

## Taking a real merchant further

The real path uses authorized Shopify read-only sync, Grantex-issued and
verified OACP artifacts, an AgenticOrg cache, merchant-scoped buyer access,
and an external A2A client configured for the endpoint. Do not substitute
this synthetic record for signed authority. A2A interoperability is not a
promise that every vendor automatically discovers this seller. Payment and
mandate execution are owned by their fintech/provider; AgenticOrg's current
A2A seller conversation is non-binding.

Next: [Commerce guide](/docs/commerce), [API and A2A integration](/docs/api-sdk-mcp).
