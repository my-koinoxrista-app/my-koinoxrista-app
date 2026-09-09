"""Immutable issued statements and their archived per-property PDFs.

No historical allocation is ever recalculated. Existing statements can receive
missing individual documents using only their original stored snapshot.
"""
import hashlib
import json
import re
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb
from configuration import load_building, load_period, parse_rule
from database import get_connection, current_tenant
from expense_engine import calculate_period, money
from pdf_generator import create_pdf


class StatementError(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _period_key(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}', value):
        raise StatementError('Μη έγκυρος μήνας εκκαθάρισης.')
    try:
        date.fromisoformat(value + '-01')
    except ValueError:
        raise StatementError('Μη έγκυρος μήνας εκκαθάρισης.') from None
    return value


def _summary(result, configuration, owner_names, tenant_names=None):
    """Copy the engine's exact monetary allocations into a portable snapshot."""
    names = configuration.get('property_names', {})
    tenant_names = tenant_names or {}
    apartments = []
    for apartment in result['building'].apartments:
        totals = {payer: Decimal('0.00') for payer in ('TENANT', 'OWNER', 'OTHER')}
        for row in result['rows']:
            totals[row['payer']] += money(row['allocations'][apartment.id])
        if sum(totals.values()) != result['apartment_totals'][apartment.id]:
            raise StatementError('Τα σύνολα υπόχρεων δεν συμφωνούν.')
        apartments.append({
            'id': apartment.id, 'code': apartment.code,
            'name': names.get(apartment.id, ''),
            'owner_name': owner_names.get(apartment.id, ''),
            'tenant_name': tenant_names.get(apartment.id, ''),
            'totals': {key: str(value) for key, value in totals.items()},
            'total': str(result['apartment_totals'][apartment.id]),
        })
    if sum((money(a['total']) for a in apartments), Decimal('0.00')) != result['grand_total']:
        raise StatementError('Το συνολικό ποσό δεν συμφωνεί.')
    rows = []
    for row in result['rows']:
        rows.append({
            'expense_id': row['expense_id'], 'category': row['category'],
            'description': row['description'], 'amount': str(money(row['amount'])),
            'payer': row['payer'], 'rule': asdict(row['rule']),
            'allocations': {aid: str(money(value)) for aid, value in row['allocations'].items()},
        })
    return {'schema_version': 2, 'rows': rows,
            'building_name': result['building'].name,
            'building_address': result['building'].address,
            'start_date': result['period'].start_date,
            'end_date': result['period'].end_date,
            'grand_total': str(result['grand_total']), 'apartments': apartments}


def _snapshot_result(configuration, period_data, report):
    """Restore stored allocations into the renderer's shape, without the engine."""
    building = load_building(configuration)
    period = load_period(period_data)
    ids = {a.id for a in building.apartments}
    summaries = {a['id']: a for a in report['apartments']}
    if len(summaries) != len(report['apartments']) or ids != set(summaries):
        raise StatementError('Το snapshot δεν συμφωνεί με τις ιδιοκτησίες.')
    if (report['building_name'] != building.name or
            report['building_address'] != building.address or
            report['start_date'] != period.start_date or
            report['end_date'] != period.end_date):
        raise StatementError('Τα στοιχεία του snapshot δεν συμφωνούν.')
    totals = {aid: money(summaries[aid]['total']) for aid in ids}
    rows = []
    accumulated = {aid: Decimal('0.00') for aid in ids}
    payer_totals = {aid: {p: Decimal('0.00') for p in ('TENANT','OWNER','OTHER')} for aid in ids}
    for raw in report['rows']:
        allocations = {aid: money(value) for aid, value in raw['allocations'].items()}
        if set(allocations) != ids or sum(allocations.values()) != money(raw['amount']):
            raise StatementError('Μη πλήρης ή ασύμφωνη κατανομή στο snapshot.')
        if raw['payer'] not in ('TENANT', 'OWNER', 'OTHER'):
            raise StatementError('Μη έγκυρος υπόχρεος στο snapshot.')
        rule = parse_rule(raw['rule'])
        for aid, amount in allocations.items():
            accumulated[aid] += amount
            payer_totals[aid][raw['payer']] += amount
        rows.append({**raw, 'amount': money(raw['amount']), 'rule': rule,
                     'allocations': allocations})
    if accumulated != totals or sum(totals.values()) != money(report['grand_total']):
        raise StatementError('Τα σύνολα του snapshot δεν συμφωνούν.')
    for aid, item in summaries.items():
        if payer_totals[aid] != {p: money(item['totals'][p]) for p in payer_totals[aid]}:
            raise StatementError('Τα σύνολα υπόχρεων του snapshot δεν συμφωνούν.')
    return {'building': building, 'period': period, 'rows': rows,
            'apartment_totals': totals, 'grand_total': money(report['grand_total'])}


def _issued_label(issued_at, revision):
    return f"Εκδόθηκε: {issued_at.astimezone(ZoneInfo('Europe/Athens')):%d/%m/%Y %H:%M} · Έκδοση {revision}"


def _archive_documents(cur, company, building_id, statement_id, configuration,
                       period_data, owner_names, report, issued_at, revision):
    """Insert all missing immutable documents; abort if an existing archive differs."""
    result = _snapshot_result(configuration, period_data, report)
    owners = {a['id']: a.get('owner_name', '') for a in report['apartments']}
    tenants = {a['id']: a.get('tenant_name', '') for a in report['apartments']}
    if owners != owner_names:
        # Legacy snapshots may omit empty owner-name keys; compare normalized values.
        if {k:v for k,v in owners.items() if v} != {k:v for k,v in owner_names.items() if v}:
            raise StatementError('Τα ονόματα του snapshot δεν συμφωνούν.')
    cur.execute('''SELECT apartment_id,property_data,pdf_data,pdf_sha256 FROM issued_property_pdfs
                   WHERE company_id=%s AND building_id=%s AND statement_id=%s''',
                (company, building_id, statement_id))
    existing = {}
    for aid, data, content, digest in cur.fetchall():
        content = bytes(content)
        if hashlib.sha256(content).hexdigest() != digest or not content.startswith(b'%PDF-'):
            raise StatementError('Το αποθηκευμένο ατομικό PDF δεν συμφωνεί με το αποτύπωμά του.')
        original = next((a for a in report['apartments'] if a['id'] == aid), None)
        if original is None or _canonical(data) != _canonical(original):
            raise StatementError('Το ατομικό αρχείο δεν συμφωνεί με το snapshot.')
        existing[aid] = digest
    ids = {a.id for a in result['building'].apartments}
    if set(existing) - ids:
        raise StatementError('Το αρχείο PDF περιέχει άγνωστη ιδιοκτησία.')
    for apartment in result['building'].apartments:
        if apartment.id in existing:
            continue
        content = create_pdf(result, period_data=period_data, configuration=configuration,
                             owner_names=owners, tenant_names=tenants,
                             issued_label=_issued_label(issued_at, revision),
                             property_ids=[apartment.id])
        if not content.startswith(b'%PDF-') or not content:
            raise StatementError('Δεν δημιουργήθηκε έγκυρο ατομικό PDF.')
        digest = hashlib.sha256(content).hexdigest()
        cur.execute('''INSERT INTO issued_property_pdfs
            (id,company_id,building_id,statement_id,apartment_id,property_data,pdf_data,pdf_sha256)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
            (str(uuid4()),company,building_id,statement_id,apartment.id,
             Jsonb(next(a for a in report['apartments'] if a['id']==apartment.id)),content,digest))


def issue_statement(building_id, period_key, reviewed_config, reviewed_period, owner_names=None):
    """Atomically issue the master and all individual PDFs from one calculation."""
    _period_key(period_key)
    if not isinstance(building_id, str) or not building_id.strip():
        raise StatementError('Επίλεξε πολυκατοικία.')
    owner_names = owner_names or {}
    if not isinstance(owner_names, dict) or any(not isinstance(k,str) or not isinstance(v,str)
                                               for k,v in owner_names.items()):
        raise StatementError('Μη έγκυρα ονόματα ιδιοκτητών.')
    owner_names = {k:v.strip() for k,v in owner_names.items() if v.strip()}
    company = current_tenant()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT configuration FROM buildings WHERE id=%s AND company_id=%s FOR UPDATE',
                    (building_id,company))
        row = cur.fetchone()
        if row is None:
            raise StatementError('Η πολυκατοικία δεν βρέθηκε.')
        configuration = row[0]
        if _canonical(configuration) != _canonical(reviewed_config):
            raise StatementError('Η παραμετροποίηση άλλαξε. Αποθήκευσε και υπολόγισε ξανά.')
        cur.execute('SELECT data FROM monthly_periods WHERE building_id=%s AND period_key=%s FOR UPDATE',
                    (building_id,period_key))
        row = cur.fetchone()
        if row is None:
            raise StatementError('Δεν υπάρχει αποθηκευμένη περίοδος.')
        period_data = row[0]
        if _canonical(period_data) != _canonical(reviewed_period):
            raise StatementError('Οι δαπάνες ή οι ημερομηνίες άλλαξαν. Αποθήκευσε και υπολόγισε ξανά.')
        building = load_building(configuration)
        period = load_period(period_data)
        if building.id != building_id or not period.expenses:
            raise StatementError('Δεν μπορεί να εκδοθεί κενή ή μη έγκυρη εκκαθάριση.')
        if set(owner_names) - {a.id for a in building.apartments}:
            raise StatementError('Άγνωστη ιδιοκτησία στα ονόματα ιδιοκτητών.')
        cur.execute('SELECT apartment_id,tenant_name FROM property_delivery_contacts WHERE building_id=%s',
                    (building_id,))
        tenant_names = dict(cur.fetchall())
        cur.execute('''SELECT id,revision,issued_at,configuration,period_data,owner_names,report_data
                       FROM issued_statements WHERE building_id=%s AND period_key=%s
                       ORDER BY revision DESC LIMIT 1''', (building_id,period_key))
        previous = cur.fetchone()
        if previous and all(_canonical(a)==_canonical(b) for a,b in (
                (previous[3],configuration),(previous[4],period_data),(previous[5],owner_names),
                ({a['id']:a.get('tenant_name','') for a in previous[6]['apartments']},
                 {a.id:tenant_names.get(a.id,'') for a in building.apartments}))):
            _archive_documents(cur,company,building_id,str(previous[0]),previous[3],previous[4],
                               previous[5],previous[6],previous[2],previous[1])
            return {'id':str(previous[0]),'revision':previous[1],
                    'issued_at':previous[2],'reused':True}
        result = calculate_period(building,period)
        summary = _summary(result,configuration,owner_names,tenant_names)
        revision = (previous[1]+1) if previous else 1
        cur.execute('SELECT now()')
        issued_at = cur.fetchone()[0]
        label = _issued_label(issued_at,revision)
        pdf_data = create_pdf(result,period_data=period_data,configuration=configuration,
                              owner_names=owner_names,tenant_names=tenant_names,issued_label=label)
        statement_id = str(uuid4())
        cur.execute('''INSERT INTO issued_statements
            (id,building_id,period_key,revision,issued_at,configuration,period_data,
             owner_names,report_data,pdf_data,pdf_sha256)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (statement_id,building_id,period_key,revision,issued_at,Jsonb(configuration),
             Jsonb(period_data),Jsonb(owner_names),Jsonb(summary),pdf_data,
             hashlib.sha256(pdf_data).hexdigest()))
        _archive_documents(cur,company,building_id,statement_id,configuration,period_data,
                           owner_names,summary,issued_at,revision)
    return {'id':statement_id,'revision':revision,'issued_at':issued_at,'reused':False}


def list_statements(building_id):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT id,period_key,revision,issued_at,report_data
                       FROM issued_statements WHERE building_id=%s
                       ORDER BY period_key DESC,revision DESC''',(building_id,))
        rows = cur.fetchall()
    return [{'id':str(sid),'period_key':key,'revision':revision,'issued_at':issued_at,
             'total':report['grand_total'],'property_count':len(report['apartments'])}
            for sid,key,revision,issued_at,report in rows]


def get_statement(building_id, statement_id):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT period_key,revision,issued_at,report_data,pdf_data,pdf_sha256
                       FROM issued_statements WHERE building_id=%s AND id=%s''',(building_id,statement_id))
        row = cur.fetchone()
    if row is None:
        raise StatementError('Η εκκαθάριση δεν βρέθηκε σε αυτή την πολυκατοικία.')
    key,revision,issued_at,report,pdf_data,digest = row
    pdf_data = bytes(pdf_data)
    if hashlib.sha256(pdf_data).hexdigest()!=digest:
        raise StatementError('Το αποθηκευμένο PDF δεν συμφωνεί με το αποτύπωμά του.')
    return {'id':str(statement_id),'period_key':key,'revision':revision,
            'issued_at':issued_at,'report':report,'pdf':pdf_data}


