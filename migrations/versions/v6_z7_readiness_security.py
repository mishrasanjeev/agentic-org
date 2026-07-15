"""Harden readiness identity, evidence trust, governance history, and RLS.

Revision ID: v6z7_readiness_security
Revises: v6z6_connector_company_scope
Create Date: 2026-07-15
"""

from alembic import op

revision = "v6z7_readiness_security"
down_revision = "v6z6_connector_company_scope"
branch_labels = None
depends_on = None


_TABLES = (
    "capability_readiness_records",
    "capability_evidence_records",
    "capability_promotion_events",
)


def _install_rls(*, exact_writes: bool) -> None:
    for table in _TABLES:
        if exact_writes:
            write_scope = """
                tenant_id::text = current_setting('agenticorg.tenant_id', true)
                AND company_id IS NOT DISTINCT FROM
                    NULLIF(current_setting('agenticorg.company_id', true), '')::uuid
            """
        else:
            write_scope = """
                tenant_id::text = current_setting('agenticorg.tenant_id', true)
                AND (
                    company_id IS NULL
                    OR (
                        COALESCE(current_setting('agenticorg.company_id', true), '') <> ''
                        AND company_id::text = current_setting('agenticorg.company_id', true)
                    )
                )
            """
        op.execute(f"DROP POLICY IF EXISTS {table}_scope_isolation ON {table}")
        op.execute(f"""
            CREATE POLICY {table}_scope_isolation ON {table}
            USING (
                tenant_id::text = current_setting('agenticorg.tenant_id', true)
                AND (
                    company_id IS NULL
                    OR (
                        COALESCE(current_setting('agenticorg.company_id', true), '') <> ''
                        AND company_id::text = current_setting('agenticorg.company_id', true)
                    )
                )
            )
            WITH CHECK ({write_scope})
        """)


