"""Stripe transport shared by the public checkout and webhook processes."""
import json
import os
import re
import urllib.parse
import urllib.request


class PaymentError(ValueError):
    pass


def public_base_url():
    value = os.getenv('KOINOXRISTA_PAYMENT_PUBLIC_BASE_URL', '').strip().rstrip('/')
    url = urllib.parse.urlsplit(value)
    local = url.hostname in ('localhost', '127.0.0.1')
    if (not url.hostname or url.username or url.password or url.query or url.fragment
            or url.path or (url.scheme != 'https' and not (local and url.scheme == 'http'))):
        raise PaymentError('Ρύθμισε το δημόσιο HTTPS URL της υπηρεσίας πληρωμών (Render).')
    return value


def account_for(company_id):
    """Server-controlled company/account mapping, never an arbitrary UI account."""
    try:
        mapping = json.loads(os.getenv('KOINOXRISTA_STRIPE_COMPANY_ACCOUNTS', '{}'))
        account = mapping.get(str(company_id), '')
    except (ValueError, AttributeError):
        raise PaymentError('Μη έγκυρη αντιστοίχιση εταιρειών Stripe.') from None
    if not isinstance(account, str) or not re.fullmatch(r'acct_[A-Za-z0-9]+', account):
        raise PaymentError('Δεν έχει αντιστοιχιστεί λογαριασμός Stripe στην εταιρεία.')
    return account


def stripe_request(account_id, path, form=None, idempotency_key=None):
    secret = os.getenv('KOINOXRISTA_STRIPE_SECRET_KEY', '').strip()
    mode = os.getenv('KOINOXRISTA_STRIPE_MODE', 'direct')
    own_account = os.getenv('KOINOXRISTA_STRIPE_ACCOUNT_ID', '').strip()
    if not secret.startswith(('sk_test_', 'sk_live_', 'rk_test_', 'rk_live_')):
        raise PaymentError('Ρύθμισε έγκυρο Stripe secret key στην υπηρεσία πληρωμών.')
    headers = {'Authorization': f'Bearer {secret}', 'Stripe-Version': '2025-02-24.acacia'}
    if mode == 'connect':
        headers['Stripe-Account'] = account_id
    elif mode != 'direct' or account_id != own_account:
        raise PaymentError('Ο λογαριασμός Stripe δεν αντιστοιχεί στις ρυθμίσεις του server.')
    if idempotency_key:
        headers['Idempotency-Key'] = idempotency_key
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    request = urllib.request.Request('https://api.stripe.com/v1/' + path,
                                     data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read())
        if not isinstance(result, dict) or not result.get('id'):
            raise ValueError('Invalid Stripe response')
        return result
    except Exception:
        # Provider errors may contain sensitive request data; do not expose them.
        raise PaymentError('Η υπηρεσία πληρωμών δεν είναι διαθέσιμη. Δοκίμασε ξανά.') from None


def checkout_form(row):
    base = public_base_url()
    token = str(row['payment_token'])
    return {
        'mode': 'payment',
        'payment_method_types[0]': 'card',
        'line_items[0][price_data][currency]': row['currency'],
        'line_items[0][price_data][unit_amount]': str(row['amount_cents']),
        'line_items[0][price_data][product_data][name]': f'Κοινόχρηστα {row["period_key"]}',
        'line_items[0][quantity]': '1',
        'client_reference_id': token,
        'metadata[payment_token]': token,
        'payment_intent_data[metadata][payment_token]': token,
        'success_url': f'{base}/payment/success',
        'cancel_url': f'{base}/payment/cancelled',
    }
