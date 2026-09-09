"""Install migration 009 and a fresh least-privilege runtime role atomically."""
import getpass
import os
from pathlib import Path
import secrets
import sys

import psycopg
from psycopg import sql
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
ROLE = 'koinoxrista_app'


def main():
    target = ROOT / '.env.tenancy'
    if target.exists():
        raise SystemExit('The runtime credential file already exists. Review the installation; no changes made.')
    print('This installer requires migrations 007 and 008, a verified backup, and the database administrator.')
    print('It creates a restricted role and RLS policies. It does not move or delete financial records.')
    if input('Type MIGRATE to continue: ').strip() != 'MIGRATE':
        raise SystemExit('Cancelled.')
    password = getpass.getpass('Database administrator password: ')
    if not password:
        raise SystemExit('Administrator password is required.')
    app_password = secrets.token_urlsafe(48)
    signing_key = secrets.token_bytes(32)
    created = False
    try:
        with psycopg.connect(
            host=os.getenv('POSTGRES_HOST', '127.0.0.1'),
            port=int(os.getenv('POSTGRES_PORT', '5433')),
            dbname=os.getenv('POSTGRES_DB', 'koinoxrista'),
            user=os.getenv('POSTGRES_ADMIN_USER', 'koinoxrista'),
            password=password,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), current_user")
                print('Connected to:', *cur.fetchone())
                cur.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (ROLE,))
                if cur.fetchone():
                    raise RuntimeError('Runtime role already exists. Review the current state; no changes made.')
                cur.execute("SELECT to_regclass('public.issued_statements'), to_regclass('public.company_memberships')")
                if any(v is None for v in cur.fetchone()):
                    raise RuntimeError('Apply migrations 007 and 008 first. The database was not changed.')
                cur.execute(sql.SQL('CREATE ROLE {} LOGIN NOINHERIT NOBYPASSRLS PASSWORD {}').format(
                    sql.Identifier(ROLE), sql.Literal(app_password)))
                cur.execute((ROOT / 'migrations' / '009_tenant_security.sql').read_text(encoding='utf-8'))
                cur.execute('INSERT INTO tenant_security.scope_secret(id,secret) VALUES(true,%s)', (signing_key,))
                # Create the credential file before commit, so an I/O failure rolls back DDL.
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                fd = os.open(target, flags, 0o600)
                created = True
                with os.fdopen(fd, 'w') as out:
                    out.write(f'POSTGRES_APP_USER={ROLE}\nPOSTGRES_APP_PASSWORD={app_password}\n'
                              f'KOINOXRISTA_SCOPE_KEY={signing_key.hex()}\n')
            # psycopg commits the entire role+RLS transaction here.
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise
    print('Migration 009 committed. Runtime credentials are in .env.tenancy (mode 600).')
    print('Copy its three variables into .env, remove privileged database credentials from the runtime environment,')
    print('and retain administrator access only for offline migrations and backups.')
    print('Do not publish the application until the two-company integration tests pass.')


if __name__ == '__main__':
    main()
