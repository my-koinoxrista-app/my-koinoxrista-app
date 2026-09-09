"""Final recipient review and manual SMTP submission for one issued revision."""
import smtplib
import ssl
import os
from pathlib import Path

from dotenv import dotenv_values

import pandas as pd
import streamlit as st
from psycopg import Error as DatabaseError

import statement_delivery as delivery
from statement_store import get_property_pdf, StatementError

STATUS = {
    'PENDING':'Έτοιμο - δεν στάλθηκε',
    'SENDING':'Σε εξέλιξη / απαιτείται έλεγχος',
    'ACCEPTED':'Αποδεκτό από SMTP',
    'FAILED':'Απορρίφθηκε - δυνατότητα επανάληψης',
    'UNKNOWN':'Αβέβαιο - απαιτείται έλεγχος',
}


def _money(value):
    return f'{float(value):,.2f} €'


def _filename(statement,code):
    safe=''.join(c if c.isalnum() or c in '-_' else '_' for c in code)[:50]
    return f"koinoxrista_{statement['period_key']}_v{statement['revision']}_{safe}.pdf"


def render_contacts(building_id):
    st.subheader('Ένοικοι & email')
    st.caption('Τα στοιχεία αποθηκεύονται στην επιλεγμένη εταιρεία. Δεν επηρεάζουν χιλιοστά ή υπολογισμούς.')
    contacts=delivery.list_contacts(building_id)
    with st.form(f'email_contacts_{building_id}'):
        rows=pd.DataFrame([{'id':c['id'],'Ιδιοκτησία':c['code'],
            'Ένοικος':c['tenant_name'],'Email':c['email'],'Ενεργή αποστολή':c['enabled']}
            for c in contacts])
        edited=st.data_editor(rows,hide_index=True,use_container_width=True,num_rows='fixed',
            disabled=['id','Ιδιοκτησία'],column_order=['Ιδιοκτησία','Ένοικος','Email','Ενεργή αποστολή'],
            column_config={'id':None,'Ένοικος':st.column_config.TextColumn('Ένοικος',max_chars=200),
                'Email':st.column_config.TextColumn('Email',max_chars=254),
                'Ενεργή αποστολή':st.column_config.CheckboxColumn('Ενεργή αποστολή')},
            key=f'email_contacts_editor_{building_id}')
        st.caption('Ενεργοποίησε την αποστολή μόνο για διευθύνσεις που έχεις ελέγξει και δικαιούσαι να χρησιμοποιείς.')
        saved=st.form_submit_button('Αποθήκευση στοιχείων επικοινωνίας',type='primary')
    if saved:
        try:
            def clean(value):
                return '' if value is None or pd.isna(value) else str(value)
            delivery.save_contacts(building_id,[{'id':r['id'],'tenant_name':clean(r['Ένοικος']),
                'email':clean(r['Email']),'enabled':bool(r['Ενεργή αποστολή'])} for r in edited.to_dict('records')])
            st.success('Τα στοιχεία επικοινωνίας αποθηκεύτηκαν.')
            st.rerun()
        except (delivery.DeliveryError,DatabaseError,KeyError,TypeError) as exc:
            st.error(str(exc))


def _company_settings():
    from database import current_tenant
    company=current_tenant()
    with st.expander('Ρυθμίσεις αποστολέα'):
        settings=delivery.company_settings()
        st.caption('Η αποστολή γίνεται από τον επιλεγμένο λογαριασμό email. Το Reply-To καθορίζει πού φτάνουν οι απαντήσεις.')
        with st.form(f'company_mail_settings_{company}'):
            name=st.text_input('Όνομα αποστολέα / εταιρείας',settings['display_name'],key=f'mail_name_{company}')
            reply=st.text_input('Reply-To (προαιρετικό)',settings['reply_to'],key=f'mail_reply_{company}')
            saved=st.form_submit_button('Αποθήκευση ρυθμίσεων')
        if saved:
            try:
                delivery.save_company_settings(name,reply)
                st.success('Οι ρυθμίσεις αποθηκεύτηκαν.')
                st.rerun()
            except (delivery.DeliveryError,DatabaseError) as exc:
                st.error(str(exc))



