import unittest
from unittest.mock import patch
import monthly_store


class MonthlyBatchTests(unittest.TestCase):
    @patch.object(monthly_store, 'get_connection')
    def test_many_buildings_use_one_query(self, connect):
        cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [('a', {'expenses': [{'amount': '12.50'}]})]
        result = monthly_store.load_periods_data(['a', 'b'], '2026-09')
        self.assertEqual(result, {'a': {'expenses': [{'amount': '12.50'}]}})
        connect.assert_called_once()
        cursor.execute.assert_called_once()
        self.assertEqual(cursor.execute.call_args.args[1], (['a', 'b'], '2026-09'))

    @patch.object(monthly_store, 'get_connection')
    def test_empty_company_needs_no_connection(self, connect):
        self.assertEqual(monthly_store.load_periods_data([], '2026-09'), {})
        connect.assert_not_called()