def _install_transition_guard(*, governance_events: bool) -> None:
    if governance_events:
        op.execute("""
            CREATE OR REPLACE FUNCTION guard_capability_readiness_transition() RETURNS trigger AS $$
            BEGIN
                IF ROW(NEW.domain, NEW.title, NEW.description, NEW.scope_disposition,
                       NEW.scope_condition, NEW.scope_details, NEW.required_gate_ids,
                       NEW.traceability, NEW.feature_flag, NEW.created_by, NEW.created_at)
                   IS DISTINCT FROM
                   ROW(OLD.domain, OLD.title, OLD.description, OLD.scope_disposition,
                       OLD.scope_condition, OLD.scope_details, OLD.required_gate_ids,
                       OLD.traceability, OLD.feature_flag, OLD.created_by, OLD.created_at)
                THEN RAISE EXCEPTION 'registered capability metadata is immutable'; END IF;

                IF ROW(NEW.internal_maturity_state, NEW.release_gate_state,
                       NEW.public_availability_state, NEW.claim_state, NEW.gate_results,
                       NEW.permitted_claim_ids, NEW.limitations, NEW.owners,
                       NEW.approver_ids, NEW.review_expires_at, NEW.current_promotion_sequence)
                   IS DISTINCT FROM
                   ROW(OLD.internal_maturity_state, OLD.release_gate_state,
                       OLD.public_availability_state, OLD.claim_state, OLD.gate_results,
                       OLD.permitted_claim_ids, OLD.limitations, OLD.owners,
                       OLD.approver_ids, OLD.review_expires_at, OLD.current_promotion_sequence)
                THEN
                    IF NEW.current_promotion_sequence <> OLD.current_promotion_sequence + 1
                       OR NOT EXISTS (
                        SELECT 1 FROM capability_promotion_events e
                        WHERE e.readiness_record_id = NEW.id
                          AND e.sequence = NEW.current_promotion_sequence
                          AND e.to_internal_maturity = NEW.internal_maturity_state
                          AND e.to_release_gate = NEW.release_gate_state
                          AND e.to_public_availability = NEW.public_availability_state
                          AND e.to_claim_state = NEW.claim_state
                          AND e.gate_results_snapshot = NEW.gate_results
                          AND e.permitted_claim_ids = NEW.permitted_claim_ids
                          AND e.limitations = NEW.limitations
                          AND e.requested_by = NEW.updated_by
                          AND e.ownership_snapshot->'owners' = NEW.owners
                          AND e.ownership_snapshot->'approver_ids' = COALESCE(
                              (
                                SELECT jsonb_agg(value ORDER BY value)
                                FROM jsonb_array_elements_text(NEW.approver_ids) AS items(value)
                              ),
                              '[]'::jsonb
                          )
                          AND (e.ownership_snapshot->>'review_expires_at')::timestamptz
                              IS NOT DISTINCT FROM NEW.review_expires_at
                          AND (
                            (
                              (NEW.owners IS DISTINCT FROM OLD.owners
                               OR NEW.approver_ids IS DISTINCT FROM OLD.approver_ids)
                              AND NEW.review_expires_at IS NOT DISTINCT FROM OLD.review_expires_at
                              AND e.event_type = 'owners_rotated'
                            )
                            OR (
                              NEW.owners IS NOT DISTINCT FROM OLD.owners
                              AND NEW.approver_ids IS NOT DISTINCT FROM OLD.approver_ids
                              AND NEW.review_expires_at IS DISTINCT FROM OLD.review_expires_at
                              AND e.event_type = 'review_renewed'
                            )
                            OR (
                              NEW.owners IS NOT DISTINCT FROM OLD.owners
                              AND NEW.approver_ids IS NOT DISTINCT FROM OLD.approver_ids
                              AND NEW.review_expires_at IS NOT DISTINCT FROM OLD.review_expires_at
                              AND e.event_type IN ('promoted', 'demoted', 'attested')
                            )
                          )
                    ) THEN
                        RAISE EXCEPTION 'readiness changes require a matching immutable governance event';
                    END IF;
                ELSIF ROW(NEW.updated_by, NEW.updated_at)
                      IS DISTINCT FROM ROW(OLD.updated_by, OLD.updated_at)
                THEN
                    RAISE EXCEPTION 'readiness audit metadata requires a matching immutable governance event';
                END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql;
        """)
    else:
        op.execute("""
            CREATE OR REPLACE FUNCTION guard_capability_readiness_transition() RETURNS trigger AS $$
            BEGIN
                IF ROW(NEW.domain, NEW.title, NEW.description, NEW.scope_disposition, NEW.scope_condition,
                       NEW.scope_details, NEW.required_gate_ids, NEW.owners, NEW.approver_ids,
                       NEW.traceability, NEW.feature_flag, NEW.review_expires_at, NEW.created_by, NEW.created_at)
                   IS DISTINCT FROM
                   ROW(OLD.domain, OLD.title, OLD.description, OLD.scope_disposition, OLD.scope_condition,
                       OLD.scope_details, OLD.required_gate_ids, OLD.owners, OLD.approver_ids,
                       OLD.traceability, OLD.feature_flag, OLD.review_expires_at, OLD.created_by, OLD.created_at)
                THEN RAISE EXCEPTION 'registered capability metadata is immutable'; END IF;
                IF ROW(NEW.internal_maturity_state, NEW.release_gate_state, NEW.public_availability_state,
                       NEW.claim_state, NEW.gate_results, NEW.permitted_claim_ids, NEW.limitations,
                       NEW.current_promotion_sequence)
                   IS DISTINCT FROM
                   ROW(OLD.internal_maturity_state, OLD.release_gate_state, OLD.public_availability_state,
                       OLD.claim_state, OLD.gate_results, OLD.permitted_claim_ids, OLD.limitations,
                       OLD.current_promotion_sequence)
                THEN
                    IF NEW.current_promotion_sequence <> OLD.current_promotion_sequence + 1 OR NOT EXISTS (
                        SELECT 1 FROM capability_promotion_events e
                        WHERE e.readiness_record_id = NEW.id
                          AND e.sequence = NEW.current_promotion_sequence
                          AND e.to_internal_maturity = NEW.internal_maturity_state
                          AND e.to_release_gate = NEW.release_gate_state
                          AND e.to_public_availability = NEW.public_availability_state
                          AND e.to_claim_state = NEW.claim_state
                          AND e.gate_results_snapshot = NEW.gate_results
                          AND e.permitted_claim_ids = NEW.permitted_claim_ids
                          AND e.limitations = NEW.limitations
                          AND e.requested_by = NEW.updated_by
                    ) THEN
                        RAISE EXCEPTION 'readiness changes require a matching immutable promotion event';
                    END IF;
                END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql;
        """)


