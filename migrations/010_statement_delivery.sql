-- Archived individual statements, tenant contacts and audited SMTP submission.
-- Apply ONCE after 009, with the PostgreSQL administrator and a verified backup.
-- No existing financial rows, PDFs, allocations or memberships are modified.
BEGIN;
DO $preflight$
BEGIN
    IF to_regclass('public.issued_statements') IS NULL
       OR to_regclass('public.company_memberships') IS NULL
       OR to_regprocedure('public.koinoxrista_can_access(uuid)') IS NULL
       OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='koinoxrista_app'
                      AND NOT rolsuper AND NOT rolbypassrls) THEN
        RAISE EXCEPTION 'Migration 009 is required before 010.';
    END IF;
    IF to_regclass('public.issued_property_pdfs') IS NOT NULL
       OR to_regclass('public.property_delivery_contacts') IS NOT NULL
       OR to_regclass('public.company_mail_settings') IS NOT NULL
       OR to_regclass('public.statement_email_deliveries') IS NOT NULL
       OR to_regclass('public.property_email_contacts') IS NOT NULL
       OR to_regclass('public.company_email_settings') IS NOT NULL THEN
        RAISE EXCEPTION 'Delivery tables already exist. Review the schema; do not rerun 010.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid='public.apartments'::regclass
                   AND contype IN ('p','u') AND (SELECT array_agg(a.attname::text ORDER BY k.ord) FROM unnest(conkey) WITH ORDINALITY k(attnum,ord) JOIN pg_attribute a ON a.attrelid=conrelid AND a.attnum=k.attnum)=ARRAY['building_id','id']) THEN
        RAISE EXCEPTION 'Expected composite apartment key is missing.';
    END IF;
    IF EXISTS (SELECT 1 FROM public.issued_statements s
               LEFT JOIN public.buildings b ON b.id=s.building_id
               WHERE b.id IS NULL OR b.company_id IS NULL) THEN
        RAISE EXCEPTION 'Unassigned issued statements must be reviewed.';
    END IF;
END $preflight$;

ALTER TABLE public.buildings ADD CONSTRAINT buildings_company_id_id_delivery_key UNIQUE(company_id,id);
ALTER TABLE public.issued_statements ADD CONSTRAINT issued_statements_building_id_id_delivery_key UNIQUE(building_id,id);

