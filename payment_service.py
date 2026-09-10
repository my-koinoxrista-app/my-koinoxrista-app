"""Company-scoped hosted payments backed by a provider adapter.

The application stores an immutable payment request per issued property. Stripe
Checkout is the first adapter; the domain status is changed only by the signed
webhook path in payment_webhook.py.
"""
import json
import os
import urllib.parse
import urllib.request
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID, uuid4

from database import current_tenant, get_connection
from statement_store import ensure_property_pdfs


class PaymentError(ValueError):
    pass


def _uuid(value):
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise PaymentError('Μη έγκυρο payment request.') from None


def _config():
    secret = os.environ.get('KOINOXRISTA_STRIPE_SECRET_KEY', '').strip()
    base_url = os.environ.get('KOINOXRISTA_PAYMENT_PUBLIC_BASE_URL', '').strip().rstrip('/')
    if not secret or not base_url.startswith(('https://', 'http://')):
        raise PaymentError('Δεν έχει ρυθμιστεί ο payment provider.')
    return secret, base_url


def provider_settings():
    company = current_tenant()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT provider,provider_account_id FROM company_payment_settings WHERE company_id=%s', (company,))
        row = cur.fetchone()
    if row is None:
        return None
    return {'provider': row[0], 'account_id': row[1]}


def save_provider_settings(provider_account_id):
    account = str(provider_account_id or '').strip()
    if not account or len(account) > 200 or any(c.isspace() for c in account):
        raise PaymentError('Συμπλήρωσε έγκυρο account id payment provider.')
    company = current_tenant()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''INSERT INTO company_payment_settings(company_id,provider,provider_account_id)
            VALUES(%s,'stripe',%s) ON CONFLICT(company_id) DO UPDATE SET
            provider='stripe',provider_account_id=EXCLUDED.provider_account_id,updated_at=now()''',
            (company, account))


def _stripe_request(account_id, form):
    secret, _ = _config()
    encoded = urllib.parse.urlencode(form).encode()
    request = urllib.request.Request(
        'https://api.stripe.com/v1/checkout/sessions', data=encoded, method='POST',
        headers={'Authorization': f'Bearer {secret}', 'Stripe-Account': account_id,
                 'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode())
    except Exception as exc:
        raise PaymentError('Ο payment provider δεν δημιούργησε checkout link.') from exc
    if not payload.get('id') or not payload.get('url'):
        raise PaymentError('Ο payment provider επέστρεψε μη έγκυρο checkout.')
    return payload


def _form_for(request_row, base_url):
    token = str(request_row['payment_token'])
    code = request_row['property_code']
    amount = request_row['amount_cents']
    return {
        'mode': 'payment',
        'line_items[0][price_data][currency]': request_row['currency'],
        'line_items[0][price_data][unit_amount]': str(amount),
        'line_items[0][price_data][product_data][name]': f'Κοινόχρηστα {code} {request_row["period_key"]}',
        'line_items[0][quantity]': '1',
        'client_reference_id': token,
        'metadata[payment_token]': token,
        'metadata[statement_id]': str(request_row['statement_id']),
        'metadata[apartment_id]': request_row['apartment_id'],
        'success_url': f'{base_url}/payment/success?token={token}',
        'cancel_url': f'{base_url}/payment/cancelled?token={token}',
    }


def ensure_payment_requests(building_id, statement_id):
    """Create one idempotent request and hosted checkout per property PDF."""
    company = current_tenant()
    statement_id = _uuid(statement_id)
    settings = provider_settings()
    if settings is None:
        return {'ready': False, 'created': 0, 'error': 'Δεν έχει οριστεί payment provider account για την εταιρεία.'}
    _, base_url = _config()
    documents = ensure_property_pdfs(building_id, statement_id)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT s.period_key,s.revision FROM issued_statements s
            JOIN buildings b ON b.id=s.building_id
            WHERE s.id=%s AND s.building_id=%s AND b.company_id=%s''',
            (statement_id, building_id, company))
        statement = cur.fetchone()
        if statement is None:
            raise PaymentError('Η εκκαθάριση δεν βρέθηκε.')
        period_key, revision = statement
        requests = []
        for document in documents:
            prop = document['property']
            amount = Decimal(str(prop['total'])).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            cents = int(amount * 100)
            if cents <= 0:
                continue
            cur.execute('''INSERT INTO payment_requests
                (id,company_id,building_id,statement_id,statement_revision,apartment_id,property_pdf_id,
                 amount_cents,period_key,payment_token,provider,provider_account_id)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(statement_id,apartment_id) DO UPDATE SET
                    provider_account_id=EXCLUDED.provider_account_id,updated_at=now()
                RETURNING id,payment_token,provider_session_id,checkout_url,status''',
                (str(uuid4()), company, building_id, statement_id, revision, document['apartment_id'],
                 document['id'], cents, period_key, str(uuid4()), settings['provider'], settings['account_id']))
            row = cur.fetchone()
            requests.append({'id': str(row[0]), 'payment_token': str(row[1]),
                             'provider_session_id': row[2], 'checkout_url': row[3],
                             'status': row[4], 'property_code': prop['code'],
                             'apartment_id': document['apartment_id'], 'pdf_id': document['id'],
                             'statement_id': statement_id, 'period_key': period_key,
                             'amount_cents': cents, 'currency': 'eur'})
    created = 0
    for request_row in requests:
        if request_row['checkout_url']:
            continue
        payload = _stripe_request(settings['account_id'], _form_for(request_row, base_url))
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute('''UPDATE payment_requests SET provider_session_id=%s,checkout_url=%s,updated_at=now()
                WHERE id=%s AND checkout_url='' RETURNING id''',
                (payload['id'], payload['url'], request_row['id']))
            if cur.fetchone():
                created += 1
    return {'ready': True, 'created': created, 'count': len(requests), 'error': ''}


def list_payment_requests(building_id, statement_id):
    company = current_tenant()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT p.apartment_id,p.amount_cents,p.currency,p.status,p.checkout_url,
            p.provider_payment_id,p.failure_reason,p.paid_at,p.refunded_at,
            coalesce(p.payment_token::text,'') FROM payment_requests p
            WHERE p.company_id=%s AND p.building_id=%s AND p.statement_id=%s
            ORDER BY p.apartment_id''', (company, building_id, _uuid(statement_id)))
        return [{'apartment_id': r[0], 'amount_cents': r[1], 'currency': r[2], 'status': r[3],
                 'checkout_url': r[4], 'provider_payment_id': r[5], 'failure_reason': r[6],
                 'paid_at': r[7], 'refunded_at': r[8], 'payment_token': r[9]} for r in cur.fetchall()]
