# Configurable channel workflow release

## Boundaries

This release adds authenticated channel management and signed inbound text
callbacks. Provider credentials belong to the workspace user and are encrypted
with tenant-scoped keys. New connections start disabled; unlinked messages
require explicit review. No provider account approval or actual message/call
delivery is implied by a local test.

Deploy API, worker and beat from the same immutable image. The worker includes
the channel dispatch tasks and beat publishes the durable inbox sweep. Updating
the UI or API alone leaves callbacks queued without a consumer.

## Before rollout

1. Require full local preflight, Docker HTTP/worker and PostgreSQL RLS tests,
   browser tests and the normal PR checks to pass.
2. Review migration `v6_z83_channel_bindings`: three new tenant-scoped tables,
   forced RLS, composite tenant/binding references and no customer backfill.
   Apply through the repository's migration-first helper, not startup DDL.
   Alembic version step-back retains all three tables, encrypted rows and RLS;
   upgrading again is idempotent. This supports migration rehearsals, not a
   destructive production rollback. Production recovery remains a forward fix
   or an explicitly approved database recovery, with the channel history retained.
3. Record the exact release commit and existing API/UI/worker/beat revisions.
   Keep production backups, infrastructure and unrelated service settings
   unchanged.
4. Confirm an approved account-backed test plan. Credentials go through the
   protected UI or approved secret-management process, never chat or Git.

### Opt-in Telegram answer upgrade

Apply `v6z86_telegram_answers` before rolling the API and workers. Its additive
columns retain status-only defaults on existing bindings and receipts. No global
feature flag needs enabling. Deploy all runtime services and the UI from the
same tested release while preserving existing environment controls and limits.

Test the default status path, consent captured at receipt, linked private-sender
authorization rechecked after execution, pending approvals, revocation and
provider-send failure locally. A send failure must not replay the agent. The
account-backed smoke must compare the actual Telegram answer with the same
authenticated inbox run, not just a generic completion notice. Use a dedicated
tool-free agent, an approved total model allowance and one complete synthetic
message. Record/restore destination, reply mode and Send replies according to
the owner's instructions. Do not rotate credentials or replace a bot webhook.

Include a real PostgreSQL regression with different API and worker signing
keys. Sender proof must come from authenticated intake inside tenant-encrypted
message data, remain connection-scoped, and still match the live delegated link.
Do not fix answer rejection by copying service keys, bypassing sender checks or
changing feature flags. Missing/malformed proof, an expired/revoked link or a
changed owner/target must still withhold the answer. Receipts from before the
proof was introduced are not backfilled or replayed.

Only final output is eligible for export. Treat the opt-in as permission to send
that output to Telegram, not a data-loss-prevention guarantee. Trace/tool-call
records remain private and recognized secret-bearing output is withheld. Schema
step-back retains the consent columns; old workers continue status-only replies.

## Unicode and bounded answer-context repair

Before releasing a language/context repair, replay the actual Unicode question
through model-input assembly and the packaged privacy detector. JSON Unicode
escapes can resemble identifiers to English recognizers; keep natural-language
Unicode readable rather than bypassing PII scanning. Exercise both direct
LangGraph and base-agent reasoning/synthesis message construction. Retain email,
date, credential and numeric-quantity privacy regressions.

For the existing explicit answer-mode opt-in only, verify two private linked
turns using real PostgreSQL forced RLS and different API/worker signing keys.
The second run may receive bounded safe text from confirmed replies for the
same tenant, binding, user, delegation and agent/company. It must still get a
new execution checkpoint. Test old targets/companies, foreign sender proof,
expired history, status-only receipts and ambiguous provider sends as exclusions.
Exclude configured traffic splits and recheck internal context scope after the
API reloads/resolves the target, before any model call. Verify target/company/tool
changes refuse dispatch without spending or handing context to an alternate
agent. Retain the existing routing correlation, distinct from execution state.
Keep the binding claim until answer acknowledgement is stamped into encrypted
receipt data so a follow-up cannot race that stamp. Never retry an uncertain
send or backfill old answers as acknowledged. No schema, flags, keys or budget
changes are needed; the banking dialogue/action runtime remains unchanged.

Account-backed Hindi/multi-turn acceptance is separate from these local tests.
Record each run and actual reply, count its model cost against the approved
remaining allowance, and preserve the owner's current permanent route. Do not
restore an obsolete test route. Until fresh replies pass, report the language
bug as open in production, even when the code and local tests pass.

## Reasoning default rollout

Existing service environment overrides survive an image rollout. To apply the
requested new primary and fallback across API, worker and beat, set both
operator-only deployment options before invoking `scripts/deploy_cloud_run.sh`
with the exact approved main commit, `--with-migrations` and normal revision
health verification:

```bash
export AGENTICORG_DEPLOY_LLM_PRIMARY=gemini-3.5-flash-lite
export AGENTICORG_DEPLOY_LLM_FALLBACK=gemini-3.5-flash-lite
```

Omitting both options preserves installed service settings. Supplying just one
or an invalid model identifier fails before image building. These options never
contain credentials and do not rewrite explicitly configured existing agents.
Tier overrides remain separate. Verify the model with the workspace's own
Google account, quota and billing; deterministic local model responses are not
Google inference evidence. Speech recognition and synthesis remain separately
configured providers.

## Verify and recover

- Verify the migration job and schema, image digests, revision readiness and
  traffic for API/UI/worker/beat. Confirm health commit and DB/Redis checks.
- Check the public guide and authenticated Channels screen. An unauthenticated
  management request must fail; unknown or invalidly signed callbacks must not
  create work. Never use a customer callback secret for a public probe.
- With an approved test tenant, save a disabled connection, register its
  callback, enable, privately link or approve one message, inspect the run and
  encrypted inbox result, then disable and test refusal.
- Check bad signatures, duplicate delivery, changed-content replay, credential
  rotation, paused agent target and user revocation. Native status replies need
  separate provider acknowledgement; ambiguous delivery is not automatically
  retried.
- Run durable workflow-state and event-wait tests as a non-superuser,
  non-RLS-bypass role with forced RLS, including a real transform workflow and
  fresh-worker reload. Verify transaction-local tenant context is reset when
  a pooled connection is reused. A provider acknowledgement or a green test
  using a database superuser cannot establish workflow completion.
- If runtime health fails, leave traffic on the existing healthy revision
  where possible and prepare a forward fix with the current migration head.
  An older image's strict startup schema check can reject the newer database
  revision on a cold start: traffic rollback alone is not a verified recovery.
  Recheck schema compatibility, health and model settings before declaring
  recovery. Database restore requires separate approval. Do not delete receipts,
  change production revision stamps or replay ambiguous executions to simulate
  recovery. The additive schema and forced RLS remain in place.

## Account-backed acceptance still required

Meta application permissions, WhatsApp business/number assets, Telegram bot
registration, Viber commercial bot access, Mailgun receiving DNS/routes and
Twilio phone/speech setup are account-specific. See the
[customer account guide](../user-guide/channel-workflows.md). WeChat uses an
approved signed gateway in this release, not a native adapter. Messaging
attachments and audio notes are not ingested by these text callbacks; use the
separate knowledge and voice flows. No call to an unapproved phone destination
is permitted.
