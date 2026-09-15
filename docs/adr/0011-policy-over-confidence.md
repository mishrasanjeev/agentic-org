# ADR 0011: Gate case recommendations on a deterministic policy, not model confidence

- **Status**: Accepted
- **Date**: 2026-09-15
- **Deciders**: Sanjeev, Engineering team

## Context

Governed case work — business onboarding, screening disposition — ends in a
recommendation that a human approves: proceed, request more information,
escalate, or stop. Something has to decide how risky a case is and therefore
what the recommendation is and how much scrutiny the approval needs.

The agent runtime already computes a `confidence` for every run
(`core/langgraph/agent_graph.py`): the model's self-reported number or label,
blended with tool success signals. Below `confidence_floor` a run is routed to
human review. That is a reasonable heuristic for general task agents, and it is
the obvious thing to reuse. It is the wrong thing to gate a regulated
recommendation on:

- **It is not calibrated.** A self-reported confidence of 0.9 means different
  things from different models, from the same model after a provider update,
  and from the same prompt with different tool output. Nothing ties it to an
  error rate.
- **It cannot be explained or re-derived.** An auditor asking why a case was
  rated low risk gets "the model said 0.93". The same case evaluated tomorrow
  may produce 0.88.
- **It is attacker-influenced.** The model reads applicant-supplied material.
  Text that talks the model into a confident answer moves the gate.
- **It will not survive model-risk review.** A control whose threshold moves
  whenever the model changes is not a control.

Options considered:

- **Confidence floor, tuned per model.** Keeps the existing mechanism, but
  every model change becomes a recalibration exercise and the result is still
  not explainable per case.
- **A second model as judge.** Adds cost and a second uncalibrated number.
- **Deterministic rules over structured evidence.** Rules a compliance owner
  can read, version and sign off, evaluated over fields that came from
  providers (not from model free text), with an explicit, recorded reason for
  every outcome.

## Decision

Case recommendations are gated by a deterministic policy result. Model
confidence is recorded as metadata and is never a gate.

- Policies are versioned YAML files evaluated by `core/policy/` over a plain
  nested mapping of the case's evidence fields. The result is a tier
  (`low` < `medium` < `high` < `blocked`), a score and ordered reasons, each
  naming the rule that fired. See `docs/policies/authoring.md`.
- The engine does not import or call any model, network, database or LangChain
  code; tests forbid the model factories and the network and check the
  package's imports.
- Identical policy and evidence produce an identical result, across runs,
  processes and hash seeds (a property test over 1,000 generated cases per
  policy). The result carries no timestamp.
- Every policy problem is found when the file is loaded, with a reason code;
  evaluation cannot fail on policy shape. Missing or malformed evidence moves a
  case towards the stricter tier and is flagged `indeterminate`, never silently
  passed.
- Every result records the policy id, version, status, reviewer, the SHA-256
  of the policy file, the engine version, every input read and a hash of those
  inputs, so the memo and the evidence package can show exactly which rules
  fired over which values and the outcome can be re-derived.
- Shipped policies are examples. A policy can only be marked `production` with
  a named `reviewed_by`; loading an example logs a warning every time; a
  deployment can refuse examples outright with `require_production=True`.
- Model confidence may appear in the memo and the evidence package as metadata
  alongside the policy result. No governed workflow may branch on it.

### Relationship to `core/approvals/policy_engine.py`

The approvals module is also called a policy engine, and the two are
complementary, not alternatives:

| | `core/policy/` (this ADR) | `core/approvals/policy_engine.py` |
|---|---|---|
| Question answered | How risky is this case, and why? | Who must approve, in what order, with what quorum? |
| Input | Case evidence fields | An approval request, its context and the votes cast |
| Definition | Versioned YAML files, reviewed and hashed | `approval_policies` / `approval_steps` rows per tenant |
| Output | Tier, score, ordered reasons | Next step: advance, collect, reject or complete |
| Model involvement | None | None |

A workflow evaluates the case policy first; its tier and reasons are part of
what the approver sees, and a workflow may use the tier as context when an
approval policy chooses its steps. The approvals engine is unchanged by this
decision.

## Consequences

- Recommendations are explainable and reproducible from the evidence package
  alone, and a policy change is a reviewed, versioned diff rather than a model
  swap.
- Policies only see what providers and extractors put into structured evidence
  fields. A risk the rules do not describe is not caught by the policy; the
  human approval remains the backstop, and rules must be maintained by the
  compliance owner as the programme changes.
- Failing towards the stricter tier on missing evidence will send some cases to
  a more senior review than a complete record would. That is the intended cost:
  the reasons say which evidence was missing.
- The existing confidence floor keeps routing general task agents to review. It
  is not removed by this decision, and it must not be used as the gate for
  governed case recommendations.
- Changing evaluation semantics (operators, missing-evidence handling, scoring,
  reason order) requires bumping `ENGINE_VERSION`, because past results cite it.
