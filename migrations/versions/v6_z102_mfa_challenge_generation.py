# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Persist the MFA challenge epoch without resetting existing revocations.

Revision ID: v6z102_mfa_challenge_generation
Revises: v6z99_onboarding_cases
Create Date: 2026-10-27

Additive on users; existing row-level security policies are unchanged.
Apply before deploying code that reads or increments the durable epoch.
"""

from alembic import op

revision = "v6z102_mfa_challenge_generation"
down_revision = "v6z99_onboarding_cases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_class
                WHERE oid = to_regclass('users') AND relkind IN ('r', 'p')
            ) THEN
                RAISE EXCEPTION 'MFA epoch migration requires a users table';
            END IF;

            -- Keep validation and backfill stable against concurrent writes.
            LOCK TABLE users IN ACCESS EXCLUSIVE MODE;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS
                mfa_challenge_generation BIGINT NOT NULL DEFAULT 0;

            IF EXISTS (
                SELECT 1 FROM pg_catalog.pg_attribute
                WHERE attrelid = 'users'::regclass
                  AND attname = 'mfa_challenge_generation' AND NOT attisdropped
                  AND (atttypid <> 'pg_catalog.int8'::regtype
                       OR attgenerated <> '' OR attidentity <> '')
            ) THEN
                RAISE EXCEPTION 'users.mfa_challenge_generation must be an ordinary BIGINT column';
            END IF;

            IF EXISTS (SELECT 1 FROM users WHERE mfa_challenge_generation < 0) THEN
                RAISE EXCEPTION 'users.mfa_challenge_generation contains a negative epoch';
            END IF;

            UPDATE users SET mfa_challenge_generation = 0
                WHERE mfa_challenge_generation IS NULL;
            ALTER TABLE users ALTER COLUMN mfa_challenge_generation SET DEFAULT 0;
            ALTER TABLE users ALTER COLUMN mfa_challenge_generation SET NOT NULL;
        END
        $$;
        """
    )


def downgrade() -> None:
    # Forward-only: dropping durable epochs could revive revoked challenges.
    raise RuntimeError("MFA challenge revocations are forward-only; downgrade is not supported")
