-- Migration 008: Sales Pipeline for Sales Agent
-- Lead tracking, email sequences, and pipeline management.

BEGIN;

-- 1. Lead pipeline table
CREATE TABLE IF NOT EXISTS lead_pipeline (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),

  -- Lead info (from demo request)
  name VARCHAR(255) NOT NULL,
  email VARCHAR(255) NOT NULL,
  company VARCHAR(255),
  role VARCHAR(100),
  phone VARCHAR(50),
  source VARCHAR(50) NOT NULL DEFAULT 'website',

  -- Pipeline state
  stage VARCHAR(50) NOT NULL DEFAULT 'new',
    -- new → contacted → qualified → demo_scheduled → demo_done → trial → negotiation → closed_won → closed_lost
  score INTEGER NOT NULL DEFAULT 0,
    -- 0-100 lead score based on role, company size, engagement
  score_factors JSONB NOT NULL DEFAULT '{}',

  -- Assignment
  assigned_agent_id UUID REFERENCES agents(id),
  assigned_human VARCHAR(255),

  -- Qualification (BANT)
  budget VARCHAR(100),
  authority VARCHAR(100),
  need TEXT,
  timeline VARCHAR(100),

  -- Tracking
  last_contacted_at TIMESTAMPTZ,
  next_followup_at TIMESTAMPTZ,
  followup_count INTEGER NOT NULL DEFAULT 0,
  demo_scheduled_at TIMESTAMPTZ,
  trial_started_at TIMESTAMPTZ,
  deal_value_usd NUMERIC(10, 2),
  lost_reason TEXT,
  notes TEXT,

  -- Metadata
  utm_source VARCHAR(100),
  utm_medium VARCHAR(100),
  utm_campaign VARCHAR(100),
  page_visits JSONB NOT NULL DEFAULT '[]',

  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lead_pipeline_tenant ON lead_pipeline(tenant_id, stage);
CREATE INDEX IF NOT EXISTS idx_lead_pipeline_email ON lead_pipeline(email);
CREATE INDEX IF NOT EXISTS idx_lead_pipeline_followup ON lead_pipeline(next_followup_at) WHERE stage NOT IN ('closed_won', 'closed_lost');

-- 2. Email sequence tracking
CREATE TABLE IF NOT EXISTS email_sequences (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  lead_id UUID NOT NULL REFERENCES lead_pipeline(id),
  sequence_name VARCHAR(100) NOT NULL DEFAULT 'initial_outreach',
  step_number INTEGER NOT NULL DEFAULT 0,
  email_subject VARCHAR(500),
  email_body TEXT,
  sent_at TIMESTAMPTZ,
  opened_at TIMESTAMPTZ,
  replied_at TIMESTAMPTZ,
  status VARCHAR(30) NOT NULL DEFAULT 'pending',
    -- pending → sent → opened → replied → bounced
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_email_sequences_lead ON email_sequences(lead_id, sequence_name, step_number);

COMMIT;
