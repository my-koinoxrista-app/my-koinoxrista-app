"""Fail-closed database access. The runtime role never owns the schema."""
import os
import json
import hmac
import hashlib
import time
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID
import psycopg
from dotenv import load_dotenv

load_dotenv()
_scope = ContextVar('koinoxrista_scope', default=None)

class AccessError(ValueError):
    pass

def _identity(issuer, subject):
    if not all(isinstance(v, str) and v.strip() for v in (issuer, subject)):
        raise AccessError('Απαιτείται πιστοποιημένη ταυτότητα.')
    return issuer, subject

@contextmanager
def identity_scope(issuer, subject):
    token = _scope.set((*_identity(issuer, subject), None))
    try:
        yield
    finally:
        _scope.reset(token)

@contextmanager
def tenant_scope(issuer, subject, company_id):
    issuer, subject = _identity(issuer, subject)
    try:
        company_id = str(UUID(str(company_id)))
    except (ValueError, TypeError, AttributeError):
        raise AccessError('Μη έγκυρη εταιρεία.') from None
    token = _scope.set((issuer, subject, company_id))
    try:
        yield
    finally:
        _scope.reset(token)

def current_tenant():
    scope = _scope.get()
    if scope is None or scope[2] is None:
        raise AccessError('Απαιτείται εταιρική συνεδρία.')
    return scope[2]

def _connect():
    if os.getenv('POSTGRES_PASSWORD') or os.getenv('KOINOXRISTA_ADMIN_DSN'):
        raise AccessError('Αφαίρεσε τα διαχειριστικά database credentials από το περιβάλλον της εφαρμογής.')
    password = os.getenv('POSTGRES_APP_PASSWORD')
    user = os.getenv('POSTGRES_APP_USER', 'koinoxrista_app')
    if not password or user != 'koinoxrista_app':
        raise AccessError('Δεν έχουν ρυθμιστεί τα περιορισμένα database credentials.')
    return psycopg.connect(
        host=os.getenv('POSTGRES_HOST', '127.0.0.1'),
        port=int(os.getenv('POSTGRES_PORT', '5433')),
        dbname=os.getenv('POSTGRES_DB', 'koinoxrista'),
        user=user, password=password,
        options='-c search_path=public -c row_security=on',
    )

def _signed_context(scope):
    """Short-lived, exact-byte signed claims. The signing key is never sent to SQL."""
    key_hex = os.getenv('KOINOXRISTA_SCOPE_KEY', '')
    try:
        key = bytes.fromhex(key_hex)
    except ValueError:
        key = b''
    if len(key) < 32:
        raise AccessError('Δεν έχει ρυθμιστεί το μυστικό εταιρικής πρόσβασης.')
    payload = json.dumps({'iss': scope[0], 'sub': scope[1], 'company': scope[2],
                          'exp': int(time.time()) + 300},
                         ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    signature = hmac.new(key, payload.encode('utf-8'), hashlib.sha256).hexdigest()
    return payload, signature


def _scoped_connection(require_company):
    scope = _scope.get()
    if scope is None or (require_company and scope[2] is None):
        raise AccessError('Απαιτείται εταιρική συνεδρία.')
    payload, signature = _signed_context(scope)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT set_config(%s,%s,true)', ('app.context', payload))
            cur.execute('SELECT set_config(%s,%s,true)', ('app.signature', signature))
            if scope[2] is not None:
                cur.execute('SELECT public.koinoxrista_can_access(%s::uuid)', (scope[2],))
                if cur.fetchone()[0] is not True:
                    raise AccessError('Δεν υπάρχει πρόσβαση σε αυτή την εταιρεία.')
        return conn
    except BaseException:
        conn.close()
        raise


def get_connection():
    """Return a tenant-scoped connection; never fall back to the owner role."""
    return _scoped_connection(True)

def get_identity_connection():
    """Only identity/membership discovery is permitted without a company."""
    return _scoped_connection(False)

def check_connection():
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT version()')
        return cur.fetchone()[0]
