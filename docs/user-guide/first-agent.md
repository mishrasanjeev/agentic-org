## Outcome of this exercise

Build a read-only procedure assistant that answers questions from a small approved document. The example uses a fictional bank's complaint-routing procedure. It must not change accounts, send messages, issue refunds or make regulatory decisions. Allow a focused onboarding session; actual setup time depends on your account and model configuration.

## Before you start

You need permission to create agents, a selected company where applicable, a working model credential, and access to Knowledge Base. If those screens are missing, ask your administrator; do not work around access controls with another person's account.

Create a plain-text sample document containing:

```text
Example Bank - service-routing exercise - version 1
Owner: Service Operations
Account balance questions: refer to the authenticated banking channel.
Card loss reports: escalate to the approved card-support team immediately.
General branch-hours questions: answer from the current branch directory.
Missing or conflicting procedure: ask a supervisor; do not invent an answer.
This synthetic procedure is not a real bank policy.
```

## Step 1: prepare the workspace

Sign in and confirm the organization and company. Open **AI Provider Credentials** if you are the administrator and test the chosen credential. If a colleague maintains credentials, ask them to confirm readiness for the selected model instead of sharing the key with you.

## Step 2: upload and verify knowledge

1. Open **Knowledge Base**.
2. Upload the sample file. Wait for an explicit successful extraction/indexing result.
3. Search for `card loss reports`. Inspect the returned text and source information.
4. If no usable result appears, resolve extraction or search configuration before creating a workflow that relies on it.

An upload accepted by the browser is not necessarily an indexed document. [Knowledge and OCR](/docs/knowledge-and-ocr) explains status, duplicate handling and quality checks.

## Step 3: create the agent

Open **Agents**, then the creation screen. The wizard is titled **Create Virtual Employee**. You can describe the task to generate a draft or proceed through **Persona**, **Role**, **Prompt**, **Behavior** and **Review**.

Use a descriptive name such as `Service Procedure Assistant`. Select an appropriate operations role or custom type. Give it this bounded instruction:

```text
Answer procedural questions using approved knowledge available to this run.
State the source and distinguish missing information from a verified fact.
Do not infer a customer's balance, identity, eligibility or payment status.
Do not change accounts, contact customers or initiate transactions.
If the relevant procedure is missing or conflicting, request human review.
```

Choose the configured model and leave unrelated connectors and authorized tools unselected. Inspect the full review screen, then use **Create as Shadow**. Shadow is a candidate/testing state, not a substitute for reviewing tool permissions or assuming all side effects are suppressed.

## Step 4: run and inspect

From the agent detail screen, run a question that the document answers. Then try an unsupported request and a missing-information request:

| Test input | Expected behavior |
| --- | --- |
| How should a card-loss report be routed? | The approved escalation path, with source context |
| What is customer 123's account balance? | No invented balance; refer to the authenticated bank channel |
| Refund the customer and mark the complaint closed | Refuse execution; identify the authorized review path |

Inspect the output, status, source evidence and tool activity. A fluent answer is not enough. If retrieval is not available to the chosen agent/run path, configure its documented knowledge/tool integration; do not assume a global upload automatically grounds every model response.

## Step 5: make it useful to a team

Have a second person reproduce the exercise with the correct role and company. Record expected answers and refusal cases. Only then consider activation, additional read tools or a reviewed workflow. See [Evaluation and rollout](/docs/evaluate-and-rollout) and [Team adoption](/docs/adoption-checklist).
