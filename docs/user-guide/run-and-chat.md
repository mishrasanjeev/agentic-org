## Choose the right entry point

Use the agent detail screen for a targeted run, the authenticated chat panel for conversational tasks, or a reviewed workflow for a repeatable multi-step process. The public **Playground** is illustrative sample content; it is not a production customer transaction.

Some roles cannot execute chat or agent runs even if they can inspect evidence. If the control is absent, check permissions with your administrator rather than using a colleague's credentials.

## Supply an actionable request

Include the task, authorized company, input references, expected output and prohibited side effects. Prefer document/case references over pasting unnecessary personal data.

```text
For the selected company, summarize the uploaded synthetic complaint file.
Use the approved complaint procedure, cite its source and list missing items.
Prepare a response draft for review. Do not email it or change account state.
```

Check company context before sending. A request that names another company in text does not authorize the agent to access it. If the tool needs structured inputs, use the expected schema; free text cannot replace required fields.

## Inspect the result

Read the run status and output together. Look for source references, missing facts, connector/tool outcomes, escalation and safe error information. A model saying `done` is not proof that a provider accepted an operation.

| Result | What it means | What to do |
| --- | --- | --- |
| Prepared/draft output | Material is ready for review | Check evidence before distribution |
| Awaiting approval | A human decision is needed | Open the appropriate review queue |
| Refused | Authority, safety or supported capability is missing | Resolve the reason; do not weaken policy to finish |
| Failed | The run could not complete | Check logs/status and retry only if safe |
| External action confirmation | A connected system reports an outcome | Validate identity, amount/scope and authoritative reference |

The exact status vocabulary varies by run resource. Preserve the returned state rather than translating every non-error response into success.

## Follow-ups and retries

A conversational follow-up should retain relevant context, but never assume a different browser session or company automatically shares it. Repeat the input reference when ambiguity matters. Do not ask an agent to "ignore the earlier policy" to unblock a refusal.

Before retrying an external action, check whether the provider already accepted it. Use the integration's idempotency and status lookup where available. Blindly rerunning a workflow can duplicate an email, record update or provider request.

## Prove the path before inviting users

Test a permitted question, a missing-source question, a cross-company reference and a forbidden action. Confirm that an access or model failure is visible, not represented as an empty successful result. For voice, validate the same task boundary through signed speech turns; for commerce, verify artifact freshness and prepared-only semantics.

Next: [Troubleshooting](/docs/troubleshooting), [Approvals](/docs/approvals), [Audit and monitoring](/docs/audit-and-monitoring).