def upgrade() -> None:
    op.execute("ALTER TABLE capability_evidence_records ALTER COLUMN reviewed_at DROP NOT NULL")
    op.execute("ALTER TABLE capability_evidence_records ALTER COLUMN reviewed_by DROP NOT NULL")
    op.execute(
        "ALTER TABLE capability_evidence_records "
        "ADD COLUMN IF NOT EXISTS trust_state VARCHAR(20) NOT NULL DEFAULT 'unverified'"
    )
    op.execute("ALTER TABLE capability_evidence_records ADD COLUMN IF NOT EXISTS submitted_by VARCHAR(255)")
    op.execute("UPDATE capability_evidence_records SET submitted_by = created_by WHERE submitted_by IS NULL")
    op.execute("ALTER TABLE capability_evidence_records ALTER COLUMN submitted_by SET NOT NULL")
    op.execute("ALTER TABLE capability_evidence_records DROP CONSTRAINT IF EXISTS ck_capability_evidence_times")
    op.execute("""
        ALTER TABLE capability_evidence_records
        ADD CONSTRAINT ck_capability_evidence_times CHECK (
          observed_at < expires_at
          AND (reviewed_at IS NULL OR (observed_at <= reviewed_at AND reviewed_at < expires_at))
        )
    """)
    op.execute(
        "ALTER TABLE capability_evidence_records "
        "DROP CONSTRAINT IF EXISTS ck_capability_evidence_trust_state"
    )
    op.execute(
        "ALTER TABLE capability_evidence_records "
        "ADD CONSTRAINT ck_capability_evidence_trust_state "
        "CHECK (trust_state IN ('unverified', 'verified', 'rejected'))"
    )
    op.execute("ALTER TABLE capability_promotion_events DROP CONSTRAINT IF EXISTS ck_capability_promotion_event_type")
    op.execute("""
        ALTER TABLE capability_promotion_events
        ADD CONSTRAINT ck_capability_promotion_event_type CHECK (
          event_type IN (
            'registered', 'promoted', 'demoted', 'attested',
            'review_renewed', 'owners_rotated'
          )
        )
    """)
    _install_transition_guard(governance_events=True)
    _install_rls(exact_writes=True)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM capability_promotion_events
            WHERE event_type IN ('review_renewed', 'owners_rotated')
          ) THEN
            RAISE EXCEPTION 'cannot downgrade readiness security with governance history present';
          END IF;
          IF EXISTS (
            SELECT 1 FROM capability_evidence_records
            WHERE reviewed_at IS NULL OR reviewed_by IS NULL
          ) THEN
            RAISE EXCEPTION 'cannot downgrade readiness security with unreviewed evidence present';
          END IF;
        END $$;
    """)
    _install_transition_guard(governance_events=False)
    _install_rls(exact_writes=False)
    op.execute("ALTER TABLE capability_promotion_events DROP CONSTRAINT IF EXISTS ck_capability_promotion_event_type")
    op.execute("""
        ALTER TABLE capability_promotion_events
        ADD CONSTRAINT ck_capability_promotion_event_type
        CHECK (event_type IN ('registered', 'promoted', 'demoted', 'attested'))
    """)
    op.execute("ALTER TABLE capability_evidence_records DROP CONSTRAINT IF EXISTS ck_capability_evidence_trust_state")
    op.execute("ALTER TABLE capability_evidence_records DROP CONSTRAINT IF EXISTS ck_capability_evidence_times")
    op.execute("""
        ALTER TABLE capability_evidence_records
        ADD CONSTRAINT ck_capability_evidence_times
        CHECK (observed_at <= reviewed_at AND reviewed_at < expires_at)
    """)
    op.execute("ALTER TABLE capability_evidence_records ALTER COLUMN reviewed_at SET NOT NULL")
    op.execute("ALTER TABLE capability_evidence_records ALTER COLUMN reviewed_by SET NOT NULL")
    op.execute("ALTER TABLE capability_evidence_records DROP COLUMN submitted_by")
    op.execute("ALTER TABLE capability_evidence_records DROP COLUMN trust_state")
