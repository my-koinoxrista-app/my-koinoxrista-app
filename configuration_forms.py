"""Streamlit configuration forms. Financial rules remain in expense_engine."""
import copy
import hashlib
import json
import re
from decimal import Decimal
from uuid import uuid4

import pandas as pd
import streamlit as st

from allocation_configuration import (
    CATEGORY_TEMPLATES, active_category_ids, set_active_categories,
    visible_allocation_categories,
)
from configuration import load_building
from expense_engine import number
from facilities import normalize_facilities, normalize_heating
from property_metadata import normalize_property_names


PROPERTY_LABELS = {
    'APARTMENT': 'Διαμέρισμα', 'SHOP': 'Κατάστημα', 'OFFICE': 'Γραφείο',
    'STORAGE': 'Αποθήκη', 'PARKING': 'Θέση στάθμευσης', 'OTHER': 'Άλλο',
}
PAYER_LABELS = {'TENANT': 'Ενοικιαστής', 'OWNER': 'Ιδιοκτήτης', 'OTHER': 'Άλλος'}
RULE_LABELS = {'WEIGHTED': 'Με χιλιοστά', 'EQUAL': 'Ισόποσα', 'DIRECT': 'Απευθείας χρεώσεις'}
TABLE_LABELS = {'general': 'Γενικά χιλιοστά', 'elevator': 'Ανελκυστήρας', 'heating': 'Θέρμανση'}
FACILITY_LABELS = {'elevator': 'Ανελκυστήρας', 'shared_water': 'Κοινόχρηστο νερό', 'garden': 'Κήπος'}
HEATING_OPTIONS = {
    'fuel_type': {'OIL': 'Πετρέλαιο', 'NATURAL_GAS': 'Φυσικό αέριο', 'ELECTRIC': 'Ηλεκτρικό', 'OTHER': 'Άλλο', 'NONE': 'Χωρίς καύσιμο'},
    'system_type': {'CENTRAL': 'Κεντρική', 'AUTONOMOUS': 'Αυτόνομη', 'NONE': 'Χωρίς θέρμανση'},
    'metering_type': {'NONE': 'Χωρίς μετρητές', 'HOUR_METER': 'Ωρομετρητές', 'HEAT_METER': 'Θερμιδομετρητές', 'OTHER': 'Άλλο'},
}
SEWAGE = {'NETWORK': 'Αποχέτευση', 'SEPTIC': 'Βόθρος', 'NONE': 'Δεν υπάρχει', 'OTHER': 'Άλλο'}


def required(value, label):
    if blank(value):
        raise ValueError(f'Συμπλήρωσε: {label}.')
    value = str(value).strip()
    return value


def decimal_text(value, label, positive=False, whole_digits=12, decimal_places=6):
    value = number(str(value).strip().replace(',', '.'))
    if value < 0 or (positive and value <= 0) or abs(value) >= Decimal(10) ** whole_digits:
        raise ValueError(f'{label}: μη έγκυρη τιμή ή υπέρβαση ορίου.')
    quantum = Decimal(1).scaleb(-decimal_places)
    if value != value.quantize(quantum):
        raise ValueError(f'{label}: επιτρέπονται έως {decimal_places} δεκαδικά.')
    return str(value)


def floor_value(value):
    if blank(value):
        return None
    if isinstance(value, bool):
        raise ValueError('Ο όροφος πρέπει να είναι ακέραιος.')
    try:
        numeric = number(str(value).strip())
    except ValueError:
        raise ValueError('Ο όροφος πρέπει να είναι ακέραιος.') from None
    if numeric != numeric.to_integral_value():
        raise ValueError('Ο όροφος πρέπει να είναι ακέραιος.')
    result = int(numeric)
    if not -(2 ** 31) <= result < 2 ** 31:
        raise ValueError('Ο όροφος είναι εκτός του επιτρεπτού εύρους.')
    return result


def identifier(value):
    value = required(value, 'ID')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Το ID επιτρέπει λατινικά γράμματα, αριθμούς, _ και -.')
    return value


def apply_change(config, callback):
    candidate = copy.deepcopy(config)
    callback(candidate)
    load_building(candidate)
    candidate['property_names'] = normalize_property_names(
        candidate.get('property_names', {}),
        [item['id'] for item in candidate['apartments']],
    )
    return candidate


