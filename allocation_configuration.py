"""Configure allocation tables and expense categories without UI or SQL."""

import copy
from dataclasses import replace
from typing import Mapping

from expense_engine import (
    AllocationError, AllocationTable, Category, Rule, number,
    validate_building,
)


# Suggested names only. The user must choose the rule for each building.
CATEGORY_TEMPLATES = {
    'general_electricity': 'Κοινόχρηστο ρεύμα',
    'cleaning': 'Καθαριότητα',
    'elevator': 'Ανελκυστήρας',
    'shared_water': 'Κοινόχρηστο νερό',
    'septic_emptying': 'Εκκένωση βόθρου / λυμάτων',
    'gardening': 'Κηπουρική',
    'heating_oil': 'Πετρέλαιο θέρμανσης',
    'natural_gas': 'Φυσικό αέριο θέρμανσης',
    'heating_maintenance': 'Συντήρηση θέρμανσης',
    'repairs': 'Επισκευές',
    'upgrades': 'Αναβαθμίσεις / ανακαινίσεις',
}


def _required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise AllocationError(f'{label} is required.')
    return value.strip()


def configure_table(building, *, table_id, name, weights,
                    expected_total, source_reference):
    """Return a validated building with a created or updated table.

    The expected total and source are mandatory for newly configured tables.
    Existing categories retain their table references when a table is updated.
    """
    table_id = _required_text(table_id, 'Table ID')
    name = _required_text(name, 'Table name')
    source_reference = _required_text(source_reference, 'Source reference')
    if not isinstance(weights, Mapping):
        raise AllocationError('Weights must be a mapping of property IDs to values.')
    property_ids = {apartment.id for apartment in building.apartments}
    if set(weights) != property_ids:
        raise AllocationError('The table must contain exactly the building properties.')
    normalized = {key: number(value) for key, value in weights.items()}
    if any(value < 0 for value in normalized.values()):
        raise AllocationError('Weights cannot be negative.')
    expected = number(expected_total)
    if expected <= 0 or sum(normalized.values()) != expected:
        raise AllocationError('Weights must reconcile with the declared total.')
    table = AllocationTable(table_id, name, normalized, expected, source_reference)
    tables = dict(building.tables)
    tables[table_id] = table
    updated = replace(building, tables=tables)
    validate_building(updated)
    return updated


def configure_category(building, *, category_id, name, rule, payer='TENANT'):
    """Return a validated building with an explicit category allocation rule."""
    category_id = _required_text(category_id, 'Category ID')
    name = _required_text(name, 'Category name')
    if not isinstance(rule, Rule):
        raise AllocationError('A Rule object is required.')
    categories = dict(building.categories)
    categories[category_id] = Category(category_id, name, rule, payer)
    updated = replace(building, categories=categories)
    validate_building(updated)
    return updated


def table_rule(table_id):
    return Rule('WEIGHTED', table_id=_required_text(table_id, 'Table ID'))


def equal_rule(participants):
    return Rule('EQUAL', participants=tuple(participants))


def direct_rule():
    return Rule('DIRECT')


# The selection is UI metadata, not part of the financial domain model.
# An explicit empty list means that no categories are selected.
SELECTION_KEY = 'active_category_ids'


FACILITY_CATEGORY_IDS = frozenset({
    'elevator', 'shared_water', 'septic_emptying', 'gardening',
    'heating_oil', 'natural_gas', 'heating_maintenance',
})


def selected_facility_category_ids(config):
    """Suggest categories from the building's actual installation choices."""
    facilities = config.get('facilities') or {}
    heating = config.get('heating') or {}
    selected = set()
    if facilities.get('elevator') is True:
        selected.add('elevator')
    if facilities.get('shared_water') is True:
        selected.add('shared_water')
    if facilities.get('sewage') == 'SEPTIC':
        selected.add('septic_emptying')
    if facilities.get('garden') is True:
        selected.add('gardening')

    fuel = heating.get('fuel_type')
    system = heating.get('system_type')
    if system not in (None, 'NONE') and fuel not in (None, 'NONE'):
        selected.add('heating_maintenance')
        if fuel == 'OIL':
            selected.add('heating_oil')
        elif fuel == 'NATURAL_GAS':
            selected.add('natural_gas')
    return selected


def active_category_ids(config):
    """Return the explicit selection, or a conservative legacy suggestion.

    Existing configurations have no selection field. On first use, suggest
    categories supported by installations and custom (non-template) IDs.
    Standard general expenses must be selected explicitly. Once saved, even
    an empty selection is authoritative and installation changes do not
    silently reactivate categories.
    """
    categories = config.get('categories', [])
    known = [item['id'] for item in categories]
    if SELECTION_KEY not in config:
        suggested = selected_facility_category_ids(config)
        return [cid for cid in known if cid in suggested or cid not in CATEGORY_TEMPLATES]
    selected = config[SELECTION_KEY]
    if not isinstance(selected, list) or any(not isinstance(cid, str) for cid in selected):
        raise AllocationError('Η επιλογή κατηγοριών πρέπει να είναι λίστα IDs.')
    if len(selected) != len(set(selected)) or set(selected) - set(known):
        raise AllocationError('Η επιλογή περιέχει διπλότυπες ή άγνωστες κατηγορίες.')
    return list(selected)


def set_active_categories(config, category_ids):
    """Save the chosen IDs without removing financial categories or rules."""
    from configuration import load_building

    candidate = copy.deepcopy(config)
    candidate[SELECTION_KEY] = list(category_ids)
    active_category_ids(candidate)
    load_building(candidate)
    return candidate


def visible_allocation_categories(config):
    """Only explicitly selected categories appear in the allocation editor."""
    selected = set(active_category_ids(config))
    return [category for category in config.get('categories', [])
            if category['id'] in selected]
