# Runtime guardrails: engine and policy

Guardrails are configurable checks on the text that flows through a model or
tool call. A tenant administrator writes rules; each rule names a stage of the
call, a detector, and what happens when the detector finds something. The
engine (`core/governance/guardrails`) evaluates every matching rule in priority
order and records the outcome with the request's correlation id, so a guardrail
outcome, the model call it guarded and the audit row share one id.

Off by default. With `guardrails.enforce` off for the tenant (the authority
flag, operator managed; `AGENTICORG_GUARDRAILS_ENFORCE=true` for the whole
deployment) every rule runs in **flag-only mode**: findings are metered and
logged, the text travels on unchanged and nothing is blocked. With it on,
`mask`, `redact` and `tokenise` transform the text before it travels on,
`block` refuses the stage with `E1016`, and each applied action writes a
signed audit row. Flag-only mode is how a deployment sees what a rule set
would do before it bites.

The hooks are behind `AGENTICORG_GUARDRAILS_HOOKS_ENABLED` (off by default):
on, every stage is evaluated at its call site, in flag-only mode until
`guardrails.enforce` is on for the tenant; off, the hooks return what they
were given and read nothing.

## Where the stages apply

| Stage | Call site | What passes | A transform | A block |
|---|---|---|---|---|
| `input` | the agent graph's reasoning node, before every model call | the newest human or tool message of the turn | replaces that message's content | ends the run with status `guardrail_blocked` and `E1016` |
| `output` | the reasoning node, after every model call | the model's answer (tool calls untouched) | replaces the answer's content | ends the run with `guardrail_blocked` |
| `retrieval` | the knowledge search results; the governed case's rendered evidence before its model call | each retrieved chunk; the whole evidence context | replaces the chunk or context | withholds the chunk (the search returns the rest); skips the case's model call with failure `guardrail_blocked` |
| `action` | the connector dispatch boundary, before any provider side effect | the connector, tool and JSON arguments | not allowed: an action rule flags or blocks | the tool returns `{"error": "guardrail_blocked"}` and the step fails |

Each hook attributes the outcome to the run's routing decision when the caller
does not name the tenant, agent and use case, so a guardrail outcome, the model
call it guarded and the audit row share one correlation id.

## A rule

