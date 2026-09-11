"""Verified Stripe webhook handling for hosted payment requests."""
import hashlib
import hmac
import json
import os
import time
from uuid import UUID

from psycopg.types.json import Jsonb
from payment_webhook_database import get_payment_connection


class WebhookError(ValueError):
    pass


def _signature(raw_body, header):
    secret = os.environ.get('KOINOXRISTA_STRIPE_WEBHOOK_SECRET', '').strip()
    if not secret.startswith('whsec_'):
        raise RuntimeError('Webhook secret is not configured.')
    if not header:
        raise WebhookError('Απουσιάζει η υπογραφή webhook.')
    values = {}
    for item in header.split(','):
        key, sep, value = item.partition('=')
        if sep:
            values.setdefault(key, []).append(value)
    try:
        timestamp = int(values['t'][0])
        supplied = values['v1']
    except (KeyError, ValueError):
        raise WebhookError('Μη έγκυρη υπογραφή webhook.') from None
    if abs(time.time() - timestamp) > 300:
        raise WebhookError('Το webhook είναι εκπρόθεσμο.')
    signed = f'{timestamp}.'.encode() + raw_body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, value) for value in supplied):
        raise WebhookError('Η υπογραφή webhook δεν είναι έγκυρη.')


def handle_stripe_webhook(raw_body, signature):
    if not isinstance(raw_body, bytes) or len(raw_body) > 1_000_000:
        raise WebhookError('Μη έγκυρο σώμα webhook.')
    _signature(raw_body, signature)
    try:
        event = json.loads(raw_body.decode('utf-8'))
        if not isinstance(event, dict) or not isinstance(event.get('type'), str):
            raise ValueError('Invalid event')
        if event['type'] not in ('checkout.session.completed',
                'checkout.session.async_payment_succeeded',
                'checkout.session.async_payment_failed', 'charge.refunded'):
            return 'IGNORED'
        obj = event['data']['object']
        if not isinstance(obj, dict) or not isinstance(obj.get('metadata', {}), dict):
            raise ValueError('Invalid event object')
        token = obj.get('metadata', {}).get('payment_token')
        if token is None:
            return 'IGNORED'
        UUID(str(token))
        if not isinstance(event.get('livemode'), bool):
            raise ValueError('Missing mode')
        mode = os.getenv('KOINOXRISTA_STRIPE_MODE', 'direct')
        if mode == 'direct':
            account_id = os.getenv('KOINOXRISTA_STRIPE_ACCOUNT_ID', '').strip()
            if not account_id.startswith('acct_'):
                raise RuntimeError('Stripe account is not configured.')
            if event.get('account') and event['account'] != account_id:
                raise ValueError('Unexpected connected account')
        elif mode == 'connect':
            account_id = event.get('account', '')
            if not isinstance(account_id, str) or not account_id.startswith('acct_'):
                raise ValueError('Missing connected account')
        else:
            raise RuntimeError('Invalid Stripe mode.')
    except (KeyError, TypeError, ValueError, UnicodeDecodeError):
        raise WebhookError('Μη έγκυρο webhook πληρωμής.') from None
    with get_payment_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT public.koinoxrista_payment_webhook(%s,%s)',
                    (Jsonb(event), account_id))
        return cur.fetchone()[0]
