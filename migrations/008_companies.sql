-- Initial company ownership. Apply only after a verified backup.
-- Does not enable public multi-tenant access or RLS.
BEGIN;
CREATE TABLE companies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL CHECK (length(btrim(name)) > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE company_memberships (
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, issuer, subject),
    CHECK (length(issuer) > 0 AND length(subject) > 0)
);
CREATE INDEX company_memberships_identity_idx ON company_memberships (issuer, subject);
ALTER TABLE buildings ADD COLUMN company_id UUID REFERENCES companies(id) ON DELETE RESTRICT;
-- A single initial company receives all existing buildings, including test data.
-- Rename it after deployment; do not infer ownership from building names.
INSERT INTO companies (name) VALUES ('Αρχική εταιρεία');
UPDATE buildings SET company_id = (SELECT id FROM companies LIMIT 1)
WHERE company_id IS NULL;
ALTER TABLE buildings ALTER COLUMN company_id SET NOT NULL;
CREATE INDEX buildings_company_idx ON buildings (company_id);
COMMIT;
