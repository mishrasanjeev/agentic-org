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

A completed status and high confidence do not establish answer accuracy. For a
numeric task, check the operands and calculation independently. Pre-model PII
protection can replace sensitive values with placeholders; do not ask the agent
to guess a hidden operand or turn protection off. Explicit counts such as
`7, 11 and 13 items` must remain quantities, while dates and identifiers remain
protected. Report an incorrect result with its request reference, without
copying personal data into a support ticket.

For an agent using `gemini-3.5-flash-lite`, the optional per-agent thinking setting
is described in [Model and credential setup](/docs/model-setup). Test the same
request before and after a reviewed configuration change; extra thinking and
model-reported confidence are not substitutes for checking the answer.

The **Why did the agent do this?** panel reads stored evidence, without making a
new model request. Operators can enable direct-run audit history with
`AGENTICORG_AGENT_EXPLANATION_AUDIT_ENABLED=true` (default off). With it enabled,
a direct **Run Agent** result is visible after refresh as well as results from
the durable task store; the newest record wins. Legacy audit records may lack
tool-call details. The panel reports that absence rather than inventing a
successful tool call or claiming no tools ran. Review decisions remain in
**Approvals**; a historical review trigger is not a current approval decision.

## Follow-ups and retries

Chat passes earlier turns to a follow-up only when the deployment turns on conversational services (`AGENTICORG_CONVERSATION_V2_ENABLED`, off by default). While it is off, each message runs on its own even though the panel shows the earlier messages, so repeat the document, case and company reference every time. While it is on, recent turns from the same user, company and agent reach the agent as context; another user, company or agent does not share them. Do not ask an agent to "ignore the earlier policy" to unblock a refusal.

Before retrying an external action, check whether the provider already accepted it. Use the integration's idempotency and status lookup where available. Blindly rerunning a workflow can duplicate an email, record update or provider request.

## Prove the path before inviting users

Test a permitted question, a missing-source question, a cross-company reference and a forbidden action. Confirm that an access or model failure is visible, not represented as an empty successful result. For voice, validate the same task boundary through signed speech turns; for commerce, verify artifact freshness and prepared-only semantics.

Next: [Troubleshooting](/docs/troubleshooting), [Approvals](/docs/approvals), [Audit and monitoring](/docs/audit-and-monitoring).
