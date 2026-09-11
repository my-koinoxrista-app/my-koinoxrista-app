-- Apply after 011 as the schema owner, after a verified backup / Neon branch.
-- A dedicated login may execute only the three public payment functions below.
BEGIN;
CREATE ROLE koinoxrista_webhook LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO koinoxrista_webhook;
ALTER TABLE public.payment_requests ADD COLUMN checkout_generation integer NOT NULL DEFAULT 0;
ALTER TABLE public.payment_requests ADD COLUMN stripe_checkout_url text NOT NULL DEFAULT '';
ALTER TABLE public.payment_requests ADD COLUMN checkout_expires_at bigint;
ALTER TABLE public.payment_requests ADD COLUMN livemode boolean;
ALTER TABLE public.payment_requests ADD COLUMN refunded_cents bigint NOT NULL DEFAULT 0;

-- Old broad privileges allowed the portal to forge a payment status.
REVOKE ALL ON FUNCTION public.koinoxrista_payment_webhook(text,text,uuid,text,text,text,text)
    FROM koinoxrista_app, PUBLIC;
DROP FUNCTION public.koinoxrista_payment_webhook(text,text,uuid,text,text,text,text);
REVOKE INSERT,UPDATE ON public.payment_requests FROM koinoxrista_app;
GRANT INSERT(id,company_id,building_id,statement_id,statement_revision,apartment_id,
    property_pdf_id,amount_cents,period_key,payment_token,provider,provider_account_id)
    ON public.payment_requests TO koinoxrista_app;
GRANT UPDATE(checkout_url,updated_at) ON public.payment_requests TO koinoxrista_app;
REVOKE ALL ON public.payment_webhook_events FROM koinoxrista_app;
DROP POLICY payment_event_scope ON public.payment_webhook_events;

-- The migration owner is also the SECURITY DEFINER function owner. Explicit
-- owner policies support FORCE RLS even on managed PostgreSQL without superuser.
DO $block$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO koinoxrista_webhook', current_database());
    EXECUTE format('CREATE POLICY payment_function_owner ON public.payment_requests TO %I USING (true) WITH CHECK (true)', current_user);
    EXECUTE format('CREATE POLICY payment_event_owner ON public.payment_webhook_events TO %I USING (true) WITH CHECK (true)', current_user);
END $block$;

CREATE FUNCTION public.koinoxrista_validate_payment_request() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $function$
DECLARE doc public.issued_property_pdfs%ROWTYPE; stmt public.issued_statements%ROWTYPE;
BEGIN
    SELECT * INTO doc FROM public.issued_property_pdfs WHERE id=NEW.property_pdf_id;
    SELECT * INTO stmt FROM public.issued_statements WHERE id=NEW.statement_id;
    IF doc.id IS NULL OR stmt.id IS NULL
       OR (doc.company_id,doc.building_id,doc.statement_id,doc.apartment_id)
          IS DISTINCT FROM (NEW.company_id,NEW.building_id,NEW.statement_id,NEW.apartment_id)
       OR NEW.amount_cents IS DISTINCT FROM (round((doc.property_data->>'total')::numeric,2)*100)::bigint
       OR NEW.statement_revision IS DISTINCT FROM stmt.revision
       OR NEW.period_key IS DISTINCT FROM stmt.period_key
       OR NEW.provider <> 'stripe'
       OR NOT EXISTS(SELECT 1 FROM public.company_payment_settings
                     WHERE company_id=NEW.company_id AND provider_account_id=NEW.provider_account_id)
    THEN RAISE EXCEPTION 'Invalid issued payment request' USING ERRCODE='22023'; END IF;
    RETURN NEW;
END $function$;
CREATE TRIGGER payment_request_insert_check BEFORE INSERT ON public.payment_requests
FOR EACH ROW EXECUTE FUNCTION public.koinoxrista_validate_payment_request();