def _smtp_test_panel(sid):
    """Allow a harmless SMTP test even when production delivery is disabled."""
    with st.expander('Δοκιμή σύνδεσης Gmail / SMTP', expanded=True):
        try:
            delivery.smtp_settings()
        except delivery.DeliveryError as exc:
            st.warning(str(exc))
        st.caption('Στέλνει μόνο δοκιμαστικό κείμενο, χωρίς PDF ή στοιχεία ενοίκων. Δεν ενεργοποιεί την αποστολή κοινοχρήστων.')
        with st.form(f'smtp_test_{sid}'):
            test_email=st.text_input('Δοκιμαστικός παραλήπτης (δικό σου email)',key=f'smtp_test_recipient_{sid}')
            test_sent=st.form_submit_button('Αποστολή δοκιμαστικού email χωρίς PDF')
        if test_sent:
            try:
                delivery.send_test_email(test_email)
            except delivery.DeliveryError as exc:
                st.error(str(exc))
            except ssl.SSLCertVerificationError:
                st.error('Η επαλήθευση πιστοποιητικού TLS απέτυχε. Έλεγξε το CA bundle και τις ρυθμίσεις δικτύου. Μην απενεργοποιήσεις την επαλήθευση SSL.')
            except smtplib.SMTPAuthenticationError:
                st.error('Το Gmail απέρριψε τα στοιχεία σύνδεσης. Έλεγξε τον λογαριασμό και τον κωδικό εφαρμογής στο .env.')
            except smtplib.SMTPRecipientsRefused:
                st.error('Ο SMTP server απέρριψε τον δοκιμαστικό παραλήπτη.')
            except (smtplib.SMTPException, ssl.SSLError, OSError, TimeoutError):
                st.error('Η δοκιμή SMTP απέτυχε. Έλεγξε τη σύνδεση, τις ρυθμίσεις και τα logs. Δεν στάλθηκε PDF.')
            else:
                st.success('Ο SMTP server αποδέχτηκε το δοκιμαστικό μήνυμα. Έλεγξε τα Εισερχόμενα και το Spam πριν ενεργοποιήσεις πραγματικές αποστολές.')


def _smtp_diagnostics():
    """Read-only diagnostics; never expose SMTP credentials or secret values."""
    module_file = Path(delivery.__file__).resolve()
    env_file = Path(getattr(delivery, 'SMTP_ENV_FILE', module_file.parent / '.env'))
    file_values = dotenv_values(env_file) if env_file.is_file() else {}
    flag = 'KOINOXRISTA_EMAIL_SENDING_ENABLED'
    file_enabled = str(file_values.get(flag, '')).strip().lower() == 'true'
    process_enabled = os.environ.get(flag, '').strip().lower() == 'true'
    effective_enabled = None
    config_reader = getattr(delivery, '_smtp_config', None)
    if callable(config_reader):
        # Read only the enable flag from the service's effective configuration.
        effective_enabled = str(config_reader().get(flag, '')).strip().lower() == 'true'
    available = delivery.smtp_settings_available()
    ready = delivery.smtp_ready()
    return ready, {
        'UI version': 'smtp-20260909-1',
        'Delivery version': getattr(delivery, 'SMTP_CONFIG_VERSION', 'older version'),
        'Delivery module': str(module_file),
        'SMTP configuration file': str(env_file),
        'Configuration file exists': env_file.is_file(),
        'Enabled in file': file_enabled,
        'Enabled in process': process_enabled,
        'Effective enabled': effective_enabled if effective_enabled is not None else 'Not exposed by service',
        'SMTP settings available': available,
        'SMTP ready': ready,
    }


def _preview_table(entries):
    return pd.DataFrame([{
        'Ιδιοκτησία':e['code'],'Ένοικος':e['tenant_name'] or '—',
        'Email':e['email'] or '—','Χρέωση €':float(e['property']['total']),
        'Κατάσταση': STATUS.get(e['delivery']['status'],'—') if e['delivery'] else
                     ('Διαθέσιμο' if e['enabled'] and e['email'] and e['tenant_name'] and e['name_matches'] else
                      'Έλεγχος ονόματος ενοίκου' if not e['name_matches'] else 'Χωρίς ενεργό email')}
        for e in entries])


