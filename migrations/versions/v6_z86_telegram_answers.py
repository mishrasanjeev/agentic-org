# SPDX-License-Identifier: Apache-2.0
"""Opt-in final-answer replies; existing bindings and intake stay status-only."""

from alembic import op

revision = "v6z86_telegram_answers"
down_revision = "v6z85_lead_delivery_spend"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF to_regclass('public.channel_bindings') IS NOT NULL THEN
                ALTER TABLE channel_bindings ADD COLUMN IF NOT EXISTS
                    reply_mode VARCHAR(16) NOT NULL DEFAULT 'status';
                IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid='channel_bindings'::regclass AND conname='ck_channel_reply_mode') THEN
                    ALTER TABLE channel_bindings ADD CONSTRAINT ck_channel_reply_mode
                        CHECK (reply_mode IN ('status','agent_answer'));
                END IF;
            END IF;
            IF to_regclass('public.channel_deliveries') IS NOT NULL THEN
                ALTER TABLE channel_deliveries ADD COLUMN IF NOT EXISTS
                    reply_mode VARCHAR(16) NOT NULL DEFAULT 'status';
            END IF;
        END $$;
    """)


def downgrade() -> None:
    # Application rollback retains consent snapshots; old workers stay status-only.
    pass
