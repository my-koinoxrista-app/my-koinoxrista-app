-- Apply after 011 with an offline administrator and a verified backup.
BEGIN;
CREATE ROLE koinoxrista_webhook LOGIN NOINHERIT NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO koinoxrista_webhook;
REVOKE EXECUTE ON FUNCTION public.koinoxrista_payment_webhook(text,text,uuid,text,text,text,text) FROM koinoxrista_app;
REVOKE INSERT ON public.payment_webhook_events FROM koinoxrista_app;
REVOKE UPDATE ON public.payment_requests FROM koinoxrista_app;
GRANT UPDATE(provider_session_id,checkout_url,updated_at) ON public.payment_requests TO koinoxrista_app;
ALTER TABLE public.payment_requests ADD COLUMN livemode boolean NOT NULL DEFAULT false;
ALTER TABLE public.payment_requests ADD COLUMN refunded_cents bigint NOT NULL DEFAULT 0 CHECK(refunded_cents>=0 AND refunded_cents<=amount_cents);
ALTER TABLE public.payment_requests DROP CONSTRAINT payment_requests_status_check;
ALTER TABLE public.payment_requests ADD CHECK(status IN ('UNPAID','PAID','FAILED','REFUNDED','PARTIALLY_REFUNDED'));

-- The UI may create requests but cannot forge an initial paid/refunded state.
CREATE FUNCTION public.koinoxrista_payment_initial_state()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 IF current_user='koinoxrista_app' AND
   (NEW.status<>'UNPAID' OR NEW.provider_payment_id<>'' OR NEW.refunded_cents<>0
    OR NEW.paid_at IS NOT NULL OR NEW.refunded_at IS NOT NULL) THEN
  RAISE EXCEPTION 'Payment status requires verified webhook' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER payment_initial_state BEFORE INSERT ON public.payment_requests
 FOR EACH ROW EXECUTE FUNCTION public.koinoxrista_payment_initial_state();

CREATE FUNCTION public.koinoxrista_confirm_stripe_event(e jsonb)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
 r public.payment_requests%ROWTYPE;
 o jsonb := e->'data'->'object';
 kind text := e->>'type';
 token uuid;
 inserted integer;
 refunded bigint;
BEGIN
 IF kind NOT IN ('checkout.session.completed','checkout.session.async_payment_succeeded',
    'checkout.session.async_payment_failed','charge.refunded') THEN RETURN 'IGNORED'; END IF;
 IF coalesce(e->>'id','') !~ '^evt_' OR coalesce(e->>'account','') !~ '^acct_'
    OR jsonb_typeof(e->'livemode') IS DISTINCT FROM 'boolean' THEN
  RAISE EXCEPTION 'Invalid Stripe event' USING ERRCODE='22023';
 END IF;
 IF kind='charge.refunded' THEN
  SELECT * INTO r FROM public.payment_requests
   WHERE provider='stripe' AND provider_payment_id=o->>'payment_intent'
    AND provider_account_id=e->>'account' FOR UPDATE;
 ELSE
  token := (o->'metadata'->>'payment_token')::uuid;
  SELECT * INTO r FROM public.payment_requests WHERE payment_token=token FOR UPDATE;
 END IF;
 IF NOT FOUND THEN RETURN 'UNMATCHED'; END IF;
 IF r.provider_account_id IS DISTINCT FROM e->>'account' OR r.livemode IS DISTINCT FROM (e->>'livemode')::boolean
    OR r.livemode IS DISTINCT FROM (o->>'livemode')::boolean
    OR r.currency IS DISTINCT FROM o->>'currency' THEN
  RAISE EXCEPTION 'Payment identity mismatch' USING ERRCODE='22023';
 END IF;
 IF kind='charge.refunded' THEN
  IF r.amount_cents IS DISTINCT FROM (o->>'amount')::bigint THEN
   RAISE EXCEPTION 'Payment amount mismatch' USING ERRCODE='22023';
  END IF;
  refunded := (o->>'amount_refunded')::bigint;
  IF refunded IS NULL OR refunded<0 OR refunded>r.amount_cents THEN
   RAISE EXCEPTION 'Invalid refund amount' USING ERRCODE='22023';
  END IF;
 ELSE
  IF r.provider_session_id IS NULL THEN
   RAISE EXCEPTION 'Checkout not yet saved; retry event' USING ERRCODE='40001';
  END IF;
  IF r.provider_session_id IS DISTINCT FROM o->>'id' OR r.amount_cents IS DISTINCT FROM (o->>'amount_total')::bigint
     OR o->>'mode' IS DISTINCT FROM 'payment' THEN
   RAISE EXCEPTION 'Checkout mismatch' USING ERRCODE='22023';
  END IF;
 END IF;
 INSERT INTO public.payment_webhook_events(provider,provider_event_id) VALUES('stripe',e->>'id') ON CONFLICT DO NOTHING;
 GET DIAGNOSTICS inserted=ROW_COUNT;
 IF inserted=0 THEN RETURN 'DUPLICATE'; END IF;
 IF kind='charge.refunded' THEN
  UPDATE public.payment_requests SET refunded_cents=greatest(refunded_cents,refunded),
   status=CASE WHEN greatest(refunded_cents,refunded)=amount_cents THEN 'REFUNDED'
               WHEN greatest(refunded_cents,refunded)>0 THEN 'PARTIALLY_REFUNDED' ELSE status END,
   refunded_at=CASE WHEN refunded>0 THEN coalesce(refunded_at,now()) ELSE refunded_at END,
   updated_at=now() WHERE id=r.id;
 ELSIF kind IN ('checkout.session.completed','checkout.session.async_payment_succeeded') AND o->>'payment_status'='paid' THEN
  IF coalesce(o->>'payment_intent','') !~ '^pi_' THEN RAISE EXCEPTION 'Missing payment intent'; END IF;
  UPDATE public.payment_requests SET
   status=CASE WHEN refunded_cents=amount_cents THEN 'REFUNDED' WHEN refunded_cents>0 THEN 'PARTIALLY_REFUNDED' ELSE 'PAID' END,
   provider_payment_id=o->>'payment_intent',failure_reason='',paid_at=coalesce(paid_at,now()),updated_at=now() WHERE id=r.id;
 ELSIF kind='checkout.session.async_payment_failed' THEN
  UPDATE public.payment_requests SET status='FAILED',failure_reason='Η πληρωμή δεν ολοκληρώθηκε.',updated_at=now()
   WHERE id=r.id AND status IN ('UNPAID','FAILED');
 END IF;
 RETURN 'APPLIED';
END; $$;
REVOKE ALL ON FUNCTION public.koinoxrista_confirm_stripe_event(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.koinoxrista_confirm_stripe_event(jsonb) TO koinoxrista_webhook;
COMMIT;
