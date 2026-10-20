# Why OACP Keeps Buyer Agents Honest

> **Update, 20 October 2026.** Since this post was written, Grantex signs every OACP artifact
> (ES256 detached JWS) and AgenticOrg's `main` branch verifies each one before caching it. A
> deterministic commerce arbiter decides every purchase handoff, POS handoff and checkout
> completion (allow or refuse, never waiting for a person), and every commerce change is written
> to the commerce ledger. UCP and ACP checkout, server-verified Grantex mandates and a Plural
> payment executor exist behind flags that default to off, so with the defaults nothing moves money. The AgenticOrg changes are not in its production
> deployment yet. Current state: [OACP explained](oacp-one-trust-layer-under-ucp-acp-ap2.md) and
> the [truth inventory](../oacp/truth-inventory.md).

## Summary

OACP keeps buyer agents honest by making source, freshness, scope, revocation posture, risk tier, and non-execution flags mandatory runtime inputs.

## Target Audience

Buyer-agent builders, safety teams, and support operators.

## Architecture Diagram

```mermaid
flowchart TD
  shopify[Shopify facts] --> evidence[Redacted evidence]
  evidence --> artifact[Grantex OACP artifact]
  artifact --> cache[AgenticOrg cache evaluator]
  cache --> decision{Answer, refresh, prepare, refuse}
  decision --> answer[Grounded answer]
  decision --> refuse[Safe blocker]
```

## End-To-End Flow

The buyer asks a question. AgenticOrg loads scoped cache records, checks artifact TTL and source freshness, filters private or executable fields, and returns either a grounded answer, a refresh instruction, a prepared handoff, or a blocker.

## What Is Implemented Now

The runtime builds cache records from Grantex artifacts, answers from cache, generates source labels, blocks stale/missing artifacts, and refuses purchase preparation when required artifacts or Plural/Pine capability evidence are missing.

## What Requires External Approval Or Config

Channel rollout, merchant source approval, provider capability configuration, and any future execution path.

## Failure Modes

- Cache record expired.
- Buyer asks for payment or order success.
- Source refs are missing.
- Provider capability evidence is stale.

## Safe User Wording Examples

- "I can answer from the current merchant snapshot."
- "I cannot confirm payment from cached artifacts."
- "This request needs refreshed source evidence."