def add_property(config, row):
    def change(data):
        apartment = dict(row)
        aid = identifier(apartment.get('id') or 'a-' + uuid4().hex)
        if aid in {a['id'] for a in data['apartments']}:
            raise ValueError('Το ID ιδιοκτησίας υπάρχει ήδη.')
        apartment['id'] = aid
        apartment['code'] = required(apartment.get('code'), 'Κωδικός ιδιοκτησίας')
        apartment['property_type'] = {label: key for key, label in PROPERTY_LABELS.items()}.get(
            apartment.get('property_type'), apartment.get('property_type') or 'APARTMENT')
        apartment['floor'] = floor_value(apartment.get('floor'))
        area = apartment.get('area_sqm')
        apartment['area_sqm'] = decimal_text(area, 'Εμβαδόν', True, 8, 2) if not blank(area) else None
        display_name = apartment.pop('name', '')
        data['apartments'].append(apartment)
        if display_name:
            data.setdefault('property_names', {})[aid] = display_name
        for table in data['tables']:
            table['weights'][aid] = '0'
    return apply_change(config, change)


def save_table(config, table):
    def change(data):
        table['id'] = identifier(table['id'])
        if any(t['id'] == table['id'] for t in data['tables']):
            raise ValueError('Το ID πίνακα υπάρχει ήδη.')
        data['tables'].append(table)
    return apply_change(config, change)


def assign_category_tables(config, assignments):
    """Apply explicit category rules without changing shares or issued records."""
    available = {t['id'] for t in config['tables']}
    def change(data):
        categories = {c['id']: c for c in data['categories']}
        for cid, tid in assignments.items():
            if cid not in categories:
                raise ValueError('Άγνωστη κατηγορία δαπανών.')
            if isinstance(tid, dict) and tid.get('type') in ('EQUAL', 'DIRECT'):
                categories[cid]['rule'] = copy.deepcopy(tid)
            elif isinstance(tid, str) and tid in available:
                categories[cid]['rule'] = {'type': 'WEIGHTED', 'table_id': tid}
            else:
                raise ValueError('Επίλεξε πίνακα ή τρόπο κατανομής για κάθε κατηγορία.')
    return apply_change(config, change)


def save_expense_category(config, item, *, create=False):
    """Save a category and select newly created categories for allocation."""
    def change(data):
        category = copy.deepcopy(item)
        category['id'] = identifier(category['id'])
        category['name'] = required(category['name'], 'Όνομα')
        existing = {c['id'] for c in data['categories']}
        if create:
            if category['id'] in existing:
                raise ValueError('Το ID κατηγορίας υπάρχει ήδη.')
            data['categories'].append(category)
            data['active_category_ids'] = active_category_ids(config) + [category['id']]
        else:
            if category['id'] not in existing:
                raise ValueError('Η κατηγορία δεν βρέθηκε.')
            data['categories'] = [category if c['id'] == category['id'] else c
                                  for c in data['categories']]
    return apply_change(config, change)


def save_properties(config, rows):
    """Validate edits while preserving stable property IDs and references."""
    def change(data):
        existing = {item['id']: item for item in data['apartments']}
        old_names = data.get('property_names', {})
        seen = set()
        apartments = []
        names = {}
        for row in rows:
            if all(blank(row.get(key)) for key in ('code', 'name', 'floor', 'area_sqm')):
                if blank(row.get('id')):
                    continue
            aid = row.get('id')
            if blank(aid):
                aid = None
            if aid is not None and not isinstance(aid, str):
                raise ValueError('Μη έγκυρο ID ιδιοκτησίας.')
            if aid is not None and aid != '':
                if aid not in existing or aid in seen:
                    raise ValueError('Άγνωστο ή διπλό ID ιδιοκτησίας. Τα IDs δεν αλλάζουν.')
            else:
                aid = 'a-' + uuid4().hex
            seen.add(aid)
            code = required(row.get('code', ''), 'Κωδικός ιδιοκτησίας')
            ptype = row.get('property_type') or 'APARTMENT'
            ptype = {label: key for key, label in PROPERTY_LABELS.items()}.get(ptype, ptype)
            floor = row.get('floor')
            area = row.get('area_sqm')
            apartment = dict(existing.get(aid, {}))
            apartment.update(
                id=aid, code=code, property_type=ptype,
                floor=floor_value(floor),
                area_sqm=decimal_text(area, 'Εμβαδόν', True, 8, 2)
                if not blank(area) else None,
            )
            apartments.append(apartment)
            name = row.get('name', old_names.get(aid, ''))
            if not blank(name):
                if not isinstance(name, str):
                    raise ValueError('Το όνομα ιδιοκτησίας πρέπει να είναι κείμενο.')
                names[aid] = name.strip()
        missing = set(existing) - seen
        if missing:
            raise ValueError(
                'Για αφαίρεση ιδιοκτησίας χρησιμοποίησε τη χωριστή ενέργεια '
                '«Διαγραφή ιδιοκτησίας», ώστε να ελεγχθούν οι εξαρτήσεις της.'
            )
        data['apartments'] = apartments
        data['property_names'] = normalize_property_names(names, seen)
        for table in data['tables']:
            for aid in seen - set(existing):
                table['weights'][aid] = '0'
    return apply_change(config, change)


