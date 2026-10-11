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
   or restoration of the recorded application revision with the schema retained.
3. Record the exact release commit and existing API/UI/worker/beat revisions.
   Keep production backups, infrastructure and unrelated service settings
   unchanged.
4. Confirm an approved account-backed test plan. Credentials go through the
   protected UI or approved secret-management process, never chat or Git.

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
- If runtime health fails, restore the previously recorded image/traffic and
  model environment values. The additive migration remains in place. Do not
  delete receipts or replay ambiguous executions to simulate recovery.

## Account-backed acceptance still required

Meta application permissions, WhatsApp business/number assets, Telegram bot
registration, Viber commercial bot access, Mailgun receiving DNS/routes and
Twilio phone/speech setup are account-specific. See the
[customer account guide](../user-guide/channel-workflows.md). WeChat uses an
approved signed gateway in this release, not a native adapter. Messaging
attachments and audio notes are not ingested by these text callbacks; use the
separate knowledge and voice flows. No call to an unapproved phone destination
is permitted.
