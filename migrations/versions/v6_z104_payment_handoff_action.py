# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Commerce arbiter: allow the ``payment_handoff`` action.

Revision ID: v6z104_payment_handoff_action
Revises: v6z102_mfa_challenge_generation
Create Date: 2026-10-29

A UCP or ACP Complete for a merchant that takes payment on its own page (a
Shopify cart permalink or a Plural hosted checkout) seals a ``payment_handoff``
decision (core/commerce/payment_handoff.py). The action check constraint on
``commerce_arbiter_decisions`` is widened to accept it; every existing row
already satisfies the wider check. No other schema change: the hand-off state
lives in the checkout session's state and the merchant's Plural credentials in
``connector_configs`` (encrypted, tenant row-level security unchanged).
"""

from alembic import op

revision = "v6z104_payment_handoff_action"
down_revision = "v6z102_mfa_challenge_generation"
branch_labels = None
depends_on = None

ACTIONS = ("purchase_handoff", "mandate_request", "pos_handoff", "session_complete", "refund", "payment_handoff")


def upgrade() -> None:
    allowed = ", ".join(f"'{action}'" for action in ACTIONS)
    op.execute("ALTER TABLE commerce_arbiter_decisions DROP CONSTRAINT IF EXISTS ck_commerce_arbiter_action;")
    op.execute(
        "ALTER TABLE commerce_arbiter_decisions ADD CONSTRAINT ck_commerce_arbiter_action "
        f"CHECK (action IN ({allowed}));"  # nosec B608 - the action names are the constants above
    )


def downgrade() -> None:
    # Forward-only: sealed payment_handoff decisions are ledger evidence and would violate the narrower check.
    pass
