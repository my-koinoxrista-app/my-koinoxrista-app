-- Run with scripts/apply_tenancy.py, after 007 and 008 and a verified backup.
-- The installer creates koinoxrista_app and executes this file in one transaction.
-- Never run with the runtime role. This migration is intentionally not idempotent.
DO $preflight$
DECLARE
    missing text[];
    existing_policies integer;
    extra text;
    r record;
BEGIN
    SELECT array_agg(name) INTO missing FROM unnest(ARRAY[
        'companies','company_memberships','buildings','apartments',
        'allocation_tables','allocation_shares','allocation_rules',
        'allocation_rule_participants','expense_categories','building_facilities',
        'heating_systems','monthly_periods','receipt_documents','issued_statements'
    ]) AS name WHERE to_regclass('public.' || name) IS NULL;
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Missing required tables: %', missing;
    END IF;
    IF EXISTS(SELECT 1 FROM buildings WHERE company_id IS NULL) THEN
        RAISE EXCEPTION 'Unassigned buildings. Review migration 008 before continuing.';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='koinoxrista_app'
        AND (rolsuper OR rolbypassrls OR rolcreaterole)) THEN
        RAISE EXCEPTION 'Runtime role has forbidden privileges.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='koinoxrista_app') THEN
        RAISE EXCEPTION 'Create the restricted role with the installer first.';
    END IF;
    SELECT count(*) INTO existing_policies FROM pg_policy p
        JOIN pg_class c ON c.oid=p.polrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=ANY(ARRAY[
            'companies','company_memberships','buildings','apartments',
            'allocation_tables','allocation_shares','allocation_rules',
            'allocation_rule_participants','expense_categories','building_facilities',
            'heating_systems','monthly_periods','receipt_documents','issued_statements']);
    IF existing_policies <> 0 THEN
        RAISE EXCEPTION 'Existing RLS policies detected. Review schema; do not overwrite them.';
    END IF;
    -- Reject unknown tables linked to the building-owned graph.
    SELECT c.relname INTO extra FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind IN ('r','p')
      AND c.relname <> ALL(ARRAY[
        'companies','company_memberships','buildings','apartments',
        'allocation_tables','allocation_shares','allocation_rules',
        'allocation_rule_participants','expense_categories','building_facilities',
        'heating_systems','monthly_periods','receipt_documents','issued_statements'])
      AND (EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid
                   AND a.attname='building_id' AND NOT a.attisdropped)
           OR EXISTS (SELECT 1 FROM pg_constraint f WHERE f.conrelid=c.oid
                  AND f.contype='f' AND f.confrelid IN (
                      SELECT oid FROM pg_class WHERE relname=ANY(ARRAY[
                        'buildings','apartments','allocation_tables','allocation_shares',
                        'allocation_rules','allocation_rule_participants','expense_categories',
                        'building_facilities','heating_systems','monthly_periods',
                        'receipt_documents','issued_statements'])
                      AND relnamespace='public'::regnamespace))) LIMIT 1;
    IF extra IS NOT NULL THEN
        RAISE EXCEPTION 'Unreviewed dependent table: %', extra;
    END IF;
    -- The child policy depends on a real building FK, not merely a matching column.
    FOR r IN SELECT * FROM (VALUES
        ('apartments','buildings'),('allocation_tables','buildings'),
        ('allocation_rules','buildings'),('expense_categories','buildings'),
        ('building_facilities','buildings'),('heating_systems','buildings'),
        ('monthly_periods','buildings'),('receipt_documents','buildings'),
        ('issued_statements','buildings')
    ) AS expected(child,parent) LOOP
        IF NOT EXISTS(SELECT 1 FROM pg_constraint f
            WHERE f.contype='f' AND f.conrelid=to_regclass('public.'||r.child)
              AND f.confrelid=to_regclass('public.'||r.parent)
              AND f.confdeltype IN ('a','r')
              AND EXISTS(SELECT 1 FROM unnest(f.conkey) WITH ORDINALITY ck(attnum,ord)
                JOIN unnest(f.confkey) WITH ORDINALITY pk(attnum,ord) USING(ord)
                JOIN pg_attribute ca ON ca.attrelid=f.conrelid AND ca.attnum=ck.attnum
                JOIN pg_attribute pa ON pa.attrelid=f.confrelid AND pa.attnum=pk.attnum
                WHERE ca.attname='building_id' AND pa.attname='id')) THEN
            RAISE EXCEPTION 'Missing building FK for %',r.child;
        END IF;
    END LOOP;
    IF (SELECT count(*) FROM pg_constraint WHERE conrelid='public.buildings'::regclass
        AND conname='buildings_company_id_fkey' AND contype='f') <> 1 THEN
        RAISE EXCEPTION 'Expected building-to-company FK is missing.';
    END IF;
    FOR r IN SELECT * FROM (VALUES
        ('allocation_shares','allocation_tables','allocation_table_id'),
        ('allocation_shares','apartments','apartment_id'),
        ('allocation_rule_participants','allocation_rules','rule_id'),
        ('allocation_rule_participants','apartments','apartment_id'),
        ('allocation_rules','allocation_tables','allocation_table_id'),
        ('expense_categories','allocation_rules','default_rule_id')
    ) AS expected(child,parent,reference_column) LOOP
        IF NOT EXISTS(SELECT 1 FROM pg_constraint f
            WHERE f.contype='f' AND f.conrelid=to_regclass('public.'||r.child)
              AND f.confrelid=to_regclass('public.'||r.parent)
              AND f.confdeltype IN ('a','r')
              AND (SELECT array_agg(ca.attname::text ORDER BY ck.ord)
                   FROM unnest(f.conkey) WITH ORDINALITY ck(attnum,ord)
                   JOIN pg_attribute ca ON ca.attrelid=f.conrelid AND ca.attnum=ck.attnum)
                  = ARRAY['building_id',r.reference_column]
              AND (SELECT array_agg(pa.attname::text ORDER BY pk.ord)
                   FROM unnest(f.confkey) WITH ORDINALITY pk(attnum,ord)
                   JOIN pg_attribute pa ON pa.attrelid=f.confrelid AND pa.attnum=pk.attnum)
                  = ARRAY['building_id','id']) THEN
            RAISE EXCEPTION 'Missing composite FK from % to %',r.child,r.parent;
        END IF;
    END LOOP;
