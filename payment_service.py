"""Company-scoped hosted payments backed by a provider adapter.

The application stores an immutable payment request per issued property. Stripe
Checkout is the first adapter; the domain status is changed only by the signed
webhook path in payment_webhook.py.
"""
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID, uuid4

from database import current_tenant, get_connection
from statement_store import ensure_property_pdfs
from stripe_checkout import PaymentError, public_base_url, account_for


def _uuid(value):
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise PaymentError('Μη έγκυρο payment request.') from None


def provider_settings():
    company = current_tenant()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT provider,provider_account_id FROM company_payment_settings WHERE company_id=%s', (company,))
        row = cur.fetchone()
    if row is None:
        return None
    return {'provider': row[0], 'account_id': row[1]}


def save_provider_settings(provider_account_id):
    company = current_tenant()
    account = account_for(company)
    if str(provider_account_id).strip() != account:
        raise PaymentError('Επίλεξε τον λογαριασμό Stripe που έχει εγκριθεί για την εταιρεία.')
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''INSERT INTO company_payment_settings(company_id,provider,provider_account_id)
            VALUES(%s,'stripe',%s) ON CONFLICT(company_id) DO UPDATE SET
            provider='stripe',provider_account_id=EXCLUDED.provider_account_id,updated_at=now()''',
            (company, account))


def ensure_payment_requests(building_id, statement_id):
    """Create one idempotent request and hosted checkout per property PDF."""
    company = current_tenant()
    statement_id = _uuid(statement_id)
    settings = provider_settings()
    if settings is None:
        return {'ready': False, 'created': 0, 'error': 'Δεν έχει οριστεί payment provider account για την εταιρεία.'}
    base_url = public_base_url()
    if settings['account_id'] != account_for(company):
        raise PaymentError('Οι ρυθμίσεις Stripe της εταιρείας χρειάζονται ενημέρωση.')
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
                    updated_at=now()
                RETURNING id,payment_token,provider_session_id,checkout_url,status,provider_account_id''',
                (str(uuid4()), company, building_id, statement_id, revision, document['apartment_id'],
                 document['id'], cents, period_key, str(uuid4()), settings['provider'], settings['account_id']))
            row = cur.fetchone()
            requests.append({'id': str(row[0]), 'payment_token': str(row[1]),
                             'provider_session_id': row[2], 'checkout_url': row[3],
                             'status': row[4], 'account_id': row[5], 'property_code': prop['code'],
                             'apartment_id': document['apartment_id'], 'pdf_id': document['id'],
                             'statement_id': statement_id, 'period_key': period_key,
                             'amount_cents': cents, 'currency': 'eur'})
    created = 0
    for request_row in requests:
        link = f"{base_url}/pay/{request_row['payment_token']}"
        if request_row['checkout_url'] == link:
            continue
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("""UPDATE payment_requests SET checkout_url=%s,updated_at=now()
                WHERE id=%s RETURNING id""", (link, request_row['id']))
            if cur.fetchone():
                created += 1
    return {'ready': True, 'created': created, 'count': len(requests), 'error': ''}


def list_payment_requests(building_id, statement_id):
    company = current_tenant()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT p.apartment_id,p.amount_cents,p.currency,p.status,p.checkout_url,
            p.provider_payment_id,p.failure_reason,p.paid_at,p.refunded_at,
            coalesce(p.payment_token::text,''),p.refunded_cents FROM payment_requests p
            WHERE p.company_id=%s AND p.building_id=%s AND p.statement_id=%s
            ORDER BY p.apartment_id''', (company, building_id, _uuid(statement_id)))
        return [{'apartment_id': r[0], 'amount_cents': r[1], 'currency': r[2], 'status': r[3],
                 'checkout_url': r[4], 'provider_payment_id': r[5], 'failure_reason': r[6],
                 'paid_at': r[7], 'refunded_at': r[8], 'payment_token': r[9], 'refunded_cents': r[10]} for r in cur.fetchall()]
