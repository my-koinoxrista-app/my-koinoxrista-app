"""Small PostgreSQL store for demo monthly periods and receipt metadata."""
import json
from psycopg.types.json import Jsonb
from database import get_connection


def initialize():
    """Check the required tables without creating or changing schema."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT to_regclass('public.monthly_periods'),
                             to_regclass('public.receipt_documents')""")
        if any(value is None for value in cur.fetchone()):
            raise RuntimeError('Λείπουν πίνακες αποθήκευσης. Εφάρμοσε τα migrations.')


def load_period_data(building_id, key):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT data FROM monthly_periods WHERE building_id=%s AND period_key=%s', (building_id, key))
        row = cur.fetchone()
    return row[0] if row else {'expenses': []}


def save_period_data(building_id, key, data):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''INSERT INTO monthly_periods (building_id, period_key, data)
            VALUES (%s,%s,%s) ON CONFLICT (building_id, period_key)
            DO UPDATE SET data=EXCLUDED.data, updated_at=now()''', (building_id, key, Jsonb(data)))


def find_receipt(building_id, digest):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT id, period_key, extracted FROM receipt_documents WHERE building_id=%s AND sha256=%s', (building_id, digest))
        return cur.fetchone()


def save_receipt(document):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''INSERT INTO receipt_documents
            (id, building_id, period_key, filename, sha256, content_type, extracted)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (building_id,sha256) DO NOTHING''',
            (document['id'], document['building_id'], document['period_key'], document['filename'], document['sha256'], document['content_type'], Jsonb(document['extracted'])))


def get_receipt(document_id, building_id):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT filename, content_type, extracted FROM receipt_documents WHERE id=%s AND building_id=%s', (document_id, building_id))
        return cur.fetchone()


def import_receipt(document, expenses):
    """Atomically register one receipt and append its approved charges once."""
    building_id, key = document['building_id'], document['period_key']
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''INSERT INTO monthly_periods (building_id,period_key,data)
            VALUES (%s,%s,%s) ON CONFLICT DO NOTHING''',
            (building_id, key, Jsonb({'expenses': []})))
        cur.execute('SELECT data FROM monthly_periods WHERE building_id=%s AND period_key=%s FOR UPDATE', (building_id, key))
        data = cur.fetchone()[0]
        cur.execute('''INSERT INTO receipt_documents
            (id,building_id,period_key,filename,sha256,content_type,extracted)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (building_id,sha256) DO NOTHING
            RETURNING id''',
            (document['id'], building_id, key, document['filename'], document['sha256'], document['content_type'], Jsonb(document['extracted'])))
        if cur.fetchone() is None:
            raise ValueError('Αυτό το παραστατικό έχει ήδη εισαχθεί στην πολυκατοικία.')
        existing = {item['id'] for item in data.get('expenses', [])}
        data.setdefault('expenses', []).extend(item for item in expenses if item['id'] not in existing)
        cur.execute('''UPDATE monthly_periods SET data=%s,updated_at=now()
            WHERE building_id=%s AND period_key=%s''', (Jsonb(data), building_id, key))
    return data