| Field | Meaning |
|---|---|
| `name`, `priority`, `enabled` | Enabled rules matching a stage are evaluated in ascending `priority` (then name); every matching rule applies. |
| `stage` | `input` (what goes to the model), `retrieval` (documents retrieved into the context), `output` (what the model returned), `action` (a tool call's arguments). |
| `detector` | `sensitive_data`: the platform's PII analyser where installed, its regex recognisers otherwise, plus a Luhn-checked card-number check; `options.entities` narrows the kinds (`CREDIT_CARD`, `AADHAAR`, `PAN`, `GSTIN`, `EMAIL`, `UPI`, `PHONE`). `toxicity`: the content-safety classifier with its keyword fallback. `pattern`: `options.patterns`, the administrator's own regular expressions (`options.kind` names the finding, `options.ignore_case` defaults to true). `injection`: the phrasings by which a text tries to take over the model (instruction overrides, system-prompt disclosure, persona switches, jailbreak markers, fake system blocks, standing orders, false authority) and invisible characters, each with its own confidence; `options.patterns` adds the administrator's own; direct in a message and indirect inside a retrieved document alike. `output_policy` (output stage; flag or block): `max_length`, `require_json`, `required_keys`, `forbidden_phrases`, `no_urls`. `grounding` (output stage; flag or block): the answer's claims held against the context the run retrieved, see [Grounding](#grounding). |
| `action` | `flag` records the finding. `mask` replaces each span with asterisks, `redact` with `<KIND>`, `tokenise` (sensitive data only) with a reversible `<KIND_n>` token whose original is returned in the result's token map. `block` refuses the stage. |
| `threshold` | A rule applies when the detector's best score is at or above it (0 to 1; sensitive-data and pattern findings score 1, toxicity scores the classifier's confidence). |
| `agent_id`, `use_case`, `risk_tier` | Narrow the rule; empty applies to every call at the stage. `risk_tier` is `low`, `medium`, `high` or `critical`. |
| `reason` | Why the rule exists; recorded in the audit row. |

Example: card numbers never leave the platform in a model's answer, a
drafting agent's output must not mention a competitor's product names, a
retrieved document that tries to instruct the model is withheld, and a
tool call carrying a card number is refused.

```json
{"name": "cards-out", "stage": "output", "detector": "sensitive_data", "action": "redact",
 "options": {"entities": ["CREDIT_CARD"]}, "reason": "card numbers are redacted before delivery"}
{"name": "no-competitor-names", "stage": "output", "detector": "pattern", "action": "block",
 "agent_id": "a2f4...", "options": {"patterns": ["\\bAcmePay\\b", "\\bZetaCard\\b"], "kind": "competitor"},
 "reason": "drafts must not name competitor products"}
{"name": "no-injected-documents", "stage": "retrieval", "detector": "injection", "action": "block", "threshold": 0.7,
 "reason": "a document that instructs the model is withheld"}
{"name": "no-cards-in-tool-calls", "stage": "action", "detector": "sensitive_data", "action": "block",
 "options": {"entities": ["CREDIT_CARD"]}, "reason": "card numbers never reach a connector"}
```

## Evaluation

- `evaluate(stage, text, tenant_id=..., agent_id=..., use_case=..., risk_tier=...)`
  returns the text to travel on, whether the stage may continue, whether the
  rules were enforced, and one outcome per rule that applied (detector,
  action, number and kinds of findings, score, whether the action was applied,
  whether it blocked or transformed).
- Every matching rule applies; transforms compose in priority order (a later
  rule sees the earlier rule's text); a block wins over a transform.
- Each detector runs off the event loop under a time budget
  (`AGENTICORG_GUARDRAILS_DETECTOR_TIMEOUT_SECONDS`, 2 seconds). A detector
  that fails, runs out of time or is unknown cannot say what it would have
  found: with enforcement on in a strict runtime, a rule that would transform
  or block then refuses the stage rather than letting the text through
  unchecked; otherwise the failure is logged and the rule records no outcome.
- A pattern rule's expressions are refused when they are longer than 512
  characters, nest or repeat unbounded quantifiers (`(a+)+`), or use
  backreferences, the shapes that backtrack catastrophically; a rule takes at
  most 32 patterns and scans at most
  `AGENTICORG_GUARDRAILS_PATTERN_MAX_CHARS` (50,000) characters.
- A rule's `options` must belong to its detector (`entities` for
  `sensitive_data`, `patterns`, `kind` and `ignore_case` for `pattern`, none
  for `toxicity`), and `entities` must be a list of the supported kinds.
- Rules and the flag are read through a five-second shared cache and then the
  database. In a strict runtime an unreadable rule set or flag refuses the
  stage; a relaxed runtime lets the text through unguarded and logs it.
- A dry run (`POST /api/v1/guardrails/evaluate`) applies transforms to the
  returned text and reports a block as `allowed: false`, and meters, logs and
  audits nothing.

## Observing it

- `agenticorg_guardrail_outcomes_total{stage,detector,action,mode}` counts
  every outcome by mode (`flag_only`, `enforced`).
- `GET /compliance/evidence-package` carries a `guardrails` section: whether
  the hooks are on and the tenant enforces, the rules in effect by stage, and
  the blocked and transformed outcomes of the last thirty days.
- Every outcome is logged as `guardrail_outcome` with the correlation id, the
  rule, the detector, the action, the finding count and kinds, and whether the
  action was applied.
- With enforcement on, an applied block or transform writes a signed audit row
  (`guardrail.outcome`, actor `guardrails`, outcome `blocked` or
  `transformed`, the rule as the resource, the correlation id as the trace id).
- Every rule change writes a signed audit row (`guardrail_rule.set`,
  `.update`, `.delete`).

## API (tenant administrators)

| Method and path | Purpose |
|---|---|
| `GET /api/v1/guardrails/status` | Whether guardrails enforce for the tenant, the mode, whether the hooks are on in the deployment, the stages, detectors, actions and risk tiers a rule may name, and the active rules. |
| `GET /api/v1/guardrails/rules` | List rules (`include_disabled=false` to hide disabled ones). |
| `POST /api/v1/guardrails/rules` | Create a rule (201). |
| `PATCH /api/v1/guardrails/rules/{id}` | Change a rule; the merged rule is re-validated. |
| `DELETE /api/v1/guardrails/rules/{id}` | Delete a rule (204). |
| `POST /api/v1/guardrails/evaluate` | Dry-run a stage over a text. For a grounding rule, `context` (retrieved texts) and `user_input` are what the text is held against. |

## Console

Tenant administrators manage rules at `/dashboard/settings/guardrails`
(`ui/src/pages/Guardrails.tsx`). The page says which mode applies (hooks off,
flag-only or enforcing), lists the rules with what each applies to, adds and
changes a rule (the options are edited as JSON, starting from an example for
the chosen detector), enables, disables and deletes one, and dry-runs a stage
over a text, showing what each rule would do and the text as the rules would
leave it. The dry run names the call it stands for (agent, use case, risk
tier), because a rule narrowed to one of those takes part only when the dry
run names the same one, and for a grounding rule it takes the retrieved
context and what the user wrote. The live mode shown beside the result comes
from the status, not from the dry run. Every action goes
through the endpoints above, so the same validation, attribution and signed
audit rows apply. The page changes rules only; turning enforcement on stays
with the `guardrails.enforce` flag.

## Grounding

A `grounding` rule checks, at the output stage, whether each claim of the
answer is supported by what the run retrieved
(`core/governance/guardrails/grounding.py`). The context is what the run
retrieved: the conversation's tool results (a knowledge search, a connector
read) and the evidence a governed case hands its model. Unless the rule sets
`include_user_input` to false, what the user wrote adds to the words a claim
may draw on, but it never counts as retrieved context: a run that retrieved
nothing has no context, whatever the user said.

