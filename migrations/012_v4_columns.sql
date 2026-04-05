-- v4.0.0: Add prompt_amendments column to agents table (self-improving agents)
-- Safe to run multiple times (IF NOT EXISTS pattern)

DO $$
BEGIN
    -- prompt_amendments: JSONB list of learned prompt amendments
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'agents' AND column_name = 'prompt_amendments'
    ) THEN
        ALTER TABLE agents ADD COLUMN prompt_amendments JSONB DEFAULT '[]'::jsonb;
    END IF;
END
$$;
