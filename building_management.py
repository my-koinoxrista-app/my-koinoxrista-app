"""Building cleanup UI. Deletion safeguards remain in the service/repository."""
from contextlib import nullcontext
import pandas as pd
import streamlit as st
from psycopg import Error as DatabaseError

from building_service import list_cleanup_candidates, delete_empty_buildings
from building_lifecycle import list_archive_candidates, set_building_archived


def _label(item):
    return item['name'] + (f" — {item['address']}" if item.get('address') else '')


def render(embedded=False):
    """Return deleted IDs, or an empty list. Never mutate page widget state."""
    with (nullcontext() if embedded else st.expander('Διαχείριση πολυκατοικιών')):
        st.caption('Αρχειοθέτηση, επαναφορά και οριστική διαγραφή κενών πολυκατοικιών.')
        try:
            buildings = list_archive_candidates()
            for archived, title in ((False, 'Ενεργές πολυκατοικίες'), (True, 'Αρχείο πολυκατοικιών')):
                items = {b['id']: b for b in buildings if b['archived'] == archived}
                with st.expander(title, expanded=archived):
                    if not items:
                        st.caption('Δεν υπάρχουν εγγραφές.')
                        continue
                    selected = st.selectbox(title, list(items),
                        format_func=lambda bid, items=items: _label(items[bid]), key=f'archive_building_{archived}')
                    st.caption('Το ιστορικό και τα παραστατικά διατηρούνται. Η επαναφορά εμφανίζει ξανά την πολυκατοικία στις ενεργές επιλογές.')
                    if st.button('Επαναφορά' if archived else 'Αρχειοθέτηση', key=f'archive_action_{archived}'):
                        set_building_archived(selected, not archived)
                        return [selected]
        except (ValueError, DatabaseError) as exc:
            st.error(str(exc))
        st.divider()
        try:
            candidates = list_cleanup_candidates()
        except (ValueError, DatabaseError) as exc:
            st.error(f'Δεν ήταν δυνατός ο έλεγχος εξαρτήσεων: {exc}')
            return []
        if not candidates:
            st.info('Δεν υπάρχουν πολυκατοικίες.')
            return []

        eligible = {item['id']: item for item in candidates if item['eligible']}
        c1, c2 = st.columns(2)
        c1.metric('Σύνολο', len(candidates))
        c2.metric('Χωρίς ιστορικό', len(eligible))
        st.caption('Κενή θεωρείται η πολυκατοικία χωρίς αποθηκευμένες περιόδους, παραστατικά ή άγνωστες εξαρτήσεις. Οι ρυθμίσεις δεν θεωρούνται ιστορικό.')

        rows = []
        for item in candidates:
            rows.append({
                'Πολυκατοικία': item['name'],
                'Διεύθυνση': item.get('address') or '—',
                'Ιστορικές εγγραφές': item['history_count'],
                'Κατάσταση': 'Διαγράψιμη' if item['eligible'] else 'Προστατευμένη',
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                     column_config={'Ιστορικές εγγραφές': st.column_config.NumberColumn(format='%d')})

        blocked = [item for item in candidates if not item['eligible']]
        if blocked:
            with st.expander('Προστατευμένες πολυκατοικίες'):
                for item in blocked:
                    st.write(f"**{item['name']}** — {item.get('address') or 'Χωρίς διεύθυνση'}")
                    st.caption(', '.join(f'{table}: {count}' for table, count in item['blockers'].items()))

        if not eligible:
            st.info('Δεν υπάρχουν πολυκατοικίες διαθέσιμες για διαγραφή.')
            return []

        st.divider()
        st.markdown('**Διαγραφή κενών πολυκατοικιών**')
        st.caption('Επίλεξε μία ή περισσότερες πολυκατοικίες. Δεν απαιτείται πληκτρολόγηση επιβεβαίωσης.')
        nonce = st.session_state.get('cleanup_form_nonce', 0)
        with st.form(f'cleanup_form_{nonce}'):
            selected = st.multiselect(
                'Πολυκατοικίες προς διαγραφή', list(eligible),
                format_func=lambda bid: _label(eligible[bid]),
                placeholder='Επίλεξε πολυκατοικίες',
                key=f'cleanup_selected_{nonce}',
            )
            st.caption('Η διαγραφή είναι οριστική. Οι περίοδοι, τα παραστατικά και οι άγνωστες εξαρτήσεις δεν διαγράφονται. Αν εντοπιστούν, ακυρώνεται ολόκληρη η ενέργεια.')
            confirmed = st.checkbox('Κατανοώ ότι η οριστική διαγραφή δεν αναιρείται.')
            submitted = st.form_submit_button('Οριστική διαγραφή επιλεγμένων', type='secondary')

        if submitted:
            if not confirmed:
                st.warning('Επιβεβαίωσε την οριστική διαγραφή.')
                return []
            if not selected:
                st.warning('Επίλεξε τουλάχιστον μία πολυκατοικία.')
                return []
            try:
                count = delete_empty_buildings(selected)
                st.session_state.cleanup_form_nonce = nonce + 1
                st.success(f'Διαγράφηκαν {count} πολυκατοικίες.')
                return selected
            except (ValueError, DatabaseError) as exc:
                st.error(str(exc))
    return []