The check is deterministic and lexical: no model call, the same verdict for
the same texts, and it runs inside the detector time budget.

1. The answer is split into sentences. A question, or a sentence with fewer
   than `min_claim_words` content words (default 4), is not a claim.
2. A claim's support is the share of its content words (stopwords dropped,
   plurals folded, figures normalised so `5,000.00` and `5000` are one figure)
   that occur in the context.
3. A claim with support below `min_support` (default 0.5) is an
   `unsupported_claim` with a score of one minus its support.
4. A figure in a claim that occurs nowhere in the context is an
   `unsupported_number` with a score of at least 0.9, whatever the claim's
   support: an invented amount, rate or date is reported even when every
   other word matches.

The rule's `threshold` is how unsupported a claim must be for the rule to
apply; `flag` records it and `block` suppresses the answer (the run ends
`guardrail_blocked`) when `guardrails.enforce` is on. A grounding rule never
rewrites an answer.

With nothing retrieved in the run the rule is silent, because there is
nothing to hold the answer against. Set `require_context` to true for an
agent that must answer only from retrieved material: an answer given with
nothing retrieved is then reported as `no_context`, even when it only repeats
what the user stated.

```json
{"name": "answers-from-the-policy", "stage": "output", "detector": "grounding", "action": "block",
 "agent_id": "policy-assistant", "threshold": 0.5,
 "options": {"min_support": 0.5, "require_context": true},
 "reason": "the policy assistant answers only from retrieved policy text"}
```

What it is not. It does not judge meaning: a claim that reuses the context's
words to say the opposite passes, and a faithful paraphrase in other words is
flagged. It is a floor that catches answers written without the context and
figures that were never retrieved; model-graded faithfulness belongs to the
evaluation framework. Start in flag-only mode, read the outcomes
(`kinds` carries `unsupported_claim`, `unsupported_number`, `no_context`) and
tune `min_support` per agent before blocking.

Findings and outcomes carry positions, kinds and a support figure; never the
answer or the context.

## Runbook: redact card numbers in every answer

1. Create the `cards-out` rule above.
2. `POST /api/v1/guardrails/evaluate` with `stage: output` and a text holding a
   test card number; confirm the returned text carries `<CREDIT_CARD>`.
3. Watch `agenticorg_guardrail_outcomes_total{action="redact",mode="flag_only"}`
   in production for a day: every count is an answer that would have been
   redacted.
4. Turn `guardrails.enforce` on for the tenant; the counts move to
   `mode="enforced"` and each redaction writes an audit row.
