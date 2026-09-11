"""Streamlit payment settings and per-statement payment dashboard."""
import pandas as pd
import streamlit as st
from psycopg import Error as DatabaseError

from payment_service import (PaymentError, ensure_payment_requests,
                             list_payment_requests, provider_settings,
                             save_provider_settings)

STATUS_LABELS = {
    'UNPAID': 'Απλήρωτο', 'PAID': 'Πληρωμένο',
    'FAILED': 'Απέτυχε', 'REFUNDED': 'Επιστροφή',
    'PARTIALLY_REFUNDED': 'Μερική επιστροφή',
}


def render(building_id, statement):
    st.subheader('Πληρωμές')
    settings = provider_settings()
    with st.expander('Payment provider account', expanded=settings is None):
        st.caption('Κάθε εταιρεία χρησιμοποιεί το δικό της provider account. Το secret παραμένει στις ρυθμίσεις του server.')
        account = st.text_input('Stripe account id', value=settings['account_id'] if settings else '',
                                placeholder='acct_...', key=f'payment_account_{building_id}')
        if st.button('Αποθήκευση payment account', key=f'save_payment_account_{building_id}'):
            try:
                save_provider_settings(account)
                st.success('Το payment account αποθηκεύτηκε.')
                st.rerun()
            except (PaymentError, DatabaseError) as exc:
                st.error(str(exc))
    if settings is None:
        st.info('Ρύθμισε πρώτα το payment provider account της εταιρείας.')
        return
    if st.button('Δημιουργία / έλεγχος payment links', key=f'ensure_payments_{statement["id"]}'):
        try:
            result = ensure_payment_requests(building_id, statement['id'])
            if result.get('error'):
                st.warning(result['error'])
            else:
                st.success(f"Ελέγχθηκαν {result.get('count', 0)} payment requests.")
            st.rerun()
        except (PaymentError, DatabaseError, ValueError) as exc:
            st.error(str(exc))
    try:
        requests = list_payment_requests(building_id, statement['id'])
    except (PaymentError, DatabaseError, ValueError) as exc:
        st.error(str(exc))
        return
    if not requests:
        st.info('Δεν έχουν δημιουργηθεί payment requests για αυτή την εκκαθάριση.')
        return
    rows = []
    for item in requests:
        rows.append({
            'Ιδιοκτησία': item['apartment_id'],
            'Ποσό €': item['amount_cents'] / 100,
            'Κατάσταση': STATUS_LABELS.get(item['status'], item['status']),
            'Payment link': item['checkout_url'] or 'Δεν δημιουργήθηκε',
            'Αιτία αποτυχίας': item['failure_reason'],
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                 column_config={'Payment link': st.column_config.LinkColumn('Payment link')})
