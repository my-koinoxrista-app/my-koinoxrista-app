"""Tenant-scoped recipient management and audited, explicitly approved SMTP delivery.

The SMTP operation is outside database transactions. A committed claim prevents
automatic duplicate submissions. ACCEPTED means accepted by the SMTP server,
not delivered to an inbox, read, or paid.
"""
import hashlib
from html import escape
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from uuid import UUID, uuid4
from pathlib import Path

import certifi
from dotenv import dotenv_values

# SMTP configuration is read explicitly. Do not mutate the process environment:
# database credentials, OIDC and tenant context are unrelated to email settings.
SMTP_CONFIG_VERSION = "smtp-20260909-1"
SMTP_ENV_FILE = Path(__file__).resolve().parent / ".env"
SMTP_KEYS = (
    'SMTP_HOST', 'SMTP_PORT', 'SMTP_SECURITY', 'SMTP_USERNAME',
    'SMTP_PASSWORD', 'SMTP_FROM_EMAIL', 'SMTP_FROM_NAME',
)


def _smtp_config():
    """Local SMTP values override stale inherited values; other env is untouched.

    A deployment without a local .env continues to use its injected environment.
    Read each time so the enable flag cannot remain cached after configuration.
    """
    keys = {'KOINOXRISTA_' + key for key in SMTP_KEYS}
    keys.add('KOINOXRISTA_EMAIL_SENDING_ENABLED')
    values = {key: os.environ.get(key, '') for key in keys}
    if SMTP_ENV_FILE.is_file():
        values.update({key: value for key, value in dotenv_values(SMTP_ENV_FILE).items()
                       if key in keys and value is not None})
    from smtp_profiles import selected_values
    values.update(selected_values())
    return values


from database import current_tenant, get_connection
from statement_store import ensure_property_pdfs, list_property_pdfs, StatementError

MAX_BATCH = 25
MAX_PDF_BYTES = 10_000_000


class DeliveryError(ValueError):
    pass


def _text(value, limit=200):
    value = ' '.join(str(value or '').split())
    if len(value)>limit or any(ord(c)<32 or ord(c)==127 for c in value):
        raise DeliveryError('Μη έγκυρο ή υπερβολικά μεγάλο κείμενο.')
    return value


