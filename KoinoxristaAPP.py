"""Company-scoped common-expenses administration interface."""

import calendar
from decimal import Decimal
import copy
import json
from datetime import date
from uuid import uuid4
from html import escape

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from psycopg import Error as DatabaseError

from building_service import create_building, update_building, get_building, list_buildings, create_test_building, delete_test_building, get_building_configuration
from configuration import building_json, load_building, load_period
import configuration_forms
from expense_engine import calculate_period, money
from pdf_generator import create_pdf
import monthly_store as store
import receipt_import as receipts
import receipt_management_ui
import building_management
import statement_history_ui
from statement_store import issue_statement, StatementError
from automatic_statement_email import auto_email_enabled, send_after_issue
from payment_service import ensure_payment_requests, PaymentError
from ui_theme import brand, hero, page_header, section_title, empty_state


load_dotenv()
# Page configuration and the visual theme are owned by CompanyPortal.



def encode(value):
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def reset_result():
    st.session_state.result = None
    st.session_state.pop("result_source", None)


def select_building(building_id):
    building = get_building(building_id)
    st.session_state.building_id = building.id
    st.session_state.config = get_building_configuration(building.id)
    st.session_state.pop('period_scope', None)
    st.session_state.pop('pending_receipts', None)
    reset_result()


def save_configuration(candidate, create=False):
    building = (
        create_building(candidate)
        if create
        else update_building(st.session_state.building_id, candidate)
    )
    select_building(building.id)
    st.success('Η πολυκατοικία αποθηκεύτηκε.')
    st.rerun()


def apply_config(candidate):
    load_building(candidate)
    st.session_state.config = candidate
    reset_result()
    st.success('Οι αλλαγές εφαρμόστηκαν προσωρινά. Πάτησε Αποθήκευση στη βάση.')


def save_period():
    data = {
        'start_date': st.session_state.period_start.isoformat(),
        'end_date': st.session_state.period_end.isoformat(),
        'expenses': st.session_state.expenses,
    }
    load_period(data)
    store.save_period_data(
        st.session_state.building_id,
        st.session_state.period_key,
        data,
    )
    reset_result()
    return data



# Building navigation is a single sidebar selector, with no secondary dashboard picker.
_BUILDING_STATE = ('building_id', 'config', 'period_scope', 'pending_receipts',
                   'result', 'result_source', 'expenses', 'period_key',
                   'period_start', 'period_end', 'selected_month', 'billing_month_selector')


def _clear_building_context():
    for key in _BUILDING_STATE:
        st.session_state.pop(key, None)
    # Widget values from a previous building must not be reused as new financial inputs.
    for key in list(st.session_state):
        if key.startswith(('pdf_owner_', 'expense_category_', 'expense_editor_', 'start_', 'end_')):
            st.session_state.pop(key, None)


def _apply_building_selection(chosen):
    """Run only inside render(), while the authenticated tenant scope is active."""
    if chosen:
        select_building(chosen)
        st.session_state.page = 'Πολυκατοικία'
    else:
        _clear_building_context()
        st.session_state.page = 'Αρχική'


def _create_building_page():
    page_header('Νέα πολυκατοικία',
                'Ξεκινήστε με τα βασικά στοιχεία. Οι ιδιοκτησίες και οι κανόνες συμπληρώνονται στη συνέχεια.')
    with st.form('new_building_form', clear_on_submit=True):
        name = st.text_input('Όνομα πολυκατοικίας', placeholder='π.χ. Κάδμου 18–20')
        address = st.text_input('Διεύθυνση (προαιρετικό)', placeholder='Οδός και αριθμός')
        submitted = st.form_submit_button('Δημιουργία πολυκατοικίας', type='primary')
    if submitted:
        try:
            created = create_test_building(name, address)
            select_building(created.id)
            st.session_state['_ui_requested_building'] = created.id
            st.session_state.page = 'Πολυκατοικία'
            st.rerun()
        except (ValueError, DatabaseError) as exc:
            st.error(str(exc))


def _manage_buildings_page():
    page_header('Διαχείριση πολυκατοικιών',
                'Αρχειοθετήστε, επαναφέρετε ή διαγράψτε οριστικά κενές πολυκατοικίες.')
    deleted = building_management.render(embedded=True)
    if deleted:
        if st.session_state.get('building_id') in deleted:
            _clear_building_context()
            st.session_state['_ui_requested_building'] = ''
        st.session_state.page = 'Αρχική'
        st.rerun()


