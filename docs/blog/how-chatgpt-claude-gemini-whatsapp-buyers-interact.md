# How ChatGPT, Claude, Gemini, WhatsApp, And Telegram Buyers Interact

```mermaid
flowchart LR
  chat[Buyer channel] --> bridge[AgenticOrg bridge]
  bridge --> ask[Shared buyer question path]
  ask --> cache[OACP cache]
  cache --> response[Answer with source/freshness]
  ask --> block[Commitment blocker when evidence is missing]
```

Every channel uses the same seller-agent facts. MCP tools serve ChatGPT and
Claude-style clients. OpenAPI and A2A metadata serve Gemini and hosted/action
clients. WhatsApp and Telegram use verified webhooks. The answer path is shared,
so channel differences do not create different commerce truth.

The channels can ask questions, inspect product snapshots, and request a
prepared handoff. They cannot create orders or payments by themselves.

