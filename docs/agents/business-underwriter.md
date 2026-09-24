# Business Onboarding Underwriter

The Business Onboarding Underwriter (`core/agents/business_underwriter/`) investigates one business
application through a [verification provider](../providers/writing-a-verification-provider.md)
and hands off a cited `underwriting_memo` for a human decision. It never approves, declines,
closes or files anything: it holds no tool that could.

<!-- snippet: tests/unit/business_underwriter/test_underwriter_run.py#run-underwriter -->
```python
from connectors.providers.mock import MockProvider
from core.agents.business_underwriter import UnderwriterConfig, UnderwriterDependencies, run_underwriter
from core.cases.grant_authorizer import case_authorizer
from core.policy import EXAMPLES_DIR, load_policy

provider = MockProvider()
application = provider.fixture("us-missing-owner-cinderpath").application
outcome = await run_underwriter(
    tenant_id="",
    case_id="case-0001",
    application=application,
    config=UnderwriterConfig(policy=load_policy(EXAMPLES_DIR / "business_onboarding_us.yaml")),
    deps=UnderwriterDependencies(
        provider=provider,
        authorizer=case_authorizer("", "case-0001", "business_underwriter", "aml.cdd.onboarding"),
    ),
)
assert outcome.status == "completed"
memo = outcome.memo  # schema: underwriting_memo, every section cites evidence
record = outcome.case_record()  # prompt digest, policy result, every tool call with hashes
```

The empty tenant ID is a placeholder. A real run needs a tenant UUID, one active shared
`business_underwriter` registration, an allowed case purpose and a valid delegated grant;
otherwise the provider call is refused.

## What a run does