def ensure_property_pdfs(building_id, statement_id):
    """Archive individual PDFs for a legacy issue, without changing its master."""
    company = current_tenant()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT configuration FROM buildings WHERE id=%s AND company_id=%s FOR UPDATE',
                    (building_id,company))
        if cur.fetchone() is None:
            raise StatementError('Η πολυκατοικία δεν βρέθηκε.')
        cur.execute('''SELECT configuration,period_data,owner_names,report_data,issued_at,revision,
                       pdf_data,pdf_sha256 FROM issued_statements WHERE building_id=%s AND id=%s''',
                    (building_id,statement_id))
        row = cur.fetchone()
        if row is None:
            raise StatementError('Η εκκαθάριση δεν βρέθηκε.')
        configuration,period_data,owners,report,issued_at,revision,master,digest = row
        if hashlib.sha256(bytes(master)).hexdigest()!=digest:
            raise StatementError('Το αρχικό PDF δεν συμφωνεί με το αποτύπωμά του.')
        _archive_documents(cur,company,building_id,statement_id,configuration,period_data,
                           owners,report,issued_at,revision)
    return list_property_pdfs(building_id,statement_id)


def list_property_pdfs(building_id, statement_id):
    company = current_tenant()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT id,apartment_id,property_data,pdf_sha256
                       FROM issued_property_pdfs WHERE company_id=%s AND building_id=%s AND statement_id=%s
                       ORDER BY property_data->>'code',apartment_id''',(company,building_id,statement_id))
        rows = cur.fetchall()
    return [{'id':str(id),'apartment_id':aid,'property':data,'sha256':digest}
            for id,aid,data,digest in rows]


def get_property_pdf(building_id, statement_id, apartment_id):
    company = current_tenant()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT property_data,pdf_data,pdf_sha256 FROM issued_property_pdfs
                       WHERE company_id=%s AND building_id=%s AND statement_id=%s AND apartment_id=%s''',
                    (company,building_id,statement_id,apartment_id))
        row = cur.fetchone()
    if row is None:
        raise StatementError('Το ατομικό PDF δεν βρέθηκε.')
    data,content,digest = row
    content = bytes(content)
    if hashlib.sha256(content).hexdigest()!=digest:
        raise StatementError('Το ατομικό PDF δεν συμφωνεί με το αποτύπωμά του.')
    return {'property':data,'pdf':content,'sha256':digest}
