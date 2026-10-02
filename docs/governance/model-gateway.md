# Model gateway: routing policy

The model gateway sits in front of every model provider. With it on, a model call
that carries a tenant asks the gateway which provider and model to use before a
credential is resolved or a provider is called. Tenant administrators write the
routing policies; changing a policy needs no change to the agents or applications
that make the calls.

Off by default. The authority flag `model_gateway.enabled` (operator managed)
turns it on per tenant, `AGENTICORG_MODEL_GATEWAY_ENABLED=true` for the whole
deployment. Off, the gateway returns the caller's own choice and reads nothing.

## A policy

| Field | Meaning |
|---|---|
| `name`, `priority`, `enabled` | Enabled policies are evaluated in ascending `priority` (then name); the first match decides. |
| `use_case` | What kind of call: `agent_run`, `agent_resume` or `completion` (the direct router). Empty matches every use case. |
| `sensitivity` | The data sensitivity of the request: `public`, `internal`, `confidential` or `restricted`. Agent runs carry the sensitivity recorded on the agent (`llm_config.sensitivity`). |
| `agent_id`, `business_unit`, `language` | Further match fields; `business_unit` is matched against the agent's domain. Empty matches everything. |
| `provider`, `model`, `tier` | What a match gets: a provider (with a model, or the provider's first catalogue model), a model, or a cost tier (`tier1`, `tier2`, `tier3`, resolved like the smart router's tiers). |
| `allowed_providers` | A fence: a provider outside the list is refused, with the policy named, rather than replaced. |
| `in_region_only` | The call may only use a provider inside the deployment or one attested for the tenant's data region. |
| `reason` | Why the policy exists; recorded in the audit row. |

A policy must route (provider, model or tier), fence (`allowed_providers`) or
restrict (`in_region_only`); a provider and model given together are checked
against the provider catalogue.

Example: everything the finance agents do stays with one provider, and anything
tagged restricted stays in region.

```json
{"name": "finance-in-house", "priority": 10, "business_unit": "finance",
 "provider": "openai_compatible", "allowed_providers": ["openai_compatible", "ollama"],
 "reason": "finance data is processed on the in-house endpoint"}
{"name": "restricted-in-region", "priority": 20, "sensitivity": "restricted",
 "in_region_only": true, "reason": "restricted data never leaves the data region"}
```

## Decision rules

- The first enabled policy in priority order whose match fields all equal the
  request's decides. A request no policy matches keeps what the caller asked for
  and is logged as a pass-through.
- A request tagged `restricted`, or matched by a policy with `in_region_only`,
  may only use a provider inside the deployment (`ollama`, `vllm`, the local
  embedding and speech models) or one with an active residency attestation for
  the tenant's region (in-region processing and no training). This is checked
  whether or not residency enforcement is on. Otherwise the call is refused with
  `E1014`; it is never re-routed to an outside provider.
- A policy's `allowed_providers` is a hard fence: a provider outside it refuses
  the call with the policy named, so a misconfigured policy fails loudly.
- Policies and the flag are read through a five-second shared cache and then the
  database. In a strict runtime a read failure refuses the call; a relaxed
  runtime passes the caller's choice through and logs it.

## Where it applies

| Call site | Use case | Notes |
|---|---|---|
| Agent runner, `run_agent` | `agent_run` | Decided before the credential prefetch; a refusal returns a run result with status `model_gateway_refused`. |
| Agent runner, `resume_agent` | `agent_resume` | Same, for a run resumed after a human decision. |
| `LLMRouter.complete` | `completion` | Workflow generation, the replanner and the other direct callers that pass a tenant. A gateway-chosen model is an explicit selection: failover stays within its provider. |

Not yet routed through the gateway: the sidecar model calls that build a model
without a prefetched decision (explanations, SOP parsing, feedback analysis). They
keep the tenant's default model and are covered by residency enforcement.
Planned next: access policies by application and identity, per-model concurrency
and rate limits, model-level metrics and routing records in the audit trail, and
cost-aware routing.

## Observing it

- Every decision is logged as `model_gateway_decision` (or `model_gateway_refused`)
  with `correlation_id`, `use_case`, `policy_id`, `provider`, `model`, `restricted`
  and `reason`.
- `agenticorg_model_gateway_decisions_total{outcome}` counts `applied`,
  `passthrough` and `refused` decisions.
- Every policy change writes a signed audit row (`model_gateway_policy.set`,
  `.update`, `.delete`).

## API (tenant administrators)

| Method and path | Purpose |
|---|---|
| `GET /api/v1/model-gateway/status` | Whether the gateway is on and the active policies. |
| `GET /api/v1/model-gateway/policies` | List policies (`include_disabled=false` to hide disabled ones). |
| `POST /api/v1/model-gateway/policies` | Create a policy (201). |
| `PATCH /api/v1/model-gateway/policies/{id}` | Change a policy; the merged policy is re-validated. |
| `DELETE /api/v1/model-gateway/policies/{id}` | Delete a policy (204). |
| `POST /api/v1/model-gateway/evaluate` | Dry-run a described request: the decision it would get, or the refusal, as data. |

## Runbook: move a business unit to one provider

1. Create the policy with `business_unit`, `provider` and `allowed_providers`.
2. `POST /api/v1/model-gateway/evaluate` with a representative request; confirm the decision.
3. Turn `model_gateway.enabled` on for the tenant; watch
   `agenticorg_model_gateway_decisions_total{outcome="applied"}` rise and the
   `model_gateway_decision` log lines name the policy.
