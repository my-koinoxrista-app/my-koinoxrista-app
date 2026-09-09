"""Small receipt deletion UI; returns True when the current building changes."""
import streamlit as st
from psycopg import Error as DatabaseError

import receipt_management as lifecycle


def render(building_id):
    with st.expander('Διαχείριση / διαγραφή παραστατικών'):
        st.caption('Διάλεξε παραστατικό για να αφαιρέσεις το ίδιο και όλες τις '
                   'συνδεδεμένες χρεώσεις του. Οι υπόλοιπες δαπάνες δεν επηρεάζονται.')
        try:
            documents = lifecycle.list_receipts(building_id)
        except (ValueError, DatabaseError) as exc:
            st.error(f'Δεν ήταν δυνατή η φόρτωση παραστατικών: {exc}')
            return False
        if not documents:
            st.info('Δεν υπάρχουν αποθηκευμένα παραστατικά.')
            return False
        labels = {item['id']: f"{item['filename']} · {item['period_key']}"
                  for item in documents}
        selected = st.selectbox('Παραστατικό προς διαγραφή', list(labels),
                                format_func=lambda did: labels[did],
                                key=f'receipt_delete_select_{building_id}')
        item = next(doc for doc in documents if doc['id'] == selected)
        st.write(f"**{item['filename']}**")
        st.caption(f"Περίοδος: {item['period_key']} · "
                   f"Συνδεδεμένες χρεώσεις: {item['charge_count']} · "
                   f"Σύνολο: {item['total']:.2f} €")
        st.warning('Η διαγραφή από τη βάση είναι οριστική. Το αρχικό αρχείο '
                   'θα μεταφερθεί σε τοπικό φάκελο αρχειοθέτησης. '
                   'Η ενέργεια δεν διαθέτει αναίρεση μέσα από την εφαρμογή.')
        if st.button('Διαγραφή παραστατικού και χρεώσεων', type='secondary',
                     key=f'receipt_delete_button_{building_id}'):
            try:
                result = lifecycle.delete_receipt(building_id, selected)
            except (ValueError, DatabaseError) as exc:
                st.error(str(exc))
                return False
            st.session_state['receipt_delete_notice'] = (
                f"Το παραστατικό διαγράφηκε μαζί με {result['charge_count']} "
                f"χρεώσεις. {result['file_message']}")
            return True
    return False
