# Commerce Agent C5I Synthetic Merchant Dataset

Status: implementation-only documentation and validation for internal smoke/dev flows. This does not deploy, create cloud resources, merge, change production config, enable production Commerce V1, enable checkout/payment creation, enable live payments, enable live Plural, touch secrets, or approve any real merchant.

## Dataset

The AgenticOrg synthetic mapping lives at `docs/examples/commerce-agent-c5i-synthetic-merchant.dataset.json`.

It mirrors the Grantex C5I synthetic internal smoke dataset by ID and purpose, but it is not an AgenticOrg production configuration. It gives local smoke/dev flows stable fake values for connector and guardrail checks without exposing a real merchant or asserting production readiness.

Pinned synthetic IDs:

- Merchant: `mch_synth_internal_smoke_0001`
- Agent: `cag_synth_internal_smoke_sales_0001`
- Product: `cprd_synth_internal_smoke_widget_0001`
- Variant: `cvar_synth_internal_smoke_widget_0001_a`
- Provider: `mock`

All IDs and names must keep synthetic/internal/smoke markers. Currency `ZZZ`, placeholder host `commerce-synth-smoke.example.invalid`, and country/currency placeholders are intentionally fake.

## Explicit Non-Approval

This dataset does not approve or authorize:

- Production discovery.
- `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED`.
- Grantex public discovery flags or merchant allowlist values.
- Any production config value.
- Checkout creation.
- Payment intent creation.
- Live payments.
- Live Plural.
- A real production merchant.

Certification and readiness claims remain `none`. The synthetic merchant ID must not be presented as a merchant approval, a certification, a production authorization, or safe for public discovery.

## AgenticOrg Gate Posture

AgenticOrg commerce discovery remains hidden by default. The Commerce Sales Agent stays out of public MCP and A2A discovery unless a separate reviewed change explicitly enables the gate after Grantex read-only discovery approval.

This dataset does not change `core/commerce/discovery_gate.py`, does not set `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED`, and does not make checkout, payment, live payment, live Plural, or provider credential paths available.

## Smoke Scope

Allowed internal smoke/dev cases are read-only and synthetic:

- Connector health.
- Merchant profile read through Grantex.
- Catalog search read through Grantex.
- Catalog item read through Grantex.
- Inventory read through Grantex.
- No direct provider call regression.

Blocked cases remain blocked:

- Checkout creation.
- Payment creation.
- Provider credential handling.
- Direct Stripe, Plural, Pine, or provider payment paths.
- Live payment rails.
- Public discovery enablement.

## Validation

Run the focused validator after changing the dataset or this guide:

```powershell
python scripts/validate_commerce_c5i_synthetic_dataset.py
```

The validator rejects production-looking merchant IDs, realistic merchant names, secret-like values, provider credential material, live-payment claims, and certification/readiness overclaims.
