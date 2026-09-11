"""Streamlit payment settings and per-statement payment dashboard."""
import pandas as pd
import streamlit as st
from psycopg import Error as DatabaseError

from stripe_checkout import account_for

from payment_service import (PaymentError, ensure_payment_requests,
                             list_payment_requests, provider_settings,
                             save_provider_settings)

STATUS_LABELS = {
    'UNPAID': 'Απλήρωτο', 'PAID': 'Πληρωμένο',
    'FAILED': 'Απέτυχε', 'REFUNDED': 'Επιστροφή',
}


def render(building_id, statement):
    st.subheader('Πληρωμές')
    try:
        settings = provider_settings()
        from database import current_tenant
        approved_account = account_for(current_tenant())
    except (PaymentError, DatabaseError):
        st.info('Ρύθμισε πρώτα την υπηρεσία πληρωμών και την αντιστοίχιση Stripe της εταιρείας.')
        return
    with st.expander('Λογαριασμός πληρωμών', expanded=settings is None):
        st.caption('Ο λογαριασμός εγκρίνεται από τις ρυθμίσεις της υπηρεσίας πληρωμών.')
        account = st.text_input('Stripe account id', value=approved_account, disabled=True,
                                placeholder='acct_...', key=f'payment_account_{building_id}')
        if st.button('Ενεργοποίηση λογαριασμού πληρωμών', key=f'save_payment_account_{building_id}'):
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
    from company_auth import identity
    from database import current_tenant
    issuer, subject = identity(st.user)
    _render_status(building_id, statement['id'], issuer, subject, current_tenant())


@st.fragment(run_every='15s')
def _render_status(building_id, statement_id, issuer, subject, company_id):
    from datetime import datetime, timezone
    from company_auth import identity
    if (not st.user.is_logged_in or identity(st.user) != (issuer, subject)
            or float(st.user.get('exp', 0)) <= datetime.now(timezone.utc).timestamp()):
        st.info('Συνδεθείτε ξανά για ενημέρωση πληρωμών.')
        return
    st.caption('Η κατάσταση ενημερώνεται αυτόματα από το Stripe. Έλεγχος κάθε 15 δευτερόλεπτα.')
    try:
        from database import tenant_scope
        with tenant_scope(issuer, subject, company_id):
            requests = list_payment_requests(building_id, statement_id)
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
            'Επιστροφή €': item.get('refunded_cents', 0) / 100,
            'Αιτία αποτυχίας': item['failure_reason'],
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                 column_config={'Payment link': st.column_config.LinkColumn('Payment link')})
