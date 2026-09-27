# Vault Key Rotation

The credential vault (`core/crypto/credential_vault.py`) seals every LangGraph
checkpoint and, for tenants without a KMS key, the secrets the platform stores:
connector credentials, GSTN portal passwords, tenant AI provider keys, voice
transcripts and SIP settings, pseudonym maps, SSO client secrets, case push
signing keys, governed-case excerpts, and short-lived OAuth handoff state in
Redis. A tenant with a customer key (`tenants.byok_kek_resource`), or every
tenant when `AGENTICORG_PLATFORM_KEK` is set, gets KMS envelope ciphertext
(`env1:`) for those secrets instead; checkpoints always use the vault.

This runbook replaces the vault key without making any of that unreadable. It
uses only the tools in the repository: `core/crypto/rewrap.py`,
`core/crypto/verify_all.py` and `scripts/deploy_cloud_run.sh`. Rehearse it
locally first ([Rehearse locally](#rehearse-locally)), and read
[Known limitations](#known-limitations) before you start: some of them decide
whether the old key can be retired at all.

## How the keyring works

- `AGENTICORG_VAULT_KEYRING` is a comma-separated list of `id:key` entries. The
  first entry encrypts; every entry decrypts. Example shape:
  `v3:<key 3>,v2:<key 2>`.
- New ciphertext is stamped with the id of the entry that sealed it:
  `agko_v<id>$<token>`. Decryption tries the stamped id first, then every other
  entry. Unstamped ciphertext from before the keyring counts as id `legacy`.
- Without `AGENTICORG_VAULT_KEYRING` the vault uses one key, id `legacy`, from
  `AGENTICORG_VAULT_KEY`, else `AGENTICORG_SECRET_KEY`. Once the keyring is set,
  neither of those feeds the vault.
- The keyring is read from the process environment only, never from `.env`. A
  keyring with no usable entry, or an entry with no key material, is refused in
  every runtime. Unless `AGENTICORG_ENV` is `local`, `dev`, `development`,
  `test` or `ci` (an unset value counts as production), so are a missing vault
  key and a placeholder published in this repository. The API and the worker
  do not start with a refused key.
- Ids are labels for key material. `rewrap --key-id` and `verify_all --check`
  go by the stamped id, so never reuse an id for different material. Use ids
  such as `v2`, `v3`: an id cannot contain `,` or `:`, and `$` would break the
  stamp.
- A key cannot contain `,`, and whitespace around an entry is stripped. Nothing
  enforces a minimum length (FINDINGS A-73): generate 48 random bytes, as below.
- A running process keeps the keyring it started with; the checkpoint store
  reads it once, when it opens. Every change needs new revisions of the API,
  the worker and beat, and takes effect only once no instance of an older
  revision is left ([Roll the services](#roll-the-services)).

## Before you start

### Access and settings

You need Secret Manager access to the keyring secret, `run.services.update` on
`agenticorg-api`, `agenticorg-worker` and `agenticorg-beat`, and a way to run the
maintenance tools against the production database (below). Set:

```bash
export GCP_PROJECT_ID=<project id>
export CLOUD_RUN_REGION=<Cloud Run region>
export KEYRING_SECRET=<Secret Manager secret mounted as AGENTICORG_VAULT_KEYRING>
export DEPLOYED_SHA=<full commit the services run: the "commit" field of GET /api/v1/health>
```

Never enable shell tracing (`set -x`) while following this runbook, and never
print a key. The commands below handle key material only inside a subshell and
a pipe.

### Check what each service reads

List each service's variables and secret mounts, never their values:

```bash
for svc in agenticorg-api agenticorg-worker agenticorg-beat; do
  gcloud run services describe "$svc" --project="$GCP_PROJECT_ID" --region="$CLOUD_RUN_REGION" --format=json |
    python -c 'import json, sys
svc = json.load(sys.stdin)
for e in svc["spec"]["template"]["spec"]["containers"][0].get("env", []):
    ref = (e.get("valueFrom") or {}).get("secretKeyRef")
    print(svc["metadata"]["name"], e["name"], ref["name"] + ":" + ref["key"] if ref else "(plain value)")'
done
```

- All three services must mount `AGENTICORG_VAULT_KEYRING` from
  `$KEYRING_SECRET` at version `latest`. FINDINGS A-71 records beat running with
  only `AGENTICORG_SECRET_KEY`; add the mount there before you start, then
  [roll the services](#roll-the-services):

  ```bash
  gcloud run services update agenticorg-beat --project="$GCP_PROJECT_ID" --region="$CLOUD_RUN_REGION" \
    --update-secrets=AGENTICORG_VAULT_KEYRING="$KEYRING_SECRET":latest --no-traffic
  ```

- A service that pins a version number (`$KEYRING_SECRET:7`) never picks up a
  new version; point it at `latest` the same way.
- No `AGENTICORG_VAULT_KEYRING` anywhere: follow
  [Moving off a single key](#moving-off-a-single-key) instead of the steps below.
- Cloud Run resolves `latest` when an instance starts. After you add a version,
  new instances of the current revisions (autoscaling) read it before you roll.
  Every step below is safe in that mixed state; skipping a step is not.

List the keyring's ids, in order, without the keys:

```bash
gcloud secrets versions access latest --secret="$KEYRING_SECRET" --project="$GCP_PROJECT_ID" |
  tr ',' '\n' | cut -d: -f1
```

The first id is active. Set `OLD_ID` to it and `NEW_ID` to an id this
deployment has never used:

```bash
export OLD_ID=<first id listed>
export NEW_ID=<unused id, e.g. the next vN>
```

### Where to run the maintenance tools

`rewrap` and `verify_all` import the application's settings, so they need what
the API needs in a strict runtime: `AGENTICORG_ENV` as deployed,
`AGENTICORG_SECRET_KEY`, `AGENTICORG_DB_URL`, and `AGENTICORG_REDIS_URL` set to a
host that is not localhost (they never connect to Redis). `rewrap` also needs the
keyring; `verify_all` reads only the stamps.

The `agenticorg-migrate` Cloud Run job runs the API image with `python` as its
command, so one execution can run either tool by overriding its arguments.
`scripts/deploy_cloud_run.sh --create-migrate-job` sets only its image, command
and one variable, and on an existing job its `--set-env-vars` removes every
other plain variable, so do not run it again once you have added them. Check the
job has the variables above (by name, as for the services, under
`spec.template.spec.template.spec.containers[0]`) and add the keyring if it is
missing:

```bash
gcloud run jobs update agenticorg-migrate --project="$GCP_PROJECT_ID" --region="$CLOUD_RUN_REGION" \
  --update-secrets=AGENTICORG_VAULT_KEYRING="$KEYRING_SECRET":latest
```

The job must run the same image as the services: an older image has older
`rewrap` and `verify_all` code, which may register fewer columns and so report a
key unreferenced that the deployed code still reads. The deploy script updates
the job's image only with `--with-migrations` or `--create-migrate-job`, so a
release deployed without them leaves the job behind. Compare the images:

```bash
for svc in agenticorg-api agenticorg-worker agenticorg-beat; do
  gcloud run services describe "$svc" --project="$GCP_PROJECT_ID" --region="$CLOUD_RUN_REGION" \
    --format="value(spec.template.spec.containers[0].image)"
done
gcloud run jobs describe agenticorg-migrate --project="$GCP_PROJECT_ID" --region="$CLOUD_RUN_REGION" \
  --format="value(spec.template.spec.template.spec.containers[0].image)"
```

The three services must print the same image, ending in `:$DEPLOYED_SHA`; if
they do not, stop and finish or roll back that release first. If the job prints
anything else, give it the services' image:

```bash
gcloud run jobs update agenticorg-migrate --project="$GCP_PROJECT_ID" --region="$CLOUD_RUN_REGION" \
  --image=<the image the three services printed>
```

Compare again before each tool run if a release was deployed in between.

A command written below as `python -m core.crypto.rewrap --dry-run` then runs as:

```bash
gcloud run jobs execute agenticorg-migrate --project="$GCP_PROJECT_ID" --region="$CLOUD_RUN_REGION" \
  --wait --args=-m,core.crypto.rewrap,--dry-run
```

`--wait` fails when the tool exits non-zero; the output is in the execution's
logs. Add `--task-timeout=3600s` for a long rewrap. Cloud Run may retry a failed
task, and every command here is safe to repeat. A trusted machine with the same
environment, a checkout of `$DEPLOYED_SHA` and a database connection works too.

### Backups

`rewrap` re-encrypts rows in place. Backups and point-in-time recovery from
before it still hold rows sealed under the old key, so keep the old key's
material (the earlier Secret Manager versions: disable them later, never destroy
them) for as long as those backups are retained
([Backup and disaster recovery](../BACKUP_AND_DR.md)). Confirm a recent backup
exists before step 4.

Record the baseline: every vault key id and KMS key referenced, per registered
column.

```bash
python -m core.crypto.verify_all
```

## Roll the services

Every keyring change ends with this. `agenticorg-api`, `agenticorg-worker` and
`agenticorg-beat` each get a new revision, whose instances read the keyring when
they start, and the roll is complete only when no instance of an older revision
is left. The roll uses `scripts/deploy_cloud_run.sh`, the release script, so it
re-applies more than the keyring.

### What the deploy script re-applies

With `--sha "$DEPLOYED_SHA" --skip-build --traffic latest` the script updates
each service with `gcloud run services update --no-traffic`, the API first, the
worker and beat once the API is healthy, and the UI last:

| Service | What it sets |
| --- | --- |
| `agenticorg-api` | image `<registry>/agenticorg:$DEPLOYED_SHA`; `AGENTICORG_GIT_SHA=$DEPLOYED_SHA`; `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED` from your shell, `false` when it is unset |
| `agenticorg-worker`, `agenticorg-beat` | the same image; `AGENTICORG_GIT_SHA=$DEPLOYED_SHA`; command `python` with argument `scripts/run_worker.py` or `scripts/run_beat.py` |
| `agenticorg-ui` | image `<registry>/agenticorg-ui-cloudrun:$DEPLOYED_SHA`; `GIT_SHA=$DEPLOYED_SHA` |

`<registry>` is the `registry` line of the script's plan. Variables are set with `--update-env-vars`, so other variables and secret mounts
stay as they are. The script then moves 100% of each service's traffic to the
revision it created, which ends any split between revisions. It probes the new
API revision through a temporary tag, `deploy-` and the first seven characters
of the commit, which it removes at the end, and waits for `HEALTH_URL` to report
`healthy` and the commit. When a step fails it moves traffic back. With
`--skip-build` both images must already be in the registry; when one is missing
the script stops before changing anything.

So a roll is safe only when it re-applies what is already there.

### Before each roll

List what each service runs now. This prints the image, three named variables
that hold no secret, and the traffic entries:

```bash
for svc in agenticorg-api agenticorg-worker agenticorg-beat agenticorg-ui; do
  gcloud run services describe "$svc" --project="$GCP_PROJECT_ID" --region="$CLOUD_RUN_REGION" --format=json |
    python -c 'import json, sys
svc = json.load(sys.stdin)
name = svc["metadata"]["name"]
container = svc["spec"]["template"]["spec"]["containers"][0]
print(name, "image", container["image"])
for e in container.get("env", []):
    if e["name"] in ("AGENTICORG_GIT_SHA", "GIT_SHA", "AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED"):
        print(name, e["name"], e.get("value", "(secret)"))
for t in svc["status"].get("traffic", []):
    print(name, "traffic", t.get("revisionName", "LATEST"), str(t.get("percent", 0)) + "%", t.get("tag", ""))'
done
```

- Every image must end in `:$DEPLOYED_SHA`, and `AGENTICORG_GIT_SHA` (`GIT_SHA`
  on the UI) must be `$DEPLOYED_SHA`. Otherwise the roll would also deploy code:
  stop, and finish or roll back that release first.
- A service that splits traffic between revisions loses the split. Stop unless
  that is intended.
- Keep the list: after the roll it tells the old revisions from the new ones.

Give the script the discovery flag the API has now. The application treats
`1`, `true`, `yes`, `on` and `enabled`, in any case, as on and anything else,
including no value, as off; the script accepts only lower-case values and stops
on any other:

```bash
export AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED=<true if the API printed an on value, otherwise false>
```

### Roll

```bash
ROLL_ID="vault-$(date -u +%Y%m%d%H%M%S)"
for svc in agenticorg-api agenticorg-worker agenticorg-beat; do
  gcloud run services update "$svc" --project="$GCP_PROJECT_ID" --region="$CLOUD_RUN_REGION" \
    --update-labels=vault-keyring-roll="$ROLL_ID" --no-traffic
done
scripts/deploy_cloud_run.sh --sha "$DEPLOYED_SHA" --skip-build --traffic latest --dry-run
scripts/deploy_cloud_run.sh --sha "$DEPLOYED_SHA" --skip-build --traffic latest
```

The label makes a new revision even though nothing else changed. `--traffic
latest` is explicit so that a `TRAFFIC_MODE` left in your shell cannot turn the
roll into staging only. The dry run only reads. Check its plan: the commit is
`$DEPLOYED_SHA`, it names the four services, `build` is `skip`, `traffic` is
`latest`, and its two image lines are the images listed above. It does not print
the variables it will set, which is why the flag is exported first. The real run
asks `Proceed? [y/N]`.

A revision whose keyring is malformed never becomes ready (the API and the
worker refuse to start), so the script stops and traffic stays on, or moves back
to, the previous revisions.

### Wait for the old revisions to drain

1. Run the listing from [Before each roll](#before-each-roll) again. The API,
   the worker and beat must each send 100% to a revision that was not in the
   earlier list. An older revision that still has a tag can keep instances
   running (Cloud Run keeps tagged revisions started when minimum instances are
   set on the revision); remove the tag:

   ```bash
   gcloud run services update-traffic <service> --project="$GCP_PROJECT_ID" --region="$CLOUD_RUN_REGION" \
     --remove-tags=<tag>
   ```

2. Wait until no older revision of the three services has an instance left. An
   instance keeps the keyring it started with: the API's old instances finish
   their requests and the worker's keep taking Celery tasks until Cloud Run
   stops them. In Cloud Monitoring, chart `run.googleapis.com/container/instance_count`
   for the resource type `cloud_run_revision`, filtered to each service and
   grouped by `revision_name`. The metric is sampled, so wait until every older
   revision has shown no instances for several minutes.
3. Note the time in UTC (`date -u +%Y-%m-%dT%H:%M:%SZ`). The roll is complete:
   from then on every running instance of the three services started after the
   keyring change.

## Rotate

### 1. Stage the new key as a decrypt-only entry

Add the new key at the end of the keyring. The active key does not change, so
nothing is sealed under the new key yet; this step makes sure every process can
open it before anything is.

```bash
(
  set +x
  current="$(gcloud secrets versions access latest --secret="$KEYRING_SECRET" --project="$GCP_PROJECT_ID")"
  new_key="$(openssl rand -base64 48 | tr -d '\r\n')"
  printf '%s,%s:%s' "$current" "$NEW_ID" "$new_key" |
    gcloud secrets versions add "$KEYRING_SECRET" --project="$GCP_PROJECT_ID" --data-file=-
)
```

List the ids again: `$OLD_ID` first, `$NEW_ID` last.

### 2. Roll

[Roll the services](#roll-the-services). Do not promote the new key until the
roll is complete, with the old revisions drained: a process that cannot open the
new key fails on everything sealed under it.

### 3. Promote the new key and roll again

Move the new entry to the front. The old key stays as a decrypt-only entry.

```bash
(
  set +x
  current="$(gcloud secrets versions access latest --secret="$KEYRING_SECRET" --project="$GCP_PROJECT_ID")"
  staged="${current##*,}"
  rest="${current%,*}"
  case "$staged" in
    "$NEW_ID":*) ;;
    *) echo "the last entry is not $NEW_ID; stop" >&2; exit 1 ;;
  esac
  printf '%s,%s' "$staged" "$rest" |
    gcloud secrets versions add "$KEYRING_SECRET" --project="$GCP_PROJECT_ID" --data-file=-
)
```

List the ids (`$NEW_ID` first, then `$OLD_ID`), then
[roll the services](#roll-the-services) and keep the time the roll completed,
once the old revisions had drained: it is the checkpoint cutoff in step 5. An
instance that started before this promote seals under `$OLD_ID` until it stops,
so the time traffic moved is too early. From here new ciphertext is stamped
`$NEW_ID`, and anything sealed under `$OLD_ID` still opens.

### 4. Rewrap

`rewrap` walks the registered columns tenant by tenant and re-encrypts every row
whose stamp is not the active id. Run it outside busy hours and just after a
token refresh cycle (every 15 minutes): it can overwrite a credential that
changes while it runs (FINDINGS A-83).

Check the baseline first. If `verify_all` listed an `[envelope]` key under
`connector_configs.credentials_encrypted`, `gstn_credentials.password_encrypted`
or `tenant_ai_credentials.credentials_encrypted`, those columns hold KMS rows
(`env1:`), which rewrap counts as `legacy` and cannot decrypt (FINDINGS A-30).
Then add `--key-id="$OLD_ID"` to the dry run and the rewrap below, so they move
only rows stamped `$OLD_ID`. With `OLD_ID=legacy` that does not separate them:
the rewrap stops at the first KMS row, so stop after step 3 and keep the old key
as a decrypt-only entry.

```bash
python -m core.crypto.rewrap --dry-run
```

The first line must read `active key id: <NEW_ID>`. Any other id means the
environment does not have the new keyring (with no keyring at all the tools fall
back to `AGENTICORG_VAULT_KEY`, then `AGENTICORG_SECRET_KEY`, as `legacy`): stop
and fix the environment. The dry run then prints the rows pending per column and
`pending total`, and writes nothing.

```bash
python -m core.crypto.rewrap
```

Each re-encrypted row prints one JSON line (`ts`, `column`, `row_id`,
`tenant_id`, `company_id`, `old_kid`, `new_kid`); keep the output as the record
of the rotation. Progress goes to stderr. Rows are committed in batches of 100
per tenant scope (`--batch-size`), so a run that stops part-way keeps what it
committed and a later run picks up the rest. `--column` limits a run to one
registered column and `--key-id` to rows stamped with one id.

If a row cannot be decrypted, rewrap rolls back that row's batch, stops the
whole run and exits 1 with
`abort: decrypt failed on <column> row <row id> (stamp='<id>'): InvalidToken: ...`,
which also lists the key ids it tried; the `rewrap_decrypt_failed` log line adds
the tenant and company. Running it again fails on the same row and never reaches
the rows after it, so do not rerun it as it is:

- A KMS row (the value starts with `env1:`) when you did not pass `--key-id`:
  rerun with `--key-id="$OLD_ID"` as above, or, with `OLD_ID=legacy`, stop after
  step 3 and keep the old key.
- Anything else: stop the rotation here. Keep `$OLD_ID` and every other entry in
  the keyring, do not continue to step 5 or 6, and investigate that row. No key
  in the keyring opens it: its stamp names a key the keyring does not hold (the
  error lists the ids it tried), or the entry with that id holds different
  material (see "No decrypt check before a roll" under
  [Known limitations](#known-limitations)), or the value is damaged. The steps
  only add and reorder entries copied from the secret, so such a row could not
  be opened before this rotation either. Rerun only once the cause is fixed.

To see how a failing value starts, as the database owner:

```sql
SET row_security = off;
SELECT left(credentials_encrypted->>'_encrypted', 16) FROM connector_configs WHERE id = '<row id>';
-- gstn_credentials:      SELECT left(password_encrypted, 16) ...
-- tenant_ai_credentials: SELECT left(credentials_encrypted->>'_encrypted', 16) ...
```

`env1:` is a KMS row, `agko_v<id>$` a vault value stamped `<id>`, anything else
an unstamped (`legacy`) vault value. The first 16 characters are a stamp or
format marker and the start of a token header; they reveal nothing of the
secret.

```bash
python -m core.crypto.rewrap --verify
```

Exits 0 when every row rewrap can move is on the active key. KMS rows keep it at
1 (FINDINGS A-30); step 5 is the check that decides.

### 5. Confirm nothing references the old key

```bash
python -m core.crypto.verify_all --check="$OLD_ID"
```

Step 6 needs exit 0 and `verify-all: OK — key '<OLD_ID>' is not referenced.`
Exit 2 (`still referenced. Refuse to retire.`) means a registered column still
holds a row under the old key: run the rewrap again, then this check. If the
only references are in `voice_calls.transcript_encrypted` or
`case_pseudonym_maps.mapping_encrypted`, rewrap cannot move them (FINDINGS A-30):
keep the old key.

`verify_all` does not see every place the vault writes. Run these two checks as
the database owner as well; `SET row_security = off` makes the first fail rather
than undercount under row-level security.

Values outside the registered columns (FINDINGS A-82), by key id. Any row whose
`key_id` is `$OLD_ID` keeps the old key in the keyring.

```sql
SET row_security = off;
WITH sealed(location, ciphertext) AS (
  SELECT 'sso_configs.config', config->>'client_secret_enc' FROM sso_configs
  UNION ALL
  SELECT 'case_push_endpoints.signing_keys_encrypted', signing_keys_encrypted->>'_encrypted'
    FROM case_push_endpoints
  UNION ALL
  SELECT 'governed_cases.excerpts_encrypted', e->>'text_encrypted'
    FROM governed_cases,
         jsonb_array_elements(CASE WHEN jsonb_typeof(excerpts_encrypted) = 'array'
                                   THEN excerpts_encrypted ELSE '[]'::jsonb END) AS e
  UNION ALL
  SELECT 'tenants.settings.voice_configs', c.value->'credentials_encrypted'->>'_encrypted'
    FROM tenants,
         jsonb_each(CASE WHEN jsonb_typeof(settings->'voice_configs') = 'object'
                         THEN settings->'voice_configs' ELSE '{}'::jsonb END) AS c
)
SELECT location,
       CASE WHEN starts_with(ciphertext, 'env1:') THEN 'envelope'
            WHEN ciphertext ~ '^agko_v[^$]+[$]' THEN substring(ciphertext FROM '^agko_v([^$]+)[$]')
            ELSE 'legacy' END AS key_id,
       count(*) AS values
  FROM sealed
 WHERE ciphertext IS NOT NULL
 GROUP BY 1, 2
 ORDER BY 1, 2;
```

Checkpoints (FINDINGS A-21), when `AGENTICORG_LANGGRAPH_CHECKPOINTER=postgres`:

```sql
SELECT count(*) FROM checkpoints
 WHERE (checkpoint->>'ts')::timestamptz < '<when the step 3 roll completed, UTC>';
```

The count must be 0. Checkpoints go only when their whole thread is deleted
(FINDINGS A-24): a run resumed to completion or into a rejection deletes its
own, and everything else waits for the manual cleanup in
[Runbooks](../RUNBOOKS.md#agent-runs-paused-for-approval-checkpoint-store-and-resume).
That cleanup deletes a thread only when its newest checkpoint is more than a day
old (the longest approval window, 4 hours by default, plus a margin) and no
pending, unexpired approval points at it. A thread still in use keeps its older
checkpoints. So the count reaches 0 only once every thread with a checkpoint
from before the cutoff has been deleted, by its resume or by a cleanup run, and
a cleanup removes such a thread no sooner than a day after its newest
checkpoint. Until then, keep the old key.

OAuth handoff state in Redis expires within 15 minutes and needs no check.

### 6. Retire the old key

Only after every check in step 5 passes:

```bash
(
  set +x
  current="$(gcloud secrets versions access latest --secret="$KEYRING_SECRET" --project="$GCP_PROJECT_ID")"
  kept="$(printf '%s' "$current" | tr ',' '\n' | grep -v "^${OLD_ID}:" | paste -sd, -)"
  [ "$kept" != "$current" ] || { echo "no $OLD_ID entry; stop" >&2; exit 1; }
  case "$kept" in
    "$NEW_ID":*) ;;
    *) echo "the first entry is not $NEW_ID; stop" >&2; exit 1 ;;
  esac
  printf '%s' "$kept" |
    gcloud secrets versions add "$KEYRING_SECRET" --project="$GCP_PROJECT_ID" --data-file=-
)
```

List the ids, then [roll the services](#roll-the-services). Keep the earlier
secret versions ([Backups](#backups)): disable them once the rotation is
verified, and destroy them only when no backup needs them.

### 7. Check afterwards

- `python -m core.crypto.verify_all` lists `$NEW_ID` as the only vault id (plus
  any KMS keys).
- `GET /api/v1/health` is healthy, and the worker's logs have no
  `Refusing to start the worker`.
- The logs have no `InvalidToken`, `checkpoint_decrypt_failed`,
  `tenant_ai_credential_decrypt_failed` or `case_excerpt_undecryptable`, and
  connector readiness shows no `credentials_not_decryptable`.
- A connector health check passes, and SSO sign-in works for a tenant with OIDC
  configured.

On any decryption failure, [put the old key back](#after-step-6-retired).

## Moving off a single key

A deployment without `AGENTICORG_VAULT_KEYRING` seals under
`AGENTICORG_VAULT_KEY`, or, when that is unset, `AGENTICORG_SECRET_KEY`
(FINDINGS A-71), with id `legacy`. Set `CURRENT_KEY_SECRET` to the secret the
service listing shows behind that variable, `OLD_ID=legacy` and `NEW_ID=v2`.

1. Check the current key can become a keyring entry unchanged. This prints a
   verdict, never the key:

   ```bash
   gcloud secrets versions access latest --secret="$CURRENT_KEY_SECRET" --project="$GCP_PROJECT_ID" |
     python -c 'import sys; v = sys.stdin.read(); print("ok" if v and v == v.strip() and "," not in v else "stop")'
   ```

   `stop` means a keyring cannot hold the value as it is (a comma splits it,
   surrounding whitespace is stripped), so rows sealed under it would not open.
   Do not continue.

2. Create the keyring, staged: `legacy` active, the new key decrypt-only.

   ```bash
   (
     set +x
     current="$(gcloud secrets versions access latest --secret="$CURRENT_KEY_SECRET" --project="$GCP_PROJECT_ID")"
     new_key="$(openssl rand -base64 48 | tr -d '\r\n')"
     printf 'legacy:%s,%s:%s' "$current" "$NEW_ID" "$new_key" |
       gcloud secrets create "$KEYRING_SECRET" --project="$GCP_PROJECT_ID" --data-file=-
   )
   ```

3. Mount it on the three services, and on the maintenance job as above, then
   [roll the services](#roll-the-services):

   ```bash
   for svc in agenticorg-api agenticorg-worker agenticorg-beat; do
     gcloud run services update "$svc" --project="$GCP_PROJECT_ID" --region="$CLOUD_RUN_REGION" \
       --update-secrets=AGENTICORG_VAULT_KEYRING="$KEYRING_SECRET":latest --no-traffic
   done
   ```

   From here `AGENTICORG_VAULT_KEY` and `AGENTICORG_SECRET_KEY` no longer feed the
   vault on those services. Keep `AGENTICORG_SECRET_KEY`: the application still
   uses it (step 4).

4. Continue with [step 3](#3-promote-the-new-key-and-roll-again). With
   `OLD_ID=legacy`, `verify_all --check=legacy` counts both `agko_vlegacy$` and
   unstamped rows, and rewrap moves both. After step 6, `AGENTICORG_VAULT_KEY` is
   unused and can be removed. `AGENTICORG_SECRET_KEY` then seals nothing stored,
   but rotating it still has effects. It signs the platform's own access tokens
   (`auth/jwt.py`), keys the SSO sign-in state (`auth/sso/state_token.py`),
   signs client portal invitation and access tokens (`api/v1/client_portal.py`)
   and audit rows (`core/tool_gateway/audit_logger.py`), and derives every
   tenant's provider webhook inbox path (`core/cases/provider_webhooks.py`). A
   rotation signs everyone out, fails SSO sign-ins in progress, invalidates
   outstanding client portal links, leaves audit rows signed before it
   unverifiable with the new key, and moves every inbox path, so each provider
   needs its new path
   ([Runbooks](../RUNBOOKS.md#provider-webhooks-verification-failures-and-replays)).

## Rollback

Moving traffic back to an earlier Cloud Run revision does not restore an earlier
keyring: revisions read `latest` when their instances start. Restore the secret,
then [roll the services](#roll-the-services). To make a previous version the
latest again (`gcloud secrets versions list "$KEYRING_SECRET"` lists them):

```bash
(
  set +x
  gcloud secrets versions access <previous version> --secret="$KEYRING_SECRET" --project="$GCP_PROJECT_ID" |
    gcloud secrets versions add "$KEYRING_SECRET" --project="$GCP_PROJECT_ID" --data-file=-
)
```

### After step 1 or 2 (staged)

Restore the version from before step 1 and roll. Nothing was sealed under the
new key.

### After step 3 or 4 (promoted)

Put `$OLD_ID` first again by restoring the step 1 version (it holds both keys),
roll, then run `python -m core.crypto.rewrap`: it moves rows sealed under the new
key back to the active one. Remove the new key only after
`python -m core.crypto.verify_all --check="$NEW_ID"` exits 0 and the step 5
checks pass for it, with the completion of this roll as the checkpoint cutoff.

### After step 6 (retired)

Restore the step 3 version (new key first, old key decrypt-only) and roll.
Removing a key changes no row, so putting it back restores every value sealed
under it.

## Known limitations

- **Checkpoints (FINDINGS A-21).** Neither tool reads the checkpoint tables, so
  `verify_all` can call a key unreferenced while paused runs still need it, and
  rewrap never moves checkpoints. Use the step 5 checkpoint query.
- **JSONB and KMS rows (FINDINGS A-30).** Rewrap skips
  `voice_calls.transcript_encrypted` and `case_pseudonym_maps.mapping_encrypted`
  without counting them, while `verify_all` reports their key, so an old key
  referenced only there cannot be retired. It treats KMS envelope rows (`env1:`)
  in the other columns as unstamped `legacy` rows: a plain rewrap stops at the
  first one and `--verify` always reports them. When tenants have KMS keys, run
  `python -m core.crypto.rewrap --key-id="$OLD_ID"`, which skips them (unless
  `OLD_ID` is `legacy`), and rely on `verify_all --check`.
- **Secret-key fallback (FINDINGS A-71).** Without a keyring the vault derives its
  key from `AGENTICORG_SECRET_KEY`, which also signs tokens, and a service
  without the keyring mount uses a different key from the others. Mount the
  keyring on every service and job.
- **No minimum key strength (FINDINGS A-73).** Any non-blank key that is not a
  published placeholder is accepted. Generate keys as in step 1.
- **Values the tools do not see (FINDINGS A-82).** SSO client secrets, case push
  signing keys, governed-case excerpts and voice SIP settings are sealed with the
  vault but not registered with `verify_all` or rewrap. Use the step 5 query; a
  value under the old key keeps the old key in the keyring.
- **Writes during a rewrap (FINDINGS A-83).** Rewrap reads a batch, then writes
  each row back without checking it is unchanged, so a credential stored in
  between (a token refresh, a reconnect) is replaced by the value rewrap read.
  After a rewrap, check connector health and reconnect any connector whose
  refresh fails.
- **The secrets rotation workflow (FINDINGS A-84).** Never pass the keyring, or
  `AGENTICORG_VAULT_KEY`, to `.github/workflows/secrets-rotation.yml`: it would
  replace the whole value with random bytes and no id.
- **No decrypt check before a roll.** `verify_all` and `rewrap --verify` read
  stamps only, and rewrap decrypts only the rows it moves. An entry with the right
  id and the wrong key passes both until something decrypts under it, which is
  why the steps copy entries from the secret instead of retyping them.

## Rehearse locally

Run the whole procedure against a scratch database first, from the repository
root. The tools and their output are the same; locally the keyring is an exported
variable instead of a secret.

```bash
docker run -d --name vault-rehearsal-pg -e POSTGRES_DB=rehearsal -e POSTGRES_USER=rehearsal \
  -e POSTGRES_PASSWORD=rehearsal -p 127.0.0.1:55432:5432 pgvector/pgvector:pg16
until docker exec vault-rehearsal-pg pg_isready -U rehearsal -d rehearsal >/dev/null 2>&1; do sleep 1; done
export AGENTICORG_ENV=development
export AGENTICORG_DB_URL=postgresql+asyncpg://rehearsal:rehearsal@127.0.0.1:55432/rehearsal
export AGENTICORG_MIGRATION_AUDIT_DIR="$(mktemp -d)"   # keeps migrations/audit/ unchanged
python scripts/alembic_migrate.py

v1_entry="v1:$(openssl rand -base64 48 | tr -d '\r\n')"
v2_entry="v2:$(openssl rand -base64 48 | tr -d '\r\n')"
export AGENTICORG_VAULT_KEYRING="$v1_entry"
sealed="$(python -c 'from core.crypto.credential_vault import encrypt_credential; print(encrypt_credential("example-token"), end="")')"
docker exec vault-rehearsal-pg psql -U rehearsal -d rehearsal -c "
  INSERT INTO tenants (id, name, slug, data_region, settings)
    VALUES ('00000000-0000-4000-8000-000000000001', 'Rehearsal', 'rehearsal', 'IN', '{}');
  INSERT INTO connector_configs (id, tenant_id, connector_name, auth_type, status, credentials_encrypted)
    VALUES ('00000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-000000000001',
            'example', 'api_key', 'active', jsonb_build_object('_encrypted', '$sealed'));"

export AGENTICORG_VAULT_KEYRING="$v1_entry,$v2_entry"   # 1. stage
python -m core.crypto.rewrap --dry-run                    #    active key id: v1, pending total: 0
export AGENTICORG_VAULT_KEYRING="$v2_entry,$v1_entry"   # 3. promote
python -m core.crypto.rewrap --dry-run                    # 4. active key id: v2, pending total: 1
python -m core.crypto.rewrap
python -m core.crypto.rewrap --verify
python -m core.crypto.verify_all --check=v1               # 5. exit 0
export AGENTICORG_VAULT_KEYRING="$v2_entry"               # 6. retire
python -m core.crypto.verify_all                          # 7. only v2
docker rm -f vault-rehearsal-pg
```

## Command reference

Registered columns (`_SCANNERS` in `core/crypto/verify_all.py`):
`connector_configs.credentials_encrypted`, `gstn_credentials.password_encrypted`,
`tenant_ai_credentials.credentials_encrypted`, `voice_calls.transcript_encrypted`
and `case_pseudonym_maps.mapping_encrypted`.

| Command | What it does | Exit codes |
| --- | --- | --- |
| `python -m core.crypto.rewrap --dry-run` | Prints the active key id, rows pending per column and the total. No writes. | 0; 1 if a tenant scan fails |
| `python -m core.crypto.rewrap` | Re-encrypts every pending row under the active key and prints one JSON line per row. Safe to repeat. | 0 done; 1 a row failed (its batch rolled back and the run stopped there) or a tenant scan failed |
| `python -m core.crypto.rewrap --verify` | Read-only: is every row on the active key? | 0 yes; 1 no, or a scan failed |
| `python -m core.crypto.verify_all` | Prints every vault key id and KMS key referenced, per column. | 0; 1 if the scan fails |
| `python -m core.crypto.verify_all --check=<id>` | The same, and refuses if `<id>` is referenced. | 0 not referenced; 2 referenced; 1 if the scan fails |

`rewrap` options: `--column=<table.column>` (one registered column; any other
value exits 2), `--key-id=<id>` (only rows stamped `<id>`), `--batch-size=<n>`
(rows per batch, default 100; below 1 exits 2). A malformed keyring, or no vault
key at all outside a local or test runtime, stops `rewrap` with an error that
names the setting, never the key (exit 1).
Both tools visit every tenant, and every company within it, through row-level
security scopes, and fail rather than report a partial result when the tenant
list cannot be read completely.
