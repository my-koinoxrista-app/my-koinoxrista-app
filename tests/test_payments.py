import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import patch, MagicMock
from uuid import uuid4

import payment_webhook as webhook
from payment_service import _form_for


class PaymentTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'KOINOXRISTA_STRIPE_WEBHOOK_SECRET': 'whsec_fixture',
            'KOINOXRISTA_PAYMENT_MODE': 'test',
            'KOINOXRISTA_WEBHOOK_DSN': 'postgresql://fixture.invalid/test',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.event = {'id': 'evt_fixture', 'type': 'checkout.session.completed',
                      'livemode': False, 'account': 'acct_fixture',
                      'data': {'object': {'metadata': {'payment_token': str(uuid4())}}}}

    def signed(self, event=..., timestamp=1000):
        body = json.dumps(self.event if event is ... else event).encode()
        sig = hmac.new(b'whsec_fixture', str(timestamp).encode()+b'.'+body, hashlib.sha256).hexdigest()
        return body, f't={timestamp},v1={sig}'

    @patch.object(webhook.time, 'time', return_value=1000)
    @patch.object(webhook.psycopg, 'connect')
    def test_no_login_required_and_dedicated_role(self, connect, clock):
        cur = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [('koinoxrista_webhook',), ('APPLIED',)]
        self.assertEqual(webhook.handle_stripe_webhook(*self.signed()), 'APPLIED')
        self.assertIn('koinoxrista_confirm_stripe_event', cur.execute.call_args[0][0])

    @patch.object(webhook.time, 'time', return_value=1000)
    @patch.object(webhook.psycopg, 'connect')
    def test_rejects_app_role(self, connect, clock):
        cur = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = ('koinoxrista_app',)
        with self.assertRaises(RuntimeError): webhook.handle_stripe_webhook(*self.signed())

    @patch.object(webhook.time, 'time', return_value=1000)
    @patch.object(webhook.psycopg, 'connect')
    def test_invalid_or_expired_signatures_never_reach_database(self, connect, clock):
        body, header = self.signed()
        for args in [(body+b' ', header), self.signed(timestamp=1), (body, '')]:
            with self.assertRaises(webhook.WebhookError): webhook.handle_stripe_webhook(*args)
        connect.assert_not_called()

    @patch.object(webhook.time, 'time', return_value=1000)
    @patch.object(webhook.psycopg, 'connect')
    def test_live_event_rejected_in_test_service(self, connect, clock):
        self.event['livemode'] = True
        with self.assertRaises(webhook.WebhookError): webhook.handle_stripe_webhook(*self.signed())
        connect.assert_not_called()

    @patch.object(webhook.time, 'time', return_value=1000)
    @patch.object(webhook.psycopg, 'connect')
    def test_unrelated_events_ignored(self, connect, clock):
        self.event['type'] = 'customer.created'
        self.assertEqual(webhook.handle_stripe_webhook(*self.signed()), 'IGNORED')
        connect.assert_not_called()

    @patch.object(webhook.time, 'time', return_value=1000)
    def test_malformed_payload(self, clock):
        for payload in [[], None, {'type': 'checkout.session.completed', 'data': []}]:
            with self.assertRaises(webhook.WebhookError): webhook.handle_stripe_webhook(*self.signed(payload))

    def test_checkout_amount_is_integer_cents(self):
        form = _form_for(dict(payment_token=uuid4(), property_code='A1', amount_cents=1234,
            currency='eur', period_key='2026-09', statement_id=uuid4(), apartment_id='a1'), 'https://payments.example.com')
        self.assertEqual(form['line_items[0][price_data][unit_amount]'], '1234')
        self.assertTrue(form['success_url'].startswith('https://payments.example.com/payment/success?'))


class ReturnPageTests(unittest.TestCase):
    def handler(self, path):
        from payment_webhook_server import WebhookHandler
        handler = object.__new__(WebhookHandler)
        handler.path = path
        handler._reply = MagicMock()
        return handler

    def test_success_does_not_confirm_or_echo_token(self):
        handler = self.handler('/payment/success?token=private-token')
        handler.do_GET()
        status, body, content_type = handler._reply.call_args[0]
        self.assertEqual(status, 200)
        self.assertNotIn(b'private-token', body)
        self.assertIn('μόλις ληφθεί'.encode(), body)

    def test_oversize_request_rejected_before_body_read(self):
        handler = self.handler('/webhooks/stripe')
        handler.headers = {'Content-Length': '1000001'}
        handler.rfile = MagicMock()
        handler.do_POST()
        self.assertEqual(handler._reply.call_args[0][0], 413)
        handler.rfile.read.assert_not_called()

    def test_unknown_route(self):
        handler = self.handler('/unknown')
        handler.do_GET()
        self.assertEqual(handler._reply.call_args[0][0], 404)


if __name__ == '__main__': unittest.main()
