-- Repair for migration 010: company_mail_settings has no building_id.
-- Apply once to the intended database as the migration administrator.
-- No tables, financial records, PDFs, delivery statuses, or RLS policies are changed.
-- This is a standalone repair, NOT a rerun of migration 010 or a lifecycle 011.
BEGIN;
DO $preflight$
DECLARE
    t text;
BEGIN
    IF to_regprocedure('public.koinoxrista_delivery_guard()') IS NULL THEN
        RAISE EXCEPTION 'Migration 010 delivery guard is missing.';
    END IF;
    FOREACH t IN ARRAY ARRAY[
        'property_delivery_contacts', 'company_mail_settings',
        'issued_property_pdfs', 'statement_email_deliveries'
    ] LOOP
        IF to_regclass('public.' || t) IS NULL THEN
            RAISE EXCEPTION 'Expected migration 010 table is missing: %', t;
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_trigger tr
            WHERE tr.tgrelid = to_regclass('public.' || t)
              AND tr.tgname = 'delivery_owner_guard'
              AND NOT tr.tgisinternal
              AND tr.tgfoid = to_regprocedure('public.koinoxrista_delivery_guard()')
        ) THEN
            RAISE EXCEPTION 'Expected delivery guard trigger is missing or changed on %', t;
        END IF;
    END LOOP;
END $preflight$;

CREATE OR REPLACE FUNCTION public.koinoxrista_delivery_guard()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $function$
DECLARE actual_company uuid; expected_property jsonb; expected_apartment text; expected_digest text;
BEGIN
    IF TG_OP='UPDATE' THEN
        IF NEW.company_id IS DISTINCT FROM OLD.company_id THEN
            RAISE EXCEPTION 'Company ownership cannot be changed' USING ERRCODE='42501';
        END IF;
        IF TG_TABLE_NAME <> 'company_mail_settings'
           AND (to_jsonb(NEW)->>'building_id') IS DISTINCT FROM
               (to_jsonb(OLD)->>'building_id') THEN
            RAISE EXCEPTION 'Company ownership cannot be changed' USING ERRCODE='42501';
        END IF;
        IF TG_TABLE_NAME='statement_email_deliveries' THEN
            IF (to_jsonb(NEW) - ARRAY['status','claim_token','attempt_count','claimed_at','accepted_at','last_error'])
               IS DISTINCT FROM
               (to_jsonb(OLD) - ARRAY['status','claim_token','attempt_count','claimed_at','accepted_at','last_error']) THEN
                RAISE EXCEPTION 'Prepared delivery content is immutable' USING ERRCODE='42501';
            END IF;
        END IF;
    END IF;
    IF TG_TABLE_NAME='company_mail_settings' THEN
        actual_company := NEW.company_id;
    ELSE
        SELECT b.company_id INTO actual_company FROM public.buildings b WHERE b.id=NEW.building_id;
    END IF;
    IF actual_company IS NULL OR NEW.company_id IS DISTINCT FROM actual_company THEN
        RAISE EXCEPTION 'Company ownership mismatch' USING ERRCODE='42501';
    END IF;
    IF session_user='koinoxrista_app' AND NOT public.koinoxrista_can_access(actual_company) THEN
        RAISE EXCEPTION 'Company access denied' USING ERRCODE='42501';
    END IF;
    IF TG_TABLE_NAME='issued_property_pdfs' AND TG_OP='INSERT' THEN
        SELECT elem.value INTO expected_property
        FROM public.issued_statements s,
             LATERAL jsonb_array_elements(s.report_data->'apartments') AS elem(value)
        WHERE s.id=NEW.statement_id AND s.building_id=NEW.building_id
          AND elem.value->>'id'=NEW.apartment_id;
        IF expected_property IS NULL OR NEW.property_data IS DISTINCT FROM expected_property
           OR NEW.pdf_sha256 IS DISTINCT FROM encode(public.digest(NEW.pdf_data,'sha256'),'hex') THEN
            RAISE EXCEPTION 'Archived property PDF does not match its issued snapshot' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_TABLE_NAME='statement_email_deliveries' AND TG_OP='INSERT' THEN
        SELECT p.apartment_id,p.pdf_sha256 INTO expected_apartment,expected_digest
        FROM public.issued_property_pdfs p
        WHERE p.id=NEW.property_pdf_id AND p.company_id=NEW.company_id
          AND p.building_id=NEW.building_id AND p.statement_id=NEW.statement_id;
        IF expected_apartment IS DISTINCT FROM NEW.apartment_id
           OR expected_digest IS DISTINCT FROM NEW.pdf_sha256 THEN
            RAISE EXCEPTION 'Delivery does not match its archived property PDF' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_TABLE_NAME='statement_email_deliveries' THEN
        IF TG_OP='INSERT' THEN
            IF NEW.status<>'PENDING' OR NEW.attempt_count<>0
               OR NEW.claim_token IS NOT NULL OR NEW.claimed_at IS NOT NULL
               OR NEW.accepted_at IS NOT NULL OR NEW.last_error<>'' THEN
                RAISE EXCEPTION 'Delivery must start pending' USING ERRCODE='42501';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END $function$;

COMMIT;
