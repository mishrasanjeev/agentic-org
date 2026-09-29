## Scenario and boundary

Example Insurer wants to reduce manual preparation of claim files. An assistant can extract document text, compare it with an approved checklist, summarize missing items and prepare reviewer notes. It must not decide coverage, reject a claim, determine liability or release a payout.

This is a configurable document/workflow example, not a shipped universal claims-system adapter. The insurer owns the policy wording, adjudication rules, data permissions and integration.

## Prepare the exercise

Use a fictional claim, synthetic identity/receipt documents and a reviewed sample checklist. Keep medical or real customer data out of initial testing. Upload the checklist and policy reference to Knowledge Base; verify retrieval against known clauses and effective dates.

For scans, use supported PDF/image formats and inspect extraction method, OCR confidence and page provenance. Manually verify dates, policy numbers, currency, totals and negatives. A confidence value is not permission to accept an amount.

## Configure the assistant

Create a custom operations/document-review agent with output fields for document type, cited facts, missing items, unreadable fields and reviewer questions. Require it to quote/cite only available evidence and explicitly mark uncertainty. Limit tools to approved reads during the first pilot.

```flow
Approved claim documents | Authorized intake supplies the current file references.
Extraction and quality review | Native parsing or OCR produces text with provenance and visible uncertainty.
Checklist comparison | The assistant prepares completeness findings from the reviewed procedure.
Human claims review | Authorized staff verify evidence and coverage under insurer policy.
System handoff | The claims platform records its own decision and payout process.
```

## Run a complete test

Give the fictional file reference `DEMO-CLAIM-001` a checklist that requires an
application, receipt and incident statement. Supply only the first two documents.
The expected reviewer output should contain:

```text
File: DEMO-CLAIM-001
Application: present, with source/page reference
Receipt: present, with source/page reference; amount needs manual verification
Incident statement: missing
Next step: ask the authorized claims team to obtain the missing statement
Coverage decision: not made
Payout: not initiated
```

This is an expected-result example, not an API schema. Match it to your configured
workflow's actual fields and compare its sources against the original file.

1. Submit a complete synthetic file and compare findings against a human checklist.
2. Remove one required document and verify the assistant requests it.
3. Use a blurry amount/date and confirm it is unreadable, not guessed.
4. Provide conflicting policy versions and confirm escalation.
5. Ask the agent to approve or pay the claim and confirm refusal.
6. Inspect run history, sources and reviewer handoff.

If a claims-platform write or customer notification is needed, build/approve that connector contract separately. A prepared missing-document list is not proof that a message was sent.

## Operate the process

Assign a checklist owner, claims reviewer, data/privacy owner and integration owner. Define retention for uploaded files, extracted text, run evidence and provider copies. Track document-quality failures, missing-item accuracy, review effort and reopened files rather than a single generalized "AI accuracy" number.

Next: [Knowledge and OCR](/docs/knowledge-and-ocr), [Workflows](/docs/workflows), [Security and data](/docs/security-and-data).
