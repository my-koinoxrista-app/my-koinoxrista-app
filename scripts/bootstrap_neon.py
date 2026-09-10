"""Bootstrap a fresh Neon database with the Koinoxrista schema and tenant security.

Run with NEON_ADMIN_DSN set to a Neon direct/admin connection string. The script
aborts when the target already contains application tables; it never overwrites
an existing database.
"""
import os
from pathlib import Path
import secrets
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
ROLE = 'koinoxrista_app'
MIGRATIONS = [
    'schema.sql', '002_apartments.sql', '003_allocation_tables.sql',
    '004_expense_categories.sql', '005_building_facilities.sql',
    '006_composite_keys.sql', '007_issued_statements.sql',
    '008_companies.sql', '009_tenant_security.sql',
    '010_statement_delivery.sql', '011_payments.sql',
]


def runtime_dsn(admin_dsn, password):
    parsed = urlsplit(admin_dsn)
    if not parsed.scheme or not parsed.hostname:
        raise SystemExit('NEON_ADMIN_DSN must be a valid PostgreSQL URL.')
    host = quote(ROLE, safe='') + ':' + quote(password, safe='') + '@' + parsed.hostname
    if parsed.port:
        host += f':{parsed.port}'
    return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))


def main():
    dsn = os.environ.get('NEON_ADMIN_DSN', '').strip()
    if not dsn:
        raise SystemExit('Set NEON_ADMIN_DSN in the terminal first.')
    app_password = secrets.token_urlsafe(48)
    scope_key = secrets.token_hex(32)
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.companies'), to_regclass('public.buildings')")
            existing = cur.fetchone()
            if any(existing):
                raise SystemExit('Target already contains application tables; no changes made.')
            cur.execute(sql.SQL('CREATE ROLE {} LOGIN NOINHERIT NOBYPASSRLS PASSWORD {}').format(
                sql.Identifier(ROLE), sql.Literal(app_password)))
            for filename in MIGRATIONS:
                print(f'Applying {filename}...')
                if filename == '009_tenant_security.sql':
                    cur.execute((ROOT / 'migrations' / filename).read_text(encoding='utf-8'))
                    cur.execute('INSERT INTO tenant_security.scope_secret(id,secret) VALUES(true,%s)',
                                (bytes.fromhex(scope_key),))
                else:
                    cur.execute((ROOT / 'migrations' / filename).read_text(encoding='utf-8'))
        conn.commit()
    print('\nNeon bootstrap completed.')
    print('Add these values to Streamlit Cloud Secrets:')
    print(f'POSTGRES_APP_DSN = "{runtime_dsn(dsn, app_password)}"')
    print(f'POSTGRES_APP_USER = "{ROLE}"')
    print(f'POSTGRES_APP_PASSWORD = "{app_password}"')
    print(f'KOINOXRISTA_SCOPE_KEY = "{scope_key}"')
    print('\nThe DSN above uses the restricted runtime role, not the Neon administrator.')


if __name__ == '__main__':
    main()
