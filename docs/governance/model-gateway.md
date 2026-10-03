# Model gateway: routing policy, access policy and limits

The model gateway sits in front of every model provider. With it on, a model call
that carries a tenant asks the gateway which provider and model to use before a
credential is resolved or a provider is called, whether this caller may use them,
and whether the model has room for the call. Tenant administrators write the
routing policies, access policies and limits; changing any of them needs no
change to the agents or applications that make the calls.

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
| `targets` | Instead of one provider, model or tier: a weighted split, `[{"provider", "model", "weight"}, ...]`. Each call lands on one target, stable for its correlation id and proportional to the weights over many calls; every target is checked against the catalogue and must sit inside `allowed_providers` when that is set. |
| `cost_aware`, `max_failure_rate` | With `cost_aware`, the targets are candidates and each call gets the cheapest one (by list price, see below) whose observed failure rate over the quality window stays at or under `max_failure_rate` (`AGENTICORG_MODEL_GATEWAY_MAX_FAILURE_RATE`, 0.05, when unset). Only candidates inside `allowed_providers` are considered, and the fence is checked on the choice as on any other route. A candidate with no observations counts as healthy; an unpriced candidate ranks last. When the records cannot be read the choice is by price alone; when no candidate is healthy the least failing one is chosen; both are logged and named in the decision's reason. |
| `allowed_providers` | A fence: a provider outside the list is refused, with the policy named, rather than replaced. |
| `in_region_only` | The call may only use a provider inside the deployment or one attested for the tenant's data region. |
| `reason` | Why the policy exists; recorded in the audit row. |

A policy must route (provider, model, tier or targets), fence (`allowed_providers`)
or restrict (`in_region_only`); a provider and model given together are checked
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

## Access policies

An access policy says who may use which provider or model. It is evaluated after
the routing policies have chosen the provider and model, so it fences what the
call will actually use.

| Field | Meaning |
|---|---|
| `name`, `priority`, `enabled` | Enabled access policies are evaluated in ascending `priority` (then name); the first match decides. A call no access policy matches is allowed. |
| `use_case`, `sensitivity`, `agent_id`, `business_unit`, `language` | Match fields, as on a routing policy. |
| `application`, `principal` | Who is calling. The auth middleware binds the caller's identity for every authenticated request: `application` is the API key's name, `agent:<id>` for an Agent Passport, `console` for a human session; `principal` is exactly the actor the audit rows record, `user:<id>` for a session with a user id, otherwise the authentication mode and subject (`api_key:apikey:<prefix>`, `grantex:<subject>`, `commerce_buyer:<subject>`, `legacy:<subject>`), so a principal copied from an audit row matches. Work outside a request (a worker task, a schedule) carries no identity and is not matched by a policy that names either field. |
| `provider`, `model` | Match the provider and model the routing chose. |
| `effect` | `deny` refuses the call. `allow` lets it through, fenced to `allowed_providers` and `allowed_models` when those are set. |
| `reason` | Why the policy exists; recorded in the audit row. |

Example: one application may use the frontier model; no one else may.

```json
{"name": "advisory-app-frontier", "priority": 10, "application": "advisory-app",
 "model": "gpt-4o", "effect": "allow", "reason": "the advisory application is approved for the frontier model"}
{"name": "frontier-denied", "priority": 20, "model": "gpt-4o", "effect": "deny",
 "reason": "every other caller uses the standard models"}
```

A refusal by an access policy carries `E1014` with `kind: "access"` and the
policy named.

## Per-model limits

A limit caps a provider (`model` empty) or one model: `max_concurrency` is the
number of model calls in flight at once, `requests_per_minute` the number that
may start per minute (a token bucket). A provider-wide limit and a model limit
both apply to a call on that model. Limits apply to every call while the gateway
is on, whether or not a routing policy matched it.

