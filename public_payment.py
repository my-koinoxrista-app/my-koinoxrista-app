"""Resolve a permanent, unguessable email link to a current Stripe Checkout."""
import time
from uuid import UUID
from psycopg.types.json import Jsonb
from payment_webhook_database import get_payment_connection
from stripe_checkout import PaymentError, account_for, checkout_form, stripe_request


def open_checkout(token):
    token = str(UUID(str(token)))
    with get_payment_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT public.koinoxrista_checkout_request(%s)', (token,))
        row = cur.fetchone()[0]
        if row is None:
            return 'NOT_FOUND', None
        if row['status'] in ('PAID', 'REFUNDED'):
            return row['status'], None
        if account_for(row['company_id']) != row['account_id']:
            raise PaymentError('Η αντιστοίχιση λογαριασμού πληρωμής χρειάζεται έλεγχο.')
        if row['session_id']:
            # Always check canonical status before offering another checkout.
            session = stripe_request(row['account_id'], 'checkout/sessions/' + row['session_id'])
            if row['livemode'] is None and session.get('status') in ('open', 'complete'):
                account = stripe_request(row['account_id'], 'account')
                if account['id'] != row['account_id']:
                    raise PaymentError('Το Stripe key ανήκει σε διαφορετικό λογαριασμό.')
                cur.execute('SELECT public.koinoxrista_checkout_save(%s,%s)', (token, Jsonb(session)))
            if session.get('status') == 'complete':
                return 'PENDING', None  # Only signed webhooks can mark it paid.
            if session.get('status') == 'open':
                if session.get('expires_at', 0) > time.time() and session.get('url'):
                    return 'REDIRECT', _checkout_url(session['url'])
                return 'PENDING', None
            if session.get('status') != 'expired':
                raise PaymentError('Μη αναμενόμενη κατάσταση πληρωμής.')
        account = stripe_request(row['account_id'], 'account')
        if account['id'] != row['account_id']:
            raise PaymentError('Το Stripe key ανήκει σε διαφορετικό λογαριασμό.')
        session = stripe_request(row['account_id'], 'checkout/sessions', checkout_form(row),
                                 f"koinoxrista:{token}:{row['generation'] + 1}")
        url = _checkout_url(session.get('url', ''))
        cur.execute('SELECT public.koinoxrista_checkout_save(%s,%s)', (token, Jsonb(session)))
        return 'REDIRECT', url


def _checkout_url(url):
    if not isinstance(url, str) or not url.startswith('https://checkout.stripe.com/') or any(c in url for c in '\r\n'):
        raise PaymentError('Μη έγκυρος σύνδεσμος Stripe.')
    return url