END $preflight$;

-- The application role receives no ownership, schema creation, role management,
-- TRUNCATE, or administrative grants. It must never inherit the owner role.
ALTER ROLE koinoxrista_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO koinoxrista_app;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM koinoxrista_app;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM koinoxrista_app;
DO $database_grants$
BEGIN
    EXECUTE format('REVOKE CREATE, TEMPORARY ON DATABASE %I FROM PUBLIC',current_database());
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO koinoxrista_app',current_database());
END $database_grants$;

-- A private signing key verifies claims without exposing it to the runtime role.
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;
DO $crypto$
BEGIN
    IF to_regprocedure('public.hmac(bytea,bytea,text)') IS NULL THEN
        RAISE EXCEPTION 'pgcrypto must be installed in public for this migration.';
    END IF;
END $crypto$;
CREATE SCHEMA tenant_security;
REVOKE ALL ON SCHEMA tenant_security FROM PUBLIC;
CREATE TABLE tenant_security.scope_secret (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    secret bytea NOT NULL CHECK (octet_length(secret) >= 32)
);
REVOKE ALL ON tenant_security.scope_secret FROM PUBLIC;

-- SECURITY DEFINER runs as the owner. No function exposes the signing key or
-- signs caller-supplied claims. A malformed/expired context is denied.
CREATE FUNCTION public.koinoxrista_verified_claims()
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $function$
DECLARE
    payload text := current_setting('app.context',true);
    signature text := current_setting('app.signature',true);
    key bytea;
    claims jsonb;
BEGIN
    IF payload IS NULL OR signature IS NULL THEN RETURN NULL; END IF;
    SELECT secret INTO key FROM tenant_security.scope_secret WHERE id=true;
    IF key IS NULL OR signature IS DISTINCT FROM
       encode(public.hmac(convert_to(payload,'UTF8'),key,'sha256'),'hex') THEN
        RETURN NULL;
    END IF;
    claims := payload::jsonb;
    IF jsonb_typeof(claims) <> 'object'
       OR jsonb_typeof(claims->'exp') <> 'number'
       OR (claims->>'exp')::numeric <= extract(epoch FROM statement_timestamp())
       OR NULLIF(claims->>'iss','') IS NULL
       OR NULLIF(claims->>'sub','') IS NULL THEN
        RETURN NULL;
    END IF;
    RETURN claims;
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range
            OR invalid_parameter_value THEN
    RETURN NULL;
END $function$;
REVOKE ALL ON FUNCTION public.koinoxrista_verified_claims() FROM PUBLIC;
-- Returns only the caller's verified claims, never the private signing key.
GRANT EXECUTE ON FUNCTION public.koinoxrista_verified_claims() TO koinoxrista_app;

