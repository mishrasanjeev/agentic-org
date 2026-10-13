# Marketing enquiry delivery and Gemini spend controls

This runbook covers the public demo enquiry, not tenant messaging, password
resets, customer confirmations or autonomous campaign permission.

## What each status means

| State | Evidence | What it does not prove |
| --- | --- | --- |
| `received` | The enquiry was persisted | Email arrival, agent completion or qualified demand |
| `pending` | A durable delivery step awaits a worker | The SMTP server has accepted a message |
| `processing` | One worker claimed the step | Success or permission to run it again |
| `smtp_accepted` | The configured SMTP server accepted the message | Inbox arrival, reading or a positive reply |
| `failed` | The transport or agent refused/failed | A completed action |
| `unknown` | A worker lost contact before recording the outcome | A safe reason to resend automatically |
| `needs_review` | Sales processing reached a human approval boundary | An authorized completed external action |

The intake response retains the legacy `*_sent` fields as false: background work
has not been verified at response time. It also reports pending mail status,
whether sales processing is queued and the durable request identifier. It makes
no guaranteed two-minute response claim.

Authorized staff can read the ten newest `demo_delivery` results on
`GET /api/v1/sales/pipeline/{lead_id}` in the owning tenant. There is no anonymous
delivery-status endpoint and no email body, contact details or secret in that
projection. SMTP acceptance still needs a separate inbox check for an E2E verdict.

## Configure SMTP without sharing secrets

1. Choose the actual authenticated mailbox, not merely an alias or display sender.
2. For Google Workspace, confirm the user exists and Gmail is enabled. Sign in
   privately as that user, enable permitted two-step verification and create an
   app password. Do not weaken an institutional authentication policy. If app
   passwords are prohibited, use an approved OAuth-capable integration instead.
3. Add the password through the protected secret store. Never put it in source,
   an issue, chat, shell arguments, public examples or test logs.
4. Configure `AGENTICORG_SMTP_LOGIN` independently of the visible
   `AGENTICORG_DEMO_SENDER`. Gmail defaults to `smtp.gmail.com`, SSL and port 465.
   Only Gmail's recognized four-group app-password display spacing is removed;
   arbitrary passwords for other SMTP servers are not modified.
5. Gmail uses explicit LOGIN challenge/response over validated TLS. SMTP failure
   logging reports an exception class/code, not authentication bytes or recipients.
   A 535 is an account/credential rejection, not a successful connection.

Example non-secret settings:

```text
AGENTICORG_SMTP_LOGIN=sender@example.org
AGENTICORG_DEMO_SENDER=sender@example.org
AGENTICORG_SMTP_HOST=smtp.gmail.com
AGENTICORG_SMTP_PORT=465
AGENTICORG_SMTP_SECURITY=ssl
AGENTICORG_DEMO_NOTIFY_TO=sales@example.org
AGENTICORG_DEMO_DURABLE_DELIVERY_ENABLED=true
```

The hosted marketing notification inbox remains the configured Orchestrum owner
inbox. Requester confirmations go to the requester; operational customer mail and
tenant workflows are not redirected. Plain SMTP is allowed only for a loopback
capture service in a relaxed local/test environment, never in production.

## Durable processing and recovery

Apply `v6z85_lead_delivery_spend` before enabling the durable flag. The flag
defaults off for rollback compatibility; production should enable it on API,
worker and beat together. The legacy mode remains in-process and is not durable.

Enquiry input is encrypted under the owning tenant's configured vault/KEK and
stored in an RLS-protected outbox. Broker messages carry only tenant/job
identifiers. The worker claims notification, confirmation and sales processing
independently with atomic state transitions. Re-delivery of the same job does not
repeat claimed/completed external actions. A one-minute sweep recovers pending
jobs after a missed broker publish. Failed SMTP actions are not automatically
resent; processing steps older than fifteen minutes become `unknown` for review.
This is not a promise of exactly-once email delivery across a network failure.

Fix the cause before scheduling another approved test. Do not replay historical
prospects as a mail probe. Failed/review-paused sales results do not create contact
history, scheduled follow-ups or outbound mail. Sales output mail must match the
stored lead recipient, and contact timestamps/counts change only after SMTP
acceptance.

## Spend accounting without tenant-row access

Tenant caps still use a tenant-bound session and the authenticated tenant scope.
The platform cap reads the no-argument `public.gemini_daily_platform_spend()`
function. It returns only today's UTC Gemini cost total, including historical
rows, and has a pinned `pg_catalog` search path and fully qualified table.

Its restricted NOLOGIN/NOBYPASSRLS owner receives SELECT on only the three
required accounting columns through a role-specific read policy. An ordinary
runtime role gets EXECUTE only. Existing tenant policies and FORCE RLS remain
enabled; the router no longer sets row security off. Migration credentials need
permission to create/manage the restricted role; do not grant the runtime a
general bypass capability to make the migration succeed.

PostgreSQL 16+ retains an ADMIN-only role-management grant for the role creator,
with INHERIT and SET disabled. The migration verifies that the installer cannot
inherit aggregation read privileges or switch into the role. If a deployment
shares its application and migration SQL identity, that existing administrative
identity can still manage roles; this is not a new promise of a least-privilege
database login. Prefer a dedicated migration identity and grant the ordinary
runtime identity only function EXECUTE. No public route exposes role management.

Both tenant and platform caps retain their existing values. A missing function,
database failure or unknown spend refuses the model call. A lookup failure is
not evidence that a monetary limit was exhausted. This repair does not add atomic
in-flight budget reservations or claim to solve concurrent cap overshoot.

## Local and production acceptance

1. Replay bad SMTP credentials, missing accounting capability, cross-tenant reads,
   exhausted tenant/platform caps and malformed input. Each must fail closed.
2. Apply the migration to fresh and populated local Postgres. Repeat it and check
   function ownership, ACL, RLS and absence of inherited/SET aggregate-role access.
3. Use a real local SMTP capture service, API, Redis and Celery worker. Inspect
   both captured messages and persisted step outcomes. Replay the job; no extra
   messages may appear. Stop/start the worker to prove pending-job survival.
4. Run the repository preflight, integration suite and browser form regression.
   Merge only with clean PR/final-main checks, then use migration-first rollout.
5. Verify one approved owner-only production enquiry, its persisted lead/outbox,
   two SMTP results and the actual inbox. Do not replace inbox proof with the
   form's success screen, a mock transport or a pre-completion boolean.

Google's [app-password guidance](https://support.google.com/accounts/answer/185833)
and [Workspace SMTP setup](https://support.google.com/a/answer/176600) explain
provider prerequisites. Python documents the [SMTP authentication contract](https://docs.python.org/3/library/smtplib.html).