-- Lock is held through Checkout creation and saving in the same transaction.
-- Possession of the unguessable UUID grants only access to this payment.
CREATE FUNCTION public.koinoxrista_checkout_request(p_token uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $function$
DECLARE r public.payment_requests%ROWTYPE;
BEGIN
    SELECT * INTO r FROM public.payment_requests WHERE payment_token=p_token FOR UPDATE;
    IF NOT FOUND THEN RETURN NULL; END IF;
    RETURN jsonb_build_object('payment_token',r.payment_token,'company_id',r.company_id,
        'account_id',r.provider_account_id,'amount_cents',r.amount_cents,'currency',r.currency,
        'period_key',r.period_key,'status',r.status,'session_id',r.provider_session_id,
        'stripe_checkout_url',r.stripe_checkout_url,'expires_at',r.checkout_expires_at,
        'generation',r.checkout_generation,'livemode',r.livemode);
END $function$;

CREATE FUNCTION public.koinoxrista_checkout_save(p_token uuid,p_session jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $function$
DECLARE r public.payment_requests%ROWTYPE;
BEGIN
    SELECT * INTO STRICT r FROM public.payment_requests WHERE payment_token=p_token FOR UPDATE;
    IF r.status IN ('PAID','REFUNDED')
       OR coalesce(p_session->>'id','') NOT LIKE 'cs_%'
       OR (p_session->>'status'='open' AND coalesce(p_session->>'url','') NOT LIKE 'https://checkout.stripe.com/%')
       OR (p_session->>'amount_total')::bigint IS DISTINCT FROM r.amount_cents
       OR p_session->>'currency' IS DISTINCT FROM r.currency
       OR p_session->'metadata'->>'payment_token' IS DISTINCT FROM p_token::text
       OR jsonb_typeof(p_session->'livemode') IS DISTINCT FROM 'boolean'
       OR (r.livemode IS NOT NULL AND r.livemode IS DISTINCT FROM (p_session->>'livemode')::boolean)
       OR (p_session->>'status' IS DISTINCT FROM 'open' AND NOT (
           p_session->>'status' IS NOT DISTINCT FROM 'complete' AND r.livemode IS NULL
           AND p_session->>'id' IS NOT DISTINCT FROM r.provider_session_id))
       OR (p_session->>'expires_at')::bigint IS NULL
    THEN RAISE EXCEPTION 'Invalid checkout session' USING ERRCODE='22023'; END IF;
    UPDATE public.payment_requests SET provider_session_id=p_session->>'id',
        stripe_checkout_url=coalesce(p_session->>'url',''), checkout_expires_at=(p_session->>'expires_at')::bigint,
        checkout_generation=checkout_generation+1,livemode=(p_session->>'livemode')::boolean,
        updated_at=now() WHERE id=r.id;
END $function$;

CREATE FUNCTION public.koinoxrista_payment_webhook(p_event jsonb,p_account text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $function$
DECLARE r public.payment_requests%ROWTYPE; obj jsonb := p_event->'data'->'object';
    kind text := p_event->>'type'; token uuid; inserted integer; target_status text;
BEGIN
    IF kind NOT IN ('checkout.session.completed','checkout.session.async_payment_succeeded',
                    'checkout.session.async_payment_failed','charge.refunded') THEN RETURN 'IGNORED'; END IF;
    IF p_event->>'id' IS NULL OR length(p_event->>'id') NOT BETWEEN 3 AND 250 THEN
        RAISE EXCEPTION 'Invalid event id' USING ERRCODE='22023'; END IF;
    token := (obj->'metadata'->>'payment_token')::uuid;
    IF token IS NULL THEN RETURN 'IGNORED'; END IF;
    SELECT * INTO r FROM public.payment_requests WHERE payment_token=token FOR UPDATE;
    IF NOT FOUND THEN RETURN 'IGNORED'; END IF;
    IF r.provider_account_id IS DISTINCT FROM p_account
       OR r.livemode IS NULL OR r.livemode IS DISTINCT FROM (p_event->>'livemode')::boolean
       OR r.livemode IS DISTINCT FROM (obj->>'livemode')::boolean
       OR obj->>'currency' IS DISTINCT FROM r.currency THEN
        RAISE EXCEPTION 'Payment account, mode or currency mismatch' USING ERRCODE='22023'; END IF;
    IF kind='charge.refunded' THEN
        IF obj->>'object' IS DISTINCT FROM 'charge'
           OR (obj->>'amount')::bigint IS DISTINCT FROM r.amount_cents
           OR obj->>'payment_intent' IS DISTINCT FROM r.provider_payment_id
           OR r.provider_payment_id='' THEN
            -- A refund can precede the success event. Roll back and ask Stripe to retry.
            RAISE EXCEPTION 'Refund does not match a confirmed payment' USING ERRCODE='40001'; END IF;
        IF (obj->>'amount_refunded')::bigint NOT BETWEEN 0 AND r.amount_cents
           OR obj->>'amount_refunded' IS NULL THEN
            RAISE EXCEPTION 'Invalid refund amount' USING ERRCODE='22023'; END IF;
    ELSE
        IF obj->>'object' IS DISTINCT FROM 'checkout.session'
           OR obj->>'id' IS DISTINCT FROM r.provider_session_id
           OR (obj->>'amount_total')::bigint IS DISTINCT FROM r.amount_cents
           OR obj->>'mode' IS DISTINCT FROM 'payment'
           OR obj->>'status' IS DISTINCT FROM 'complete' THEN
            RAISE EXCEPTION 'Checkout mismatch' USING ERRCODE='22023'; END IF;
        IF kind='checkout.session.async_payment_failed' THEN target_status := 'FAILED';
        ELSIF obj->>'payment_status'='paid' AND coalesce(obj->>'payment_intent','') LIKE 'pi_%' THEN
            target_status := 'PAID';
        ELSE target_status := 'UNPAID'; END IF;
    END IF;
    INSERT INTO public.payment_webhook_events(provider,provider_event_id)
        VALUES('stripe',p_event->>'id') ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS inserted = ROW_COUNT;
    IF inserted=0 THEN RETURN 'DUPLICATE'; END IF;
    IF kind='charge.refunded' THEN
        UPDATE public.payment_requests SET
            refunded_cents=greatest(refunded_cents,(obj->>'amount_refunded')::bigint),
            status=CASE WHEN (obj->>'amount_refunded')::bigint=amount_cents THEN 'REFUNDED' ELSE status END,
            refunded_at=now(),updated_at=now() WHERE id=r.id;
    ELSIF target_status='PAID' THEN
        UPDATE public.payment_requests SET status='PAID',provider_payment_id=obj->>'payment_intent',
            failure_reason='',paid_at=coalesce(paid_at,now()),updated_at=now()
            WHERE id=r.id AND status<>'REFUNDED';
    ELSIF target_status='FAILED' THEN
        UPDATE public.payment_requests SET status='FAILED',failure_reason='Η πληρωμή απέτυχε.',updated_at=now()
            WHERE id=r.id AND status NOT IN ('PAID','REFUNDED');
    END IF;
    RETURN 'APPLIED';
END $function$;
REVOKE ALL ON FUNCTION public.koinoxrista_checkout_request(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.koinoxrista_checkout_save(uuid,jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.koinoxrista_payment_webhook(jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.koinoxrista_checkout_request(uuid),
    public.koinoxrista_checkout_save(uuid,jsonb),public.koinoxrista_payment_webhook(jsonb,text)
    TO koinoxrista_webhook;
COMMIT;
