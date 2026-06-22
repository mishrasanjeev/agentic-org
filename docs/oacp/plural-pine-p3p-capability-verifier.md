# Plural/Pine P3P Capability Verifier Guide

Canonical end-to-end flow: [OACP end-user flow](end-user-flow.md).

AgenticOrg verifies provider-owned Pine Labs Plural/P3P capability metadata without storing raw payment secrets in OACP artifacts.

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
