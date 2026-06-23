# How Buyer Agents Shop Safely With OACP

Canonical end-to-end flow: [OACP end-user flow](../end-user-flow.md).

Buyer agents shop safely when every answer comes from valid OACP artifacts and every risky action is prepared or refused.

```mermaid
flowchart TD
  ask[Buyer asks] --> cache[OACP cache]
  cache --> valid{Valid, fresh, in scope?}
  valid -->|Yes| answer[Answer with source/freshness]
  valid -->|No| refuse[Refuse or refresh]
  ask --> commit{Commitment request?}
  commit -->|Yes| prep[Prepare handoff or block]
```

## Can Do

Ask, compare, reason, explain source/freshness, and prepare a non-executing handoff.

## Cannot Do

Invent paid states, create order, create checkout, reserve stock, set up mandate, or call private merchant systems outside approved runtime.