def _property_removal_blockers(config, apartment_id, period_data=None):
    """Check the selected property, never unrelated building expenses."""
    from property_removal import check_removal
    from database import get_connection

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT configuration FROM buildings WHERE id=%s FOR UPDATE',
                    (config['id'],))
        row = cur.fetchone()
        check_removal(cur, row[0] if row else None, config,
                      apartment_id, period_data)
    return []


def remove_property(config, apartment_id, period_data=None):
    """Remove unused configuration only; never renumber IDs or redistribute weights."""
    blockers = _property_removal_blockers(config, apartment_id, period_data)
    if blockers:
        raise ValueError('Η διαγραφή δεν επιτρέπεται: ' + ' '.join(blockers))

    def change(data):
        data['apartments'] = [a for a in data['apartments'] if a['id'] != apartment_id]
        data.setdefault('property_names', {}).pop(apartment_id, None)
        for table in data['tables']:
            table['weights'].pop(apartment_id, None)
        # Other allocation rules are left unchanged. A reference to this ID
        # has already been rejected above rather than silently changing a rule.
    return apply_change(config, change)


def set_property_archived(config, apartment_id, archived):
    """Keep the property and its history; validate allocation eligibility."""
    if not isinstance(archived, bool):
        raise ValueError('Μη έγκυρη κατάσταση αρχειοθέτησης.')
    def change(data):
        apartment = next((a for a in data['apartments'] if a['id'] == apartment_id), None)
        if apartment is None:
            raise ValueError('Η ιδιοκτησία δεν βρέθηκε.')
        apartment['archived'] = archived
    return apply_change(config, change)


