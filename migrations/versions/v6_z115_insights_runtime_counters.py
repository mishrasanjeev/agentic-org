# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights runtime counters: tool calls written by the tool gateway, guardrail events and security events.

Revision ID: v6z115_insights_counters
Revises: v6z114_insights_operations
Create Date: 2026-11-04

- ``tool_calls`` gets a writer (the tool gateway, ``core/insights/runtime_counters.py``).
  A gateway call has no workflow step, and its agent may not be a stored agent,
  so ``step_exec_id`` and ``agent_id`` become nullable. New columns:
  ``connector_name`` (the gateway addresses connectors by name), ``run_id``
  (the run the call was made in, when known), ``attempt`` (1 for the first
  try) and ``replayed`` (answered from the idempotency store, not dispatched).
  ``ix_tool_calls_tenant_called`` serves the period reads. The table already has
  forced row-level security on ``agenticorg.tenant_id``.
- ``guardrail_events``: per tenant and UTC day, how many guardrail outcomes each
  stage, detector, action and mode produced.
- ``security_events_daily``: per tenant and UTC day, how many of each security
  event kind happened (failed sign-ins, rate-limit and scope denials, session
  revocations, API-key use, webhook signature failures).

Both new tables are tenant-scoped with forced row-level security on
``agenticorg.tenant_id``, as every Insights table, and hold counts only.
Downgrade drops them and restores the two NOT NULL constraints; it refuses while
a tool call without a step or an agent exists.
"""

from alembic import op

revision = "v6z115_insights_counters"
down_revision = "v6z114_insights_operations"
branch_labels = None
depends_on = None

# The kinds as of this revision (core.models.insights.SECURITY_EVENT_KINDS must start equal).
SECURITY_KINDS = (
    "login_failed",
    "rate_limited",
    "scope_denied",
    "session_revoked",
    "api_key_used",
    "webhook_signature_failed",
)


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation
        ON {table}
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def upgrade() -> None:
    op.execute("ALTER TABLE tool_calls ALTER COLUMN step_exec_id DROP NOT NULL;")
    op.execute("ALTER TABLE tool_calls ALTER COLUMN agent_id DROP NOT NULL;")
    op.execute("ALTER TABLE tool_calls ADD COLUMN IF NOT EXISTS connector_name VARCHAR(100);")
    op.execute("ALTER TABLE tool_calls ADD COLUMN IF NOT EXISTS run_id UUID;")
    op.execute("ALTER TABLE tool_calls ADD COLUMN IF NOT EXISTS attempt SMALLINT NOT NULL DEFAULT 1;")
    op.execute("ALTER TABLE tool_calls ADD COLUMN IF NOT EXISTS replayed BOOLEAN NOT NULL DEFAULT false;")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tool_calls_tenant_called ON tool_calls (tenant_id, called_at);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS guardrail_events (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            day DATE NOT NULL,
            stage VARCHAR(16) NOT NULL,
            detector VARCHAR(64) NOT NULL,
            action VARCHAR(16) NOT NULL,
            mode VARCHAR(16) NOT NULL,
            count BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_guardrail_events_day UNIQUE (tenant_id, day, stage, detector, action, mode),
            CONSTRAINT ck_guardrail_events_count CHECK (count >= 0)
        );
        """
    )
    _rls("guardrail_events")

    kinds = ",".join(f"'{k}'" for k in SECURITY_KINDS)
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS security_events_daily (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            day DATE NOT NULL,
            event_kind VARCHAR(32) NOT NULL,
            count BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_security_events_daily_day UNIQUE (tenant_id, day, event_kind),
            CONSTRAINT ck_security_events_daily_kind CHECK (event_kind IN ({kinds})),
            CONSTRAINT ck_security_events_daily_count CHECK (count >= 0)
        );
        """
    )
    _rls("security_events_daily")


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM tool_calls WHERE step_exec_id IS NULL OR agent_id IS NULL) THEN
                RAISE EXCEPTION 'gateway tool calls exist; delete them before downgrading v6z115';
            END IF;
        END $$
        """
    )
    op.execute("DROP TABLE IF EXISTS security_events_daily;")
    op.execute("DROP TABLE IF EXISTS guardrail_events;")
    op.execute("DROP INDEX IF EXISTS ix_tool_calls_tenant_called;")
    for column in ("replayed", "attempt", "run_id", "connector_name"):
        op.execute(f"ALTER TABLE tool_calls DROP COLUMN IF EXISTS {column};")
    op.execute("ALTER TABLE tool_calls ALTER COLUMN agent_id SET NOT NULL;")
    op.execute("ALTER TABLE tool_calls ALTER COLUMN step_exec_id SET NOT NULL;")
