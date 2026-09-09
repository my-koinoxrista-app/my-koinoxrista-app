"""Authenticated entry point for the shared company administration UI."""
from datetime import datetime, timezone

import streamlit as st

from company_auth import identity, companies_for_user
from database import tenant_scope, AccessError
from ui_theme import apply_theme, brand, account, page_header


def _configured():
    try:
        auth = st.secrets['auth']
        return all(auth[k] for k in ('redirect_uri', 'cookie_secret', 'client_id',
                                      'client_secret', 'server_metadata_url'))
    except (KeyError, FileNotFoundError):
        return False


def _clear_workspace(scope_key):
    """Discard old widgets, drafts, calculated results and pending uploads."""
    if st.session_state.get('_portal_scope') != scope_key:
        for key in list(st.session_state):
            if not key.startswith('_portal_'):
                del st.session_state[key]
        st.session_state['_portal_scope'] = scope_key


def main():
    st.set_page_config(page_title='Koinoxrista | Εταιρικός χώρος', page_icon='🏢',
                       layout='wide', initial_sidebar_state='expanded')
    apply_theme()
    if not _configured():
        st.error('Δεν έχει ρυθμιστεί η πιστοποίηση OIDC.')
        st.stop()

    if not st.user.is_logged_in:
        st.markdown('<div class="k-login-shell"></div>', unsafe_allow_html=True)
        left, right = st.columns([1, 1.05], gap='large', vertical_alignment='center')
        with left:
            brand('Εταιρικός χώρος')
            st.markdown('<div class="k-eyebrow">Καλώς ήρθατε</div>'
                        '<h1 class="k-login-title">Η διαχείριση των κτηρίων σας<br>σε έναν χώρο.</h1>'
                        '<p class="k-login-copy">Οργανώστε τις πολυκατοικίες, τις δαπάνες και τις εκκαθαρίσεις '
                        'της εταιρείας σας σε έναν ενιαίο χώρο.</p>', unsafe_allow_html=True)
            if st.button('Σύνδεση με Google', type='primary', use_container_width=True,
                         key='_portal_login'):
                st.login()
            st.markdown('<p class="k-login-foot">Ασφαλής είσοδος μέσω Google. '
                        'Η πρόσβαση στα δεδομένα απαιτεί έγκριση από την εταιρεία σας.</p>',
                        unsafe_allow_html=True)
        with right:
            st.markdown('<div class="k-login-scene" role="img" '
                        'aria-label="Διακριτική αρχιτεκτονική απεικόνιση πολυκατοικίας"></div>',
                        unsafe_allow_html=True)
        st.stop()

    try:
        issuer, subject = identity(st.user)
        exp = st.user.get('exp')
        if exp is None or datetime.now(timezone.utc).timestamp() >= float(exp):
            st.warning('Η συνεδρία έληξε. Συνδεθείτε ξανά.')
            st.logout()
            st.stop()
        companies = companies_for_user(issuer, subject)
    except (ValueError, AccessError):
        st.error('Η ταυτότητα ή η εταιρική πρόσβαση δεν είναι έγκυρη.')
        st.stop()

    if not companies:
        page_header('Δεν υπάρχει εταιρική πρόσβαση',
                    'Ο λογαριασμός σας έχει συνδεθεί, αλλά δεν ανήκει ακόμη σε εταιρεία.')
        st.info('Ζητήστε από τον διαχειριστή να συνδέσει τον λογαριασμό σας με εταιρεία.')
        with st.expander('Στοιχεία ταυτότητας για τον διαχειριστή'):
            st.caption('Τα αναγνωριστικά προέρχονται από την πιστοποιημένη σύνδεση. '
                       'Δεν είναι κωδικοί και δεν παρέχουν από μόνα τους πρόσβαση.')
            st.code(f'iss: {issuer}\nsub: {subject}', language='text')
            st.caption('Ο διαχειριστής τα χρησιμοποιεί στο offline company_admin.py '
                       'για να εγκρίνει την πρόσβαση. Μην στείλετε ID tokens ή άλλα credentials.')
        if st.button('Αποσύνδεση'):
            st.logout()
        st.stop()

    options = {str(cid): name for cid, name in companies}
    with st.sidebar:
        brand()
        account(st.user.get('name') or 'Συνδεδεμένος χρήστης', st.user.get('email', ''))
        st.markdown('<div class="k-side-label">Εταιρικός χώρος</div>', unsafe_allow_html=True)
        if len(options) == 1:
            selected = next(iter(options))
            st.caption(options[selected])
            # Keep the choice stable without creating an unnecessary dropdown.
            st.session_state['_portal_company'] = selected
        else:
            if st.session_state.get('_portal_company') not in options:
                st.session_state['_portal_company'] = next(iter(options))
            selected = st.selectbox('Εταιρεία', list(options),
                format_func=lambda key: options[key], key='_portal_company',
                label_visibility='collapsed')
        st.caption('Κοινά δεδομένα για όλους τους χρήστες της εταιρείας.')
        st.divider()
        if st.button('Αποσύνδεση', use_container_width=True, key='_portal_logout'):
            st.logout()

    _clear_workspace((issuer, subject, selected))
    # The selected company comes only from the verified membership lookup.
    try:
        with tenant_scope(issuer, subject, selected):
            from KoinoxristaAPP import render
            render()
    except AccessError:
        st.error('Η εταιρική πρόσβαση δεν είναι πλέον διαθέσιμη.')
        st.stop()


if __name__ == '__main__':
    main()