def blank(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        raise ValueError('Μη έγκυρη τιμή στον πίνακα ιδιοκτησιών.') from None


def commit(config, callback, message='Οι αλλαγές εφαρμόστηκαν προσωρινά.'):
    try:
        candidate = callback()
        load_building(candidate)
        st.session_state.config = candidate
        st.session_state.result = None
        st.success(message + ' Αποθήκευσέ τες στη βάση.')
        st.rerun()
    except (ValueError, KeyError, TypeError) as exc:
        st.error(str(exc))


def render(config, building_id):
    """Editable configuration, validated by the existing domain model."""
    revision = hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()[:12]
    scope = 'details'
    counters = {}

    def widget(kind, label, *args, **kwargs):
        # Explicit, stable keys; the selected record and configuration version
        # are part of the namespace so unrelated forms never share state.
        position = counters.get(scope, 0)
        counters[scope] = position + 1
        kwargs['key'] = f'cfg_{building_id}_{revision}_{scope}_{position}'
        return getattr(st, kind)(label, *args, **kwargs)

    def text(label, value='', **kwargs):
        return widget('text_input', label, str(value) if value is not None else '', **kwargs)

    def choose(label, options, value=None, labels=None):
        if not options:
            st.info('Δεν υπάρχουν διαθέσιμες επιλογές.')
            return None
        return widget('selectbox', label, options, index=options.index(value) if value in options else 0,
                      format_func=(lambda x: labels.get(x, x)) if labels else str)

    st.subheader('Παραμετροποίηση')
    st.caption('Οι αλλαγές εφαρμόζονται προσωρινά. Αποθήκευσέ τες στη βάση από το τελευταίο κουμπί.')
    with st.expander('Στοιχεία πολυκατοικίας', expanded=True):
        with st.form('building_details'):
            name = text('Όνομα πολυκατοικίας', config['name'])
            address = text('Διεύθυνση', config.get('address', ''))
            st.caption('Μπορείς να αφαιρέσεις και την παλιά σήμανση [TEST] από το όνομα. Το μόνιμο ID δεν αλλάζει.')
            if st.form_submit_button('Εφαρμογή στοιχείων'):
                commit(config, lambda: apply_change(config, lambda d: d.update(name=required(name, 'Όνομα'), address=address)))

    with st.expander('Ιδιοκτησίες', expanded=True):
        st.caption(
            'Συμπλήρωσε τον κωδικό (π.χ. Α1), το όνομα / περιγραφή, '
            'τον τύπο, τον όροφο και το εμβαδόν. Το όνομα είναι '
            'προαιρετικό και δεν καθορίζει κυριότητα, υπόχρεο ή χιλιοστά.'
        )
        st.caption(
            'Οι νέες ιδιοκτησίες παίρνουν μηδενικά χιλιοστά σε κάθε '
            'υπάρχοντα πίνακα, χωρίς ανακατανομή.'
        )
        scope = 'properties'
        apartment_ids = [a['id'] for a in config['apartments']]
        names = config.get('property_names', {})
        labels = {a['id']: a['code'] for a in config['apartments']}
        from statement_delivery import list_contacts, save_contacts
        contacts = list_contacts(building_id)
        contacts_by_id = {contact['id']: contact for contact in contacts}

        # Only existing properties appear in the editor. Internal IDs are
        # restored by row position and never sent to the visible DataFrame.
        property_columns = [
            'code', 'name', 'tenant_name', 'email', 'email_enabled',
            'property_type', 'floor', 'area_sqm',
        ]
        visible_properties = [a for a in config['apartments'] if not a.get('archived', False)]
        existing_ids = [a['id'] for a in visible_properties]
        rows = [{
            'code': a['code'],
            'name': names.get(a['id'], ''),
            'tenant_name': contacts_by_id.get(a['id'], {}).get('tenant_name', ''),
            'email': contacts_by_id.get(a['id'], {}).get('email', ''),
            'email_enabled': contacts_by_id.get(a['id'], {}).get('enabled', False),
            'property_type': PROPERTY_LABELS.get(
                a.get('property_type', 'APARTMENT'), 'Διαμέρισμα'),
            'floor': '' if a.get('floor') is None else str(a['floor']),
            'area_sqm': '' if a.get('area_sqm') is None else str(a['area_sqm']),
        } for a in visible_properties]
        frame = pd.DataFrame(rows, columns=property_columns)

        st.markdown(f'**Καταχωρισμένες ιδιοκτησίες: {len(rows)}**')
        with st.form(f'properties_table_v4_{building_id}_{revision}'):
            edited = st.data_editor(
                frame, hide_index=True, use_container_width=True,
                num_rows='fixed',
                key=f'property_grid_v4_{building_id}_{revision}',
                column_order=property_columns,
                column_config={
                    'code': st.column_config.TextColumn(
                        'Κωδικός', help='Π.χ. Α1, Κ1, ΑΠ1.'),
                    'name': st.column_config.TextColumn(
                        'Όνομα / περιγραφή',
                        help='Προαιρετικό. Δεν αποτελεί στοιχείο κυριότητας.'),
                    'email': st.column_config.TextColumn(
                        'Email ενοίκου', max_chars=254),
                    'email_enabled': st.column_config.CheckboxColumn(
                        'Ενεργή αποστολή'),
                    'property_type': st.column_config.SelectboxColumn(
                        'Τύπος', options=list(PROPERTY_LABELS.values()), required=True),
                    'floor': st.column_config.TextColumn(
                        'Όροφος', help='Ακέραιος αριθμός, προαιρετικό.'),
                    'area_sqm': st.column_config.TextColumn(
                        'Εμβαδόν m²', help='Προαιρετικό, έως 2 δεκαδικά.'),
                },
            )
            if st.form_submit_button('Εφαρμογή αλλαγών'):
                def save_visible_properties():
                    records = edited.to_dict('records')
                    if len(records) != len(existing_ids):
                        raise ValueError('Ο αριθμός γραμμών άλλαξε. Φόρτωσε ξανά τον πίνακα.')
                    for index, row in enumerate(records):
                        row['id'] = existing_ids[index]
                    records.extend(dict(a, name=names.get(a['id'], ''))
                                   for a in config['apartments'] if a.get('archived', False))
                    candidate = save_properties(config, records)
                    updated_contacts = []
                    for contact in contacts:
                        updated = dict(contact)
                        row = next((item for item in records if item['id'] == contact['id']), None)
                        if row is not None and 'email' in row:
                            updated['tenant_name'] = row.get('tenant_name', '')
                            updated['email'] = row.get('email', '')
                            updated['enabled'] = bool(row.get('email_enabled', False))
                        updated_contacts.append(updated)
                    save_contacts(building_id, updated_contacts)
                    return candidate
                commit(config, save_visible_properties)

        with st.expander('＋ Προσθήκη ιδιοκτησίας'):
            with st.form(f'add_property_v4_{building_id}_{revision}', clear_on_submit=True):
                new_code = st.text_input('Κωδικός', placeholder='π.χ. Α4')
                new_name = st.text_input('Όνομα / περιγραφή (προαιρετικό)')
                new_type = st.selectbox('Τύπος', list(PROPERTY_LABELS),
                                        format_func=lambda value: PROPERTY_LABELS[value])
                col_floor, col_area = st.columns(2)
                new_floor = col_floor.text_input('Όροφος', placeholder='π.χ. 1')
                new_area = col_area.text_input('Εμβαδόν m²', placeholder='π.χ. 75,50')
                if st.form_submit_button('Προσθήκη ιδιοκτησίας', type='primary'):
                    commit(config, lambda: add_property(config, {
                        'code': new_code, 'name': new_name,
                        'property_type': new_type, 'floor': new_floor,
                        'area_sqm': new_area,
                    }), 'Η ιδιοκτησία προστέθηκε προσωρινά.')

        with st.expander('Αρχειοθέτηση / επαναφορά ιδιοκτησίας'):
            st.caption('Η αρχειοθέτηση κρύβει την ιδιοκτησία από τον ενεργό πίνακα και διατηρεί το ιστορικό. '
                       'Απαιτούνται μηδενικά χιλιοστά και καμία συμμετοχή σε κανόνες κατανομής. '
                       'Στους πίνακες χιλιοστών παραμένει ορατή για έλεγχο.')
            archive_id = st.selectbox('Ιδιοκτησία', apartment_ids,
                format_func=lambda aid, labels=labels: labels[aid] + (' · Αρχειοθετημένη' if next(
                    a for a in config['apartments'] if a['id'] == aid).get('archived', False) else ''),
                key=f'property_archive_{building_id}_{revision}')
            if archive_id:
                is_archived = next(a for a in config['apartments'] if a['id'] == archive_id).get('archived', False)
                if st.button('Επαναφορά ιδιοκτησίας' if is_archived else 'Αρχειοθέτηση ιδιοκτησίας',
                             key=f'property_archive_action_{building_id}_{revision}'):
                    commit(config, lambda: set_property_archived(config, archive_id, not is_archived))

        with st.expander('Οριστική διαγραφή ιδιοκτησίας'):
            st.caption('Επίλεξε την ιδιοκτησία που θέλεις να αφαιρέσεις. '
                       'Δεν διαγράφονται οικονομικά δεδομένα ή ιστορικές εκκαθαρίσεις. '
                       'Η αφαίρεση δεν ανακατανέμει χιλιοστά.')
            if config['apartments']:
                choices = [a['id'] for a in config['apartments']]
                selected = st.selectbox(
                    'Ιδιοκτησία προς διαγραφή', choices,
                    format_func=lambda aid: next(
                        a['code'] + (' — ' + names.get(aid, '') if names.get(aid) else '')
                        for a in config['apartments'] if a['id'] == aid),
                    key=f'property_delete_select_{building_id}_{revision}',
                )
                confirmed = st.checkbox('Κατανοώ ότι μετά την αποθήκευση η διαγραφή δεν αναιρείται.',
                                        key=f'property_delete_confirm_{building_id}_{revision}_{selected}')
                if st.button('Οριστική διαγραφή ιδιοκτησίας', disabled=not confirmed,
                             key=f'property_delete_button_{building_id}_{revision}'):
                    try:
                        candidate = remove_property(
                            config, selected, {'expenses': st.session_state.get('expenses', [])})
                        st.session_state.config = candidate
                        st.session_state.result = None
                        st.success('Η ιδιοκτησία αφαιρέθηκε προσωρινά. Αποθήκευσε την πολυκατοικία στη βάση.')
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
                    except Exception:
                        st.error('Δεν ολοκληρώθηκε ο έλεγχος εξαρτήσεων. Η διαγραφή ακυρώθηκε.')

        st.caption('Οι αλλαγές είναι προσωρινές μέχρι να αποθηκεύσεις την πολυκατοικία στη βάση.')

    def weights_editor(table, new=False):
        """Collect weights with ordinary inputs; validate only on submission."""
        weights = table.get('weights', {})
        st.markdown('**Χιλιοστά ανά ιδιοκτησία**')
        header1, header2 = st.columns([3, 2])
        header1.caption('Ιδιοκτησία')
        header2.caption('Χιλιοστά')
        values = {}
        for apartment in config['apartments']:
            col1, col2 = st.columns([3, 2], vertical_alignment='center')
            col1.write(apartment['code'])
            values[apartment['id']] = col2.text_input(
                f"Χιλιοστά {apartment['code']}",
                value=str(weights.get(apartment['id'], '0')),
                label_visibility='collapsed',
                key=f"weights_input_{building_id}_{revision}_{scope}_{apartment['id']}",
                placeholder='0',
            )
        return values

    def validate_weights(values):
        return {
            aid: decimal_text(value, 'Χιλιοστά')
            for aid, value in values.items()
        }

    with st.expander('Εγκαταστάσεις και θέρμανση'):
        scope = 'facilities'
        with st.form('facilities_form'):
            facilities = config.get('facilities', {})
            heating = config.get('heating', {})
            values = {}
            for field, label in FACILITY_LABELS.items():
                values[field] = choose(label, [None, True, False], facilities.get(field), {None: 'Δεν έχει οριστεί', True: 'Ναι', False: 'Όχι'})
            values['sewage'] = choose('Αποχέτευση / λύματα', [None] + list(SEWAGE), facilities.get('sewage'), {None: 'Δεν έχει οριστεί', **SEWAGE})
            st.markdown('**Θέρμανση**')
            heat = dict(heating)
            for field, options in HEATING_OPTIONS.items():
                label = {'fuel_type': 'Καύσιμο', 'system_type': 'Σύστημα', 'metering_type': 'Μέτρηση'}[field]
                heat[field] = choose(label, [None] + list(options), heating.get(field), {None: 'Δεν έχει οριστεί', **options})
            st.caption('Τα στοιχεία θέρμανσης είναι πληροφοριακά· δεν προστίθεται ειδικός υπολογισμός.')
            if st.form_submit_button('Εφαρμογή εγκαταστάσεων'):
                commit(config, lambda: apply_change(config, lambda d: d.update(
                    facilities=normalize_facilities(values), heating=normalize_heating(heat))))

    with st.expander('Κατηγορίες δαπανών', expanded=True):
        st.caption('Επίλεξε ποιες κατηγορίες χρησιμοποιεί αυτή η πολυκατοικία. '
                   'Μόνο αυτές θα εμφανίζονται στην αντιστοίχιση πινάκων. '
                   'Η επιλογή δεν διαγράφει παλιές δαπάνες ή εκκαθαρίσεις.')
        if 'active_category_ids' not in config:
            st.info('Η πολυκατοικία δεν έχει ακόμη αποθηκευμένη επιλογή. '
                    'Έχουν προταθεί οι κατηγορίες των εγκαταστάσεων και οι '
                    'προσαρμοσμένες κατηγορίες. Έλεγξε και αποθήκευσε τις επιλογές σου.')
        selected_ids = set(active_category_ids(config))
        with st.form(f'category_selection_{building_id}_{revision}'):
            chosen_ids = []
            for category in config['categories']:
                cid = category['id']
                if st.checkbox(category['name'], value=cid in selected_ids,
                               key=f'category_enabled_{building_id}_{revision}_{cid}'):
                    chosen_ids.append(cid)
            if not config['categories']:
                st.info('Δεν υπάρχουν ακόμη κατηγορίες. Πρόσθεσε μία παρακάτω.')
            if st.form_submit_button('Εφαρμογή επιλογής κατηγοριών'):
                commit(config, lambda: set_active_categories(config, chosen_ids))

        with st.expander('＋ Νέα κατηγορία δαπανών / επεξεργασία'):
            st.caption('Πρόσθεσε δική σου κατηγορία ή επεξεργάσου μια υπάρχουσα. '
                       'Ο κανόνας και ο υπόχρεος ορίζονται ρητά. Δεν παράγονται χιλιοστά.')
            scope = 'category_select'
            categories = config['categories']
            labels = {c['id']: c['name'] for c in categories}
            chosen = choose('Επεξεργασία ή νέα κατηγορία', [''] + list(labels), labels={'': '＋ Νέα κατηγορία', **labels})
            current = next((c for c in categories if c['id'] == chosen), None)
            rule = current['rule'] if current else {}
            scope = 'category_' + chosen
            with st.form('category_form'):
                identity_left, identity_right = st.columns(2)
                with identity_left:
                    if current:
                        cid = current['id']
                        st.caption(f'Μόνιμο ID: {cid}')
                        name = text('Όνομα κατηγορίας', current['name'])
                    else:
                        template = choose('Προτεινόμενη ονομασία', [''] + list(CATEGORY_TEMPLATES), labels={'': 'Προσαρμοσμένη', **CATEGORY_TEMPLATES})
                        scope = 'category_new_' + template
                        name = text('Όνομα κατηγορίας', CATEGORY_TEMPLATES.get(template, ''))
                with identity_right:
                    if not current:
                        cid = text('ID κατηγορίας', template)
                    payer = choose('Υπόχρεος', list(PAYER_LABELS), current.get('payer', 'TENANT') if current else 'TENANT', PAYER_LABELS)

                rtype = choose('Κανόνας κατανομής', list(RULE_LABELS), rule.get('type', 'WEIGHTED'), RULE_LABELS)
                table_id, participants = None, []
                if rtype == 'WEIGHTED':
                    table_labels = {t['id']: t['name'] for t in config['tables']}
                    table_id = choose('Πίνακας χιλιοστών', list(table_labels), rule.get('table_id'), table_labels)
                elif rtype == 'EQUAL':
                    participants = widget('multiselect', 'Συμμετέχουσες ιδιοκτησίες', apartment_ids,
                        default=rule.get('participants', []) if rule.get('type') == 'EQUAL' else [], format_func=lambda aid: next(a['code'] for a in config['apartments'] if a['id'] == aid))
                else:
                    st.info('Οι DIRECT χρεώσεις απαιτούν ρητά ποσά ανά ιδιοκτησία, τα οποία καταχωρίζονται στη μηνιαία φόρμα.')
                if st.form_submit_button('Εφαρμογή κατηγορίας'):
                    new_rule = {'type': rtype}
                    if rtype == 'WEIGHTED':
                        if not table_id:
                            st.error('Δημιούργησε πρώτα πίνακα χιλιοστών.')
                        else:
                            new_rule['table_id'] = table_id
                    elif rtype == 'EQUAL':
                        new_rule['participants'] = participants
                    if rtype != 'WEIGHTED' or table_id:
                        item = {'id': cid, 'name': name, 'payer': payer, 'rule': new_rule}
                        commit(config, lambda: save_expense_category(config, item, create=current is None))
            st.caption('Οι παλιές κατηγορίες διατηρούνται για τις ιστορικές αναφορές.')


    with st.expander('Πίνακες χιλιοστών'):
        st.caption('Το άθροισμα πρέπει να συμφωνεί ακριβώς με το δηλωμένο σύνολο. Δεν παράγονται χιλιοστά από εμβαδά.')
        for table in sorted((t for t in config['tables'] if t['id'] in TABLE_LABELS),
                            key=lambda t: list(TABLE_LABELS).index(t['id'])):
            scope = 'table_' + table['id']
            with st.expander(TABLE_LABELS[table['id']]):
                with st.form('edit_' + table['id']):
                    name = TABLE_LABELS[table['id']]
                    source = text('Πηγή / αναφορά μελέτης', table.get('source_reference', ''))
                    expected = text('Αναμενόμενο άθροισμα', table.get('expected_total'))
                    edited = weights_editor(table)
                    if st.form_submit_button('Εφαρμογή πίνακα'):
                        def change(d):
                            target = next(t for t in d['tables'] if t['id'] == table['id'])
                            target.update(name=required(name, 'Όνομα'), source_reference=source.strip(),
                                expected_total=decimal_text(expected, 'Σύνολο', True) if expected.strip() else None,
                                weights=validate_weights(edited))
                        commit(config, lambda: apply_change(config, change))
        with st.expander('＋ Εισαγωγή πίνακα'):
            existing_tables = {t['id'] for t in config['tables']}
            options = list(TABLE_LABELS)
            default_table = next((tid for tid in options if tid not in existing_tables), options[0])
            tid = st.selectbox('Επιλογή πίνακα προς εισαγωγή', options,
                index=options.index(default_table), format_func=lambda value: TABLE_LABELS[value],
                key=f'insert_table_type_{building_id}_{revision}')
            if tid in existing_tables:
                st.info(f'Ο πίνακας «{TABLE_LABELS[tid]}» υπάρχει ήδη. Μπορείς να αλλάξεις τα χιλιοστά του από την αντίστοιχη ενότητα παραπάνω.')
            else:
                scope = 'new_table_' + tid
                with st.form(f'new_table_{building_id}_{revision}_{tid}'):
                    source = text('Πηγή / αναφορά μελέτης')
                    expected = text('Αναμενόμενο άθροισμα', '1000')
                    edited = weights_editor({})
                    if st.form_submit_button('Εισαγωγή πίνακα'):
                        commit(config, lambda: save_table(config, {'id': tid, 'name': TABLE_LABELS[tid],
                            'source_reference': required(source, 'Πηγή'), 'expected_total': decimal_text(expected, 'Σύνολο', True),
                            'weights': validate_weights(edited)}))

    selected_categories = visible_allocation_categories(config)
    if not selected_categories:
        st.info('Δεν έχεις επιλέξει κατηγορίες δαπανών. Επίλεξέ τες παραπάνω για να ορίσεις την κατανομή τους.')
    available_tables = [t['id'] for t in config['tables']]
    if selected_categories:
        with st.expander('Πίνακας ή ισόποση κατανομή ανά κατηγορία'):
            st.caption('Εμφανίζονται μόνο οι κατηγορίες που επέλεξες παραπάνω. '
                       'Αντιστοίχισε σε καθεμία τον πίνακα χιλιοστών ή έναν άλλο τρόπο κατανομής. '
                       'Οι εκδοθείσες εκκαθαρίσεις διατηρούνται.')
            assignments = {}
            old_labels = {t['id']: t['name'] for t in config['tables']}
            active_ids = [a['id'] for a in config['apartments'] if not a.get('archived', False)]
            property_labels = {a['id']: a['code'] for a in config['apartments']}
            distribution_labels = {'': 'Επίλεξε τρόπο κατανομής', **{t['id']: t['name'] for t in config['tables']},
                                   'EQUAL': 'Ισόποσα', 'DIRECT': 'Απευθείας χρεώσεις'}
            for category in selected_categories:
                rule = category['rule']
                current = rule.get('table_id') if rule['type'] == 'WEIGHTED' else rule['type']
                options = [''] + available_tables + ['EQUAL', 'DIRECT']
                selected = st.selectbox(category['name'], options,
                    index=options.index(current) if current in options else 0,
                    format_func=lambda value: distribution_labels[value],
                    key=f'category_table_{building_id}_{revision}_{category["id"]}')
                if selected == 'EQUAL':
                    participants = st.multiselect('Ιδιοκτησίες για ισόποση κατανομή · ' + category['name'],
                        [a['id'] for a in config['apartments']], default=rule.get('participants', []) if rule['type'] == 'EQUAL' else [],
                        format_func=lambda aid: property_labels[aid],
                        key=f'category_equal_{building_id}_{revision}_{category["id"]}')
                    assignments[category['id']] = {'type': 'EQUAL', 'participants': participants}
                elif selected == 'DIRECT':
                    assignments[category['id']] = {'type': 'DIRECT'}
                    st.caption('Τα ποσά ανά ιδιοκτησία καταχωρίζονται στη δαπάνη.')
                elif selected:
                    assignments[category['id']] = selected
                if rule['type'] == 'WEIGHTED' and current not in available_tables:
                    st.caption('Τρέχων πίνακας: ' + old_labels.get(current, current or '—'))
            if st.button('Εφαρμογή κατανομής ανά κατηγορία', key=f'apply_category_rules_{building_id}_{revision}'):
                commit(config, lambda: assign_category_tables(config, assignments))