Every model call is one provider request, so every call is admitted on its
own just before it is sent and gives its concurrency slot back when the model
returns: each reasoning turn of an agent run (the runner binds the run's
routing decision; the graph's reasoning node admits against it) and each direct
completion. A turn refused mid-run ends the run with status
`model_gateway_refused`. A slot a dead process never released expires after
`AGENTICORG_MODEL_GATEWAY_LEASE_SECONDS` (600 by default), longer than any
single model call. A call above a limit is refused with `E1015`, which is
retryable and carries `retry_after_seconds`; nothing is held after a refusal.
The limits live in Redis and are shared by every API and worker process. When
Redis is unavailable the call is admitted and the check is metered as
`unavailable`: limits protect providers and budgets, they are not a security
control, and a cache outage must not stop every model call.
For a tenant whose configured limits must hold during an outage, set
`AGENTICORG_MODEL_GATEWAY_LIMITS_FAIL_CLOSED=true`. With that default-off
deployment switch, an unreadable limit policy or Redis admission state refuses
the routed call with retryable `E1015` instead of bypassing its cap. Configure
the switch and the tenant's gateway flag together; this does not affect calls
when the gateway is off.

```json
{"provider": "openai", "model": "gpt-4o", "max_concurrency": 8, "requests_per_minute": 120,
 "reason": "the frontier model's contracted capacity"}
{"provider": "ollama", "max_concurrency": 4, "reason": "one in-house inference node"}
```

## Cost comparison and cost-aware routing

`core/governance/model_pricing.py` carries the published list price of every
catalogue model the platform can price (the Gemini rows are the router's own
table), prices models inside the deployment (`ollama`, `vllm`) at nothing per
token, prices an Azure deployment as its base model, and leaves
`openai_compatible` unpriced. `AGENTICORG_MODEL_PRICE_OVERRIDES_JSON`, a JSON
object keyed `provider/model` with `input` and `output` per million tokens,
replaces list prices with negotiated ones; an override that is negative or
not finite is rejected and logged. The routing records cost each call at its
model's price when one is known. The direct router does the same with
`AGENTICORG_MODEL_PRICING_FOR_ROUTER_COSTS=true` (off by default, since those
figures feed the cost counters and budget controls); off, it keeps the
historical flat rates (FINDINGS A-116).

`GET /api/v1/model-gateway/costs?window_hours=24` lists every catalogue model
and every model seen in the records with its list price, a blended per-million
rate (input weighted three to one against output) and what the records
observed over the window: calls, failures, failure rate, average latency,
average and total cost. The same observations drive cost-aware routing.

Example: a drafting use case may use any of three models; each call gets the
cheapest one that has not been failing.

```json
{"name": "drafting-cheapest", "priority": 30, "use_case": "agent_run", "business_unit": "marketing",
 "cost_aware": true, "max_failure_rate": 0.02,
 "targets": [{"provider": "openai", "model": "gpt-4o-mini"}, {"provider": "gemini", "model": "gemini-2.5-flash"},
             {"provider": "openai", "model": "gpt-4o"}],
 "reason": "drafts take the cheapest model that is behaving"}
```

## Metrics and routing records

Every model call, on the agent path (the graph's reasoning node) and the direct
router, is metered by provider and model once it ends:

| Metric | What it counts |
|---|---|
| `agenticorg_model_calls_total{provider,model,outcome}` | Calls by outcome (`completed`, `failed`). |
| `agenticorg_model_call_latency_seconds{provider,model}` | Latency histogram. |
| `agenticorg_model_call_tokens_total{provider,model,direction}` | Tokens (`input`, `output`, `total`). |
| `agenticorg_model_call_cost_usd_total{provider,model}` | Cost: the provider's list price where known, the platform's blended estimate otherwise. |
| `agenticorg_model_call_output_tokens_per_second{provider,model}` | Throughput histogram. |
| `agenticorg_model_call_errors_total{provider,model,error_type}` | Failures by error type. |
| `agenticorg_model_fallbacks_total{provider,from_model,to_model}` | Calls answered by a fallback model. |
| `agenticorg_model_admission_wait_seconds{provider,model}` | Time spent at admission under the per-model limits. |

With `AGENTICORG_MODEL_GATEWAY_RECORDS_ENABLED=true` (off by default: it adds
a write to every routed model call) each call made while the gateway is on for
the tenant also writes a routing record (`model_gateway_records`): the
correlation id, the use case and agent, the routing and access policies
evaluated, what was requested and what was chosen, the model it fell back
from, the outcome and error type, latency, admission wait, tokens and cost.
Each row is signed with the platform's audit key; the list endpoint reports
whether a row's signature still matches its fields. Records older than
`AGENTICORG_MODEL_GATEWAY_RECORDS_RETENTION_DAYS` (90) are pruned daily, for
every tenant including ones since deleted.

The correlation id is the request id the platform binds for every request and
propagates into its worker tasks, so one id links the request, each routing
decision, each model call and the audit rows of that request; a call outside a
request gets a fresh id. Time to first token needs streaming, which the
platform's model calls do not use yet.

## Decision rules

- The first enabled policy in priority order whose match fields all equal the
  request's decides. A request no policy matches keeps what the caller asked for
  and is logged as a pass-through.
- A caller that pinned no provider still names one through its model (a legacy
  agent row with `llm_provider` empty is matched and fenced by its model's provider).
- A request tagged `restricted`, or matched by a policy with `in_region_only`,
  may only use a provider inside the deployment (`ollama`, `vllm`, the local
  embedding and speech models) or one with an active residency attestation for
  the tenant's region (in-region processing and no training). This is checked
  whether or not residency enforcement is on. Otherwise the call is refused with
  `E1014`; it is never re-routed to an outside provider. A restricted request
  that names neither a provider nor a model is refused too.
- A policy's `allowed_providers` is a hard fence: a provider outside it refuses
  the call with the policy named, so a misconfigured policy fails loudly.
- After the route is chosen the first matching access policy decides whether
  this caller may use it (see above); then the per-model limits admit the call
  just before the model work starts.
- Policies, limits and the flag are read through a five-second shared cache and
  then the database. In a strict runtime a read failure refuses the call; a
  relaxed runtime passes the caller's choice through and logs it. A limit that
  cannot be checked admits the call and is metered as unavailable.

## Where it applies

| Call site | Use case | Notes |
|---|---|---|
| Agent runner, `run_agent` | `agent_run` | Decided before the credential prefetch; the decision stays bound for the run and each reasoning turn is admitted under the limits in the graph's reasoning node, with the slot released when the model returns. A refusal returns a run result with status `model_gateway_refused` and the error code (`E1014` or `E1015`). |
| Agent runner, `resume_agent` | `agent_resume` | Same, for a run resumed after a human decision. |
| `LLMRouter.complete` | `completion` | Workflow generation, the replanner and the other direct callers that pass a tenant. A gateway-chosen model is an explicit selection: failover stays within its provider, and the slot is held through the fallback call. This router dispatches by model family (`gemini`, `claude`, `gpt`); a policy that names another catalogue provider (`openai_compatible`, `azure_openai`) for a completion is refused rather than sent to the family's public API. |

Not yet routed through the gateway: the sidecar model calls that build a model
without a prefetched decision (explanations, SOP parsing, feedback analysis). They
keep the tenant's default model and are covered by residency enforcement.
Planned next: the console pages.

## Observing it

- Every decision is logged as `model_gateway_decision` (or `model_gateway_refused`)
  with `correlation_id`, `use_case`, `policy_id`, `provider`, `model`, `restricted`
  and `reason`.
- `agenticorg_model_gateway_decisions_total{outcome}` counts `applied`,
  `passthrough` and `refused` decisions.
- `agenticorg_model_gateway_limit_outcomes_total{limit,outcome}` counts every
  limit check by limit (`concurrency`, `rate`) and outcome (`allowed`,
  `rejected`, `unavailable`).
- A refusal names its kind (`routing`, `access` or `limit`) in the log line and
  in the error payload (`model_gateway.kind`).
- Every change to a routing policy, an access policy or a limit writes a signed
  audit row (`model_gateway_policy.*`, `model_gateway_access_policy.*`,
  `model_gateway_limit.*`, each with `set`, `update` and `delete`).

## Console

`/dashboard/settings/model-gateway` (tenant administrators) shows the gateway's
state and lets an administrator manage routing policies, access policies and
per-model limits, dry-run a described request, read the routing records (with
a correlation-id filter and the signature check) and compare costs. It uses
the endpoints below.

## API (tenant administrators)

| Method and path | Purpose |
|---|---|
| `GET /api/v1/model-gateway/status` | Whether the gateway is on and the active policies. |
| `GET /api/v1/model-gateway/policies` | List policies (`include_disabled=false` to hide disabled ones). |
| `POST /api/v1/model-gateway/policies` | Create a policy (201). |
| `PATCH /api/v1/model-gateway/policies/{id}` | Change a policy; the merged policy is re-validated. |
| `DELETE /api/v1/model-gateway/policies/{id}` | Delete a policy (204). |
| `GET /api/v1/model-gateway/access-policies` | List access policies (`include_disabled=false` to hide disabled ones). |
| `POST /api/v1/model-gateway/access-policies` | Create an access policy (201). |
| `PATCH /api/v1/model-gateway/access-policies/{id}` | Change an access policy; the merged policy is re-validated. |
| `DELETE /api/v1/model-gateway/access-policies/{id}` | Delete an access policy (204). |
| `GET /api/v1/model-gateway/limits` | List limits. |
| `POST /api/v1/model-gateway/limits` | Create a limit (201); one row per provider or per model. |
| `PATCH /api/v1/model-gateway/limits/{id}` | Change a limit. |
| `DELETE /api/v1/model-gateway/limits/{id}` | Delete a limit (204). |
| `POST /api/v1/model-gateway/evaluate` | Dry-run a described request (with `application` and `principal` for the access policies): the decision the routing and access policies would make, or the refusal, as data, whether or not the gateway is on (`enabled` says whether it currently applies). Limits are not applied and nothing is metered or logged. |
| `GET /api/v1/model-gateway/records` | Routing records, newest first; filter by `correlation_id`, `agent_id`, `outcome`, `before`; `limit` up to 1000. `signed` says the row's signature still matches. |
| `GET /api/v1/model-gateway/costs` | Every catalogue model and every model seen in the records, with its list price, blended rate and the observations over `window_hours`, cheapest first. |

## Runbook: move a business unit to one provider

1. Create the policy with `business_unit`, `provider` and `allowed_providers`.
2. `POST /api/v1/model-gateway/evaluate` with a representative request while the gateway is still off; confirm the decision.
3. Turn `model_gateway.enabled` on for the tenant; watch
   `agenticorg_model_gateway_decisions_total{outcome="applied"}` rise and the
   `model_gateway_decision` log lines name the policy.

## Runbook: cap a model's concurrency

1. `POST /api/v1/model-gateway/limits` with the provider, the model and
   `max_concurrency`.
2. Watch `agenticorg_model_gateway_limit_outcomes_total{limit="concurrency"}`:
   `rejected` rising means callers are hitting the cap and receiving `E1015`
   with a retry hint; `unavailable` means the limit store could not be reached
   and calls were admitted unchecked.
