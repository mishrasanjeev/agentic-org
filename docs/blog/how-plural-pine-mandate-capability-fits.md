# How Pine Labs Plural/P3P And Bank-Owned Mandate Capability Fit Into Agentic Commerce

## Summary

AgenticOrg verifies provider-owned Plural/Pine capability metadata and uses redacted evidence refs in purchase preparation. Pine Labs Plural/P3P owns mandate and payment rail execution for the current verifier path. Bank-owned, fintech, and custom provider rails can be configured as provider-owned refs, but they remain non-executing until approved adapters exist.

## Target Audience

Fintech partners, banks, operators, and commerce risk teams.

## Architecture Diagram

```mermaid
flowchart LR
  buyer[Buyer purchase intent] --> prep[AgenticOrg purchase preparation]
  prep --> cache[OACP artifact checks]
  prep --> pine[Plural/Pine verifier]
  pine --> evidence[Redacted evidence ref]
  bank[Bank/fintech/custom provider config] --> blocker[Pending adapter or approved provider path]
  evidence --> result[Prepared handoff or blocker]
```

## End-To-End Flow

AgenticOrg checks fresh OACP artifacts, product/price/inventory/policy records, and provider capability evidence. If all required evidence exists for an approved adapter, it prepares a provider-owned handoff. If the provider is a bank-owned, fintech, or custom rail without an approved adapter, it returns a pending-adapter blocker.

## What Is Implemented Now

The runtime has `/providers/plural-pine/mandate-capability/verify` and `/purchase/prepare`. It returns redacted capability evidence or exact blockers without storing raw payment secrets. Merchant config also accepts `bank`, `fintech_rail`, `custom_provider`, and `none` as provider types without marking them executable.

## What Requires External Approval Or Config

Plural/Pine credentials, bank/provider credentials where applicable, environment approval, merchant approval, provider webhook/reconciliation path, rollback owner, and support policy.

## Failure Modes

- Provider env vars or credential refs missing.
- Non-approved provider environment or pending adapter.
- Capability evidence stale.
- Buyer asks the agent to create a mandate directly.

## Safe User Wording Examples

- "Provider capability evidence is present for preparation only."
- "No mandate, payment, or order was created."
- "Plural/Pine capability is missing or stale, so this is blocked."
