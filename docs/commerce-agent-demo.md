# Commerce Agent Demo

This demo is for AgenticOrg Commerce V1 internal sandbox only. It depends on
the Grantex Commerce V1 draft PR #326 contract and uses mocked Grantex
REST/MCP responses from `evals/golden_datasets/commerce.json`.

Run:

```bash
python demos/commerce_sales_agent_demo.py
```

The demo does not require live Grantex, Docker, API keys, payment provider
runtime, or provider credentials.

## Happy Path

1. Product discovery via `grantex_commerce:catalog_search`.
2. Product Q&A grounded in `grantex_commerce:catalog_get_item`.
3. Inventory answer from `grantex_commerce:inventory_check`.
4. Cart draft via `grantex_commerce:cart_create`.
5. Consent request via `grantex_commerce:consent_request`.
6. Passport exchange via `grantex_commerce:consent_exchange`.
7. Payment intent via `grantex_commerce:payment_create_intent`.
8. Checkout handoff via `grantex_commerce:checkout_create`.
9. Payment status polling via `grantex_commerce:payment_get_status`.

## Negative Mini-Demo

- Missing consent checkout refusal.
- Unsupported EMI or discount refusal.
- Stale or unknown inventory cautious response.

## Safety Posture

- Internal sandbox only.
- No production deployment or live payment configuration.
- No direct Stripe, Plural, Pine, or other payment provider calls.
- No provider credential handling.
- Commerce Passports are Grantex outputs only and are redacted in demo output.
- Final payment confirmation remains user-controlled.

