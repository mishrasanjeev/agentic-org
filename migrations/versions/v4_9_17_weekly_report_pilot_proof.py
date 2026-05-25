"""CMO-PROD-2 — weekly_report_pilot_proofs durable verdict log.

Revision ID: v4917_weekly_report_proof
Revises: v4916_merge_p0_heads
Create Date: 2026-05-24

CMO-PROD-1 added a strict, code-backed weekly-marketing-report pilot proof
gate. CMO-PROD-2 needs a durable home for verdicts and the redacted
evidence bundle that produced them, so the CMO dashboard / future UI can
read the latest verdict per tenant + company without re-running the
validator from ephemeral state.

Schema choices:
- ``proof_id`` is the deterministic hash from
  ``core.marketing.weekly_report_pilot_proof._proof_id`` so the same
  evidence input produces the same proof identity (idempotent re-runs
  can be detected).
- ``evidence_bundle`` and ``verdict`` are JSONB. They are persisted
  *after* the validator's secret-marker redaction; raw evidence with
  tokens or API keys never lands in this table.
- ``(tenant_id, company_id, evaluated_at DESC)`` composite index supports
  the common "latest verdict per tenant/company" lookup.
- Append-only by convention: the persistence helper inserts a new row
  per evaluation. There is no UPDATE path, so this is effectively a
  proof log.

Idempotent: CREATE TABLE / INDEX use IF NOT EXISTS so re-running on an
environment that already has the table is a no-op.
"""

from __future__ import annotations

from alembic import op

revision = "v4917_weekly_report_proof"
down_revision = "v4916_merge_p0_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS weekly_report_pilot_proofs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            company_id UUID,
            proof_id TEXT NOT NULL,
            environment_type TEXT NOT NULL,
            proof_status TEXT NOT NULL,
            production_claim_allowed BOOLEAN NOT NULL DEFAULT FALSE,
            real_vendor_claim_allowed BOOLEAN NOT NULL DEFAULT FALSE,
            readiness_score INTEGER NOT NULL DEFAULT 0,
            evaluated_at TIMESTAMPTZ NOT NULL,
            evidence_bundle JSONB NOT NULL DEFAULT '{}'::jsonb,
            verdict JSONB NOT NULL DEFAULT '{}'::jsonb,
            blockers JSONB NOT NULL DEFAULT '[]'::jsonb,
            next_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
            report_artifact_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            decision_audit_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_weekly_report_pilot_proofs_tenant_id
            ON weekly_report_pilot_proofs (tenant_id);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_weekly_report_pilot_proofs_proof_id
            ON weekly_report_pilot_proofs (proof_id);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_weekly_report_pilot_proofs_company_id
            ON weekly_report_pilot_proofs (company_id);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_weekly_report_pilot_proofs_tenant_evaluated
            ON weekly_report_pilot_proofs (tenant_id, company_id, evaluated_at);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_weekly_report_pilot_proofs_tenant_evaluated;")
    op.execute("DROP INDEX IF EXISTS ix_weekly_report_pilot_proofs_company_id;")
    op.execute("DROP INDEX IF EXISTS ix_weekly_report_pilot_proofs_proof_id;")
    op.execute("DROP INDEX IF EXISTS ix_weekly_report_pilot_proofs_tenant_id;")
    op.execute("DROP TABLE IF EXISTS weekly_report_pilot_proofs;")
