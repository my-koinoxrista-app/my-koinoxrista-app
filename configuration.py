"""JSON import/export for building configuration and billing periods."""
import json
from dataclasses import asdict
from decimal import Decimal
from facilities import FacilityConfigurationError, normalize_facilities, normalize_heating
from expense_engine import (Apartment, AllocationTable, Rule, Category, Building,
                            Expense, Period, AllocationError, number, money, validate_building)


def parse_rule(data):
    return Rule(type=data['type'], table_id=data.get('table_id'),
                participants=tuple(data.get('participants', [])))


def parse_apartment(data):
    """Accept legacy two-field properties and the extended metadata."""
    area = data.get('area_sqm')
    return Apartment(
        id=data['id'],
        code=data['code'],
        property_type=data.get('property_type', 'APARTMENT'),
        floor=data.get('floor'),
        area_sqm=number(area) if area is not None else None,
        archived=data.get('archived', False),
    )


def apartment_json(apartment):
    """Serialize a property without losing Decimal precision."""
    return json.dumps(
        asdict(apartment),
        ensure_ascii=False,
        indent=2,
        default=lambda value: str(value) if isinstance(value, Decimal) else value,
    )


def load_building(data):
    if isinstance(data, str):
        data = json.loads(data)
    tables = {item['id']: AllocationTable(item['id'], item['name'],
              {key: number(value) for key, value in item['weights'].items()},
              number(item['expected_total']) if item.get('expected_total') is not None else None,
              item.get('source_reference', ''))
              for item in data['tables']}
    categories = {item['id']: Category(item['id'], item['name'], parse_rule(item['rule']),
                  item.get('payer', 'TENANT')) for item in data['categories']}
    try:
        facilities = normalize_facilities(data.get('facilities', {}))
        heating = normalize_heating(data.get('heating', {}))
    except FacilityConfigurationError as exc:
        raise AllocationError(str(exc)) from exc
    building = Building(data['id'], data['name'], data.get('address', ''),
                        tuple(parse_apartment(item) for item in data['apartments']),
                        tables, categories, facilities, heating)
    validate_building(building)
    return building


def load_period(data):
    if isinstance(data, str):
        data = json.loads(data)
    expenses = tuple(Expense(id=item['id'], category_id=item['category_id'],
                    amount=money(item['amount']), description=item.get('description', ''),
                    rule=parse_rule(item['rule']) if item.get('rule') else None,
                    direct_charges={key: money(value) for key, value in item.get('direct_charges', {}).items()},
                    override_reason=item.get('override_reason', '')) for item in data.get('expenses', []))
    return Period(data['start_date'], data['end_date'], expenses)


def to_json(obj):
    return json.dumps(asdict(obj), ensure_ascii=False, indent=2,
                      default=lambda value: str(value) if isinstance(value, Decimal) else value)


def building_json(building):
    data = asdict(building)
    data['tables'] = list(data['tables'].values())
    data['categories'] = list(data['categories'].values())
    return json.dumps(data, ensure_ascii=False, indent=2,
                      default=lambda value: str(value) if isinstance(value, Decimal) else value)