CREATE FUNCTION public.koinoxrista_my_company(p_company uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $function$
    SELECT p_company IS NOT NULL AND EXISTS(
        SELECT 1 FROM public.company_memberships m
        CROSS JOIN (SELECT public.koinoxrista_verified_claims() AS c) claims
        WHERE m.company_id=p_company
          AND m.issuer=claims.c->>'iss' AND m.subject=claims.c->>'sub'
    );
$function$;
REVOKE ALL ON FUNCTION public.koinoxrista_my_company(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.koinoxrista_my_company(uuid) TO koinoxrista_app;

CREATE FUNCTION public.koinoxrista_can_access(p_company uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $function$
    SELECT p_company IS NOT NULL AND EXISTS(
        SELECT 1 FROM public.company_memberships m
        CROSS JOIN (SELECT public.koinoxrista_verified_claims() AS c) claims
        WHERE m.company_id=p_company
          AND m.issuer=claims.c->>'iss' AND m.subject=claims.c->>'sub'
          AND p_company::text=claims.c->>'company'
    );
$function$;
REVOKE ALL ON FUNCTION public.koinoxrista_can_access(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.koinoxrista_can_access(uuid) TO koinoxrista_app;

ALTER TABLE public.company_memberships ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_membership_read ON public.company_memberships
FOR SELECT TO koinoxrista_app
USING (issuer=public.koinoxrista_verified_claims()->>'iss'
       AND subject=public.koinoxrista_verified_claims()->>'sub');
GRANT SELECT ON public.company_memberships TO koinoxrista_app;

-- Company discovery works before a company is selected, but only for a
-- verified identity. The SECURITY DEFINER helper avoids recursive auth RLS.
ALTER TABLE public.companies ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_company_read ON public.companies
FOR SELECT TO koinoxrista_app USING (public.koinoxrista_my_company(id));
GRANT SELECT ON public.companies TO koinoxrista_app;

-- Insert the trusted company from transaction-local context. The application
-- cannot provide a different owner or transfer an existing building.
CREATE FUNCTION public.koinoxrista_building_owner()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $function$
DECLARE tenant uuid;
BEGIN
    -- Preserve normal offline superuser maintenance without allowing ownership
    -- transfers through the application. The runtime role must always be signed.
    IF TG_OP='UPDATE' AND NEW.company_id IS DISTINCT FROM OLD.company_id THEN
        RAISE EXCEPTION 'Building ownership cannot be changed' USING ERRCODE='42501';
    END IF;
    IF session_user <> 'koinoxrista_app' AND EXISTS(
        SELECT 1 FROM pg_roles WHERE rolname=session_user AND rolsuper) THEN
        RETURN NEW;
    END IF;
    tenant := NULLIF(public.koinoxrista_verified_claims()->>'company','')::uuid;
    IF NOT public.koinoxrista_can_access(tenant) THEN
        RAISE EXCEPTION 'Company access denied' USING ERRCODE='42501';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.company_id IS NOT NULL AND NEW.company_id <> tenant THEN
            RAISE EXCEPTION 'Company ownership mismatch' USING ERRCODE='42501';
        END IF;
        NEW.company_id := tenant;
    ELSIF NEW.company_id IS DISTINCT FROM OLD.company_id THEN
        RAISE EXCEPTION 'Building ownership cannot be changed' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END $function$;
REVOKE ALL ON FUNCTION public.koinoxrista_building_owner() FROM PUBLIC;
CREATE TRIGGER koinoxrista_building_owner_guard
BEFORE INSERT OR UPDATE ON public.buildings
FOR EACH ROW EXECUTE FUNCTION public.koinoxrista_building_owner();

ALTER TABLE public.buildings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.buildings FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_buildings ON public.buildings FOR ALL TO koinoxrista_app
USING (public.koinoxrista_can_access(company_id))
WITH CHECK (public.koinoxrista_can_access(company_id));
GRANT SELECT,INSERT,UPDATE,DELETE ON public.buildings TO koinoxrista_app;

-- All children inherit their tenant through the existing building FK. Their
-- composite FKs continue to prevent cross-building relational references.
DO $policies$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'apartments','allocation_tables','allocation_shares','allocation_rules',
        'allocation_rule_participants','expense_categories','building_facilities',
        'heating_systems','monthly_periods','receipt_documents','issued_statements'
    ] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY tenant_owned ON public.%I FOR ALL TO koinoxrista_app '
            || 'USING (EXISTS (SELECT 1 FROM public.buildings b WHERE b.id=building_id)) '
            || 'WITH CHECK (EXISTS (SELECT 1 FROM public.buildings b WHERE b.id=building_id))',t);
        EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON public.%I TO koinoxrista_app',t);
    END LOOP;
END $policies$;

-- The immutable-history trigger remains in force, even for the new role.
-- Deny access to unreviewed functions and prevent default public execution grants.
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM koinoxrista_app;
GRANT EXECUTE ON FUNCTION public.koinoxrista_verified_claims() TO koinoxrista_app;
GRANT EXECUTE ON FUNCTION public.koinoxrista_can_access(uuid) TO koinoxrista_app;
GRANT EXECUTE ON FUNCTION public.koinoxrista_my_company(uuid) TO koinoxrista_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- Fail if any of the required child tables lacks a building_id column.
DO $verify$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'apartments','allocation_tables','allocation_shares','allocation_rules',
        'allocation_rule_participants','expense_categories','building_facilities',
        'heating_systems','monthly_periods','receipt_documents','issued_statements'
    ] LOOP
        IF NOT EXISTS(SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name=t AND column_name='building_id') THEN
            RAISE EXCEPTION 'Missing building_id in %',t;
        END IF;
    END LOOP;
END $verify$;
