import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import tenant_scope
import smtp_profiles as profiles
import statement_delivery as delivery


COMPANY = '11111111-1111-1111-1111-111111111111'
OTHER = '22222222-2222-2222-2222-222222222222'


class SenderProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / '.env'
        values = {'KOINOXRISTA_EMAIL_SENDING_ENABLED': 'true',
                  'KOINOXRISTA_SMTP_PROFILES': json.dumps({COMPANY: [{'id': 'second', 'prefix': 'KOINOXRISTA_SECOND_'}]})}
        for prefix, email in [('KOINOXRISTA_', 'first@example.com'), ('KOINOXRISTA_SECOND_', 'second@example.com')]:
            for key, value in {'SMTP_HOST': 'smtp.example.com', 'SMTP_SECURITY': 'starttls',
                               'SMTP_USERNAME': email, 'SMTP_PASSWORD': 'synthetic-password',
                               'SMTP_FROM_EMAIL': email}.items():
                values[prefix + key] = value
        path.write_text('\n'.join(f"{k}='{v}'" for k, v in values.items()))
        for module, attribute in [(profiles, 'ENV_FILE'), (delivery, 'SMTP_ENV_FILE')]:
            patcher = patch.object(module, attribute, path)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_company_isolation(self):
        with tenant_scope('issuer', 'user', OTHER):
            self.assertEqual(profiles.profiles(), {})
            with self.assertRaises(ValueError):
                with profiles.sender_scope('second'):
                    pass

    def test_selection_and_reset_and_no_secrets_in_options(self):
        with tenant_scope('issuer', 'user', COMPANY):
            accounts = delivery.sender_accounts()
            self.assertEqual([a['email'] for a in accounts], ['first@example.com', 'second@example.com'])
            self.assertTrue(all(set(a) == {'id', 'email', 'name'} for a in accounts))
            with profiles.sender_scope('second'):
                self.assertEqual(delivery.smtp_settings()['username'], 'second@example.com')
            self.assertEqual(delivery.smtp_settings()['username'], 'first@example.com')

    def test_prepared_sender_is_independent_of_current_selection(self):
        with tenant_scope('issuer', 'user', COMPANY), profiles.sender_scope('second'):
            self.assertEqual(delivery._settings_for_sender('first@example.com')['username'], 'first@example.com')
            with self.assertRaises(delivery.DeliveryError):
                delivery._settings_for_sender('unconfigured@example.com')

    def test_sender_change_invalidates_approval(self):
        with tenant_scope('issuer', 'user', COMPANY):
            data = {'statement': {'id': 'statement'}, 'entries': []}
            settings = {'display_name': 'Company', 'reply_to': ''}
            first = delivery._approval_fingerprint(data, [], settings, delivery.smtp_settings())
            with profiles.sender_scope('second'):
                second = delivery._approval_fingerprint(data, [], settings, delivery.smtp_settings())
            self.assertNotEqual(first, second)
