"""Verified Stripe webhook handling for hosted payment requests."""
import hashlib
import hmac
import json
import os
import time
from uuid import UUID

from database import get_connection


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
        event_id = str(event['id'])
        event_type = str(event['type'])
        account_id = str(event.get('account') or '')
        obj = event['data']['object']
        metadata = obj.get('metadata') or {}
        token = UUID(str(metadata.get('payment_token') or obj.get('client_reference_id')))
    except (KeyError, TypeError, ValueError, UnicodeDecodeError):
        raise WebhookError('Το webhook δεν περιέχει payment token.') from None
    provider_payment_id = str(obj.get('payment_intent') or obj.get('id') or '')
    failure = str((obj.get('last_payment_error') or {}).get('message') or '')
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT public.koinoxrista_payment_webhook(%s,%s,%s,%s,%s,%s,%s)',
                ('stripe', event_id, str(token), event_type, provider_payment_id, account_id, failure))
        return cur.fetchone()[0]
