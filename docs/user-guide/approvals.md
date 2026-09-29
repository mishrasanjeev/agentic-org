## What human review should answer

Review means understanding the evidence, authority, proposed action and possible effect. It is not clicking approve because an agent has a high confidence score. Assign the reviewer before enabling a workflow; an unowned queue creates operational delay.

Open **Approvals** with an authorized role. The standard queue supports reviewing agent/workflow escalations. Governed business cases use a separate case queue and decision-grant process described in [Governed cases](/docs/governed-cases).

## Review a standard escalation

1. Confirm the company, run/agent reference and requested action.
2. Read source evidence and any missing/conflicting information.
3. Check the policy/HITL reason and connector/tool scope.
4. Verify whether the action is a draft, an external change or a business decision.
5. Approve or reject through the supported control only within your authority.
6. Inspect the recorded result and downstream status. Approval does not prove that the provider action completed.

A failed queue load must not be interpreted as "no pending approvals". Resolve an error and refresh the authoritative state before making a decision.

## Standard approval versus governed-case decision

| Path | What it reviews | Who supplies decision authority |
| --- | --- | --- |
| Agent/workflow HITL | A run's proposed task or escalation | Authorized workflow/business reviewer under the configured rules |
| Governed-case screening review | Accept/override a screening proposal with evidence | Authenticated human analyst; an override requires a reason |
| Governed-case final decision | Exact current case version and semantic action | External decision-grant issuer and named approvers |

Do not use a generic approval button as a substitute for a governed-case decision. The case console cannot mint decision authority. Machine credentials cannot sign in as a human reviewer.

## Handling changed or incomplete evidence

If a source changes while you review, inspect the current version again. A governed-case decision request can expire, be superseded or be refused because the case changed; create a new request for the current memo instead of reusing old approval evidence.

Request missing information or reject an unsafe proposal. Do not ask the agent to fill gaps with assumptions. A refusal is a useful safety outcome when authorization or evidence is absent.

## Operating the queue

Assign primary and backup reviewers, a review target, an escalation contact and absence coverage. Track queue age and rejection reasons. Separate preparation from approval wherever your policy requires it. Reviewers need training in the task domain, not only product navigation.

For BFSI, loan, onboarding, claim, sanction, account-change and payment decisions remain with the institution's authorized process. Product functionality is not regulatory approval or certification.
