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

## Pilot scorecard

Track task correctness, source coverage, review effort, queue age, external-action failures, latency, cost and reopened errors. Write the sample size, time window and workload. Compare to a baseline using the same cases. Decide in advance what triggers a pause.

## Expand and maintain

Add one new capability at a time when its real dependency is ready. Update the matching guide, prompts, regression suite and operator training together. Review documents after each behavior change; do not leave an old deployment report as the user-facing source of truth.

The public manual, current product status and deployed runtime each serve different purposes: the manual explains use, status explains boundaries, and actual release evidence proves availability. Keep all three aligned.