def _email(value, required=False):
    value = str(value or '').strip()
    if not value and not required:
        return ''
    if (len(value)>254 or not value or any(ord(c)<32 or ord(c)==127 for c in value)
            or parseaddr(value)[1]!=value or value.count('@')!=1):
        raise DeliveryError('Συμπλήρωσε μία έγκυρη διεύθυνση email.')
    local,domain = value.rsplit('@',1)
    if (len(local)>64 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+",local)
            or local.startswith('.') or local.endswith('.') or '..' in local):
        raise DeliveryError('Μη έγκυρη διεύθυνση email.')
    try:
        domain = domain.encode('idna').decode('ascii').lower()
    except UnicodeError:
        raise DeliveryError('Μη έγκυρο domain email.') from None
    if (len(domain)>253 or '.' not in domain or any(not re.fullmatch(
            r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',p) for p in domain.split('.'))):
        raise DeliveryError('Μη έγκυρο domain email.')
    return local+'@'+domain


def _uuid(value):
    try:
        return str(UUID(str(value)))
    except (TypeError,ValueError,AttributeError):
        raise DeliveryError('Μη έγκυρο αναγνωριστικό.') from None


def smtp_settings():
    config = _smtp_config()
    values = {key: config.get('KOINOXRISTA_' + key, '').strip()
              for key in SMTP_KEYS}
    # Preserve the password exactly as supplied by the provider.
    values['SMTP_PASSWORD'] = config.get('KOINOXRISTA_SMTP_PASSWORD', '')
    if not values['SMTP_HOST'] or any(c.isspace() for c in values['SMTP_HOST']):
        raise DeliveryError('Δεν έχει ρυθμιστεί SMTP host.')
    security = values['SMTP_SECURITY'].lower()
    if security not in ('ssl', 'starttls'):
        raise DeliveryError('Απαιτείται κρυπτογραφημένη SMTP σύνδεση ssl ή starttls.')
    try:
        port = int(values['SMTP_PORT'] or ('465' if security == 'ssl' else '587'))
    except ValueError:
        raise DeliveryError('Μη έγκυρη θύρα SMTP.') from None
    if not 1 <= port <= 65535 or not values['SMTP_USERNAME'] or not values['SMTP_PASSWORD']:
        raise DeliveryError('Λείπουν έγκυρα SMTP credentials.')
    return {'host': values['SMTP_HOST'], 'port': port, 'security': security,
            'username': values['SMTP_USERNAME'], 'password': values['SMTP_PASSWORD'],
            'sender': _email(values['SMTP_FROM_EMAIL'], True),
            'sender_name': _text(values['SMTP_FROM_NAME'] or 'Koinoxrista')}


def smtp_settings_available():
    try:
        smtp_settings()
        return True
    except DeliveryError:
        return False


def smtp_ready():
    if _smtp_config().get('KOINOXRISTA_EMAIL_SENDING_ENABLED', '').strip().lower() != 'true':
        return False
    return smtp_settings_available()


def sender_accounts():
    """Return display-only account details; never expose credentials to the UI."""
    from smtp_profiles import profiles, sender_scope
    accounts = []
    for profile_id in ['default', *profiles()]:
        try:
            with sender_scope(profile_id):
                settings = smtp_settings()
            accounts.append({'id': profile_id, 'email': settings['sender'],
                             'name': settings['sender_name']})
        except DeliveryError:
            continue
    addresses = [a['email'].casefold() for a in accounts]
    if len(addresses) != len(set(addresses)):
        raise DeliveryError('Κάθε λογαριασμός αποστολής πρέπει να έχει διαφορετική διεύθυνση email.')
    return accounts


def _settings_for_sender(address):
    """Prepared messages keep their approved sender even after UI selection changes."""
    from smtp_profiles import sender_scope
    for account in sender_accounts():
        if account['email'].casefold() == address.casefold():
            with sender_scope(account['id']):
                return smtp_settings()
    raise DeliveryError('Ο αρχικός αποστολέας δεν είναι πλέον ρυθμισμένος. Η αποστολή ακυρώθηκε.')


def company_settings():
    company = current_tenant()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT display_name,reply_to FROM company_mail_settings WHERE company_id=%s',(company,))
        row = cur.fetchone()
        if row is None:
            cur.execute('SELECT name FROM companies WHERE id=%s',(company,))
            row = cur.fetchone()
            if row is None:
                raise DeliveryError('Η εταιρεία δεν βρέθηκε.')
            return {'display_name':row[0],'reply_to':''}
    return {'display_name':row[0],'reply_to':row[1]}


def save_company_settings(display_name,reply_to):
    company = current_tenant()
    name,reply = _text(display_name),_email(reply_to)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''INSERT INTO company_mail_settings(company_id,display_name,reply_to)
            VALUES(%s,%s,%s) ON CONFLICT(company_id) DO UPDATE SET
            display_name=EXCLUDED.display_name,reply_to=EXCLUDED.reply_to,updated_at=now()''',(company,name,reply))


def list_contacts(building_id):
    company = current_tenant()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT a.id,a.code,coalesce(c.tenant_name,''),coalesce(c.email,''),
            coalesce(c.email_enabled,false) FROM apartments a
            JOIN buildings b ON b.id=a.building_id
            LEFT JOIN property_delivery_contacts c ON c.building_id=a.building_id AND c.apartment_id=a.id
            WHERE b.company_id=%s AND a.building_id=%s ORDER BY a.code,a.id''',(company,building_id))
        return [{'id':r[0],'code':r[1],'tenant_name':r[2],'email':r[3],'enabled':r[4]}
                for r in cur.fetchall()]


def save_contacts(building_id,contacts):
    """Store only communication data; allocation weights and owners are untouched."""
    company = current_tenant()
    if not isinstance(contacts,list) or len(contacts)>10000:
        raise DeliveryError('Μη έγκυρα στοιχεία επικοινωνίας.')
    validated=[]
    seen=set()
    for item in contacts:
        aid=item['id']
        if not isinstance(aid,str) or aid in seen:
            raise DeliveryError('Διπλή ή μη έγκυρη ιδιοκτησία.')
        seen.add(aid)
        name,email = _text(item.get('tenant_name')),_email(item.get('email'))
        enabled = bool(item.get('enabled',False))
        if enabled and (not email or not name):
            raise DeliveryError('Συμπλήρωσε όνομα και email για τους ενεργούς παραλήπτες.')
        validated.append((aid,name,email,enabled))
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT id FROM buildings WHERE id=%s AND company_id=%s FOR UPDATE',(building_id,company))
        if cur.fetchone() is None:
            raise DeliveryError('Η πολυκατοικία δεν βρέθηκε στην εταιρεία.')
        cur.execute('SELECT id FROM apartments WHERE building_id=%s',(building_id,))
        known={r[0] for r in cur.fetchall()}
        if seen-known:
            raise DeliveryError('Άγνωστη ιδιοκτησία.')
        for aid,name,email,enabled in validated:
            cur.execute('''INSERT INTO property_delivery_contacts
                (company_id,building_id,apartment_id,tenant_name,email,email_enabled)
                VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(building_id,apartment_id) DO UPDATE SET
                tenant_name=EXCLUDED.tenant_name,email=EXCLUDED.email,
                email_enabled=EXCLUDED.email_enabled,updated_at=now()''',
                (company,building_id,aid,name,email,enabled))


def list_status(building_id,statement_id):
    company=current_tenant()
    statement_id=_uuid(statement_id)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT apartment_id,id,recipient_email,status,attempt_count,accepted_at,last_error,
            recipient_name,pdf_sha256 FROM statement_email_deliveries
            WHERE company_id=%s AND building_id=%s AND statement_id=%s''',(company,building_id,statement_id))
        return {r[0]:{'id':str(r[1]),'email':r[2],'status':r[3],'attempts':r[4],
                      'accepted_at':r[5],'error':r[6],'recipient_name':r[7],'sha256':r[8]}
                for r in cur.fetchall()}


def _subject(statement):
    return _text(f"Κοινόχρηστα {statement['building_name']} - {statement['period_key']}",250)


def _body(statement,property_data,recipient_name,payment_url=''):
    salutation=f'Αγαπητέ/ή {recipient_name},' if recipient_name else 'Καλησπέρα σας,'
    payment = (f"\nΠληρωμή online:\n{payment_url}\n"
               if payment_url else "\nΟ online σύνδεσμος πληρωμής δεν είναι διαθέσιμος. Επικοινωνήστε με την εταιρεία διαχείρισης.\n")
    return (f"{salutation}\n\nΣας αποστέλλουμε την εκκαθάριση κοινοχρήστων για την "
            f"ιδιοκτησία {property_data['code']} της πολυκατοικίας {statement['building_name']}, "
            f"περιόδου {statement['period_key']}.\n\n"
            f"Συνολική χρέωση ιδιοκτησίας: {property_data['total']} €\n"
            f"Αναλυτικά: ενοίκου {property_data['totals']['TENANT']} €, "
            f"ιδιοκτήτη {property_data['totals']['OWNER']} €, "
            f"λοιπές {property_data['totals']['OTHER']} €.\n\n"
            "Το συνημμένο περιλαμβάνει μόνο τη δική σας ιδιοκτησία."
            f"{payment}"
            "Το μήνυμα δεν αποτελεί επιβεβαίωση εξόφλησης. "
            "Για διευκρινίσεις απευθυνθείτε στην εταιρεία διαχείρισης.\n")


def preview(building_id,statement_id):
    """Return all recipients and archived files; no contact is silently selected."""
    company=current_tenant()
    statement_id=_uuid(statement_id)
    documents=ensure_property_pdfs(building_id,statement_id)
    contacts={c['id']:c for c in list_contacts(building_id)}
    statuses=list_status(building_id,statement_id)
    from payment_service import list_payment_requests
    payments={p['apartment_id']:p for p in list_payment_requests(building_id,statement_id)}
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute('''SELECT s.period_key,s.revision,s.issued_at,s.report_data
            FROM issued_statements s JOIN buildings b ON b.id=s.building_id
            WHERE s.id=%s AND s.building_id=%s AND b.company_id=%s''',(statement_id,building_id,company))
        row=cur.fetchone()
    if row is None:
        raise DeliveryError('Η εκκαθάριση δεν βρέθηκε στην εταιρεία.')
    key,revision,issued_at,report=row
    statement={'id':statement_id,'period_key':key,'revision':revision,'issued_at':issued_at,
               'building_name':report['building_name'],'building_id':building_id}
    entries=[]
    for doc in documents:
        aid=doc['apartment_id']
        contact=contacts.get(aid,{'tenant_name':'','email':'','enabled':False})
        previous=statuses.get(aid)
        archived_name=doc['property'].get('tenant_name','')
        name_matches=not archived_name or archived_name.casefold()==contact['tenant_name'].casefold()
        entries.append({'apartment_id':aid,'code':doc['property']['code'],
            'property':doc['property'],'pdf_id':doc['id'],'sha256':doc['sha256'],
            'tenant_name':contact['tenant_name'],'email':contact['email'],
            'enabled':contact['enabled'],'name_matches':name_matches,
            'archived_tenant_name':archived_name,'delivery':previous})
        entries[-1]['payment_url']=payments.get(aid,{}).get('checkout_url','')
        entries[-1]['payment_status']=payments.get(aid,{}).get('status','UNPAID')
    return {'statement':statement,'entries':entries}


def _approval_fingerprint(preview_data,selected,settings,smtp):
    """Bind final approval to exact issue, recipient, PDF and sender information."""
    import json
    payload={'statement':preview_data['statement']['id'],'company':current_tenant(),
             'selected':sorted([(e['apartment_id'],e['email'],e['tenant_name'],e['sha256'],e['pdf_id'],e['property'],e.get('payment_url',''))
                         for e in preview_data['entries'] if e['apartment_id'] in selected],key=lambda x:x[0]),
             'sender':smtp['sender'],'sender_name':smtp['sender_name'],
             'smtp_host':smtp['host'],'smtp_port':smtp['port'],'smtp_security':smtp['security'],
             'display_name':settings['display_name'],'reply_to':settings['reply_to']}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def prepare(building_id,statement_id,selected,expected_fingerprint):
    """Freeze the approved recipients, content and sender in one locked transaction."""
    if (not isinstance(selected,(list,tuple)) or not selected or len(selected)>MAX_BATCH
            or len(set(selected))!=len(selected)):
        raise DeliveryError('Επίλεξε έως 25 διαφορετικούς παραλήπτες.')
    if not smtp_ready():
        raise DeliveryError('Η αποστολή email δεν είναι ενεργοποιημένη.')
    smtp=smtp_settings()
    company=current_tenant()
    statement_id=_uuid(statement_id)
    selected=set(selected)
    prepared=[]
    with get_connection() as conn,conn.cursor() as cur:
        # Contact saves and issuance also lock this building. Nothing can change
        # between the final review and the insert of the immutable delivery rows.
        cur.execute('SELECT id FROM buildings WHERE id=%s AND company_id=%s FOR UPDATE',(building_id,company))
        if cur.fetchone() is None:
            raise DeliveryError('Η πολυκατοικία δεν βρέθηκε.')
        cur.execute('''SELECT s.period_key,s.revision,s.issued_at,s.report_data
            FROM issued_statements s WHERE s.id=%s AND s.building_id=%s''',(statement_id,building_id))
        row=cur.fetchone()
        if row is None:
            raise DeliveryError('Η εκκαθάριση δεν βρέθηκε.')
        key,revision,issued_at,report=row
        statement={'id':statement_id,'period_key':key,'revision':revision,'issued_at':issued_at,
                   'building_name':report['building_name'],'building_id':building_id}
        cur.execute('''SELECT p.id,p.apartment_id,p.property_data,p.pdf_sha256,
            coalesce(c.tenant_name,''),coalesce(c.email,''),coalesce(c.email_enabled,false)
            FROM issued_property_pdfs p LEFT JOIN property_delivery_contacts c
              ON c.building_id=p.building_id AND c.apartment_id=p.apartment_id
            WHERE p.company_id=%s AND p.building_id=%s AND p.statement_id=%s''',
            (company,building_id,statement_id))
        from payment_service import list_payment_requests
        payments={p['apartment_id']:p for p in list_payment_requests(building_id,statement_id)}
        entries=[]
        for pid,aid,property_data,digest,name,email,enabled in cur.fetchall():
            entries.append({'apartment_id':aid,'code':property_data['code'],'property':property_data,
                            'pdf_id':str(pid),'sha256':digest,'tenant_name':name,'email':email,
                            'enabled':enabled,'delivery':None,
                            'archived_tenant_name':property_data.get('tenant_name',''),
                            'name_matches':not property_data.get('tenant_name') or
                                property_data['tenant_name'].casefold()==name.casefold(),
                            'payment_url':payments.get(aid,{}).get('checkout_url','')})
        if selected-{e['apartment_id'] for e in entries}:
            raise DeliveryError('Άγνωστη ιδιοκτησία στην επιλογή.')
        cur.execute('SELECT display_name,reply_to FROM company_mail_settings WHERE company_id=%s FOR SHARE',(company,))
        row=cur.fetchone()
        if row is None:
            cur.execute('SELECT name FROM companies WHERE id=%s',(company,))
            row=cur.fetchone()
            if row is None:
                raise DeliveryError('Η εταιρεία δεν βρέθηκε.')
            settings={'display_name':row[0],'reply_to':''}
        else:
            settings={'display_name':row[0],'reply_to':row[1]}
        cur.execute('''SELECT apartment_id FROM statement_email_deliveries
                       WHERE company_id=%s AND building_id=%s AND statement_id=%s''',
                    (company,building_id,statement_id))
        existing={r[0] for r in cur.fetchall()}
        if selected & existing:
            raise DeliveryError('Υπάρχει ήδη καταγραφή αποστολής. Ελέγξτε την πριν από επανάληψη.')
        data={'statement':statement,'entries':entries}
        if _approval_fingerprint(data,selected,settings,smtp)!=expected_fingerprint:
            raise DeliveryError('Τα στοιχεία προεπισκόπησης άλλαξαν. Έλεγξέ τα ξανά.')
        for entry in entries:
            if entry['apartment_id'] not in selected:
                continue
            if not entry['enabled'] or not entry['email'] or not entry['tenant_name']:
                raise DeliveryError('Δεν έχουν ενεργοποιηθεί έγκυρα στοιχεία παραλήπτη.')
            if not entry['name_matches']:
                raise DeliveryError('Ο σημερινός ένοικος διαφέρει από το όνομα της εκδοθείσας εκκαθάρισης. Ελέγξτε την περίοδο και τον παραλήπτη.')
            email=_email(entry['email'],True)
            subject=_subject(statement)
            if not entry['payment_url']:
                raise DeliveryError('Δεν υπάρχει διαθέσιμο payment link για την ιδιοκτησία.')
            body=_body(statement,entry['property'],entry['tenant_name'],entry['payment_url'])
            delivery_id=str(uuid4())
            cur.execute('''INSERT INTO statement_email_deliveries
                (id,company_id,building_id,statement_id,apartment_id,property_pdf_id,pdf_sha256,
                 recipient_name,recipient_email,sender_name,sender_email,reply_to,email_subject,email_body,payment_url)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (delivery_id,company,building_id,statement_id,entry['apartment_id'],entry['pdf_id'],
                 entry['sha256'],_text(entry['tenant_name']),email,
                 _text(settings['display_name'] or smtp['sender_name']),smtp['sender'],
                 settings['reply_to'],subject,body,entry['payment_url']))
            prepared.append(delivery_id)
    return prepared


def _load_delivery(building_id,delivery_id):
    company=current_tenant()
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute('''SELECT d.id,d.statement_id,d.apartment_id,d.recipient_name,d.recipient_email,
            d.sender_name,d.sender_email,d.reply_to,d.email_subject,d.email_body,d.status,
            d.payment_url,d.pdf_sha256,p.pdf_data,p.pdf_sha256,s.period_key,s.revision
            FROM statement_email_deliveries d
            JOIN issued_property_pdfs p ON p.id=d.property_pdf_id AND p.statement_id=d.statement_id
            JOIN issued_statements s ON s.id=d.statement_id AND s.building_id=d.building_id
            WHERE d.id=%s AND d.company_id=%s AND d.building_id=%s''',(delivery_id,company,building_id))
        row=cur.fetchone()
    if row is None:
        raise DeliveryError('Η αποστολή δεν βρέθηκε.')
        keys=('id','statement_id','apartment_id','recipient_name','recipient_email','sender_name',
            'sender_email','reply_to','subject','body','status','payment_url','sha256','pdf',
            'stored_sha256','period_key','revision')
    d=dict(zip(keys,row))
    d['pdf']=bytes(d['pdf'])
    if (d['sha256']!=d['stored_sha256'] or hashlib.sha256(d['pdf']).hexdigest()!=d['sha256']
            or not d['pdf'].startswith(b'%PDF-') or len(d['pdf'])>MAX_PDF_BYTES):
        raise DeliveryError('Το αποθηκευμένο PDF είναι μη έγκυρο ή υπερβολικά μεγάλο.')
    return d


def _message(d):
    message=EmailMessage()
    message['From']=formataddr((d['sender_name'],d['sender_email']))
    message['To']=d['recipient_email']
    if d['reply_to']:
        message['Reply-To']=d['reply_to']
    message['Subject']=d['subject']
    message['Message-ID']=f"<{d['id']}@{d['sender_email'].rsplit('@',1)[1]}>"
    message.set_content(d['body'])
    if d['payment_url']:
        message.add_alternative(
            '<html><body><p>' + escape(d['body']).replace('\n', '<br>') +
            f'</p><p><a href="{escape(d["payment_url"], quote=True)}" '
            'style="display:inline-block;padding:12px 18px;background:#1769aa;color:#fff;'
            'text-decoration:none;border-radius:4px;font-weight:bold">Πληρωμή online</a></p></body></html>',
            subtype='html')
    message.add_attachment(d['pdf'],maintype='application',subtype='pdf',
                           filename=f"koinoxrista_{d['period_key']}_v{d['revision']}.pdf")
    return message


def _smtp_submit(message,settings):
    """Single-recipient SMTP, certificate verification and mandatory encryption."""
    context=ssl.create_default_context(cafile=certifi.where())
    if settings['security']=='ssl':
        client=smtplib.SMTP_SSL(settings['host'],settings['port'],timeout=30,context=context)
    else:
        client=smtplib.SMTP(settings['host'],settings['port'],timeout=30)
    with client:
        if settings['security']=='starttls':
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        client.login(settings['username'],settings['password'])
        refused=client.send_message(message)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)


