# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Authenticator-app (TOTP) multi-factor sign-in for users.

Revision ID: v6z94_user_totp_mfa
Revises: v6z93_custom_domain_firebase
Create Date: 2026-10-25

``users.mfa_enabled`` has existed since the first schema but nothing set it.
These columns hold the enrolment it now gates:

* ``mfa_totp_secret``: the confirmed authenticator secret, encrypted with the
  credential vault (never stored in clear).
* ``mfa_pending_secret`` / ``mfa_pending_created_at``: a secret shown as a QR
  code but not yet confirmed with a code; replaced by every new setup.
* ``mfa_last_totp_step``: the last 30-second step accepted, so a code cannot
  be replayed inside its window.
* ``mfa_recovery_codes``: SHA-256 hashes of unused one-time recovery codes.
* ``mfa_enabled_at``: when the user confirmed enrolment.

Additive columns on ``users``; its row-level security policies are unchanged.
"""

from alembic import op

revision = "v6z94_user_totp_mfa"
down_revision = "v6z93_custom_domain_firebase"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_totp_secret TEXT;")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_pending_secret TEXT;")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_pending_created_at TIMESTAMPTZ;")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_last_totp_step BIGINT;")
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_recovery_codes JSONB NOT NULL DEFAULT '[]'::jsonb;"
    )
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_enabled_at TIMESTAMPTZ;")


def downgrade() -> None:
    for column in (
        "mfa_enabled_at",
        "mfa_recovery_codes",
        "mfa_last_totp_step",
        "mfa_pending_created_at",
        "mfa_pending_secret",
        "mfa_totp_secret",
    ):
        op.execute(f"ALTER TABLE users DROP COLUMN IF EXISTS {column};")
