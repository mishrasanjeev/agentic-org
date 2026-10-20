# Source, Freshness, And Trust In AI Commerce

> **Update, 20 October 2026.** Since this post was written, Grantex signs every OACP artifact
> (ES256 detached JWS) and AgenticOrg's `main` branch verifies each one before caching it. A
> deterministic commerce arbiter decides every purchase handoff, POS handoff and checkout
> completion (allow or refuse, never waiting for a person), and every commerce change is written
> to the commerce ledger. UCP and ACP checkout surfaces exist behind flags that default to off,
> and no payment executor is registered yet. The AgenticOrg changes are not in its production
> deployment yet. Current state: [OACP explained](oacp-one-trust-layer-under-ucp-acp-ap2.md) and
> the [truth inventory](../oacp/truth-inventory.md).

## Summary

Source and freshness labels make AI commerce inspectable. OACP requires buyer answers to carry where facts came from and whether they are still usable.

## Target Audience

Product managers, UX writers, and support operators.

## Architecture Diagram

```mermaid
flowchart TD
  shopify[Shopify observed_at] --> artifact[OACP issued_at/expires_at]
  artifact --> cache[AgenticOrg freshness label]
  cache --> answer[Buyer answer]
  cache --> stale[Stale blocker]
```

## End-To-End Flow

Shopify source timestamps flow into connector evidence. Grantex artifacts carry issue and expiry windows. AgenticOrg converts those into source/freshness labels and blockers.

## What Is Implemented Now

Cache records include source refs, evidence refs, TTL, freshness, revocation posture, and non-enablement flags. Buyer answers display source labels in the runtime demo.

## What Requires External Approval Or Config

Merchant-specific source precedence, channel wording review, and public support runbooks.

## Failure Modes

- Stale source evidence.
- Missing source label in a channel.
- Cache record used for a higher-risk request than allowed.

## Safe User Wording Examples

- "Source: Shopify via Grantex artifact."
- "Freshness: current within the artifact window."
- "This snapshot is stale; refresh is required."
