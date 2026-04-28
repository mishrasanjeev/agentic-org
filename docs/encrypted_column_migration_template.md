# Encrypted-column migration template (Foundation #5)

Every alembic migration that touches an `*_encrypted` column **must**
use `core.crypto.migration_helpers.encrypted_migration` to wrap its
work. This was made mandatory after the SECRET_KEY rotation incident
(see `feedback_no_manual_qa_closure_plan.md`) which left old ciphertext
undecryptable because the migration that re-stamped it had no
dry-run, no decrypt verification, and no resumability — three gates
the wrapper now enforces.

## Why

Without the wrapper, an encrypted-column migration can fail in
subtle, expensive ways:

- A subset of rows becomes undecryptable but the migration exits
  zero (`pytest` and `alembic upgrade head` both look "green").
- A crash mid-run double-processes some rows on retry, corrupting
  envelope ciphertext that contains version stamps.
- The source ciphertext is cleared before the target is verified,
  so a partial failure leaves no rollback option.
- The audit trail is blank, making post-mortem impossible.

The wrapper makes all four loud and refuses to proceed unless the
operator explicitly chose `dry_run=False`.

## The contract

```python
from core.crypto.migration_helpers import encrypted_migration

revision = "v500_rewrap_connector_creds"
down_revision = "v497_migration_progress"


def upgrade() -> None:
    with encrypted_migration(
        revision=revision,
        table="connector_configs",
        columns=["credentials_encrypted"],
        rollback_doc=(
            "1. Stop the rewrap CLI (`pkill -f core.crypto.rewrap`). "
            "2. Restore connector_configs from the pre-migration "
            "   backup at gs://agko-db-backups/<date>/. "
            "3. Re-stamp via `python -m core.crypto.rewrap "
            "   --target=v1`. The audit log at "
            "   `migrations/audit/v500_rewrap_connector_creds.json` "
            "   has the row count and decrypt-sample state."
        ),
    ) as ctx:
        pre_count = ctx.snapshot_row_count()
        ctx.dry_run_decrypt_sample(n=50)

        for offset in ctx.iter_resumable_batches(batch=500):
            # ... read batch, re-encrypt with the new active key, write back ...
            ctx.mark_progress(rows_processed=offset + 500)

        ctx.assert_decrypt_after(n=50)
        ctx.record_audit({"pre_count": pre_count})


def downgrade() -> None:
    # Document the rollback explicitly even when alembic offers no
    # automated path — the operator reads this when things break.
    raise NotImplementedError(
        "Rewrap migrations do not auto-downgrade. See rollback_doc "
        "in upgrade() for the manual procedure."
    )
```

## What the wrapper enforces

| Gate | Behavior |
|------|----------|
| `rollback_doc` non-empty | Raises before any DB work |
| Pre-flight decrypt sample | n random rows decrypt with the active keyring; ANY failure aborts |
| Post-write decrypt sample | n random rows STILL decrypt after the migration's writes |
| Resumability | Per-(revision, table) progress row in `alembic_migration_progress`; restart picks up where it left off |
| Source preservation | `copy_then_verify_then_delete` refuses to drop the source until the target decrypts |
| Audit trail | Every wrapped migration writes `migrations/audit/<revision>.json` with pre/post state, errors, and timing |

## Dry-run mode

Set `AGENTICORG_MIGRATION_DRY_RUN=1` in the environment (or pass
`dry_run=True`) to walk the migration's plan without writing
anything to row data. The wrapper still updates the
`alembic_migration_progress` row, so the audit JSON shows what
would have changed. Use this in staging before every production
rollout of an encrypted-column migration.

## Adding a new encrypted column

1. Add the model field with the standard `_encrypted` suffix.
2. Write the alembic migration using the template above.
3. Register the column in `core/crypto/verify_all.py:_SCANNERS`
   so `python -m core.crypto.verify_all` knows about it.
4. If the column feeds an in-process cache, add the cache
   invalidator to `_CACHE_INVALIDATORS` in the same file.
5. Add a regression test in `tests/regression/test_crypto_keyring.py`
   that exercises the new column through key rotation.

## CI enforcement

`scripts/check_encrypted_migration_uses_helpers.py` (Foundation
#5 step 3) refuses any PR that adds a migration referencing
`*_encrypted` columns without importing
`core.crypto.migration_helpers`.
