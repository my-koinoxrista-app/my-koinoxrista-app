-- Run once after a verified backup. Existing data and tables are unchanged.
BEGIN;
CREATE TABLE issued_statements (
    id UUID PRIMARY KEY,
    building_id TEXT NOT NULL REFERENCES buildings(id) ON DELETE RESTRICT,
    period_key TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    configuration JSONB NOT NULL,
    period_data JSONB NOT NULL,
    owner_names JSONB NOT NULL DEFAULT '{}'::jsonb,
    report_data JSONB NOT NULL,
    pdf_data BYTEA NOT NULL,
    pdf_sha256 TEXT NOT NULL,
    UNIQUE (building_id, period_key, revision)
);
CREATE INDEX IF NOT EXISTS issued_statements_building_date
    ON issued_statements (building_id, issued_at DESC);
-- Issued records are append-only. Corrections create a new revision.
CREATE FUNCTION reject_issued_statement_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Issued statements are immutable. Create a new revision.';
END;
$$;
CREATE TRIGGER issued_statements_immutable
BEFORE UPDATE OR DELETE ON issued_statements
FOR EACH ROW EXECUTE FUNCTION reject_issued_statement_mutation();
COMMIT;
