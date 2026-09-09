import json
import unittest
from pathlib import Path
from decimal import Decimal
from dataclasses import replace
from configuration import load_building, load_period
from expense_engine import (AllocationError, Expense, Period, Rule,
                            calculate_period, allocate_weighted, money)

EXAMPLES = Path(__file__).resolve().parents[1] / 'examples'

def example(name):
    return json.loads((EXAMPLES / name).read_text(encoding='utf-8'))

class ExpenseEngineTests(unittest.TestCase):
    def setUp(self):
        self.building = load_building(example('building_demo.json'))
        self.period = load_period(example('period_demo.json'))

    def test_two_buildings_with_different_apartment_counts(self):
        for name, count in [('building_demo.json', 3), ('building_demo_2.json', 4)]:
            building = load_building(example(name))
            result = calculate_period(building, Period('2026-09-01', '2026-09-30', (Expense('e','electricity',Decimal('100.01')),)))
            self.assertEqual(len(result['apartment_totals']), count)
            self.assertEqual(sum(result['apartment_totals'].values()), Decimal('100.01'))

    def test_largest_remainder(self):
        self.assertEqual(allocate_weighted('0.02', {'a':1,'b':1,'c':1}, ['a','b','c']), {'a':Decimal('0.01'),'b':Decimal('0.01'),'c':Decimal('0.00')})

    def test_equal_participants(self):
        expense = Expense('equal','water',Decimal('10.00'),rule=Rule('EQUAL',participants=('unit-1','unit-3')),override_reason='Approved rule')
        result = calculate_period(self.building, replace(self.period, expenses=(expense,)))
        self.assertEqual(list(result['rows'][0]['allocations'].values()), [Decimal('5.00'),Decimal('0.00'),Decimal('5.00')])

    def test_direct_charges(self):
        expense = Expense('direct','repairs',Decimal('12.35'),rule=Rule('DIRECT'),direct_charges={'unit-2':Decimal('12.35')},override_reason='Exclusive expense')
        result = calculate_period(self.building, replace(self.period, expenses=(expense,)))
        self.assertEqual(result['apartment_totals']['unit-2'], Decimal('12.35'))

    def test_reconciliation(self):
        result = calculate_period(self.building,self.period)
        self.assertEqual(result['grand_total'], Decimal('375.01'))
        self.assertEqual(sum(result['apartment_totals'].values()),result['grand_total'])
        for row in result['rows']:
            self.assertEqual(sum(row['allocations'].values()),row['amount'])

    def test_invalid_inputs(self):
        for amount in ['NaN','Infinity','-1','1.001']:
            with self.subTest(amount=amount), self.assertRaises(AllocationError):
                money(amount)
        with self.assertRaises(AllocationError):
            calculate_period(self.building,replace(self.period,expenses=(Expense('x','repairs',Decimal('10'),rule=Rule('DIRECT'),direct_charges={'unit-1':Decimal('9')},override_reason='test'),)))
        with self.assertRaises(AllocationError):
            calculate_period(self.building,replace(self.period,expenses=(Expense('x','repairs',Decimal('10'),rule=Rule('DIRECT')),)))
        with self.assertRaises(AllocationError):
            calculate_period(self.building,replace(self.period,expenses=(Expense('x','unknown',Decimal('10')),)))
        with self.assertRaises(AllocationError):
            calculate_period(self.building,replace(self.period,expenses=(Expense('x','repairs',Decimal('10'),rule=Rule('EQUAL',participants=('unit-1',)),override_reason=''),)))

    def test_invalid_table_total(self):
        data=example('building_demo.json')
        data['tables'][0]['weights']['unit-1']='201'
        with self.assertRaises(AllocationError):
            load_building(data)

    def test_no_heating_formula_is_invented(self):
        with self.assertRaises(AllocationError):
            calculate_period(self.building,Period('2026-09-01','2026-09-30',(Expense('h','heating_oil',Decimal('20'),rule=Rule('HEATING_METERED'),override_reason='test'),)))

if __name__=='__main__':
    unittest.main()
