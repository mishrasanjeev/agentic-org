# OACP Shopify Merchant Checklist

Status on 2026-10-20. Items marked "flag-gated" are on `main` behind flags that default to off and
are not in the AgenticOrg production deployment yet.

- Shopify shop domain confirmed.
- Shopify Admin API read-only credential or OAuth app approved.
- Product, variant, image, price, inventory, and status read scopes available.
- Shopify webhook secret configured for product/inventory updates.
- AgenticOrg tenant id, merchant id, and seller agent id assigned.
- Grantex service token configured and tenant allowlisted.
- Grantex environment issues ES256-signed artifacts, lists an `oacp-` key in its JWKS and serves
  its status list (required for strict verification in production and staging); artifacts
  refreshed with `POST /api/v1/commerce/runtime/artifacts/refresh` after the AgenticOrg release.
- Merchant arbiter limits saved (`provider_policy.arbiter`: `max_order_amount_minor_units`,
  `currency_allowlist`, `max_quantity_per_order`); without them every handoff is refused
  `merchant_limits`.
- Buyer channels selected: web, ChatGPT, Claude, Gemini, Perplexity, WhatsApp, Telegram.
- WhatsApp and Telegram platform approvals and webhook secrets configured where used.
- Pine Labs Plural/P3P sandbox credentials configured for mandate capability verification.
- Flag-gated, optional: UCP serving host attested by DNS TXT record, signing key generated and
  profile published; UCP or ACP checkout enabled only after the staging run in the
  [launch and rollback runbook](oacp/explainers/launch-rollback-runbook.md).
- Live provider execution approval documented separately before any production payment flow. No
  payment executor is registered on `main`.
- Rollback owner named for disabling channels, revoking artifacts, unpublishing the UCP profile,
  turning off checkout flags, and disabling provider handoff.