def send_prepared(building_id,delivery_ids,submit=None):
    """Explicit synchronous submission. Never retry accepted or uncertain attempts."""
    if not isinstance(delivery_ids,(list,tuple)) or not delivery_ids or len(delivery_ids)>MAX_BATCH:
        raise DeliveryError('Μη έγκυρη επιλογή αποστολών.')
    if len(set(delivery_ids))!=len(delivery_ids):
        raise DeliveryError('Διπλή αποστολή στην επιλογή.')
    if not smtp_ready():
        raise DeliveryError('Η αποστολή email δεν είναι ενεργοποιημένη.')
    results=[]
    for raw_id in delivery_ids:
        did=_uuid(raw_id)
        # All preflight checks happen before the external SMTP operation.
        d=_load_delivery(building_id,did)
        settings=_settings_for_sender(d['sender_email'])
        if d['status'] not in ('PENDING','FAILED'):
            raise DeliveryError('Η αποστολή έχει ήδη ξεκινήσει ή ολοκληρωθεί. Δεν γίνεται αυτόματη επανάληψη.')
        message=_message(d)
        # The claim is committed before any network operation. A process crash
        # leaves SENDING and requires manual investigation rather than a retry.
        with get_connection() as conn,conn.cursor() as cur:
            cur.execute('SELECT public.koinoxrista_delivery_claim(%s::uuid,%s)',(did,d['sha256']))
            token=cur.fetchone()[0]
        status='ACCEPTED'
        error=''
        try:
            (submit or _smtp_submit)(message,settings)
        except smtplib.SMTPRecipientsRefused:
            status='FAILED'
            error='Ο SMTP server απέρριψε τον παραλήπτη.'
        except (smtplib.SMTPException,OSError,TimeoutError,ssl.SSLError):
            status='UNKNOWN'
            error='Αβέβαιο αποτέλεσμα SMTP. Ελέγξτε τον πάροχο πριν από επανάληψη.'
        except Exception:
            status='UNKNOWN'
            error='Η αποστολή διακόπηκε. Ελέγξτε τον πάροχο πριν από επανάληψη.'
        with get_connection() as conn,conn.cursor() as cur:
            cur.execute('SELECT public.koinoxrista_delivery_finish(%s::uuid,%s::uuid,%s,%s)',
                        (did,str(token),status,error))
        results.append({'id':did,'status':status,'email':d['recipient_email'],'error':error})
    return results


def approval_fingerprint(data,selected):
    """Public helper for the UI's last review, without exposing SMTP credentials."""
    return _approval_fingerprint(data,set(selected),company_settings(),smtp_settings())


def send_test_email(recipient,submit=None):
    """Explicit harmless SMTP test; never attaches financial records."""
    settings=smtp_settings()
    recipient=_email(recipient,True)
    message=EmailMessage()
    message['From']=formataddr((settings['sender_name'],settings['sender']))
    message['To']=recipient
    message['Subject']='Koinoxrista - δοκιμή email'
    message.set_content('Δοκιμή της ασφαλούς SMTP σύνδεσης. Δεν περιέχει κοινόχρηστα ή προσωπικά στοιχεία.\n')
    (submit or _smtp_submit)(message,settings)
    return True
