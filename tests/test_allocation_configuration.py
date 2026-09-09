import json
import unittest
from decimal import Decimal

from allocation_configuration import (
    CATEGORY_TEMPLATES, configure_category, configure_table,
    direct_rule, equal_rule, table_rule,
) 
from configuration import building_json, load_building
from expense_engine import AllocationError, Expense, Period, calculate_period


def sample():
    return load_building({
        'id': 'b1', 'name': 'Demo', 'address': '',
        'apartments': [{'id': 'a1', 'code': 'A1'}, {'id': 'a2', 'code': 'A2'}],
        'tables': [], 'categories': [],
    })


class AllocationConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.building = configure_table(
            sample(), table_id='general', name='Γενικά',
            weights={'a1': '600', 'a2': '400'}, expected_total='1000',
            source_reference='Εγκεκριμένος πίνακας δοκιμής',
        )

    def test_table_round_trip(self):
        self.assertEqual(load_building(building_json(self.building)), self.building)
        self.assertEqual(self.building.tables['general'].source_reference,
                         'Εγκεκριμένος πίνακας δοκιμής')

    def test_legacy_table_has_optional_source(self):
        data = json.loads(building_json(self.building))
        data['tables'][0].pop('source_reference')
        legacy = load_building(data)
        self.assertEqual(legacy.tables['general'].source_reference, '')
        self.assertEqual(legacy.tables['general'].expected_total, Decimal('1000'))

    def test_rejects_missing_or_wrong_weights(self):
        for weights, total in [({'a1': 1000}, 1000),
                               ({'a1': 600, 'a2': 300}, 1000),
                               ({'a1': -1, 'a2': 1001}, 1000),
                               ({'a1': 0, 'a2': 0}, 0)]:
            with self.subTest(weights=weights), self.assertRaises(AllocationError):
                configure_table(sample(), table_id='t', name='T', weights=weights,
                                expected_total=total, source_reference='Source')

    def test_source_is_required(self):
        with self.assertRaises(AllocationError):
            configure_table(sample(), table_id='t', name='T',
                            weights={'a1': 600, 'a2': 400}, expected_total=1000,
                            source_reference='')

    def test_category_uses_explicit_table(self):
        building = configure_category(self.building, category_id='cleaning',
                                      name='Καθαριότητα', rule=table_rule('general'))
        result = calculate_period(building, Period('2026-09-01', '2026-09-30',
                                  (Expense('e1', 'cleaning', Decimal('100.00')),)))
        self.assertEqual(result['apartment_totals'],
                         {'a1': Decimal('60.00'), 'a2': Decimal('40.00')})

    def test_unknown_table_is_rejected(self):
        with self.assertRaises(AllocationError):
            configure_category(self.building, category_id='c', name='C',
                               rule=table_rule('missing'))

    def test_equal_and_direct_rules(self):
        building = configure_category(self.building, category_id='water', name='Water',
                                      rule=equal_rule(['a1', 'a2']))
        building = configure_category(building, category_id='repair', name='Repair',
                                      rule=direct_rule(), payer='OWNER')
        result = calculate_period(building, Period('2026-09-01', '2026-09-30', (
            Expense('e1', 'water', Decimal('10.01')),
            Expense('e2', 'repair', Decimal('20.00'),
                    direct_charges={'a1': Decimal('20.00')}),
        )))
        self.assertEqual(result['grand_total'], Decimal('30.01'))
        self.assertEqual(sum(result['apartment_totals'].values()), Decimal('30.01'))
        self.assertEqual(result['rows'][1]['payer'], 'OWNER')

    def test_update_preserves_original_and_references(self):
        original = configure_category(self.building, category_id='c', name='C',
                                      rule=table_rule('general'))
        updated = configure_table(original, table_id='general', name='Γενικά',
                                  weights={'a1': 500, 'a2': 500}, expected_total=1000,
                                  source_reference='Νέος δοκιμαστικός πίνακας')
        self.assertEqual(original.tables['general'].weights['a1'], Decimal('600'))
        self.assertEqual(updated.tables['general'].weights['a1'], Decimal('500'))
        self.assertEqual(updated.categories['c'].rule.table_id, 'general')

    def test_templates_do_not_assign_rules(self):
        self.assertIn('septic_emptying', CATEGORY_TEMPLATES)
        self.assertIn('gardening', CATEGORY_TEMPLATES)
        self.assertIn('upgrades', CATEGORY_TEMPLATES)
        self.assertIn('natural_gas', CATEGORY_TEMPLATES)
        self.assertTrue(all(isinstance(value, str) for value in CATEGORY_TEMPLATES.values()))

    def test_buildings_are_independent(self):
        other = load_building({
            'id': 'b2', 'name': 'Other', 'address': '',
            'apartments': [{'id': 'x', 'code': 'X'}, {'id': 'y', 'code': 'Y'},
                           {'id': 'z', 'code': 'Z'}],
            'tables': [], 'categories': [],
        })
        other = configure_table(other, table_id='general', name='General',
                                weights={'x': 200, 'y': 300, 'z': 500},
                                expected_total=1000, source_reference='Demo')
        self.assertEqual(len(other.apartments), 3)
        self.assertEqual(len(self.building.apartments), 2)


if __name__ == '__main__':
    unittest.main()
