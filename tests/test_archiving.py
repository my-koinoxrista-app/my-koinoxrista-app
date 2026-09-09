import copy
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from configuration import building_json, load_building
from configuration_forms import set_property_archived, save_properties
from expense_engine import AllocationError, Expense, Period, Rule, calculate_period
from building_lifecycle import set_building_archived, _dependencies, CONFIG_TABLES, HISTORY_TABLES, CONFIG_REFERENCES, INDIRECT_TABLES


def sample():
    return {
        'id': 'b1', 'name': 'Building', 'address': '',
        'apartments': [{'id': 'a1', 'code': 'A1'}, {'id': 'a2', 'code': 'A2'}],
        'tables': [{'id': 'general', 'name': 'General', 'weights': {'a1': '1000', 'a2': '0'}, 'expected_total': '1000'}],
        'categories': [{'id': 'cleaning', 'name': 'Cleaning', 'rule': {'type': 'WEIGHTED', 'table_id': 'general'}}],
        'property_names': {'a2': 'Storage'},
    }


class ArchiveTests(unittest.TestCase):
    def test_property_archive_restore_roundtrip_and_no_mutation(self):
        original = sample()
        before = copy.deepcopy(original)
        archived = set_property_archived(original, 'a2', True)
        building = load_building(archived)
        self.assertTrue(load_building(building_json(building)).apartments[1].archived)
        self.assertEqual(original, before)
        self.assertEqual(archived['tables'], original['tables'])
        self.assertEqual(archived['property_names'], original['property_names'])
        restored = set_property_archived(archived, 'a2', False)
        self.assertFalse(load_building(restored).apartments[1].archived)

    def test_nonzero_shares_and_equal_participation_block_archive(self):
        with self.assertRaises(AllocationError):
            set_property_archived(sample(), 'a1', True)
        data = sample()
        data['categories'][0]['rule'] = {'type': 'EQUAL', 'participants': ['a1', 'a2']}
        with self.assertRaises(AllocationError):
            set_property_archived(data, 'a2', True)

    def test_existing_allocations_unchanged_and_overrides_blocked(self):
        building = load_building(set_property_archived(sample(), 'a2', True))
        expense = Expense('e1', 'cleaning', Decimal('10'))
        period = Period('2026-09-01', '2026-09-30', (expense,))
        self.assertEqual(calculate_period(building, period)['apartment_totals'],
                         {'a1': Decimal('10'), 'a2': Decimal('0')})
        for rule, charges in ((Rule('DIRECT'), {'a2': Decimal('10')}),
                              (Rule('EQUAL', participants=('a2',)), {})):
            with self.subTest(rule=rule), self.assertRaises(AllocationError):
                calculate_period(building, Period('2026-09-01', '2026-09-30', (
                    Expense('e2', 'cleaning', Decimal('10'), rule=rule,
                            direct_charges=charges, override_reason='Test'),)))

    def test_editor_save_preserves_archived_property(self):
        data = set_property_archived(sample(), 'a2', True)
        rows = [dict(a, name=data['property_names'].get(a['id'], '')) for a in data['apartments']]
        rows[0]['code'] = 'A01'
        result = save_properties(data, rows)
        self.assertTrue(result['apartments'][1]['archived'])
        self.assertEqual(result['property_names']['a2'], 'Storage')

    def test_building_archive_scoped_update_and_missing_record(self):
        connection = MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ('b1',)
        with patch('building_lifecycle.get_connection', return_value=connection):
            set_building_archived('b1', True)
            self.assertEqual(cursor.execute.call_args.args[1], (True, 'b1'))
            cursor.fetchone.return_value = None
            with self.assertRaises(ValueError):
                set_building_archived('other-company-building', False)

    def test_cleanup_recognizes_delivery_schema_but_keeps_it_as_dependency(self):
        refs = [('public', 'buildings', 'public', table, table + '_fk',
                 ['building_id'], ['id'], 'r')
                for table in (set(CONFIG_TABLES) | HISTORY_TABLES) - INDIRECT_TABLES]
        refs.extend(('public', parent, 'public', child, child + '_fk',
                     ['building_id', pair[0]], ['building_id', pair[1]], 'r')
                    for (child, parent), pair in CONFIG_REFERENCES.items())
        refs.extend([
            ('public', 'buildings', 'public', 'property_delivery_contacts', 'contacts_building',
             ['company_id', 'building_id'], ['company_id', 'id'], 'r'),
            ('public', 'apartments', 'public', 'property_delivery_contacts', 'contacts_property',
             ['building_id', 'apartment_id'], ['building_id', 'id'], 'r'),
            ('public', 'buildings', 'public', 'issued_property_pdfs', 'pdf_building',
             ['company_id', 'building_id'], ['company_id', 'id'], 'r'),
        ])
        cursor = MagicMock()
        cursor.fetchall.return_value = [(t,) for t in set(CONFIG_TABLES) | HISTORY_TABLES]
        with patch('building_lifecycle._references', return_value=refs):
            dependencies = _dependencies(cursor, 'public')
        self.assertIn(('public', 'property_delivery_contacts'), dependencies)
        self.assertIn(('public', 'issued_property_pdfs'), dependencies)


if __name__ == '__main__':
    unittest.main()
