## Define success before running tests

Choose the task, owner, expected output, permitted tools, prohibited actions and escalation behavior. Use representative examples, not only a single easy prompt. A confidence percentage, template name or passed unit test does not establish business accuracy.

## Build a small reviewed test set

Include normal inputs, incomplete files, conflicting procedures, stale evidence, incorrect company references, provider failures and attempts to request forbidden actions. For OCR, include realistic scans; for voice, include accent/noise and silence; for RPA, include expired sessions and changed selectors.

| Dimension | Example acceptance question |
| --- | --- |
| Grounding | Is each material fact supported by an authorized source? |
| Correctness | Does the result match the reviewed answer? |
| Permissions | Are cross-company and ungranted requests refused? |
| Human control | Does consequential work stop at the intended review boundary? |
| Recovery | Are retries bounded and duplicate effects prevented? |
| Usability | Can a second operator reproduce the task without the builder? |
| Operations | Are latency, cost, volume and failure handling acceptable for this workload? |

## Run candidates in a bounded environment

Create candidates in shadow and restrict tools to reads where possible. Shadow is an evaluation state, not proof of universal side-effect suppression. Inspect actual tool activity. Compare versions with the same test inputs and source content. Record provider/model, prompt and configuration so results are reproducible.

The public **Evaluations** page describes methodology; use the task's actual evaluation/run evidence for promotion. Jev advisory shadow work has its own access and cost/latency gate and does not replace human authority.

## Move to a limited pilot

Agree users, volume, allowed data, hours, review coverage and rollback triggers. Choose a narrow population and a named support owner. Train operators and reviewers separately. Verify that a non-admin user can complete the documented flow with their own account.

Do not enable a provider/payment/channel action simply because read-only Q&A works. Each external action needs its own account, contract, authority, failure tests and operational evidence.

## Expand deliberately

Measure actual results and reopened errors. Correct the shared cause when a bug affects multiple paths, then add regression cases that replay the failing user steps. Re-evaluate after changes to model, prompt, policy, document, connector or target site.

For a bank, the institution's risk, security, privacy and business owners approve rollout under their own process. AgenticOrg does not make a regulatory approval, certification or guaranteed service-level claim on the basis of this checklist.

Next: [Team adoption checklist](/docs/adoption-checklist), [BFSI onboarding](/docs/bfsi-business-onboarding).
