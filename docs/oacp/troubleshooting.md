# OACP Troubleshooting

Canonical end-to-end flow: [OACP end-user flow](end-user-flow.md).

| Symptom | Likely cause | Action |
| --- | --- | --- |
| Shopify sync blocked | Missing credential, invalid domain, decrypt failure, or read validation failure. | Reconnect Shopify and confirm status endpoint hides secrets. |
| Grantex authority returns 202 | Connector evidence missing. | Run Shopify sync and resubmit authority request. |
| Grantex authority returns 422 | Stale, private, executable, or invalid scope request. | Inspect requested artifact families and source observed time. |
| Buyer answer missing | Cache lacks valid catalog/price/inventory artifacts. | Refresh Grantex artifacts and cache. |
| Source label missing | UI or bridge dropped cache metadata. | Fix bridge response mapping before public use. |
| WhatsApp/Telegram blocked | Webhook secret missing or invalid. | Configure secrets and rerun webhook smoke. |
| Purchase prepare blocked | Fresh artifact or provider capability evidence missing. | Refresh source artifacts and run Plural/Pine verifier. |
| POS handoff blocked | Purchase preparation did not produce a prepared handoff, artifact refs are missing, or POS location metadata is incomplete. | Refresh artifacts, verify buyer/session scope, and check POS readiness endpoint. |
| POS simulator reports accepted | Local test handoff only. | Tell buyer staff must confirm final price and payment at the store. Do not claim purchase completion. |
| POS callback says payment confirmed but is unverified | Missing verified callback or provider evidence ref. | Downgrade to staff review and investigate callback verification. |
| User asks for paid-state proof | Unsupported from OACP cache. | Return safe wording: no payment/order was created. |

## Failure/Refusal Flow

```mermaid
flowchart TD
  error[Runtime error] --> safe{Private data involved?}
  safe -->|Yes| redact[Redact and fail closed]
  safe -->|No| classify[Classify blocker]
  classify --> user[Return safe user wording]
  classify --> ops[Log operator action]
```
