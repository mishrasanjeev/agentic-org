-- Migration 009: Org chart hierarchy support
-- Adds reporting_to display field for agent hierarchy visualization.
-- parent_agent_id FK already exists in agents table.

BEGIN;

ALTER TABLE agents ADD COLUMN IF NOT EXISTS reporting_to VARCHAR(255);

COMMIT;
