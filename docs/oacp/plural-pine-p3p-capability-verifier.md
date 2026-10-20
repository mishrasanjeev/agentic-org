# Plural/Pine P3P Capability Verifier Guide

Canonical end-to-end flow: [OACP end-user flow](end-user-flow.md).

AgenticOrg verifies provider-owned Pine Labs Plural/P3P capability metadata without storing raw payment secrets in OACP artifacts.

Merchants configure payment provider metadata in `/dashboard/commerce-runtime`. Supported config types are `plural_pine`, `bank`, `fintech_rail`, `custom_provider`, and `none`. Only the Plural/Pine verifier has a runtime capability path today. Bank-owned and other provider-owned rails stay metadata-only until their adapters and provider approvals are implemented.

## Endpoint

`POST /api/v1/commerce/runtime/providers/plural-pine/mandate-capability/verify`

## Evidence Flow

```mermaid
flowchart LR
  runtime[AgenticOrg verifier] --> provider[Pine Labs Plural/P3P]
  provider --> evidence[Redacted capability evidence ref]
  evidence --> cache[OACP capability context]
  cache --> prep[Purchase/mandate preparation]
```

## Results

The verifier can return available capability evidence, missing-env evidence, non-sandbox blocked evidence, or provider error evidence. Each result is public-safe and redacted.

## Boundary

Capability evidence is not mandate creation, payment capture, checkout creation, or order success.
The verifier never moves money, whatever the flags say.

Payment execution is a separate component: the [Plural executor](plural-executor.md). It runs
only while `PLURAL_PINE_LIVE_EXECUTION_ENABLED=true` and only on a consumed arbiter `allow`
decision. The merchant's `mandate_capability`, the configuration this verifier's evidence
describes, sets the executor's allowance:

- `max_amount_minor_units` is the most one payment may charge;
- `currency` is the currency it charges in;
- with a complete `upi_autopay` block, UPI AutoPay e-mandates are offered
  ([Mandates](mandates.md)).

Grantex may verify or sign non-sensitive evidence refs. It does not execute provider rails;
the decision to execute directly from AgenticOrg rather than through Grantex Commerce V1 is
recorded in [Plural executor](plural-executor.md#decision-direct-plural-execution).
