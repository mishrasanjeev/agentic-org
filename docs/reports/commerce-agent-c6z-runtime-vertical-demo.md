# C6Z Runtime Vertical Demo

## Scope

C6Z adds a real internal runtime path for a Seller Commerce Agent:

1. Merchant creates a Seller Commerce Agent onboarding packet in AgenticOrg.
2. Seller agent initiates Shopify Admin GraphQL read-only sync.
3. AgenticOrg stores normalized connector evidence, not raw Shopify responses or tokens.
4. AgenticOrg sends an authority request to Grantex when the Grantex internal URL/token are configured.
5. Grantex validates the request and returns internal signed OACP artifacts.
6. AgenticOrg stores Grantex artifact metadata in its durable OACP artifact cache.
7. Buyer web session answers product questions from cached artifacts and normalized source evidence.
8. MCP tools expose the same cached product facts to local agent clients.
9. Plural/Pine mandate capability can be checked only when sandbox env vars are present.

The vertical is internal, non-publication, non-certifying, non-production, and non-executing. It does not create checkout sessions, payments, mandates, orders, holds, refunds, returns, shipments, public discovery entries, or live-provider actions.

## Runtime Boundaries

- AgenticOrg owns seller onboarding, Shopify read-only connector initiation, buyer session UX, MCP bridge tools, local artifact cache consumption, and provider-owned capability checks.
- Grantex owns canonical OACP authority validation and internal artifact issuance.
- Shopify remains the operational source of record for catalog, price, media, and inventory snapshots.
- Provider rails own payment and mandate execution. C6Z only checks capability metadata.
- Valid cached artifacts can support non-binding buyer answers without routing every buyer turn through Grantex.

## Environment

Use `.env.c6z.example` as the local template and copy it to `.env.c6z.local`.

Shopify read-only sync:

- `SHOPIFY_SHOP_DOMAIN`
- `SHOPIFY_ADMIN_ACCESS_TOKEN`
- `SHOPIFY_API_VERSION`
- `SHOPIFY_WEBHOOK_SECRET` for webhook HMAC verification

Grantex internal authority request:

- `GRANTEX_COMMERCE_BASE_URL`
- `GRANTEX_COMMERCE_INTERNAL_TOKEN`

Plural/Pine capability check:

- `PLURAL_PINE_CLIENT_ID`
- `PLURAL_PINE_CLIENT_SECRET`
- `PLURAL_PINE_ENVIRONMENT`
- `PLURAL_PINE_CAPABILITY_URL`

Missing external credentials cause blocked or skipped results with exact env vars listed. The code does not claim external validation when credentials are absent.

## Demo Sequence

1. Start Grantex auth-service and AgenticOrg API/UI locally after applying migrations.
2. Open `/dashboard/commerce-runtime`.
3. Create a Seller Commerce Agent onboarding packet with merchant and seller-agent scope.
4. Run Shopify sync. If Shopify env vars are missing, the API returns `blocked_missing_shopify_env`.
5. Request Grantex authority artifacts. If Grantex env vars are missing, the API returns the redacted authority payload and `blocked_missing_grantex_env`.
6. Cache returned Grantex artifacts.
7. Ask a buyer question such as `Show me available products with prices`.
8. Connect MCP locally and call:
   - `seller.list_products`
   - `seller.search_products`
   - `seller.get_product_facts`
   - `seller.get_offer_snapshot`
   - `seller.get_inventory_snapshot`
   - `seller.ask_product_question`
9. Run the Plural/Pine capability check only with sandbox env vars present.

## Local Commands

```powershell
python scripts/alembic_migrate.py
python -m pytest tests/unit/test_oacp_c6z_runtime_vertical.py --no-cov
python -m ruff check core/commerce/c6z_runtime_vertical.py api/v1/commerce_runtime.py core/models/commerce_c6z_runtime.py tests/unit/test_oacp_c6z_runtime_vertical.py
python -m mypy core/commerce/c6z_runtime_vertical.py api/v1/commerce_runtime.py core/models/commerce_c6z_runtime.py tests/unit/test_oacp_c6z_runtime_vertical.py
```

```powershell
cd ui
npm test -- CommerceRuntimeDemo
```

```powershell
cd mcp-server
npm run build
```

## Guardrails

- Shopify access token is read from environment only.
- Raw Shopify GraphQL responses are normalized before persistence.
- Webhooks verify Shopify HMAC and store only an idempotency key foundation.
- Buyer answers include source/freshness labels and refuse final commitments.
- MCP tools read cached facts and never execute transaction actions.
- Plural/Pine verifier stores only result status, checked timestamp, expiry, environment label, and redacted evidence ref.
- No public endpoint publication, public OACP publication, payment execution, live mandate creation, live provider rail enablement, checkout/order creation, export writer, scheduler, queue, or cloud behavior is added.
