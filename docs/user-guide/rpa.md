## When to use browser automation

Use RPA for a reviewed task on an authorized web-only system when a supported API is not suitable. Prefer an API for stable structured integration. RPA is an explicit external action, not a way around permissions, website policy, MFA or CAPTCHA.

The administrator screens are **RPA Scripts** and **RPA Schedules**. Available scripts come from the configured catalog. A visible script is not permission to run it against a real customer system.

## Prepare a reviewed script

Document target system/domain, intended action, business owner, required inputs, credentials, success signal and recovery procedure. Approve the destination through normal egress policy. Private/loopback/metadata destinations and unsafe DNS behavior are guarded; do not disable those controls to complete a test.

## Run one bounded task

1. Select the reviewed script in **RPA Scripts**.
2. Inspect its expected inputs and whether it reads or changes data.
3. Use approved test credentials and synthetic records first.
4. Run explicitly and wait for the structured result.
5. Inspect durable history, duration, timeout/failure state and supported screenshots/evidence references.
6. Confirm the business outcome in the target system.

```flow
Operator selects task | The script and inputs describe an intended authorized action.
Egress and input checks | Tenant permissions and destination policy are enforced.
Browser execution | Playwright performs the bounded script with a timeout.
Result and history | Success, refusal or failure is recorded with supported evidence.
Business verification | Confirm the authoritative target record before retrying or expanding.
```

Browser navigation success does not prove a submission or account change completed. A screenshot can show what was visible, but a durable target reference/status is stronger evidence for a business action.

## Schedule only after reproducible tests

Confirm a worker/browser runtime is installed, reviewed schedule frequency and timezone, concurrency, credential handling and retry policy. A saved schedule is not proof that a runner is deployed. Verify real scheduled executions and an operator-owned alert path.

Test a selector change, expired session, missing field, blocked destination, timeout and duplicate request. Keep screenshots and logs free of unnecessary customer data and secrets. Use protected credential custody, not source-code constants.

## Recover safely

When a site changes, pause dependent schedules and inspect the changed contract. Do not blindly replay a possibly completed submit. Verify whether the target system already changed and use its documented recovery/idempotency path. A CAPTCHA or MFA step should move to an authorized human process, not an evasion mechanism.

For BFSI, first use a read-only internal/test portal task with approved network and data access. A core banking or payments portal is not an appropriate target merely because it is reachable from a browser.

Next: [Connectors](/docs/connectors), [Security and data](/docs/security-and-data), [Evaluation](/docs/evaluate-and-rollout).
