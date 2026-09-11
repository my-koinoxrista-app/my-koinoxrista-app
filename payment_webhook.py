"""Verified Stripe webhook handling for hosted payment requests."""
import hashlib
import hmac
import json
import os
import time
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb


class WebhookError(ValueError):
    pass


def _signature(raw_body, header):
    secret = os.environ.get('KOINOXRISTA_STRIPE_WEBHOOK_SECRET', '').strip()
    if not secret or not header:
        raise WebhookError('Το webhook secret δεν έχει ρυθμιστεί.')
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
        if not isinstance(event, dict):
            raise ValueError()
        kind = event.get('type')
        if kind not in ('checkout.session.completed', 'checkout.session.async_payment_succeeded',
                        'checkout.session.async_payment_failed', 'charge.refunded'):
            return 'IGNORED'
        obj = event['data']['object']
        if not isinstance(obj, dict) or not isinstance(event.get('livemode'), bool):
            raise ValueError()
        expected_live = os.environ.get('KOINOXRISTA_PAYMENT_MODE', 'test')
        if expected_live not in ('test', 'live') or event['livemode'] != (expected_live == 'live'):
            raise ValueError()
        if not str(event.get('account', '')).startswith('acct_'):
            raise ValueError()
        if kind != 'charge.refunded':
            metadata = obj.get('metadata') or {}
            if not metadata.get('payment_token'):
                return 'IGNORED'
            UUID(str(metadata['payment_token']))
    except (KeyError, TypeError, ValueError, AttributeError, UnicodeDecodeError):
        raise WebhookError('Μη έγκυρο συμβάν πληρωμής.') from None
    dsn = os.environ.get('KOINOXRISTA_WEBHOOK_DSN', '').strip()
    if not dsn:
        raise RuntimeError('Webhook database connection is not configured.')
    with psycopg.connect(dsn, connect_timeout=10) as conn, conn.cursor() as cur:
        cur.execute('SELECT current_user')
        if cur.fetchone()[0] != 'koinoxrista_webhook':
            raise RuntimeError('Webhook requires its dedicated database role.')
        cur.execute('SELECT public.koinoxrista_confirm_stripe_event(%s)', (Jsonb(event),))
        result = cur.fetchone()[0]
        if result == 'UNMATCHED' and kind == 'charge.refunded':
            # A refund may arrive before the payment confirmation; let Stripe retry.
            raise RuntimeError('Payment confirmation has not arrived yet.')
        return result
