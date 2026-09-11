"""Restricted payment-service connection; no user session or portal signing key."""
import os
import psycopg
from psycopg.conninfo import conninfo_to_dict


def get_payment_connection():
    dsn = os.getenv('KOINOXRISTA_WEBHOOK_DATABASE_URL', '').strip()
    if not dsn:
        raise RuntimeError('Payment database is not configured.')
    params = conninfo_to_dict(dsn)
    if params.get('user') != 'koinoxrista_webhook':
        raise RuntimeError('Payment database requires the restricted webhook role.')
    if params.get('host') not in ('localhost', '127.0.0.1') and params.get('sslmode') not in ('require', 'verify-ca', 'verify-full'):
        raise RuntimeError('Payment database requires TLS.')
    return psycopg.connect(dsn, connect_timeout=10,
        options='-c search_path=public -c row_security=on -c statement_timeout=30000 -c idle_in_transaction_session_timeout=60000')