CREATE TABLE public.property_delivery_contacts (
    company_id uuid NOT NULL,
    building_id text NOT NULL,
    apartment_id text NOT NULL,
    tenant_name text NOT NULL DEFAULT '',
    email text NOT NULL DEFAULT '',
    email_enabled boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(building_id,apartment_id),
    FOREIGN KEY(company_id,building_id) REFERENCES public.buildings(company_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(building_id,apartment_id) REFERENCES public.apartments(building_id,id) ON DELETE RESTRICT,
    CHECK(length(tenant_name)<=200), CHECK(length(email)<=254),
    CHECK(NOT email_enabled OR (email<>'' AND tenant_name<>'')),
    CHECK(email='' OR email=btrim(email))
);
CREATE INDEX property_delivery_contacts_company_idx ON public.property_delivery_contacts(company_id,building_id);

CREATE TABLE public.company_mail_settings (
    company_id uuid PRIMARY KEY REFERENCES public.companies(id) ON DELETE RESTRICT,
    display_name text NOT NULL DEFAULT '',
    reply_to text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK(length(display_name)<=200), CHECK(length(reply_to)<=254)
);

CREATE TABLE public.issued_property_pdfs (
    id uuid PRIMARY KEY,
    company_id uuid NOT NULL,
    building_id text NOT NULL,
    statement_id uuid NOT NULL,
    apartment_id text NOT NULL,
    property_data jsonb NOT NULL,
    pdf_data bytea NOT NULL,
    pdf_sha256 text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(statement_id,apartment_id),
    UNIQUE(company_id,building_id,statement_id,id),
    FOREIGN KEY(company_id,building_id) REFERENCES public.buildings(company_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(building_id,statement_id) REFERENCES public.issued_statements(building_id,id) ON DELETE RESTRICT,
    CHECK(octet_length(pdf_data)>0), CHECK(pdf_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK(jsonb_typeof(property_data)='object'),
    CHECK(property_data->>'id'=apartment_id)
);
CREATE INDEX issued_property_pdfs_scope_idx ON public.issued_property_pdfs(company_id,building_id,statement_id);

CREATE TABLE public.statement_email_deliveries (
    id uuid PRIMARY KEY,
    company_id uuid NOT NULL,
    building_id text NOT NULL,
    statement_id uuid NOT NULL,
    apartment_id text NOT NULL,
    property_pdf_id uuid NOT NULL,
    pdf_sha256 text NOT NULL,
    recipient_name text NOT NULL DEFAULT '',
    recipient_email text NOT NULL,
    sender_name text NOT NULL,
    sender_email text NOT NULL,
    reply_to text NOT NULL DEFAULT '',
    email_subject text NOT NULL,
    email_body text NOT NULL,
    status text NOT NULL DEFAULT 'PENDING',
    claim_token uuid,
    attempt_count integer NOT NULL DEFAULT 0,
    claimed_at timestamptz,
    accepted_at timestamptz,
    last_error text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(statement_id,apartment_id),
    FOREIGN KEY(company_id,building_id,statement_id,property_pdf_id)
        REFERENCES public.issued_property_pdfs(company_id,building_id,statement_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(building_id,statement_id) REFERENCES public.issued_statements(building_id,id) ON DELETE RESTRICT,
    CHECK(pdf_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK(length(recipient_email) BETWEEN 3 AND 254),
    CHECK(length(recipient_name)<=200), CHECK(length(sender_name)<=200),
    CHECK(length(sender_email)<=254), CHECK(length(reply_to)<=254),
    CHECK(length(email_subject)<=250), CHECK(length(email_body)<=10000),
    CHECK(length(last_error)<=1000),
    CHECK(attempt_count>=0),
    CHECK(status IN ('PENDING','SENDING','ACCEPTED','FAILED','UNKNOWN')),
    CHECK((status='SENDING')=(claim_token IS NOT NULL))
);
CREATE INDEX statement_email_deliveries_scope_idx ON public.statement_email_deliveries(company_id,building_id,statement_id);

-- No caller may alter the archived PDF or its property metadata.
CREATE TRIGGER issued_property_pdfs_immutable BEFORE UPDATE OR DELETE
    ON public.issued_property_pdfs FOR EACH ROW
    EXECUTE FUNCTION public.reject_issued_statement_mutation();

-- A definer verifies the actual building owner before the runtime may write.
-- No API accepts a free-form company owner or allows ownership transfers.
CREATE FUNCTION public.koinoxrista_delivery_guard()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $function$
DECLARE actual_company uuid; expected_property jsonb; expected_apartment text; expected_digest text;
BEGIN
    IF TG_OP='UPDATE' THEN
        IF NEW.company_id IS DISTINCT FROM OLD.company_id
           OR NEW.building_id IS DISTINCT FROM OLD.building_id THEN
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
REVOKE ALL ON FUNCTION public.koinoxrista_delivery_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.koinoxrista_delivery_guard() TO koinoxrista_app;
DO $guards$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['property_delivery_contacts','company_mail_settings','issued_property_pdfs','statement_email_deliveries'] LOOP
        EXECUTE format('CREATE TRIGGER delivery_owner_guard BEFORE INSERT OR UPDATE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.koinoxrista_delivery_guard()',t);
    END LOOP;
END $guards$;

-- Each new table is subject to the same authenticated company scope as 009.
DO $policies$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['property_delivery_contacts','issued_property_pdfs','statement_email_deliveries'] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY tenant_delivery_scope ON public.%I FOR ALL TO koinoxrista_app '
            || 'USING (public.koinoxrista_can_access(company_id) AND EXISTS '
            || '(SELECT 1 FROM public.buildings b WHERE b.id=%1$I.building_id AND b.company_id=%1$I.company_id)) '
            || 'WITH CHECK (public.koinoxrista_can_access(company_id) AND EXISTS '
            || '(SELECT 1 FROM public.buildings b WHERE b.id=%1$I.building_id AND b.company_id=%1$I.company_id))',t);
    END LOOP;
END $policies$;
ALTER TABLE public.company_mail_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.company_mail_settings FORCE ROW LEVEL SECURITY;
CREATE POLICY company_mail_scope ON public.company_mail_settings FOR ALL TO koinoxrista_app
    USING(public.koinoxrista_can_access(company_id)) WITH CHECK(public.koinoxrista_can_access(company_id));
GRANT SELECT,INSERT,UPDATE,DELETE ON public.property_delivery_contacts TO koinoxrista_app;
GRANT SELECT,INSERT,UPDATE ON public.company_mail_settings TO koinoxrista_app;
GRANT SELECT,INSERT ON public.issued_property_pdfs TO koinoxrista_app;
GRANT SELECT,INSERT ON public.statement_email_deliveries TO koinoxrista_app;

-- These functions are the only route to updating delivery status. They are
-- deliberately not general-purpose update or email-sending functions.
CREATE FUNCTION public.koinoxrista_delivery_claim(p_id uuid,p_pdf_hash text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $function$
DECLARE d public.statement_email_deliveries%ROWTYPE; token uuid;
BEGIN
    SELECT * INTO d FROM public.statement_email_deliveries WHERE id=p_id FOR UPDATE;
    IF NOT FOUND OR NOT public.koinoxrista_can_access(d.company_id)
       OR d.pdf_sha256 IS DISTINCT FROM p_pdf_hash THEN
        RAISE EXCEPTION 'Delivery not found or access denied' USING ERRCODE='42501';
    END IF;
    IF d.status NOT IN ('PENDING','FAILED') THEN
        RAISE EXCEPTION 'Delivery cannot be repeated automatically' USING ERRCODE='55000';
    END IF;
    token := pg_catalog.gen_random_uuid();
    UPDATE public.statement_email_deliveries SET status='SENDING',claim_token=token,
        attempt_count=attempt_count+1,claimed_at=now(),last_error=''
        WHERE id=p_id;
    RETURN token;
END $function$;

CREATE FUNCTION public.koinoxrista_delivery_finish(p_id uuid,p_token uuid,p_status text,p_error text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $function$
DECLARE d public.statement_email_deliveries%ROWTYPE;
BEGIN
    IF p_status NOT IN ('ACCEPTED','FAILED','UNKNOWN') THEN
        RAISE EXCEPTION 'Invalid delivery state' USING ERRCODE='22023';
    END IF;
    SELECT * INTO d FROM public.statement_email_deliveries WHERE id=p_id FOR UPDATE;
    IF NOT FOUND OR NOT public.koinoxrista_can_access(d.company_id)
       OR d.status<>'SENDING' OR d.claim_token IS DISTINCT FROM p_token THEN
        RAISE EXCEPTION 'Delivery claim is no longer valid' USING ERRCODE='42501';
    END IF;
    UPDATE public.statement_email_deliveries SET status=p_status,claim_token=NULL,
        accepted_at=CASE WHEN p_status='ACCEPTED' THEN now() ELSE accepted_at END,
        last_error=left(coalesce(p_error,''),1000) WHERE id=p_id;
END $function$;
REVOKE ALL ON FUNCTION public.koinoxrista_delivery_claim(uuid,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.koinoxrista_delivery_finish(uuid,uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.koinoxrista_delivery_claim(uuid,text) TO koinoxrista_app;
GRANT EXECUTE ON FUNCTION public.koinoxrista_delivery_finish(uuid,uuid,text,text) TO koinoxrista_app;
COMMIT;
