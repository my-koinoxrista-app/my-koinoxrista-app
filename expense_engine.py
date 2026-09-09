"""Deterministic expense allocation. No UI, database or AI dependencies."""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Mapping

CENT = Decimal('0.01')

PROPERTY_TYPES = {
    'APARTMENT', 'SHOP', 'OFFICE', 'STORAGE', 'PARKING', 'OTHER'
}


class AllocationError(ValueError):
    pass


def number(value):
    if isinstance(value, bool):
        raise AllocationError('Boolean values are not valid amounts.')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise AllocationError(f'Invalid number: {value!r}') from None
    if not result.is_finite():
        raise AllocationError('Numbers must be finite.')
    return result


def money(value):
    result = number(value)
    if result < 0 or result != result.quantize(CENT):
        raise AllocationError('Amounts must be non-negative and have at most two decimals.')
    return result.quantize(CENT)


@dataclass(frozen=True)
class Apartment:
    id: str
    code: str
    property_type: str = 'APARTMENT'
    floor: int | None = None
    area_sqm: Decimal | None = None
    archived: bool = False


@dataclass(frozen=True)
class AllocationTable:
    id: str
    name: str
    weights: Mapping[str, Decimal]
    expected_total: Decimal | None = None
    source_reference: str = ''


@dataclass(frozen=True)
class Rule:
    type: str
    table_id: str | None = None
    participants: tuple[str, ...] = ()


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    rule: Rule
    payer: str = 'TENANT'


@dataclass(frozen=True)
class Building:
    id: str
    name: str
    address: str
    apartments: tuple[Apartment, ...]
    tables: Mapping[str, AllocationTable]
    categories: Mapping[str, Category]
    facilities: Mapping = field(default_factory=dict)
    heating: Mapping = field(default_factory=dict)


@dataclass(frozen=True)
class Expense:
    id: str
    category_id: str
    amount: Decimal
    description: str = ''
    rule: Rule | None = None
    direct_charges: Mapping[str, Decimal] = field(default_factory=dict)
    override_reason: str = ''


@dataclass(frozen=True)
class Period:
    start_date: str
    end_date: str
    expenses: tuple[Expense, ...]


def validate_apartment(apartment):
    """Validate property metadata without deriving allocation weights."""
    if not isinstance(apartment.id, str) or not apartment.id.strip():
        raise AllocationError('Property ID is required.')
    if not isinstance(apartment.code, str) or not apartment.code.strip():
        raise AllocationError('Property code is required.')
    if apartment.property_type not in PROPERTY_TYPES:
        raise AllocationError(f'Unsupported property type: {apartment.property_type}')
    if apartment.floor is not None:
        if isinstance(apartment.floor, bool) or not isinstance(apartment.floor, int):
            raise AllocationError('Floor must be an integer or empty.')
    if apartment.area_sqm is not None and number(apartment.area_sqm) <= 0:
        raise AllocationError('Property area must be positive.')


def validate_building(building):
    # Facility metadata is validated independently of financial allocation.
    from facilities import (
        FacilityConfigurationError, normalize_facilities, normalize_heating,
    )
    try:
        normalize_facilities(building.facilities)
        normalize_heating(building.heating)
    except FacilityConfigurationError as exc:
        raise AllocationError(str(exc)) from exc
    ids = [a.id for a in building.apartments]
    codes = [a.code.strip().casefold() if isinstance(a.code, str) else a.code
             for a in building.apartments]
    for apartment in building.apartments:
        validate_apartment(apartment)
        if not isinstance(apartment.archived, bool):
            raise AllocationError('Invalid property archive status.')
        if apartment.archived:
            if any(number(t.weights.get(apartment.id, 0)) != 0 for t in building.tables.values()):
                raise AllocationError('Μηδένισε τα χιλιοστά πριν αρχειοθετήσεις την ιδιοκτησία.')
            if any(apartment.id in c.rule.participants for c in building.categories.values()):
                raise AllocationError('Αφαίρεσε την ιδιοκτησία από τους κανόνες κατανομής πριν την αρχειοθέτηση.')
    if not ids or len(ids) != len(set(ids)) or len(codes) != len(set(codes)):
        raise AllocationError('A building needs unique property IDs and codes.')
    if not building.id or not building.name:
        raise AllocationError('Building ID and name are required.')
    for key, table in building.tables.items():
        if key != table.id or not table.name.strip() or not table.weights:
            raise AllocationError('Invalid allocation table.')
        if set(table.weights) != set(ids):
            raise AllocationError(f'Table {key} must contain exactly the building apartments.')
        if not isinstance(table.source_reference, str):
            raise AllocationError(f'Table {key} has an invalid source reference.')
        weights = [number(w) for w in table.weights.values()]
        if any(w < 0 for w in weights) or sum(weights) <= 0:
            raise AllocationError(f'Table {key} has invalid weights.')
        if table.expected_total is not None and (
            number(table.expected_total) <= 0 or sum(weights) != number(table.expected_total)
        ):
            raise AllocationError(f'Table {key} does not match its expected total.')
    for key, category in building.categories.items():
        if key != category.id or not category.name:
            raise AllocationError('Invalid category.')
        if category.payer not in {'TENANT', 'OWNER', 'OTHER'}:
            raise AllocationError('Invalid payer type.')
        validate_rule(category.rule, building)


