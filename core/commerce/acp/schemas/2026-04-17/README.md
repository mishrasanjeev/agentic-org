# Vendored ACP 2026-04-17 JSON Schemas

Copied unchanged from `github.com/agentic-commerce-protocol/agentic-commerce-protocol`,
`spec/2026-04-17/json-schema/`, at commit `7fdd78df677a` (Apache-2.0).

| File | Used for |
| --- | --- |
| `schema.agentic_checkout.json` | Validating ACP checkout requests at the boundary (`core/commerce/acp/checkout.py`); tests validate responses, errors and order events against it |
| `schema.feed.json` | Tests validate the product feed (`Product`, `ProductsResponse`, `FeedMetadata`) against it |
| `schema.delegate_payment.json` | Tests check the delegate-payment allowance shape (`DelegatePaymentRequest`) |

The `example` members of these files contain placeholder tokens from the upstream specification,
not credentials. Refresh by copying the same files from a newer release folder and re-running the
ACP tests.
