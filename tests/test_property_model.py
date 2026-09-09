import json
import unittest
from dataclasses import replace
from decimal import Decimal
from configuration import apartment_json, building_json, load_building
from expense_engine import Apartment, AllocationError, Expense, Period, calculate_period, validate_apartment


def sample():
    return {
        'id': 'b1', 'name': 'Demo', 'address': '',
        'apartments': [{'id': 'a1', 'code': 'A1'}, {'id': 's1', 'code': 'S1', 'property_type': 'STORAGE', 'floor': -1, 'area_sqm': '12.5000'}],
        'tables': [{'id': 'general', 'name': 'General', 'weights': {'a1': '600', 's1': '400'}, 'expected_total': '1000'}],
        'categories': [{'id': 'cleaning', 'name': 'Cleaning', 'rule': {'type': 'WEIGHTED', 'table_id': 'general'}}],
    }


class PropertyModelTests(unittest.TestCase):
    def test_legacy_properties(self):
        self.assertEqual(load_building(sample()).apartments[0].property_type, 'APARTMENT')

    def test_metadata_round_trip(self):
        building = load_building(sample())
        self.assertEqual(load_building(building_json(building)), building)
        self.assertEqual(json.loads(apartment_json(building.apartments[1]))['area_sqm'], '12.5000')

    def test_metadata_does_not_change_allocation(self):
        building = load_building(sample())
        result = calculate_period(building, Period('2026-09-01', '2026-09-30', (Expense('e1', 'cleaning', Decimal('100.00')),)))
        self.assertEqual(result['apartment_totals'], {'a1': Decimal('60.00'), 's1': Decimal('40.00')})

    def test_invalid_metadata(self):
        for changes in [{'property_type': 'UNKNOWN'}, {'floor': 1.5}, {'floor': True}, {'area_sqm': 0}, {'area_sqm': -1}, {'area_sqm': 'NaN'}, {'code': ' '}]:
            with self.subTest(changes=changes), self.assertRaises(AllocationError):
                validate_apartment(replace(Apartment('a1', 'A1'), **changes))

    def test_duplicate_codes(self):
        data = sample()
        data['apartments'][1]['code'] = ' a1 '
        with self.assertRaises(AllocationError):
            load_building(data)

    def test_empty_optional_metadata(self):
        a = Apartment('a1', 'A1')
        validate_apartment(a)
        self.assertIsNone(a.floor)
        self.assertIsNone(a.area_sqm)


if __name__ == '__main__':
    unittest.main()
