## Decide the job before choosing a template

Write down the input, permitted output, source of truth, authorized tools and escalation owner. A useful job is specific: `prepare a complaint-routing summary from the current service procedure`, not `handle all banking operations`. Decide what the agent must refuse.

![Illustrative AgenticOrg agent fleet](/screenshots/agents.webp)

This existing product screenshot illustrates the fleet layout. Names, totals and confidence settings are sample values, not current registry totals or performance guarantees.

## Three starting points

Use a natural-language description to generate a candidate, configure an agent manually, or use **Create from SOP** where your role allows it. Generated prompts and SOP drafts are proposals. Read every field before creation; do not assume a parser knows your approval policy or connector permissions.

## Walk through the wizard

| Stage | What to enter | What to check |
| --- | --- | --- |
| Persona | Employee Name, designation, domain, optional avatar | Name the actual task; use approved image URLs |
| Role | Agent type/custom type, specialization, routing filters, reporting relationship | Match the business role; filters do not create access rights |
| Prompt | Reviewed template or explicit instructions | Cite sources, state missing evidence, define refusals and output structure |
| Behavior | Model/provider, routing, confidence floor, HITL condition, retries, connectors and Authorized Tools | Grant minimum access; set finite retries; review external side effects |
| Review | Full configuration and visibility | Company, tools, prompt, credentials and initial shadow status |

Personal and shared visibility depend on your role. Administrators can configure shared tenant agents; a personal agent is not exempt from tenant policy. A reporting hierarchy is for routing, not permission inheritance.

For a reasoning-only agent, clear **Use suggested tools when none selected** and
leave Authorized Tools and connectors empty. The API equivalent is
`use_default_tools: false`, `authorized_tools: []`, `connector_ids: []`.
This does not disable tenant policy, budget checks or review requirements.
Zero retries is supported when you want a failed attempt to stop without an
agent-level retry. An agent's monthly budget is not a per-call provider price
guarantee; bound the model, context, output and number of calls separately for
a cost-capped acceptance test.

Provider billing, account eligibility and quotas still apply, including to
economy models. Check current provider pricing rather than assuming a routing
tier is free. Zero agent retries does not disable a provider SDK's transport
retries. Explanation generation can make a separate model request, so include
that request and its retry/output limits in a test's total spending allowance.

For a small reasoning check, use a personal shadow agent, a pinned model,
**Disabled - Use selected model** routing, no tools/connectors, zero retries and
no voice. Ask a synthetic arithmetic question with an independently checked
answer. Return structured JSON such as `{"answer": 8, "confidence": 0.95}`.
Do not lower a review threshold just to obtain a completed result. A confidence
written inside prose is not a structured confidence field; a correct short
answer can still require review. A self-reported score is not measured accuracy.

## A prompt structure that operators can maintain

```text
Purpose: prepare a reviewable summary of the submitted service request.
Inputs: request reference, company context and approved source documents.
Use: only tools and knowledge made available to this agent.
Output: facts with sources, missing items, suggested next step, review required.
Refuse: account changes, payment execution, invented evidence and secret disclosure.
Escalate: ambiguous identity, conflicting sources, missing permission or policy.
```

Do not insert credentials, personal account numbers or legal guarantees into prompts. A prompt version is part of the evidence for a run; changing it changes the tested behavior.

## Create, test and maintain

1. Choose **Create as Shadow** after reviewing the configuration.
2. Run representative correct, incomplete, conflicting and adversarial inputs.
3. Inspect actual tool calls and source-grounding, not only prose.
4. Compare the result against a human-reviewed answer set.
5. Have the authorized owner decide whether to activate/promote the candidate.
6. Retest after model, prompt, procedure, tool or provider changes.

Inspect the detail screen's supported lifecycle actions before pausing or promoting an agent. An operator override or paused state must be accompanied by a check of workflows and integrations that call it; changing an agent does not necessarily cancel work already accepted elsewhere.

## Common mistakes

Too many tools can lead to unintended actions. Broad prompts make outputs hard to assess. Confidence thresholds are not calibrated accuracy claims. Shadow labels alone are not permission controls. Add capability only when you can explain and test its necessity.

Next: [Run and chat](/docs/run-and-chat), [Workflows](/docs/workflows), [Evaluate and roll out](/docs/evaluate-and-rollout).