def _home_summary_rows(available, period_key, load_period=None):
    """Read the current company's draft amounts, without allocating or issuing."""
    if load_period is None:
        periods = store.load_periods_data([item["id"] for item in available], period_key)
        loader = lambda building_id, key: periods.get(building_id, {"expenses": []})
    else:
        loader = load_period
    rows = []
    total = Decimal('0')
    for item in available:
        data = loader(item['id'], period_key)
        amounts = [money(expense['amount']) for expense in data.get('expenses', [])]
        subtotal = sum(amounts, Decimal('0'))
        total += subtotal
        rows.append({
            'Πολυκατοικία': item['name'],
            'Διεύθυνση': item.get('address') or '—',
            'Δαπάνες μήνα (€)': float(subtotal),
            'Πλήθος δαπανών': len(amounts),
        })
    return rows, total


def _home_currency(value):
    return f'{value:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.') + ' €'


def _render_home(available):
    """One read-only overview. Building selection belongs to the sidebar."""
    hero('Η διαχείριση των κτηρίων σας σε έναν χώρο.',
         'Μια καθαρή εικόνα των πολυκατοικιών και των καταχωρισμένων δαπανών της εταιρείας σας.',
         'Αρχική σελίδα')
    key = date.today().strftime('%Y-%m')
    rows, total = _home_summary_rows(available, key)
    c1, c2, c3 = st.columns(3)
    c1.metric('Πολυκατοικίες', len(available))
    c2.metric('Δαπάνες μήνα', _home_currency(total),
              help='Σύνολο καταχωρισμένων δαπανών, όχι εκδοθεισών εκκαθαρίσεων.')
    c3.metric('Καταχωρίσεις μήνα', sum(row['Πλήθος δαπανών'] for row in rows))
    st.caption(f'Περίοδος αναφοράς: {key} · Τα ποσά είναι καταχωρισμένες δαπάνες, όχι υπολογισμένες χρεώσεις.')
    section_title('Οι πολυκατοικίες σας', 'Η επιλογή και η δημιουργία γίνονται από το αριστερό μενού.')
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                     column_config={
                         'Δαπάνες μήνα (€)': st.column_config.NumberColumn('Δαπάνες μήνα', format='%.2f €'),
                         'Πλήθος δαπανών': st.column_config.NumberColumn('Καταχωρίσεις', format='%d'),
                     })
    else:
        empty_state('Δεν υπάρχουν ακόμη πολυκατοικίες',
                    'Δημιουργήστε την πρώτη σας πολυκατοικία από το αριστερό μενού.')

