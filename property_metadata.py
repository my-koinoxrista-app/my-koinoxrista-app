"""Optional property display names, stored only in building configuration JSONB.

These labels are not ownership records and never affect expense allocation.
"""
from collections.abc import Mapping


def normalize_property_names(names, apartment_ids):
    if not isinstance(names, Mapping):
        raise ValueError('Τα ονόματα ιδιοκτησιών πρέπει να είναι αντιστοίχιση ID και ονόματος.')
    ids = set(apartment_ids)
    result = {}
    for apartment_id, value in names.items():
        if apartment_id not in ids:
            raise ValueError(f'Άγνωστη ιδιοκτησία στα ονόματα: {apartment_id}.')
        if not isinstance(value, str):
            raise ValueError('Το όνομα ιδιοκτησίας πρέπει να είναι κείμενο.')
        name = value.strip()
        if name:
            if len(name) > 200:
                raise ValueError('Το όνομα ιδιοκτησίας δεν μπορεί να υπερβαίνει τους 200 χαρακτήρες.')
            result[apartment_id] = name
    return result


def property_names_for_save(configuration, supplied=None, existing=None):
    """Explicit names replace old names; ordinary domain saves preserve them."""
    ids = {item['id'] for item in configuration['apartments']}
    explicit = supplied is not None and 'property_names' in supplied
    source = supplied if explicit else existing or {}
    names = source.get('property_names', {})
    if not isinstance(names, Mapping):
        raise ValueError('Μη έγκυρα ονόματα ιδιοκτησιών.')
    # Only legacy, implicitly preserved metadata may contain removed IDs.
    # Reject unknown IDs in an explicit update instead of silently dropping them.
    if not explicit:
        names = {key: value for key, value in names.items() if key in ids}
    return normalize_property_names(names, ids)
