-- Migration 007: Virtual Employee Agent System
-- Enables multiple agent instances per type, persona identity, prompt templates, and audit trail.

BEGIN;

-- 1. Add persona & routing columns to agents table
ALTER TABLE agents
  ADD COLUMN IF NOT EXISTS employee_name VARCHAR(255),
  ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500),
  ADD COLUMN IF NOT EXISTS designation VARCHAR(255),
  ADD COLUMN IF NOT EXISTS specialization VARCHAR(500),
  ADD COLUMN IF NOT EXISTS routing_filter JSONB NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS is_builtin BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS system_prompt_text TEXT;

-- 2. Drop old unique constraint that prevents multiple agents of same type
ALTER TABLE agents DROP CONSTRAINT IF EXISTS agents_tenant_id_agent_type_version_key;

-- 3. Add new constraint: same type allowed if different employee_name
ALTER TABLE agents ADD CONSTRAINT agents_tenant_type_employee_version_key
  UNIQUE(tenant_id, agent_type, employee_name, version);

-- 4. Backfill existing agents
UPDATE agents SET is_builtin = TRUE WHERE is_builtin = FALSE;
UPDATE agents SET employee_name = name WHERE employee_name IS NULL;

-- 5. Index for routing queries (find active agents of a given type)
CREATE INDEX IF NOT EXISTS idx_agents_routing
  ON agents(tenant_id, agent_type, status) WHERE status = 'active';

-- 6. Create prompt_templates table
CREATE TABLE IF NOT EXISTS prompt_templates (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  name VARCHAR(255) NOT NULL,
  agent_type VARCHAR(100) NOT NULL,
  domain VARCHAR(50) NOT NULL,
  template_text TEXT NOT NULL,
  variables JSONB NOT NULL DEFAULT '[]',
  description TEXT,
  is_builtin BOOLEAN NOT NULL DEFAULT FALSE,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(tenant_id, name, agent_type)
);
CREATE INDEX IF NOT EXISTS idx_prompt_templates_type
  ON prompt_templates(tenant_id, agent_type);
CREATE INDEX IF NOT EXISTS idx_prompt_templates_domain
  ON prompt_templates(tenant_id, domain);

-- 7. Create prompt_edit_history table (audit trail)
CREATE TABLE IF NOT EXISTS prompt_edit_history (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  agent_id UUID NOT NULL REFERENCES agents(id),
  edited_by UUID REFERENCES users(id),
  prompt_before TEXT,
  prompt_after TEXT NOT NULL,
  change_reason VARCHAR(500),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_prompt_edit_history_agent
  ON prompt_edit_history(tenant_id, agent_id, created_at DESC);

COMMIT;