def render():
    try:
        store.initialize()
        available = list_buildings()
    except DatabaseError as exc:
        st.error(f'Δεν είναι διαθέσιμη η PostgreSQL: {exc}')
        st.stop()

    choices = {str(item['id']): item['name'] for item in available}
    if 'page' not in st.session_state:
        st.session_state.page = 'Αρχική'
    current = st.session_state.get('building_id')
    if current and current not in choices:
        _clear_building_context()
        st.session_state.page = 'Αρχική'
        current = None
    requested = st.session_state.pop('_ui_requested_building', None)
    if requested is not None:
        st.session_state['_ui_building_choice'] = requested if requested in choices else ''
    elif st.session_state.get('_ui_building_choice', '') not in choices:
        st.session_state['_ui_building_choice'] = current or ''

    with st.sidebar:
        st.markdown('<div class="k-side-label">Πλοήγηση</div>', unsafe_allow_html=True)
        if st.button('⌂  Αρχική σελίδα', key='ui_home', use_container_width=True):
            st.session_state.page = 'Αρχική'
            st.rerun()
        st.markdown('<div class="k-side-label">Πολυκατοικία</div>', unsafe_allow_html=True)
        chosen = st.selectbox('Επιλογή πολυκατοικίας', [''] + list(choices),
                     format_func=lambda key: choices.get(key, 'Επιλέξτε πολυκατοικία'),
                     key='_ui_building_choice', label_visibility='collapsed')
        if chosen != (current or ''):
            _apply_building_selection(chosen)
            st.rerun()
        if st.button('＋  Νέα πολυκατοικία', key='ui_new_building', use_container_width=True):
            st.session_state.page = 'Νέα πολυκατοικία'
            st.rerun()
        if current:
            st.caption('Επιλεγμένη: ' + choices[current])
        st.divider()
        if st.button('Διαχείριση πολυκατοικιών', key='ui_manage_buildings',
                     use_container_width=True):
            st.session_state.page = 'Διαχείριση πολυκατοικιών'
            st.rerun()

    if st.session_state.page == 'Αρχική':
        try:
            _render_home(available)
        except (ValueError, KeyError, TypeError, DatabaseError) as exc:
            st.error(f'Δεν ήταν δυνατή η φόρτωση της σύνοψης: {exc}')
        st.stop()

    if st.session_state.page == 'Νέα πολυκατοικία':
        _create_building_page()
        st.stop()

    if st.session_state.page == 'Διαχείριση πολυκατοικιών':
        _manage_buildings_page()
        st.stop()

    if 'config' not in st.session_state:
        empty_state('Επιλέξτε πολυκατοικία',
                    'Χρησιμοποιήστε το αριστερό μενού για να ανοίξετε ή να δημιουργήσετε μια πολυκατοικία.')
        st.stop()


    config = st.session_state.config
    building_id = st.session_state.building_id

    try:
        building = load_building(config)
    except (ValueError, KeyError, TypeError) as exc:
        st.error(f'Μη έγκυρη παραμετροποίηση: {exc}')
        st.stop()


    page_header(building.name, building.address or 'Χωρίς καταχωρισμένη διεύθυνση',
                'Πολυκατοικία')

    config_tab, bills_tab, import_tab, result_tab, history_tab = st.tabs(
        [
            'Στοιχεία & χιλιοστά',
            'Δαπάνες',
            'Παραστατικά',
            'Υπολογισμός & PDF',
            'Ιστορικό',
        ],
        key=f'building_tabs_{building_id}',
        on_change='rerun',
    )

    # Keep the selected month independent of the displayed date range.
    # The selection must be resolved before the other tabs use period data.
    today = date.today()
    year, month = st.session_state.get(
        'selected_month',
        (today.year, today.month),
    )

    if bills_tab.open:
        with bills_tab:
            st.subheader('Μηνιαίες δαπάνες')
            selected_month = st.date_input(
                'Μήνας δαπανών',
                date(year, month, 1),
                key='billing_month_selector',
            )
            year, month = selected_month.year, selected_month.month
            st.session_state.selected_month = (year, month)

    key = f'{year:04d}-{month:02d}'
    scope = (building_id, key)

    if st.session_state.get('period_scope') != scope:
        saved = store.load_period_data(building_id, key)
        st.session_state.expenses = saved.get('expenses', [])
        st.session_state.period_start = date.fromisoformat(
            saved.get('start_date', key + '-01')
        )
        st.session_state.period_end = date.fromisoformat(
            saved.get(
                'end_date',
                f'{key}-{calendar.monthrange(year, month)[1]:02d}',
            )
        )
        st.session_state.period_key = key
        st.session_state.period_scope = scope
        st.session_state.pop('pending_receipts', None)
        reset_result()


    if config_tab.open:
        with config_tab:
            configuration_forms.render(config, building_id)
            st.divider()
            if st.button('Αποθήκευση πολυκατοικίας στη βάση', type='primary'):
                try:
                    save_configuration(st.session_state.config)
                except (ValueError, KeyError, TypeError, DatabaseError) as exc:
                    st.error(str(exc))

            with st.expander('Προχωρημένα · Εξαγωγή ρυθμίσεων'):
                st.download_button('Εξαγωγή configuration JSON', encode(config),
                                   file_name=f'{building_id}.json', mime='application/json')


    if bills_tab.open:
        with bills_tab:
            st.caption(
                'Οι αλλαγές αποθηκεύονται στη βάση μόνο με το κουμπί '
                'αποθήκευσης. Μην επεξεργάζεσαι τον ίδιο μήνα από δύο παράθυρα.'
            )

            c1, c2 = st.columns(2)

            st.session_state.period_start = c1.date_input(
                'Από',
                st.session_state.period_start,
                key=f'start_{scope}',
            )
            st.session_state.period_end = c2.date_input(
                'Έως',
                st.session_state.period_end,
                key=f'end_{scope}',
            )

            if not building.categories:
                st.info('Δημιούργησε πρώτα κατηγορία και κανόνα στην Παραμετροποίηση.')

            # Keep category selection outside the form so switching to DIRECT
            # immediately displays the per-property inputs before submission.
            category_id = st.selectbox(
                'Κατηγορία',
                list(building.categories),
                format_func=lambda cid: building.categories[cid].name,
                disabled=not building.categories,
                key=f'expense_category_{building_id}',
            )

            with st.form('expense_form', clear_on_submit=True):
                description = st.text_input('Περιγραφή')
                amount = st.text_input('Ποσό (€)')
                direct_inputs = {}
                if category_id and building.categories[category_id].rule.type == 'DIRECT':
                    st.caption('Καταχώρισε το ποσό που αντιστοιχεί σε κάθε ιδιοκτησία. Το άθροισμα πρέπει να συμφωνεί με τη δαπάνη.')
                    for apartment in building.apartments:
                        if apartment.archived:
                            continue
                        direct_inputs[apartment.id] = st.text_input(
                            f'Χρέωση {apartment.code} (€)', '0', key=f'direct_{building_id}_{apartment.id}')

                if st.form_submit_button('Προσθήκη', disabled=not building.categories):
                    try:
                        value = money(amount.replace(',', '.'))
                        if value <= 0:
                            raise ValueError('Το ποσό πρέπει να είναι θετικό.')
                        expense = {
                            'id': str(uuid4()),
                            'category_id': category_id,
                            'description': description,
                            'amount': str(value),
                        }
                        if direct_inputs:
                            charges = {aid: str(money(raw.replace(',', '.'))) for aid, raw in direct_inputs.items()}
                            if sum((money(v) for v in charges.values()), money('0')) != value:
                                raise ValueError('Οι επιμέρους χρεώσεις δεν συμφωνούν με το συνολικό ποσό.')
                            expense['direct_charges'] = charges
                        candidate = list(st.session_state.expenses) + [expense]
                        data = {
                            'start_date': st.session_state.period_start.isoformat(),
                            'end_date': st.session_state.period_end.isoformat(),
                            'expenses': candidate,
                        }
                        load_period(data)
                        store.save_period_data(building_id, key, data)
                        st.session_state.expenses = candidate
                        reset_result()
                        st.rerun()
                    except (ValueError, KeyError, TypeError, DatabaseError) as exc:
                        st.error(str(exc))

            if st.session_state.expenses:
                display = pd.DataFrame(st.session_state.expenses)
                display['category_id'] = display['category_id'].map(
                    lambda cid: (
                        building.categories[cid].name
                        if cid in building.categories
                        else cid
                    )
                )

                display = display.drop(columns=['id', 'document_id', 'rule', 'direct_charges'], errors='ignore')
                st.dataframe(
                    display,
                    hide_index=True,
                    use_container_width=True,
                )

                with st.expander('Επεξεργασία / διαγραφή δαπανών'):
                    st.caption('Τα IDs και τα στοιχεία παραστατικών διατηρούνται. Η διαγραφή χρέωσης συνδεδεμένης με παραστατικό δεν υποστηρίζεται εδώ.')
                    with st.form('expense_edit_form'):
                        category_labels = {cid: category.name for cid, category in building.categories.items()}
                        category_options = list(building.categories)
                        category_display = {}
                        used_labels = set()
                        for cid in category_options:
                            base = building.categories[cid].name
                            label = base
                            suffix = 2
                            while label in used_labels:
                                label = f'{base} ({suffix})'
                                suffix += 1
                            used_labels.add(label)
                            category_display[cid] = label
                        category_lookup = {label: cid for cid, label in category_display.items()}
                        rows = []
                        for expense in st.session_state.expenses:
                            rows.append({
                                'id': expense['id'],
                                'category_id': category_display.get(expense['category_id'], expense['category_id']),
                                'description': expense.get('description', ''),
                                'amount': str(expense['amount']),
                                'delete': False,
                            })
                        edited = st.data_editor(
                            pd.DataFrame(rows), hide_index=True, use_container_width=True,
                            num_rows='fixed', disabled=['id'],
                            column_order=['category_id', 'description', 'amount', 'delete'],
                            column_config={
                                'id': None,
                                'category_id': st.column_config.SelectboxColumn(
                                    'Κατηγορία', options=list(category_lookup), required=True),
                                'description': st.column_config.TextColumn('Περιγραφή'),
                                'amount': st.column_config.TextColumn('Ποσό €'),
                                'delete': st.column_config.CheckboxColumn('Διαγραφή'),
                            },
                            key=f"expense_editor_{scope}_{st.session_state.get('expense_editor_revision', 0)}",
                        )
                        if st.form_submit_button('Επικύρωση και αποθήκευση δαπανών'):
                            try:
                                original = {item['id']: item for item in st.session_state.expenses}
                                candidate = []
                                for row in edited.to_dict('records'):
                                    item = dict(original[row['id']])
                                    if row['delete']:
                                        if item.get('document_id'):
                                            raise ValueError('Δεν επιτρέπεται διαγραφή χρέωσης συνδεδεμένης με παραστατικό από αυτή τη φόρμα.')
                                        continue
                                    category_id = category_lookup.get(row['category_id'])
                                    if category_id not in building.categories:
                                        raise ValueError('Επίλεξε έγκυρη κατηγορία.')
                                    amount = money(str(row['amount']).replace(',', '.'))
                                    if amount <= 0:
                                        raise ValueError('Το ποσό πρέπει να είναι θετικό.')
                                    effective_rule = item.get('rule') or {'type': building.categories[category_id].rule.type}
                                    if effective_rule['type'] == 'DIRECT':
                                        charges = item.get('direct_charges', {})
                                        if not charges or sum((money(v) for v in charges.values()), money('0')) != amount:
                                            raise ValueError('Οι DIRECT χρεώσεις απαιτούν επιμέρους ποσά που συμφωνούν με το σύνολο.')
                                    item.update(category_id=category_id, description=str(row['description'] or ''), amount=str(amount))
                                    candidate.append(item)
                                load_period({
                                    'start_date': st.session_state.period_start.isoformat(),
                                    'end_date': st.session_state.period_end.isoformat(),
                                    'expenses': candidate,
                                })
                                # Persist first; a failed database write must not discard the prior UI state.
                                data = {
                                    'start_date': st.session_state.period_start.isoformat(),
                                    'end_date': st.session_state.period_end.isoformat(),
                                    'expenses': candidate,
                                }
                                store.save_period_data(building_id, key, data)
                                st.session_state.expenses = candidate
                                reset_result()
                                st.success('Οι δαπάνες αποθηκεύτηκαν.')
                                st.rerun()
                            except (ValueError, KeyError, TypeError, DatabaseError) as exc:
                                st.error(str(exc))

            if st.session_state.get('receipt_delete_notice'):
                st.success(st.session_state.pop('receipt_delete_notice'))

            if receipt_management_ui.render(building_id):
                # Reload the persisted period. Never save the stale expense list after deletion.
                st.session_state.pop('period_scope', None)
                st.session_state.pop('pending_receipts', None)
                st.session_state.pop('result', None)
                st.session_state.expense_editor_revision = st.session_state.get('expense_editor_revision', 0) + 1
                st.rerun()

            document_ids = list(dict.fromkeys(
                item.get('document_id')
                for item in st.session_state.expenses
                if item.get('document_id')
            ))

            if document_ids:
                with st.expander('Αρχικά παραστατικά'):
                    for document_id in document_ids:
                        record = store.get_receipt(document_id, building_id)

                        if record:
                            filename, mime, _ = record

                            try:
                                content = receipts.read_file(document_id, building_id)

                                st.download_button(
                                    filename,
                                    content,
                                    file_name=filename,
                                    mime=mime,
                                    key=f'doc_{document_id}',
                                )

                            except FileNotFoundError:
                                st.warning(
                                    f'{filename}: το αρχείο δεν βρέθηκε '
                                    'στον τοπικό φάκελο.'
                                )

            c1, c2 = st.columns(2)

            if c1.button('Αποθήκευση περιόδου'):
                try:
                    save_period()
                    st.success('Η περίοδος αποθηκεύτηκε.')
                except (ValueError, DatabaseError) as exc:
                    st.error(str(exc))

            if c2.button('Υπολογισμός', type='primary'):
                try:
                    data = save_period()
                    period = load_period(data)
                    st.session_state.result = calculate_period(building, period)
                    st.session_state.result_source = {
                        'building_id': building_id, 'period_key': key,
                        'configuration': copy.deepcopy(config),
                        'period_data': copy.deepcopy(data),
                    }
                    st.success('Ο υπολογισμός ολοκληρώθηκε.')

                except (ValueError, KeyError, TypeError, DatabaseError) as exc:
                    st.error(str(exc))

            period_data = {
                'start_date': st.session_state.period_start.isoformat(),
                'end_date': st.session_state.period_end.isoformat(),
                'expenses': st.session_state.expenses,
            }

            with st.expander('Προχωρημένα · Εξαγωγή περιόδου'):
                st.download_button(
                    'Εξαγωγή περιόδου JSON', encode(period_data),
                    file_name=f'{key}.json', mime='application/json',
                )


    if import_tab.open:
        with import_tab:
            st.subheader('Εισαγωγή παραστατικών')
            st.caption(
                'PDF, σαρωμένα PDF και φωτογραφίες. Το AI προτείνει στοιχεία· '
                'εσύ επιβεβαιώνεις τις χρεώσεις πριν αποθηκευτούν.'
            )
            st.warning(
                'Η ανάγνωση στέλνει τα αρχεία σε εξωτερικό AI API. '
                'Χρησιμοποίησε μόνο εικονικά ή εγκεκριμένα παραστατικά. '
                'Η εισαγωγή απαιτεί ανθρώπινη επιβεβαίωση πριν από την αποθήκευση.'
            )

            uploads = st.file_uploader(
                'Επιλογή παραστατικών',
                type=['pdf', 'jpg', 'jpeg', 'png', 'webp'],
                accept_multiple_files=True,
            )

            if st.button('Ανάγνωση με AI', disabled=not uploads):
                pending = []

                for upload in uploads or []:
                    try:
                        content = upload.getvalue()
                        mime, digest = receipts.prepare_file(
                            upload.name,
                            content,
                        )

                        duplicate = store.find_receipt(building_id, digest)

                        if duplicate:
                            st.warning(
                                f'{upload.name}: έχει ήδη εισαχθεί '
                                f'(περίοδος {duplicate[1]}).'
                            )
                            continue

                        with st.spinner(f'Ανάγνωση {upload.name}...'):
                            data = receipts.extract_receipt(
                                upload.name,
                                content,
                                building.categories,
                            )

                        pending.append({
                            'id': str(uuid4()),
                            'filename': upload.name,
                            'content': content,
                            'mime': mime,
                            'sha256': digest,
                            'extracted': data,
                        })

                    except Exception as exc:
                        st.error(f'{upload.name}: {exc}')

                st.session_state.pending_receipts = pending

            pending = st.session_state.get('pending_receipts', [])

            for doc in pending:
                with st.expander(doc['filename'], expanded=True):
                    data = doc['extracted']

                    st.caption('Ελέγξτε τα στοιχεία που αναγνώρισε το AI πριν από την αποθήκευση.')
                    if data.get('warnings'):
                        st.warning(' · '.join(str(w) for w in data['warnings']))

                    data['supplier'] = st.text_input(
                        'Προμηθευτής',
                        str(data.get('supplier') or ''),
                        key=f"supplier_{doc['id']}",
                    )
                    data['invoice_number'] = st.text_input(
                        'Αριθμός παραστατικού',
                        str(data.get('invoice_number') or ''),
                        key=f"invoice_{doc['id']}",
                    )
                    data['date'] = st.text_input(
                        'Ημερομηνία παραστατικού',
                        str(data.get('date') or ''),
                        key=f"date_{doc['id']}",
                    )

                    st.caption(
                        'Έλεγξε το αρχικό αρχείο και το συνολικό ποσό. '
                        'Μπορείς να επιλέξεις μόνο τις χρεώσεις που ανήκουν '
                        'σε αυτόν τον μήνα.'
                    )

                    rows = receipts.suggested_rows(
                        data,
                        building.categories,
                    )

                    if len(rows) > 1 and data.get('total') is not None:
                        try:
                            total = sum(
                                (money(r['amount']) for r in rows),
                                money('0'),
                            )

                            if total != money(
                                str(data['total']).replace(',', '.')
                            ):
                                st.warning(
                                    'Το άθροισμα των προτεινόμενων γραμμών '
                                    'δεν συμφωνεί με το συνολικό ποσό. '
                                    'Έλεγξέ το πριν την εισαγωγή.'
                                )

                        except ValueError:
                            st.warning(
                                'Κάποια προτεινόμενα ποσά χρειάζονται διόρθωση.'
                            )

                    edited_rows = []

                    for i, row in enumerate(rows):
                        st.markdown(f'**Προτεινόμενη χρέωση {i + 1}**')

                        c1, c2, c3 = st.columns([3, 1, 2])

                        description = c1.text_input(
                            'Περιγραφή',
                            row['description'],
                            key=f"desc_{doc['id']}_{i}",
                        )

                        amount = c2.text_input(
                            'Ποσό €',
                            row['amount'],
                            key=f"amount_{doc['id']}_{i}",
                        )

                        options = [''] + list(building.categories)

                        category = c3.selectbox(
                            'Κατηγορία',
                            options,
                            index=options.index(row['category_id']),
                            format_func=lambda cid: (
                                building.categories[cid].name
                                if cid else '— Επιλογή —'
                            ),
                            key=f"cat_{doc['id']}_{i}",
                        )

                        selected = st.checkbox(
                            'Εισαγωγή',
                            value=True,
                            key=f"include_{doc['id']}_{i}",
                        )

                        edited_rows.append((
                            i,
                            description,
                            amount,
                            category,
                            selected,
                        ))

                    if st.button(
                        'Επιβεβαίωση & εισαγωγή',
                        key=f"approve_{doc['id']}",
                        type='primary',
                    ):
                        try:
                            if doc['sha256'] != receipts.prepare_file(
                                doc['filename'],
                                doc['content'],
                            )[1]:
                                raise ValueError('Το αρχείο έχει αλλάξει.')

                            charges = []

                            for i, description, amount, category, selected in edited_rows:
                                if not selected:
                                    continue

                                value = money(amount.replace(',', '.'))

                                if value <= 0 or category not in building.categories:
                                    raise ValueError(
                                        'Συμπλήρωσε θετικό ποσό και έγκυρη κατηγορία.'
                                    )

                                if building.categories[category].rule.type == 'DIRECT':
                                    raise ValueError(
                                        'Οι DIRECT χρεώσεις χρειάζονται ποσά '
                                        'ανά ιδιοκτησία. Καταχώρισέ τες χειροκίνητα.'
                                    )

                                charges.append({
                                    'id': f"{doc['id']}-{i}",
                                    'category_id': category,
                                    'amount': str(value),
                                    'description': description,
                                    'document_id': doc['id'],
                                })

                            if not charges:
                                raise ValueError('Δεν επιλέχθηκαν χρεώσεις.')

                            document = {
                                k: doc[k]
                                for k in ('id', 'filename', 'sha256')
                            }
                            document.update(
                                building_id=building_id,
                                period_key=key,
                                content_type=doc['mime'],
                                extracted=data,
                            )

                            receipts.store_file(
                                doc['id'],
                                doc['filename'],
                                doc['content'],
                                building_id,
                            )

                            saved = store.import_receipt(document, charges)
                            st.session_state.expenses = saved['expenses']
                            reset_result()

                            st.session_state.pending_receipts = [
                                d for d in pending
                                if d['id'] != doc['id']
                            ]

                            st.success(
                                'Οι χρεώσεις αποθηκεύτηκαν στη μηνιαία περίοδο.'
                            )
                            st.rerun()

                        except (ValueError, KeyError, TypeError, DatabaseError) as exc:
                            st.error(str(exc))

            if pending and st.button('Απόρριψη προτάσεων'):
                st.session_state.pending_receipts = []
                st.rerun()


    if result_tab.open:
        with result_tab:
            result = st.session_state.get('result')

            if result is None:
                st.info(
                    'Υπολόγισε πρώτα την περίοδο από την καρτέλα '
                    'Μηνιαίες δαπάνες.'
                )

            else:
                st.metric(
                    'Σύνολο περιόδου',
                    f"{result['grand_total']:.2f} €",
                )

                rows = []

                for item in result['rows']:
                    row = {
                        'Κατηγορία': item['category'],
                        'Περιγραφή': item['description'],
                        'Σύνολο': float(item['amount']),
                        'Υπόχρεος': item['payer'],
                    }

                    row.update({
                        a.code: float(item['allocations'][a.id])
                        for a in result['building'].apartments
                    })

                    rows.append(row)

                st.dataframe(
                    pd.DataFrame(rows),
                    hide_index=True,
                    use_container_width=True,
                )

                st.subheader('Σύνολα ανά ιδιοκτησία')

                st.dataframe(
                    pd.DataFrame([
                        {
                            'Ιδιοκτησία': a.code,
                            'Ποσό': float(result['apartment_totals'][a.id]),
                        }
                        for a in result['building'].apartments
                    ]),
                    hide_index=True,
                )

                st.subheader('Στοιχεία PDF')
                st.caption('Προαιρετικά ονόματα ιδιοκτητών για την εκτύπωση. Δεν αλλάζουν '
                           'τους υπολογισμούς και δεν αποθηκεύονται στη βάση.')
                owner_names = {}
                display_names = config.get('property_names', {})
                with st.expander('Ονόματα ιδιοκτητών για το PDF'):
                    for apartment in result['building'].apartments:
                        owner_names[apartment.id] = st.text_input(
                            f'Ιδιοκτήτης · {apartment.code}',
                            value=display_names.get(apartment.id, ''),
                            key=f'pdf_owner_{building_id}_{apartment.id}',
                            max_chars=200,
                        )

                st.download_button(
                    'Λήψη PDF κοινοχρήστων',
                    create_pdf(result, period_data=period_data, configuration=config,
                               owner_names=owner_names),
                    file_name=f'koinoxrista_{key}.pdf',
                    mime='application/pdf',
                )

                st.divider()
                st.subheader('Οριστικοποίηση εκκαθάρισης')
                st.caption('Αποθηκεύει το PDF και τα αποτελέσματα όπως εκδόθηκαν. '
                           'Μελλοντικές διορθώσεις δημιουργούν νέα έκδοση.')
                source = st.session_state.get('result_source')
                automatic_email = auto_email_enabled()
                if automatic_email:
                    st.info('Μετά την έκδοση θα σταλούν αυτόματα τα ατομικά PDF στους ενεργούς, έγκυρους παραλήπτες. Ελέγξτε πρώτα τα στοιχεία επικοινωνίας. Δεν γίνονται αυτόματες επαναλήψεις.')
                else:
                    st.caption('Η αυτόματη αποστολή δεν είναι ενεργή. Η έκδοση αποθηκεύει τα PDF χωρίς να στέλνει email.')
                if st.button('Οριστικοποίηση και αποθήκευση στο ιστορικό',
                             type='primary', disabled=not source, key='issue_statement'):
                    try:
                        if source['building_id'] != building_id or source['period_key'] != key:
                            raise StatementError('Η περίοδος άλλαξε. Υπολόγισε ξανά.')
                        issued = issue_statement(
                            building_id, key, source['configuration'], source['period_data'],
                            owner_names=owner_names)
                        payment_setup = ensure_payment_requests(building_id, issued['id'])
                        if not payment_setup['ready']:
                            st.warning(payment_setup['error'])
                        elif payment_setup.get('error'):
                            st.warning(payment_setup['error'])
                        if issued['reused']:
                            st.info(f"Η ίδια εκκαθάριση υπάρχει ήδη ως έκδοση {issued['revision']}.")
                        else:
                            st.success(f"Η εκκαθάριση αποθηκεύτηκε. Έκδοση {issued['revision']}. Το συνολικό και τα ατομικά PDF είναι διαθέσιμα στο Ιστορικό.")
                        if automatic_email and not issued['reused']:
                            with st.spinner('Αποστολή ατομικών PDF στους ενεργούς παραλήπτες...'):
                                outcome = send_after_issue(building_id, issued)
                            accepted = sum(r['status'] == 'ACCEPTED' for r in outcome['results'])
                            failed = sum(r['status'] == 'FAILED' for r in outcome['results'])
                            uncertain = sum(r['status'] not in ('ACCEPTED', 'FAILED') for r in outcome['results'])
                            if accepted:
                                st.success(f'{accepted} email έγιναν αποδεκτά από τον SMTP server.')
                            if failed or uncertain:
                                st.warning(f'{failed} απορρίφθηκαν, {uncertain} έχουν αβέβαιη κατάσταση. Έλεγξε το Ιστορικό αποστολών.')
                            if outcome['skipped']:
                                st.warning(f"{len(outcome['skipped'])} ιδιοκτησίες παραλείφθηκαν. Έλεγξε τα στοιχεία επικοινωνίας και το Ιστορικό αποστολών.")
                                st.dataframe(pd.DataFrame(outcome['skipped']), hide_index=True, use_container_width=True)
                            if outcome['error']:
                                st.error(outcome['error'])
                            if not outcome['results'] and not outcome['skipped'] and not outcome['error']:
                                st.info('Δεν υπάρχουν νέοι ενεργοί παραλήπτες για αυτή την έκδοση.')
                            st.caption('Η αποδοχή από SMTP δεν εγγυάται παράδοση στα Εισερχόμενα. Η εκκαθάριση παραμένει αποθηκευμένη ανεξάρτητα από το αποτέλεσμα email.')
                    except (StatementError, PaymentError, ValueError, KeyError, TypeError, DatabaseError) as exc:
                        st.error(str(exc))

                with st.expander('Προχωρημένα · Αναλυτικά δεδομένα'):
                        st.download_button(
                            'Λήψη αναλυτικού JSON',
                        encode({
                            'building_id': building_id,
                            'period': period_data,
                            'total': str(result['grand_total']),
                            'allocations': [
                                {
                                    **{
                                        k: v
                                        for k, v in item.items()
                                        if k not in ('rule', 'allocations', 'amount')
                                    },
                                    'amount': str(item['amount']),
                                    'allocations': {
                                        k: str(v)
                                        for k, v in item['allocations'].items()
                                    },
                                }
                                for item in result['rows']
                            ],
                        }),
                        file_name=f'allocations_{key}.json',
                        mime='application/json',
                    )

    if history_tab.open:
        with history_tab:
            statement_history_ui.render(building_id)


if __name__ == '__main__':
    from CompanyPortal import main
    main()