| Step | What happens | Section |
|---|---|---|
| Resolve | `resolve_business` with the declared name, jurisdiction, identifiers and address. Only a unique candidate scoring at least 0.9 is accepted; anything else is `no_registry_match`. | `identity` |
| Verify | `verify_business` starts verification (with an idempotency key unique to the case and run, so a retried call is not a second request but a re-investigation queries the provider again), then `verification_result` is polled while it returns `Pending`, honouring `retry_after_seconds` (capped by `max_poll_interval_s`) until `verification_timeout_s`, after which the section is an `error` with `provider_timeout`. | `registry` |
| Reconcile ownership | `ownership`, then [reconciliation](#ownership-reconciliation) against the declared owners. | `ownership` |
| Screen every party | `screen_business` for the business and business owners, `screen_person` for graph owners, current officers and declared owners - each party once, with the richest identifiers any source gave. | `screening` |
| Web presence | `web_presence`; each page's content goes only to the [sandboxed extractor](../security/untrusted-content.md). Only typed fields and excerpt references come back. | `web_presence`, `activity` |
| Evaluate policy | The deterministic [policy engine](../policies/authoring.md) over evidence fields computed from provider data. | `policy_result` |
| Narrate | The model writes one summary per section from statuses, finding codes, counts and policy tokens. | `narrative_summary` findings |
| Assemble and check | The memo is validated against `underwriting_memo`, and every evidence entry is checked against the records the provider actually returned in this run. Either failure fails the run. | |

A provider that does not declare a capability produces a `not_available` section with
`capability_not_supported` and a missing item; a provider error produces an `error` section with
its reason code. Neither fails the run. A provider offering only `resolve` and `verify` still
produces a complete, schema-valid memo.

## What decides and what only describes

- **Recommendation.** `approve`, `refer`, `request_information` or `decline` is computed from the
  policy tier and the missing items: `blocked` → `decline`; `high` → `refer`; otherwise any
  missing item → `request_information`; `medium` → `refer`; `low` → `approve`. It is always a
  proposal (`requires_human_decision: true`, `basis: policy_result`).
- **Findings, missing items and policy evidence** come from provider data and the application by
  fixed rules. Model output never feeds them.
- **The model** writes `narrative_summary` findings only. A summary is refused - never repaired -
  when its section has no content, a citation is not an index into that section's evidence, the
  text is empty or over 600 characters, still holds a placeholder or reference marker, or
  contains untrusted source text. Refusals are listed in the case record.
- **Model confidence** is recorded as `provenance.model_confidence`. It is metadata and gates
  nothing.

A model outage leaves the memo without summaries; the deterministic memo is complete without them.
Untrusted text about to reach the model fails the run (`untrusted_content_in_model_context`).

## Ownership reconciliation

Names are compared after Unicode normalisation, case-folding, removal of accents, punctuation and
honorifics (and legal-form words for businesses), ignoring word order. Dates of birth must agree
to the precision both sides give.

- `missing_owner` — a declared owner whose declared share is at or above
  `ownership_threshold_pct` (default 25), or unstated, and who matches no node in the graph.
- `undeclared_owner` — a node owning or controlling the subject, directly or through other nodes,
  at or above the threshold, or through a control relationship with no percentage, that matches
  no declared owner. Shares multiply along a path and the upper bound of a reported band is used.
  An interest held through a declared owner (the people behind a declared corporate shareholder)
  is treated as disclosed at that level.

## Untrusted content and personal data

The model context is built by `build_model_context` from tokens and numbers only, and
`UntrustedTextRegistry.guard_messages` checks every message before the model call. Every free-text
value from the applicant or the provider - names, aliases, associated entities, addresses,
domains, extracted website fields and excerpts - is registered before the call.

When `pseudonymisation.pre_model` is on for the tenant, the narrative call runs with a
pseudonymisation session keyed by the run id ([pseudonymisation](../security/pseudonymisation.md)).
If the flag cannot be read, the model call is not made and the memo has no summaries.

## Grants

Every provider call passes through `core/tool_gateway/provider_gateway.py`. The agent's tool set
is `resolve_business`, `verify_business`, `verification_result`, `ownership`, `screen_person`,
`screen_business` and `web_presence`; the gateway refuses to build a tool set containing anything
else. The run's grant check plugs in as `UnderwriterDependencies.authorizer`: a refusal - or an
authorizer that errors - stops the run before the provider is called, with
`failure_reason = "tool_refused:<reason>"` and no memo.

## Requests for more information

`information_request.py` turns a memo that recommends `request_information` into a proposal naming
an approved template and item codes only. The text sent to an applicant is rendered from the
template's fixed wording, and only after a human approves that exact proposal digest at a LangGraph
interrupt (`build_information_request_gate`). Agent identities (`agent:*`) cannot approve. The
shipped template is an example whose wording needs review.

## What is recorded

`UnderwritingOutcome.case_record()` holds what the evidence package needs:

- `prompt` — `prompt_id`, `version` and the SHA-256 of the exact prompt file, pinned in
  `prompts.py`; a prompt whose bytes change without a new version is refused;
- `policy_result` (the full engine result) and `policy_evidence` (its inputs);
- `tool_calls` — every call's tool, capability, outcome, reason, request and response SHA-256 and
  the upstream record identifiers it cited;
- `narrative` — accepted and refused summaries, model confidence, and whether pseudonymisation was on.

## Metrics

| Metric | Labels |
|---|---|
| `agenticorg_provider_calls_total` | `capability`, `outcome` (`ok`, `pending`, `not_available`, `denied`, `error`) |
| `agenticorg_provider_call_duration_seconds` | `capability` |
| `agenticorg_case_agent_runs_total` | `agent`, `outcome` |

Provider error rate by capability is `outcome="error"` over all outcomes for that capability.

## Tests

- `tests/unit/business_underwriter/` — runs against the mock provider in-process and over HTTP,
  graceful degradation, citation tracing, narrative validation, prompt integrity, grants,
  pseudonymisation, reconciliation, the tool gateway, information requests and a record-and-replay
  cassette.
- `tests/security/test_underwriter_adversarial.py` — hostile company names, screening aliases and
  website copy change no tool call and no policy outcome.
