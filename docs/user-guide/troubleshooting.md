## Start with the smallest failing layer

Record the exact screen, timestamp, company, safe run reference and error code. Separate sign-in, permissions, model, knowledge, connector, workflow and provider issues. Changing all configuration at once makes the cause harder to find.

## Symptom checklist

| Symptom | First checks | Safe recovery |
| --- | --- | --- |
| Cannot sign in | Correct deployment, email/invitation, expired session | Use password recovery or administrator identity support |
| Menu missing / `403` | Assigned role and route/tool scope | Have the owner grant the required permission; no shared admin login |
| Data missing after company switch | Selected company, resource ownership, binding | Return to authorized context and re-test; do not copy another company's IDs |
| Model credential missing | Provider/kind, tenant/company, model ID | Configure the correct credential and test a short task |
| Overload / rate limit | Provider incident, quota, concurrency | Bounded retry/backoff or approved model routing |
| Connector appears configured but fails | Upstream scope, secret binding, API contract, health age | Perform a bounded read test and correct configuration |
| Document upload fails | Supported type/limits, corruption, extractable text | Use the original supported file or a cleaner scan |
| OCR text is wrong | Orientation, language, scan quality, reading order | Compare against original; correct source or request manual review |
| Search has no result | Extraction/indexing, embedding availability, scope | Test a known phrase and the retrieval service separately |
| Workflow waits | Approval/event/wait step, worker availability | Review the waiting condition; do not bypass required approval |
| Workflow runs twice | Trigger/retry history, idempotency | Confirm external effects before retrying |
| Voice call has no reply | Callbacks, signature, mapped active agent, model | Inspect provider status and runtime health; no unsigned fallback |
| RPA times out | Target changes, session, selectors, approved domain | Pause schedules and revalidate the script in a test environment |
| Commerce answer is stale | Source sync, artifact TTL, revocation and merchant state | Refresh through the supported path; do not promise final inventory/price |
| Case decision refused | Current version, issuer configuration, distinct approvals | Resolve the exact reason and request a current decision |

## Distinguish refused, failed and unavailable

Refused means a boundary prevented the action. Failed means the attempted path did not complete. Unavailable can mean a provider capability is absent or a tenant feature is disabled. None should be turned into a fabricated success.

`governed_cases_disabled` is feature configuration; `decision_service_not_configured` is an issuer dependency; `case_changed` means existing decision evidence is no longer current. Do not edit an underlying record to bypass these conditions.

## Reproduce safely

Use a synthetic record with the same role, company, source version and input shape. First verify the standalone provider/model/knowledge path, then the agent, then the workflow/channel. A successful admin test does not validate an operator's permissions.

If production customer data is required to diagnose the problem, use the organization's approved private evidence channel. Screenshots can contain sensitive fields; redact before sharing.

## Escalate with useful evidence

Send expected versus actual behavior, reproduction steps, safe references, environment/version and attempted recovery. State whether a payment, email, account change or other external action may already have happened. Never send raw tokens or credentials.

When an external effect is ambiguous, pause and confirm with the system of record. A retry that makes the UI green but duplicates a customer action is not a fix.