def render(building_id,statement):
    from smtp_profiles import sender_scope, profiles
    from database import current_tenant
    try:
        accounts = delivery.sender_accounts()
        if len(accounts) < 1 + len(profiles()):
            st.info('Υπάρχουν λογαριασμοί αποστολής με ελλιπείς ρυθμίσεις SMTP. Θα εμφανιστούν στην επιλογή όταν ολοκληρωθεί η ρύθμισή τους.')
        if not accounts:
            st.warning('Δεν υπάρχει διαθέσιμος λογαριασμός αποστολής.')
            _smtp_test_panel(statement['id'])
            return
        options = {a['id']: a for a in accounts}
        key = f'smtp_sender_{current_tenant()}_{building_id}'
        if st.session_state.get(key) not in options:
            st.session_state[key] = next(iter(options))
        chosen = st.selectbox('Αποστολή από', list(options),
            format_func=lambda pid: options[pid]['email'], key=key)
        st.caption('Οι ήδη προετοιμασμένες αποστολές κρατούν τον αρχικό αποστολέα τους.')
        with sender_scope(chosen):
            _render_delivery(building_id, statement)
    except (ValueError, DatabaseError) as exc:
        st.error(str(exc))


def _render_delivery(building_id,statement):
    """Called only within the authenticated CompanyPortal tenant scope."""
    sid=statement['id']
    st.caption('Αποστολή μόνο από οριστικοποιημένη εκκαθάριση. Κάθε ένοικος λαμβάνει μόνο το PDF της ιδιοκτησίας του.')
    st.info('Τα ονόματα και email των ενοίκων καταχωρίζονται από «Στοιχεία & χιλιοστά → Ένοικοι & email». Εδώ γίνεται μόνο η τελική προεπισκόπηση και αποστολή.')
    _company_settings()
    st.divider()
    data=delivery.preview(building_id,sid)
    entries=data['entries']
    st.subheader('Τελική προεπισκόπηση')
    st.dataframe(_preview_table(entries),hide_index=True,use_container_width=True)
    st.caption('Η αποστολή δεν αποτελεί επιβεβαίωση εξόφλησης. Η παρακολούθηση πληρωμών θα προστεθεί ξεχωριστά.')
    for entry in entries:
        p=entry['property']
        with st.expander(f"{entry['code']} · {entry['tenant_name'] or 'Χωρίς όνομα'} · {_money(p['total'])}"):
            st.write(f"Ενοίκου: {_money(p['totals']['TENANT'])} · Ιδιοκτήτη: {_money(p['totals']['OWNER'])} · Λοιπές: {_money(p['totals']['OTHER'])}")
            st.caption('Το PDF έχει αποθηκευτεί στην εκδοθείσα εκκαθάριση. Δεν επανυπολογίζεται.')
            if not entry['name_matches']:
                st.warning('Το όνομα ενοίκου στο εκδοθέν PDF διαφέρει από τον σημερινό παραλήπτη. Η αποστολή μπλοκάρεται για να αποφύγουμε αποστολή παλιών χρεώσεων σε νέο ένοικο.')
            st.download_button('Προεπισκόπηση / λήψη ατομικού PDF',
                get_property_pdf(building_id,sid,entry['apartment_id'])['pdf'],
                file_name=_filename(data['statement'],entry['code']),mime='application/pdf',
                key=f"email_preview_pdf_{sid}_{entry['apartment_id']}")
            if entry['delivery']:
                d=entry['delivery']
                st.info(f"{STATUS.get(d['status'],d['status'])} · {d['email']}")
                if d['error']:
                    st.caption(d['error'])
                if d['status']=='FAILED':
                    st.warning('Επανάλαβε μόνο αφού ελέγξεις τον παραλήπτη και την προηγούμενη αποτυχία.')
                    confirmed=st.checkbox('Έλεγξα την αποτυχία και εγκρίνω νέα προσπάθεια',
                        key=f"retry_confirm_{d['id']}")
                    if st.button('Επανάληψη αποτυχημένης αποστολής',disabled=not confirmed or not delivery.smtp_ready(),
                                 key=f"retry_delivery_{d['id']}"):
                        results=delivery.send_prepared(building_id,[d['id']])
                        _results(results)
                        st.rerun()
    pending=[e for e in entries if e['delivery'] and e['delivery']['status']=='PENDING']
    if pending:
        with st.expander('Έτοιμες αποστολές που δεν έχουν σταλεί'):
            st.warning('Τα παρακάτω email έχουν ήδη προετοιμαστεί με κλειδωμένο παραλήπτη και PDF. Ελέγξτε τα πριν την αποστολή.')
            for e in pending:
                st.write(f"{e['code']} · {e['delivery']['email']}")
                approved=st.checkbox('Εγκρίνω την αποστολή',key=f"pending_confirm_{e['delivery']['id']}")
                if st.button('Αποστολή προετοιμασμένου email',disabled=not approved or not delivery.smtp_ready(),
                             key=f"pending_send_{e['delivery']['id']}"):
                    _results(delivery.send_prepared(building_id,[e['delivery']['id']]))
                    st.rerun()
    ready, diagnostics = _smtp_diagnostics()
    with st.expander('Διαγνωστικός έλεγχος SMTP', expanded=not ready):
        st.json(diagnostics)
        st.caption('Δεν εμφανίζονται κωδικοί, API keys ή στοιχεία ενοίκων.')
    if not ready:
        st.info('Η εφαρμογή δεν αναγνωρίζει ενεργή αποστολή. Δες τον διαγνωστικό έλεγχο παραπάνω. Δεν χρειάζεται νέα εκκαθάριση ή επανάληψη του δοκιμαστικού.')
        _smtp_test_panel(sid)
        return
    _smtp_test_panel(sid)
    selectable=[e for e in entries if e['enabled'] and e['email'] and e['tenant_name']
                and e['name_matches'] and e['delivery'] is None]
    if not selectable:
        st.info('Δεν υπάρχουν νέοι ενεργοί παραλήπτες για αυτή την έκδοση.')
        return
    st.caption('Προεπιλέγονται οι ενεργοί παραλήπτες από «Στοιχεία & χιλιοστά → Ένοικοι & email». Αφαίρεσε όποιον δεν θέλεις να λάβει αυτή την έκδοση. Ελέγξτε πρώτα τυχόν προηγούμενες αποστολές.')
    options={e['apartment_id']:e for e in selectable}
    selected=st.multiselect('Επιλογή παραληπτών για αυτή την έκδοση',list(options),
        default=list(options),
        format_func=lambda aid:f"{options[aid]['code']} · {options[aid]['tenant_name'] or options[aid]['email']}",
        key=f'email_select_{sid}')
    if not selected:
        return
    if len(selected)>delivery.MAX_BATCH:
        st.error('Μπορείς να στείλεις έως 25 email ανά ενέργεια.')
        return
    chosen=[options[aid] for aid in selected]
    sender = delivery.smtp_settings()
    st.write('Αποστολέας: ' + sender['sender'])
    st.dataframe(_preview_table(chosen),hide_index=True,use_container_width=True)
    st.caption('Αυτή είναι η τελική λίστα. Για παλιές εκκαθαρίσεις χωρίς αποθηκευμένο όνομα ενοίκου, επιβεβαίωσε ιδιαίτερα ότι ο παραλήπτης αφορά την περίοδο που εκδόθηκε.')
    fingerprint=delivery.approval_fingerprint(data,selected)
    approval_key=f'email_approval_{building_id}_{sid}'
    if st.session_state.get(approval_key)!=fingerprint:
        st.session_state[approval_key]=fingerprint
        st.session_state.pop(approval_key+'_confirmed',None)
    approved=st.checkbox('Έλεγξα τους παραλήπτες και τα ατομικά PDF και εγκρίνω την αποστολή.',
                         key=approval_key+'_confirmed')
    if st.button(f'Αποστολή σε {len(selected)} παραλήπτες',type='primary',
                 disabled=not approved,key=f'email_send_{sid}'):
        try:
            ids=delivery.prepare(building_id,sid,selected,fingerprint)
            st.session_state.pop(approval_key+'_confirmed',None)
            _results(delivery.send_prepared(building_id,ids))
            st.rerun()
        except (delivery.DeliveryError,StatementError,DatabaseError,ValueError) as exc:
            st.error(str(exc))
            st.warning('Αν η αποστολή έχει ξεκινήσει, μην την επαναλάβεις πριν ελέγξεις το Ιστορικό αποστολών.')


def _results(results):
    for r in results:
        if r['status']=='ACCEPTED':
            st.success(f"{r['email']}: αποδεκτό από τον SMTP server.")
        elif r['status']=='FAILED':
            st.error(f"{r['email']}: ο παραλήπτης απορρίφθηκε.")
        else:
            st.warning(f"{r['email']}: αβέβαιο αποτέλεσμα. Μην επαναλάβεις χωρίς έλεγχο.")
