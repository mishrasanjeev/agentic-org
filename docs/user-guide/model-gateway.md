## Control model routing with the Model Gateway

The Model Gateway is an administrator console for reviewing provider/model choices, routing rules, access policies, per-model limits, dry-run decisions, recent routing records and cost comparisons. It does not create provider credentials. Configure and test credentials first in [AI Provider Credentials](/docs/model-setup), then use **Settings > Model Gateway** (`/dashboard/settings/model-gateway`) for tenant-scoped gateway controls.

```flow
Prepare approved provider access | Add credentials and confirm allowed models, regions, billing owner and data terms.
Review current state | Check gateway status and existing routing, access and limit rules.
Draft one narrow rule | Set its priority, match conditions, allowed targets and reason.
Evaluate before rollout | Use the dry run with representative requests and inspect the chosen route or refusal.
Enable deliberately | Confirm tenant policy and feature configuration, then monitor routing records and workload outcomes.
```

## Choose the right control

| Control | What it decides | Good first use |
| --- | --- | --- |
| Routing policy | Which configured provider/model or weighted target is selected for a matching request | Prefer an approved lower-cost model for a bounded use case |
| Access policy | Whether a request matching a principal, application, agent, provider, model or other supported context is allowed or denied | Deny a model for a sensitive use case |
| Model limit | Maximum concurrent requests and/or requests per minute for a provider/model | Protect a shared quota from bursts |
| Dry run | What the current policy would decide for supplied request context | Compare intended behavior before changing live traffic |

Start with the smallest match scope. Priorities are ordered (lower values win); use explicit reasons that help the next administrator understand the decision. Review overlap between policies and access rules rather than assuming one rule overrides every other control. The dry-run result is a point-in-time evaluation, not a reservation of provider capacity or a guarantee about a later request.

## Verify the outcome

After a controlled test, inspect the decision shown by the console. When routing-record capture is enabled for the deployment, use the Records tab to search by correlation ID and review provider, model, outcome, latency, token/cost fields and signature status. The Costs tab compares observed calls with available list-price data; an unpriced model or missing observation is not zero cost. Actual invoices and provider billing remain authoritative.

The records feature is configuration-dependent and disabled by default. Confirm `AGENTICORG_MODEL_GATEWAY_RECORDS_ENABLED` and its retention policy with the platform owner before expecting a history. Gateway status, API authorization and tenant scope still apply; a visible rule does not prove that every application path is routed through the gateway.

## Safe rollout and recovery

1. Use synthetic or approved low-sensitivity inputs first.
2. Save the existing rule values and identify an administrator who can restore them.
3. Test allowed, denied, unmatched, provider-timeout and limit-exceeded cases.
4. Enable one change at a time, then compare the actual provider/model and failure rate.
5. Disable or revise the specific rule if outcomes differ from the dry run; record the reason and retest.

Do not paste provider keys into prompts, policy reasons or support tickets. Keep credentials in the credential manager, and ask the provider or platform administrator to resolve quota, endpoint or model-availability errors.

Next: [AI provider setup](/docs/model-setup), [Guardrails](/docs/guardrails), [Run observability](/docs/run-observability), [Security and data](/docs/security-and-data).
