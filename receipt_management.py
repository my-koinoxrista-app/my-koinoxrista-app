"""Receipt lifecycle for the local, single-operator demo.

Financial deletion is transactional. Original files are moved to a private
archive only after the database commits. No allocation or AI logic lives here.
"""
import copy
import os
from pathlib import Path
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from database import get_connection
from expense_engine import money
from receipt_import import RECEIPT_DIR


class ReceiptDeletionError(ValueError):
    pass


def _document_id(value):
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise ReceiptDeletionError('Μη έγκυρο αναγνωριστικό παραστατικού.') from None


def _expenses(data):
    if not isinstance(data, dict) or not isinstance(data.get('expenses', []), list):
        raise ReceiptDeletionError('Η περίοδος έχει μη αναμενόμενη μορφή. Η διαγραφή ακυρώθηκε.')
    expenses = data.get('expenses', [])
    if any(not isinstance(item, dict) for item in expenses):
        raise ReceiptDeletionError('Βρέθηκε μη έγκυρη χρέωση. Η διαγραφή ακυρώθηκε.')
    return expenses


def _split_expenses(data, document_id):
    """Return a copy without this receipt's charges and the removed rows."""
    kept, removed = [], []
    for item in _expenses(data):
        (removed if item.get('document_id') == document_id else kept).append(item)
    updated = copy.deepcopy(data)
    updated['expenses'] = kept
    return updated, removed


def _check_dependencies(cur):
    """Never allow an unknown FK to remove or orphan additional records."""
    cur.execute('''
        SELECT n.nspname, c.relname, f.conname
        FROM pg_constraint f
        JOIN pg_class c ON c.oid = f.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE f.contype = 'f'
          AND f.confrelid = 'receipt_documents'::regclass
    ''')
    references = cur.fetchall()
    if references:
        names = ', '.join(f'{schema}.{table} ({constraint})'
                          for schema, table, constraint in references)
        raise ReceiptDeletionError('Υπάρχουν πρόσθετες σχέσεις με παραστατικά: '
                                   + names + '. Η διαγραφή ακυρώθηκε.')


def list_receipts(building_id):
    """List stored receipts and their current linked charges, across all months."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''
            SELECT id, period_key, filename, extracted
            FROM receipt_documents WHERE building_id = %s
            ORDER BY period_key DESC, created_at DESC, id
        ''', (building_id,))
        documents = cur.fetchall()
        cur.execute('SELECT period_key, data FROM monthly_periods WHERE building_id = %s',
                    (building_id,))
        periods = cur.fetchall()
    counts = {}
    for _, data in periods:
        for item in _expenses(data):
            did = item.get('document_id')
            if did:
                current = counts.setdefault(did, {'count': 0, 'total': money('0')})
                current['count'] += 1
                current['total'] += money(item['amount'])
    return [
        {'id': did, 'period_key': key, 'filename': filename,
         'extracted': extracted if isinstance(extracted, dict) else {},
         'charge_count': counts.get(did, {}).get('count', 0),
         'total': counts.get(did, {}).get('total', money('0'))}
        for did, key, filename, extracted in documents
    ]


def _archive_file(document_id):
    """Keep a recoverable original; never use an untrusted filename as a path."""
    source = next((RECEIPT_DIR / f'{document_id}{suffix}'
                   for suffix in ('.pdf', '.jpg', '.jpeg', '.png', '.webp')
                   if (RECEIPT_DIR / f'{document_id}{suffix}').is_file()), None)
    if source is None:
        return 'Το αρχικό αρχείο δεν βρέθηκε στον τοπικό φάκελο.'
    archive = RECEIPT_DIR / '.deleted'
    archive.mkdir(parents=True, exist_ok=True)
    destination = archive / f'{document_id}-{uuid4().hex}{source.suffix}'
    os.replace(source, destination)
    return 'Το αρχικό αρχείο μεταφέρθηκε στο data/receipts/.deleted.'


def delete_receipt(building_id, document_id):
    """Remove one receipt and all its linked charges, never other expenses.

    Lock both storage tables to serialize this demo's existing write paths.
    A failure inside the transaction rolls back all financial changes.
    """
    if not isinstance(building_id, str) or not building_id.strip():
        raise ReceiptDeletionError('Επίλεξε πολυκατοικία.')
    document_id = _document_id(document_id)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('LOCK TABLE monthly_periods, receipt_documents IN SHARE ROW EXCLUSIVE MODE')
        _check_dependencies(cur)
        cur.execute('''
            SELECT period_key, filename FROM receipt_documents
            WHERE id = %s AND building_id = %s FOR UPDATE
        ''', (document_id, building_id))
        document = cur.fetchone()
        if document is None:
            raise ReceiptDeletionError('Το παραστατικό δεν βρέθηκε σε αυτή την πολυκατοικία.')
        cur.execute('SELECT period_key, data FROM monthly_periods WHERE building_id = %s FOR UPDATE',
                    (building_id,))
        changes = []
        removed_count = 0
        for period_key, data in cur.fetchall():
            updated, removed = _split_expenses(data, document_id)
            if removed:
                changes.append((period_key, updated))
                removed_count += len(removed)
        for period_key, data in changes:
            cur.execute('''UPDATE monthly_periods SET data = %s, updated_at = now()
                           WHERE building_id = %s AND period_key = %s''',
                        (Jsonb(data), building_id, period_key))
            if cur.rowcount != 1:
                raise ReceiptDeletionError('Η περίοδος άλλαξε. Η διαγραφή ακυρώθηκε.')
        cur.execute('DELETE FROM receipt_documents WHERE id = %s AND building_id = %s',
                    (document_id, building_id))
        if cur.rowcount != 1:
            raise ReceiptDeletionError('Η διαγραφή του παραστατικού δεν ολοκληρώθηκε.')
    # The database has committed. An archive failure must not falsely report
    # that the database was rolled back, or encourage a second destructive retry.
    try:
        file_message = _archive_file(document_id)
    except OSError as exc:
        file_message = ('Η διαγραφή από τη βάση ολοκληρώθηκε, αλλά το αρχείο δεν '
                        f'μεταφέρθηκε στην αρχειοθέτηση ({type(exc).__name__}).')
    return {'charge_count': removed_count, 'file_message': file_message}
