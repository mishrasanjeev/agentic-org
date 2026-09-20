# Jev Integration

AgenticOrg can optionally use TypeSafe Jev as a fast semantic decision provider.
Jev receives structured state and typed questions and returns structured answers,
probabilities, and confidence. AgenticOrg remains responsible for execution,
authentication, policy, approvals, persistence, connectors, and audit.

## Boundary

```text
Agent reasoning
  -> deterministic tenant/auth/scope checks
  -> optional Jev advisory decision
  -> AgenticOrg policy and tool gateway
  -> connector or human approval
  -> audit and durable workflow state
```

Jev is not an authorization service. A Jev answer cannot approve a payment,
override a policy, bypass consent, grant a tool scope, or authorize an
irreversible external action. Commerce Passport and Grantex checks remain
authoritative for commerce actions.

## Configuration

The default mode is `off`. The provider is constructed only by an explicit
caller, so existing agent behavior does not change when the API key is absent.

```text
AGENTICORG_JEV_MODE=off|shadow|active
AGENTICORG_JEV_TIMEOUT_SECONDS=0.8
AGENTICORG_JEV_SHADOW_SAMPLE_RATE=1.0
AGENTICORG_JEV_SHADOW_MAX_CALLS_PER_PROCESS=100
AGENTICORG_JEV_SHADOW_FAILURE_THRESHOLD=3
AGENTICORG_JEV_SHADOW_COOLDOWN_SECONDS=60
TYPESAFE_API_KEY=<server-side secret>
TYPESAFE_API_BASE_URL=https://api.typesafe.ai
TYPESAFE_MODEL=jev-latest
```

The server-side key is resolved through the existing provider credential
resolver. A tenant can use an encrypted `typesafe` / `decision` credential;
otherwise the platform `TYPESAFE_API_KEY` fallback is used according to the
existing tenant fallback policy. Raw keys are never returned or logged.

## First uses

The first reviewed integrations should be advisory decisions for tool routing,
workflow branching, loop detection, output verification, and human-escalation
recommendations. They should start in shadow mode and compare Jev decisions
with existing runtime outcomes before any active behavior is enabled.

The first runtime hook is tool-routing shadow mode. After the LLM proposes tool
calls and before the existing ToolGateway runs, AgenticOrg may send Jev only
agent type, domain, action, available tool names, and proposed tool names. Jev's
route is recorded as an observation with agreement, confidence, latency, and a
bounded outcome. The existing proposal still executes through the unchanged
ToolGateway policy path; Jev cannot alter, approve, deny, or retry it.

Shadow mode is enabled only with `AGENTICORG_JEV_MODE=shadow`. `off` is the
default. `active` is reserved and intentionally behaves as inactive until a
separate policy review, evaluation corpus, and rollback plan are approved.

Shadow mode is bounded by deterministic per-process sampling, a call budget,
and a failure circuit breaker. These are cost and availability guardrails, not
authorization controls. A sampled-out, budget-exhausted, unavailable, or
circuit-open observation always leaves the existing AgenticOrg route unchanged.

## Offline evaluation

`core.decisioning.evaluation` evaluates synthetic routing cases against a
provider without executing tools. It reports baseline agreement, confidence,
p50/p95 latency, provider outcomes, token usage, and optional cost estimates
when current provider price inputs are supplied. The evaluation corpus
must contain metadata only: no tenant content, connector parameters, customer
identifiers, credentials, Commerce Passport material, or provider payloads.

The initial corpus is `evals/golden_datasets/jev_routing.json`. It covers
commerce, support, finance, HR, and operations routing with deterministic
`no_tool` versus `tool_call` baselines. The corpus is not production traffic
and must not be presented as a Jev accuracy or launch-readiness claim.

Use the offline evaluator before changing the shadow sample rate or considering
any active policy review. An evaluation report is evidence about the provider
seam, not evidence that Jev can authorize actions.

The public evaluation page at `/evals` and its read-only
`GET /api/v1/evals/jev-shadow` panel expose the evaluation plan without
calling Jev. The panel shows the synthetic corpus size, effective mode,
sampling and budget controls, and review gates. Its status is `not_run` until
an operator explicitly runs the evaluator; the API never manufactures a
scorecard and never exposes credentials or task content.

The plan endpoint is intentionally safe to load from a browser or monitoring
check. It reports `active_routing_enabled: false`, `non_executing: true`, and
keeps the existing AgenticOrg runtime as the routing authority. A future
active-mode proposal must include a measured report, rollback procedure, and
separate human approval.

### Operator runner

Use the runner locally before any pilot. Its default is a provider-free dry
run:

```text
python scripts/run_jev_shadow_evaluation.py --dry-run
```

An explicit measured run requires a server-side `TYPESAFE_API_KEY` and the
`--live` flag. The key is read from the environment and is never printed:

```text
python scripts/run_jev_shadow_evaluation.py --live \
  --output reports/jev-shadow-YYYYMMDD.json \
  --input-cost-per-million-usd <current-input-rate> \
  --output-cost-per-million-usd <current-output-rate> \
  --max-estimated-cost-usd <approved-cap>
```

The command exits `0` only when agreement, provider failure, latency, and any
configured cost gates pass. It exits `1` for a measured gate failure and `2`
when configuration or credentials are refused. The report contains only case
IDs, route labels, aggregate metrics, usage, and gate results. It cannot
execute tools or enable active routing.

Jev should not be used for prose generation, OCR, STT/TTS, authentication,
tenant isolation, consent, payment authorization, provider credentials, or
final Grantex policy decisions.

## Rollout gates

Before enabling a decision in an active workflow, record p50/p95 latency, cost,
provider error rate, fallback rate, agreement with existing outcomes, unsafe
continuation rate, false escalation rate, and drift by tenant/workflow. Jev
timeouts, invalid responses, low confidence, and unavailable credentials must
fall back to the existing deterministic behavior or human review according to
the workflow's risk policy.
