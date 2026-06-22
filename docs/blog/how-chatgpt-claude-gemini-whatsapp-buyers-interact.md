# How ChatGPT, Claude, Gemini, Perplexity, WhatsApp, And Telegram Can Shop Through Seller Agents

## Summary

Buyer surfaces use different protocols, but they must share one OACP-backed truth path in AgenticOrg.

## Target Audience

Developers, channel owners, and go-to-market teams.

## Architecture Diagram

```mermaid
flowchart LR
  web[Web] --> runtime[AgenticOrg runtime]
  mcp[ChatGPT/Claude-style MCP] --> runtime
  openapi[Gemini/Perplexity-style OpenAPI] --> runtime
  a2a[A2A] --> runtime
  wa[WhatsApp] --> runtime
  tg[Telegram] --> runtime
  runtime --> cache[OACP cache]
  cache --> response[Answer or blocker]
```

## End-To-End Flow

1. A buyer asks through a surface.
2. The bridge normalizes the request.
3. The shared buyer question path checks OACP cache.
4. The response returns source/freshness labels.
5. Commitment requests go to purchase preparation.

## What Is Implemented Now

AgenticOrg exposes web ask, OpenAPI ask/schema, A2A agent card, surface matrix, WhatsApp webhook, Telegram webhook, protocol adapter routes, and buyer Q&A from cache.

## What Requires External Approval Or Config

WhatsApp and Telegram webhook secrets, MCP/OpenAPI client review, public channel policy, provider credentials for mandate/payment capability, and merchant launch approval.

## Failure Modes

- Channel secret missing.
- Channel payload cannot be normalized.
- Cache is stale.
- Buyer asks for execution from a discovery channel.

## Safe User Wording Examples

- "This channel can answer from cached OACP artifacts."
- "A purchase request can be prepared for review only."
- "No checkout, payment, or order was created."
