"""Payment boundary tests; no network, live secrets or database connections."""
import hashlib
import hmac
import io
import json
import os
import time
import unittest
from unittest.mock import patch, MagicMock

import payment_webhook as webhook
import payment_webhook_server as server
import payment_webhook_database as webhook_db
import stripe_checkout as stripe


class PaymentTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            'KOINOXRISTA_STRIPE_WEBHOOK_SECRET': 'whsec_synthetic',
            'KOINOXRISTA_STRIPE_MODE': 'direct',
            'KOINOXRISTA_STRIPE_ACCOUNT_ID': 'acct_synthetic',
            'KOINOXRISTA_STRIPE_SECRET_KEY': 'sk_test_synthetic',
            'KOINOXRISTA_PAYMENT_PUBLIC_BASE_URL': 'https://payments.example.com',
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def signature(self, body, timestamp=None):
        timestamp = int(time.time()) if timestamp is None else timestamp
        digest = hmac.new(b'whsec_synthetic',str(timestamp).encode()+b'.'+body,hashlib.sha256).hexdigest()
        return f't={timestamp},v1={digest}'

    def test_valid_raw_signature_and_rotated_signatures(self):
        body = b'{ "unmodified": true }'
        webhook._signature(body,self.signature(body)+',v1=old')
        with self.assertRaises(webhook.WebhookError):
            webhook._signature(body+b' ',self.signature(body))

    def test_reject_stale_future_and_malformed_signatures(self):
        for header in ('', 't=bad,v1=bad','v1=bad',self.signature(b'{}',int(time.time())-301),
                       self.signature(b'{}',int(time.time())+301)):
            with self.subTest(header=header),self.assertRaises(webhook.WebhookError):
                webhook._signature(b'{}',header)

    def test_missing_secret_is_retryable_configuration_error(self):
        os.environ.pop('KOINOXRISTA_STRIPE_WEBHOOK_SECRET')
        with self.assertRaises(RuntimeError):
            webhook._signature(b'{}','anything')

    def test_unrelated_events_need_no_database(self):
        body=json.dumps({'type':'customer.created'}).encode()
        with patch.object(webhook,'get_payment_connection') as conn:
            self.assertEqual(webhook.handle_stripe_webhook(body,self.signature(body)),'IGNORED')
            conn.assert_not_called()

    def test_invalid_json_types_are_rejected(self):
        for body in (b'null',b'[]',b'"text"',b'{',b'\xff'):
            with self.subTest(body=body),self.assertRaises(webhook.WebhookError):
                webhook.handle_stripe_webhook(body,self.signature(body))

    def test_unsigned_body_never_reaches_database(self):
        with patch.object(webhook,'get_payment_connection') as conn:
            with self.assertRaises(webhook.WebhookError):
                webhook.handle_stripe_webhook(b'{}','t=1,v1=bad')
            conn.assert_not_called()

    def call_server(self,path,method='GET',body=b'',length=None):
        headers=[]
        environ={'PATH_INFO':path,'REQUEST_METHOD':method,'wsgi.input':io.BytesIO(body),
            'CONTENT_LENGTH':str(len(body) if length is None else length)}
        output=b''.join(server.app(environ,lambda status,values:headers.append((status,dict(values)))))
        return headers[0][0],headers[0][1],output

    def test_health_and_return_pages_do_not_update_payments(self):
        with patch.object(server,'handle_stripe_webhook') as handler:
            for path in ('/healthz','/payment/success','/payment/cancelled'):
                status,headers,body=self.call_server(path)
                self.assertEqual(status,'200 OK')
                self.assertEqual(headers['Cache-Control'],'no-store')
            handler.assert_not_called()

    def test_request_limits_and_methods(self):
        with patch.object(server,'handle_stripe_webhook') as handler:
            for length,status in ((-1,'400'),(0,'400'),('abc','400'),(1000001,'413'),(5,'400')):
                self.assertTrue(self.call_server('/webhooks/stripe','POST',b'',length)[0].startswith(status))
            self.assertTrue(self.call_server('/webhooks/stripe')[0].startswith('405'))
            self.assertTrue(self.call_server('/unknown')[0].startswith('404'))
            handler.assert_not_called()

    def test_database_failure_returns_retryable_response_without_details(self):
        with patch.object(server,'handle_stripe_webhook',side_effect=RuntimeError('secret-value')):
            status,_,body=self.call_server('/webhooks/stripe','POST',b'{}')
            self.assertTrue(status.startswith('503'))
            self.assertNotIn(b'secret-value',body)

    def test_checkout_redirect_has_no_referrer(self):
        with patch.object(server,'open_checkout',return_value=('REDIRECT','https://checkout.stripe.com/c/test')):
            status,headers,_=self.call_server('/pay/token')
            self.assertTrue(status.startswith('303'))
            self.assertEqual(headers['Referrer-Policy'],'no-referrer')

    def test_database_rejects_owner_or_unencrypted_remote_connection(self):
        with patch.object(webhook_db.psycopg,'connect') as connect:
            for dsn in ('', 'postgresql://owner:fake@host/db?sslmode=require',
                        'postgresql://koinoxrista_webhook:fake@host/db'):
                os.environ['KOINOXRISTA_WEBHOOK_DATABASE_URL']=dsn
                with self.assertRaises(RuntimeError):
                    webhook_db.get_payment_connection()
            connect.assert_not_called()

    def test_public_url_rejects_credentials_paths_and_insecure_remote(self):
        for url in ('http://public.example.com','https://user:pass@host','https://host/path',
                    'https://host?token=x','https://host#x',''):
            os.environ['KOINOXRISTA_PAYMENT_PUBLIC_BASE_URL']=url
            with self.subTest(url=url),self.assertRaises(stripe.PaymentError):
                stripe.public_base_url()
        os.environ['KOINOXRISTA_PAYMENT_PUBLIC_BASE_URL']='http://localhost:8787'
        self.assertEqual(stripe.public_base_url(),'http://localhost:8787')

    def test_stripe_transport_idempotency_and_direct_account_headers(self):
        response=MagicMock()
        response.__enter__.return_value.read.return_value=b'{"id":"cs_test"}'
        with patch.object(stripe.urllib.request,'urlopen',return_value=response) as send:
            stripe.stripe_request('acct_synthetic','checkout/sessions',{'mode':'payment'},'stable-key')
            headers={k.lower():v for k,v in send.call_args.args[0].header_items()}
            self.assertEqual(headers['idempotency-key'],'stable-key')
            self.assertNotIn('stripe-account',headers)
        with self.assertRaises(stripe.PaymentError):
            stripe.stripe_request('acct_wrong','account')

    def test_connect_mode_sets_account_header(self):
        os.environ['KOINOXRISTA_STRIPE_MODE']='connect'
        response=MagicMock()
        response.__enter__.return_value.read.return_value=b'{"id":"acct_connected"}'
        with patch.object(stripe.urllib.request,'urlopen',return_value=response) as send:
            stripe.stripe_request('acct_connected','account')
            headers={k.lower():v for k,v in send.call_args.args[0].header_items()}
            self.assertEqual(headers['stripe-account'],'acct_connected')


if __name__=='__main__':
    unittest.main()
