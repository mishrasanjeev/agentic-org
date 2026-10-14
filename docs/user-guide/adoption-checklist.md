## Choose the first useful problem

Select one repeatable task with a measurable outcome, manageable data and a human owner. Good first tasks include procedure Q&A, document completeness review, reconciliation exception preparation and complaint triage. Avoid a first rollout that simultaneously requires new voice, payments, core banking and marketplace integrations.

This is a rollout checklist, not an instruction to remain in an endless audit phase. Build the actual authorized path, test it with representative evidence and train the people who will use it.

## Assign accountable owners

| Owner | Responsibility |
| --- | --- |
| Business sponsor | Outcome, budget and scope |
| Process owner | Current procedure, expected output and exceptions |
| Administrator | Workspace, roles, models and configuration |
| Integration owner | Provider contract, credentials, tool behavior and recovery |
| Reviewer | Human approvals, coverage and escalation |
| Security/data owner | Permitted information, retention and incident handling |
| Operator/support owner | Daily monitoring, user help and rollback |

## Before inviting the team

1. Complete [Your first agent](/docs/first-agent) with synthetic data.
2. Verify every required knowledge/model/connector path separately.
3. Test normal, missing, conflicting, stale and forbidden requests.
4. Confirm a non-admin operator can reproduce the exact task.
5. Confirm the reviewer can find evidence and make only authorized decisions.
6. Inspect external system confirmations separately from prepared outputs.
7. Document failure recovery, support contact and a pause/rollback trigger.

## Training session

Walk users through sign-in, company selection, task input, source inspection, run status and escalation. Show one successful task and one intentional refusal. Train reviewers to check evidence, not trust fluent wording. Let each user reproduce the path with their own role.

Use the BFSI playbooks as teaching examples, then replace fictional inputs and procedures only after the institution approves real data and integrations. Bank-specific connectivity and regulated authority must remain explicit.

## Follow one complete pilot

```flow
Choose the task | Pick document Q&A, invoice review, a service case or a seller catalogue question. Record the current process and expected answer. | Owner: Business sponsor | If blocked: Do not begin with an unbounded promise to automate everything.
Connect approved sources | Upload reviewed knowledge and configure the model and required read-only tools using your own provider credentials. | Owner: Administrator and integration owner | If blocked: Missing credentials or unsupported tools are configuration gaps, not successful integrations.
Run and inspect | Send a representative request, inspect citations and task status, and repeat with missing, stale and conflicting inputs. | Owner: Process owner | If blocked: An unsupported answer or partial run is reviewed, not described as completed work.
Review the handoff | Check the prepared output and any required human approval. Verify external writes or messages in their owning system. | Owner: Authorized reviewer | Human decision: Approve only the scoped action supported by evidence. | If blocked: A draft, catalogue artifact or approval packet is not a posted invoice, completed order or executed payment.
Measure and expand | Compare the same cases against the baseline, train users and add the next authorized capability only when its dependency works. | Owner: Sponsor and support owner | If blocked: Pause on unacceptable error, cost, latency or reopened cases; retain a recovery path.
```

For banks, use [Customer service](/docs/bfsi-customer-service) and
[Capability status](/docs/bfsi-capability-status). For fintech/platform teams,
use [API, SDK and MCP](/docs/api-sdk-mcp). For merchants, follow the
[External buyer journey](/docs/seller-a2a-commerce-journey).

## Operating across countries

Agree on data location, retention, access, supported language, provider coverage,
currency and applicable approval requirements for the actual deployment. A
deployment option or provider connector does not establish residency, regulatory
approval or support in every jurisdiction. Start with approved samples; never put
customer records, API keys or passwords in a public demo enquiry.

Discuss a specific pilot at [sanjeev@orchestrum.in](mailto:sanjeev@orchestrum.in).
Describe the task, systems and expected outcome, not confidential evidence.

## Enquiries and external messages

A demo enquiry acknowledgement means the request was received, not that email
arrived or a sales agent completed its work. SMTP acceptance, actual inbox
arrival and a reply are separate outcomes. Staff should inspect recorded delivery
states and confirm receipt in the receiving system before marking a messaging
pilot complete. Failed or uncertain sends need review, not repeated submissions.

Operators can use the [marketing delivery runbook](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/runbooks/marketing-lead-delivery.md)
for mailbox setup, protected credentials, durable worker processing and spend
controls. Customer operational messaging and tenant workflows keep their own
recipients and credentials.

## Pilot scorecard

Track task correctness, source coverage, review effort, queue age, external-action failures, latency, cost and reopened errors. Write the sample size, time window and workload. Compare to a baseline using the same cases. Decide in advance what triggers a pause.

## Expand and maintain

Add one new capability at a time when its real dependency is ready. Update the matching guide, prompts, regression suite and operator training together. Review documents after each behavior change; do not leave an old deployment report as the user-facing source of truth.

The public manual, current product status and deployed runtime each serve different purposes: the manual explains use, status explains boundaries, and actual release evidence proves availability. Keep all three aligned.
