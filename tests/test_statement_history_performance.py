import hashlib
import unittest
from unittest.mock import patch

import statement_store as store


class ArchivedHistoryTests(unittest.TestCase):
    def fixture(self, connection, count=30, corrupt=False):
        cur = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        pdf = b'%PDF-fixture'
        sha = hashlib.sha256(pdf).hexdigest()
        properties = [{'id': str(i), 'code': str(i)} for i in range(count)]
        cur.fetchone.side_effect = [({},), ({}, {}, {}, {'apartments': properties}, None, 1, pdf, sha)]
        cur.fetchall.return_value = [(str(i), str(i), prop, pdf, 'bad' if corrupt else sha)
                                    for i, prop in enumerate(properties)]
        return cur

    @patch.object(store, 'current_tenant', return_value='company')
    @patch.object(store, 'get_connection')
    @patch.object(store, '_archive_documents')
    def test_existing_archive_uses_one_connection_without_recalculation(self, archive, connection, tenant):
        cur = self.fixture(connection)
        documents = store.ensure_property_pdfs('building', 'statement', include_content=True)
        self.assertEqual(len(documents), 30)
        self.assertTrue(all(d['pdf'].startswith(b'%PDF-') for d in documents))
        connection.assert_called_once()
        self.assertEqual(cur.execute.call_count, 3)
        archive.assert_not_called()

    @patch.object(store, 'current_tenant', return_value='company')
    @patch.object(store, 'get_connection')
    def test_corrupt_archived_pdf_still_rejected(self, connection, tenant):
        self.fixture(connection, corrupt=True)
        with self.assertRaises(store.StatementError):
            store.ensure_property_pdfs('building', 'statement', include_content=True)

    @patch.object(store, 'current_tenant', return_value='company')
    @patch.object(store, 'get_connection')
    def test_default_return_does_not_include_pdf_bytes(self, connection, tenant):
        self.fixture(connection, count=1)
        documents = store.ensure_property_pdfs('building', 'statement')
        self.assertNotIn('pdf', documents[0])


if __name__ == '__main__': unittest.main()
