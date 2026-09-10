-- Hosted payment requests per issued property statement.
-- Apply once after migration 010, with a verified backup.
BEGIN;

ALTER TABLE public.statement_email_deliveries
    ADD COLUMN payment_url text NOT NULL DEFAULT '';
ALTER TABLE public.statement_email_deliveries
    ADD CONSTRAINT statement_email_payment_url_length CHECK(length(payment_url) <= 2048);

CREATE TABLE public.company_payment_settings (
    company_id uuid PRIMARY KEY REFERENCES public.companies(id) ON DELETE RESTRICT,
    provider text NOT NULL DEFAULT 'stripe',
    provider_account_id text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK(provider IN ('stripe')),
    CHECK(provider_account_id ~ '^[A-Za-z0-9_=-]{3,200}$')
);

CREATE TABLE public.payment_requests (
    id uuid PRIMARY KEY,
    company_id uuid NOT NULL,
    building_id text NOT NULL,
    statement_id uuid NOT NULL,
    statement_revision integer NOT NULL,
    apartment_id text NOT NULL,
    property_pdf_id uuid NOT NULL,
    amount_cents bigint NOT NULL,
    currency text NOT NULL DEFAULT 'eur',
    period_key text NOT NULL,
    payment_token uuid NOT NULL UNIQUE,
    provider text NOT NULL,
    provider_account_id text NOT NULL,
    provider_session_id text,
    checkout_url text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'UNPAID',
    provider_payment_id text NOT NULL DEFAULT '',
    failure_reason text NOT NULL DEFAULT '',
    refunded_at timestamptz,
    paid_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(statement_id, apartment_id),
    FOREIGN KEY(company_id,building_id,statement_id,property_pdf_id)
        REFERENCES public.issued_property_pdfs(company_id,building_id,statement_id,id) ON DELETE RESTRICT,
    CHECK(amount_cents > 0),
    CHECK(currency = 'eur'),
    CHECK(status IN ('UNPAID','PAID','FAILED','REFUNDED')),
    CHECK(length(checkout_url) <= 2048),
    CHECK(length(provider_payment_id) <= 250),
    CHECK(length(failure_reason) <= 1000)
);
CREATE INDEX payment_requests_scope_idx ON public.payment_requests(company_id,building_id,statement_id);
CREATE INDEX payment_requests_token_idx ON public.payment_requests(payment_token);

CREATE TABLE public.payment_webhook_events (
    provider text NOT NULL,
    provider_event_id text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(provider, provider_event_id)
);

ALTER TABLE public.company_payment_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.company_payment_settings FORCE ROW LEVEL SECURITY;
CREATE POLICY company_payment_scope ON public.company_payment_settings FOR ALL TO koinoxrista_app
    USING(public.koinoxrista_can_access(company_id)) WITH CHECK(public.koinoxrista_can_access(company_id));
ALTER TABLE public.payment_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.payment_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY payment_request_scope ON public.payment_requests FOR ALL TO koinoxrista_app
    USING(public.koinoxrista_can_access(company_id)) WITH CHECK(public.koinoxrista_can_access(company_id));
ALTER TABLE public.payment_webhook_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.payment_webhook_events FORCE ROW LEVEL SECURITY;
CREATE POLICY payment_event_scope ON public.payment_webhook_events FOR SELECT TO koinoxrista_app
    USING(true);

GRANT SELECT,INSERT,UPDATE ON public.company_payment_settings TO koinoxrista_app;
GRANT SELECT,INSERT,UPDATE ON public.payment_requests TO koinoxrista_app;
GRANT SELECT,INSERT ON public.payment_webhook_events TO koinoxrista_app;

CREATE OR REPLACE FUNCTION public.koinoxrista_payment_webhook(
    p_provider text, p_event_id text, p_token uuid, p_event_type text,
    p_provider_payment_id text, p_provider_account_id text, p_failure_reason text DEFAULT '')
RETURNS text LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $function$
DECLARE request_row public.payment_requests%ROWTYPE; inserted integer;
BEGIN
    IF p_provider <> 'stripe' OR length(p_event_id) < 3 OR length(p_event_id) > 250 THEN
        RAISE EXCEPTION 'Invalid payment webhook' USING ERRCODE='22023';
    END IF;
    INSERT INTO public.payment_webhook_events(provider,provider_event_id)
        VALUES(p_provider,p_event_id) ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS inserted = ROW_COUNT;
    IF NOT inserted THEN RETURN 'DUPLICATE'; END IF;
    SELECT * INTO request_row FROM public.payment_requests
        WHERE payment_token=p_token AND provider=p_provider FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'Payment request not found' USING ERRCODE='42501'; END IF;
    IF request_row.provider_account_id IS DISTINCT FROM p_provider_account_id THEN
        RAISE EXCEPTION 'Payment account mismatch' USING ERRCODE='42501';
    END IF;
    IF p_event_type IN ('checkout.session.completed','payment_intent.succeeded') THEN
        UPDATE public.payment_requests SET status='PAID',provider_payment_id=left(coalesce(p_provider_payment_id,''),250),
            failure_reason='',paid_at=coalesce(paid_at,now()),updated_at=now() WHERE id=request_row.id;
    ELSIF p_event_type IN ('payment_intent.payment_failed','checkout.session.async_payment_failed') THEN
        UPDATE public.payment_requests SET status='FAILED',provider_payment_id=left(coalesce(p_provider_payment_id,''),250),
            failure_reason=left(coalesce(p_failure_reason,''),1000),updated_at=now() WHERE id=request_row.id AND status<>'PAID';
    ELSIF p_event_type IN ('charge.refunded','refund.created') THEN
        UPDATE public.payment_requests SET status='REFUNDED',provider_payment_id=left(coalesce(p_provider_payment_id,''),250),
            refunded_at=coalesce(refunded_at,now()),updated_at=now() WHERE id=request_row.id AND status='PAID';
    END IF;
    RETURN 'APPLIED';
END $function$;
REVOKE ALL ON FUNCTION public.koinoxrista_payment_webhook(text,text,uuid,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.koinoxrista_payment_webhook(text,text,uuid,text,text,text,text) TO koinoxrista_app;

COMMIT;