def validate_rule(rule, building):
    if rule.type == 'WEIGHTED':
        if rule.table_id not in building.tables:
            raise AllocationError(f'Unknown allocation table: {rule.table_id}')
    elif rule.type == 'EQUAL':
        if not rule.participants or len(set(rule.participants)) != len(rule.participants):
            raise AllocationError('Equal allocation needs unique participants.')
        if not set(rule.participants) <= {a.id for a in building.apartments}:
            raise AllocationError('Unknown apartment in equal allocation.')
    elif rule.type != 'DIRECT':
        raise AllocationError(f'Unsupported allocation rule: {rule.type}')


def allocate_weighted(amount, weights, order):
    """Largest-remainder allocation, deterministic ties by apartment order."""
    amount = money(amount)
    weights = {key: number(value) for key, value in weights.items()}
    if not weights or any(w < 0 for w in weights.values()) or sum(weights.values()) <= 0:
        raise AllocationError('Allocation weights must be non-negative with a positive sum.')
    if set(weights) != set(order):
        raise AllocationError('Allocation participants do not match weights.')
    total_cents = int(amount / CENT)
    weight_sum = sum(weights.values())
    exact = [Decimal(total_cents) * weights[key] / weight_sum for key in order]
    base = [int(value) for value in exact]
    remainder = total_cents - sum(base)
    ranking = sorted(range(len(order)), key=lambda i: (-(exact[i] - base[i]), i))
    for i in ranking[:remainder]:
        base[i] += 1
    return {key: Decimal(cents) * CENT for key, cents in zip(order, base)}


def calculate_period(building, period):
    validate_building(building)
    from datetime import date
    try:
        if date.fromisoformat(period.start_date) > date.fromisoformat(period.end_date):
            raise ValueError()
    except ValueError:
        raise AllocationError('Invalid billing period dates.') from None
    ids = [a.id for a in building.apartments]
    totals = {key: Decimal('0.00') for key in ids}
    rows = []
    seen = set()
    for expense in period.expenses:
        if not expense.id or expense.id in seen:
            raise AllocationError('Expense IDs must be unique and non-empty.')
        seen.add(expense.id)
        if expense.category_id not in building.categories:
            raise AllocationError(f'Unknown category: {expense.category_id}')
        category = building.categories[expense.category_id]
        amount = money(expense.amount)
        rule = expense.rule or category.rule
        validate_rule(rule, building)
        archived = {a.id for a in building.apartments if a.archived}
        if archived.intersection(rule.participants) or archived.intersection(expense.direct_charges):
            raise AllocationError('Η δαπάνη αναφέρεται σε αρχειοθετημένη ιδιοκτησία. Επαναφορά ή διόρθωση της δαπάνης απαιτείται.')
        if expense.rule is not None and expense.rule != category.rule and not expense.override_reason.strip():
            raise AllocationError('A rule override requires a reason.')
        if rule.type == 'WEIGHTED':
            table = building.tables[rule.table_id]
            allocations = allocate_weighted(amount, table.weights, ids)
        elif rule.type == 'EQUAL':
            shares = allocate_weighted(amount, {key: 1 for key in rule.participants}, list(rule.participants))
            allocations = {key: shares.get(key, Decimal('0.00')) for key in ids}
        else:
            if not set(expense.direct_charges) <= set(ids):
                raise AllocationError('Direct charges contain an unknown apartment.')
            allocations = {key: money(expense.direct_charges.get(key, 0)) for key in ids}
            if sum(allocations.values()) != amount:
                raise AllocationError(f'Direct charges do not reconcile for {expense.id}.')
        if sum(allocations.values()) != amount:
            raise AllocationError('Expense allocation does not reconcile.')
        for key, value in allocations.items():
            totals[key] += value
        rows.append({'expense_id': expense.id, 'category': category.name,
                     'description': expense.description, 'amount': amount,
                     'payer': category.payer, 'rule': rule, 'allocations': allocations})
    grand_total = sum((row['amount'] for row in rows), Decimal('0.00'))
    if sum(totals.values()) != grand_total:
        raise AllocationError('Period totals do not reconcile.')
    return {'building': building, 'period': period, 'rows': rows,
            'apartment_totals': totals, 'grand_total': grand_total}
