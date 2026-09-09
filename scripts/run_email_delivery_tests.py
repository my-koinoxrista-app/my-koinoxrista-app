"""Disposable PostgreSQL 17 integration test for migration 010 and email delivery.

Run from the project with: python3 scripts/run_email_delivery_tests.py
No live database, Compose service, volume, credentials or SMTP provider is used.
"""
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from decimal import Decimal
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
SOURCES=('configuration.py','expense_engine.py','facilities.py','pdf_generator.py',
         'statement_store.py','statement_delivery.py','smtp_profiles.py','database.py')
MIGRATIONS=('schema.sql','002_apartments.sql','003_allocation_tables.sql',
            '004_expense_categories.sql','005_building_facilities.sql',
            '006_composite_keys.sql','007_issued_statements.sql','008_companies.sql',
            '009_tenant_security.sql','010_statement_delivery.sql')

STORAGE_SQL="""
CREATE TABLE public.monthly_periods (
 building_id text NOT NULL REFERENCES public.buildings(id) ON DELETE RESTRICT,
 period_key text NOT NULL, data jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(building_id,period_key));
CREATE TABLE public.receipt_documents (
 id text PRIMARY KEY, building_id text NOT NULL REFERENCES public.buildings(id) ON DELETE RESTRICT,
 period_key text NOT NULL, filename text NOT NULL, sha256 text NOT NULL,
 content_type text NOT NULL, extracted jsonb NOT NULL DEFAULT '{}'::jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(building_id,sha256));
"""


def _run(args,**kwargs):
    return subprocess.run(args,check=True,text=True,**kwargs)


def main():
    import psycopg
    _run(['docker','info','--format','{{.ServerVersion}}'],stdout=subprocess.DEVNULL)
    missing=[p for p in SOURCES if not (ROOT/p).is_file()]
    missing += [f'migrations/{p}' for p in MIGRATIONS if not (ROOT/'migrations'/p).is_file()]
    if missing:
        raise SystemExit('Missing source files: '+', '.join(missing))
    name='koinoxrista-email-test-'+uuid4().hex[:12]
    password=secrets.token_urlsafe(32)
    app_password=secrets.token_urlsafe(32)
    signing_key=secrets.token_hex(32)
    with tempfile.TemporaryDirectory(prefix='koinoxrista-email-') as tmp:
        work=Path(tmp)
        (work/'migrations').mkdir()
        (work/'scripts').mkdir()
        for source in SOURCES:
            shutil.copy2(ROOT/source,work/source)
        for source in MIGRATIONS:
            shutil.copy2(ROOT/'migrations'/source,work/'migrations'/source)
        shutil.copy2(Path(__file__).resolve(),work/'scripts'/Path(__file__).name)
        # Only synthetic credentials are passed to the isolated worker.
        env={k:v for k,v in os.environ.items() if not (
            k.startswith(('POSTGRES_','PG','KOINOXRISTA_','OPENAI_')) or
            k in ('DATABASE_URL','PYTHONPATH','PYTHONHOME','ENV_FILE','DOTENV_PATH'))}
        env.update({'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1',
            'KOINOXRISTA_TEST_CONTAINER':name,'KOINOXRISTA_TEST_PASSWORD':password,
            'KOINOXRISTA_TEST_APP_PASSWORD':app_password,'KOINOXRISTA_TEST_SIGNING_KEY':signing_key})
        started=False
        try:
            _run(['docker','run','--rm','-d','--name',name,'--label','koinoxrista.disposable=email-test',
                  '-e','POSTGRES_USER=koinoxrista','-e','POSTGRES_DB=koinoxrista',
                  '-e','POSTGRES_PASSWORD='+password,'-p','127.0.0.1::5432','postgres:17'],
                 stdout=subprocess.DEVNULL)
            started=True
            output=_run(['docker','port',name,'5432/tcp'],capture_output=True).stdout.strip()
            port=int(output.rsplit(':',1)[1])
            deadline=time.monotonic()+90
            while True:
                try:
                    with psycopg.connect(host='127.0.0.1',port=port,dbname='koinoxrista',
                                         user='koinoxrista',password=password,connect_timeout=2):
                        break
                except psycopg.OperationalError:
                    if time.monotonic()>deadline:
                        raise RuntimeError('Disposable PostgreSQL did not become ready.')
                    time.sleep(1)
            env['KOINOXRISTA_TEST_PORT']=str(port)
            print('Testing a disposable PostgreSQL and temporary source copy.',flush=True)
            _run([sys.executable,'-I',str(work/'scripts'/Path(__file__).name),'--worker'],cwd=work,env=env)
            print('ALL EMAIL DELIVERY INTEGRATION TESTS PASSED',flush=True)
        finally:
            if started:
                subprocess.run(['docker','rm','-f','-v',name],stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,check=False)
                print('Disposable PostgreSQL container removed.',flush=True)


def worker():
    # Never accept a caller-supplied database URL or credentials for this test.
    name=os.environ.get('KOINOXRISTA_TEST_CONTAINER','')
    if not name.startswith('koinoxrista-email-test-') or not os.environ.get('KOINOXRISTA_TEST_PORT'):
        raise SystemExit('This worker must be started by the disposable test runner.')
    sys.path.insert(0,str(ROOT))
    import hashlib
    import io
    import psycopg
    from psycopg import sql
    from psycopg.types.json import Jsonb
    from unittest.mock import patch
    from datetime import datetime,timezone
    from configuration import load_building,load_period
    from expense_engine import calculate_period

    port=int(os.environ['KOINOXRISTA_TEST_PORT'])
    password=os.environ['KOINOXRISTA_TEST_PASSWORD']
    app_password=os.environ['KOINOXRISTA_TEST_APP_PASSWORD']
    signing_key=os.environ['KOINOXRISTA_TEST_SIGNING_KEY']
    admin=dict(host='127.0.0.1',port=port,dbname='koinoxrista',user='koinoxrista',password=password)
    with psycopg.connect(**admin,autocommit=True) as conn:
        with conn.cursor() as cur:
            for filename in MIGRATIONS[:6]:
                cur.execute((ROOT/'migrations'/filename).read_text(encoding='utf-8'))
            cur.execute(STORAGE_SQL)
            for filename in MIGRATIONS[6:8]:
                cur.execute((ROOT/'migrations'/filename).read_text(encoding='utf-8'))
            cur.execute(sql.SQL('CREATE ROLE koinoxrista_app LOGIN PASSWORD {}').format(sql.Literal(app_password)))
            cur.execute((ROOT/'migrations'/'009_tenant_security.sql').read_text(encoding='utf-8'))
            cur.execute('INSERT INTO tenant_security.scope_secret(id,secret) VALUES(true,%s)',
                        (bytes.fromhex(signing_key),))
            cur.execute((ROOT/'migrations'/'010_statement_delivery.sql').read_text(encoding='utf-8'))
            cur.execute('SELECT id FROM companies LIMIT 1')
            company_a=str(cur.fetchone()[0])
            company_b=str(uuid4())
            cur.execute('INSERT INTO companies(id,name) VALUES(%s,%s)',(company_b,'Company B'))
            issuer='https://accounts.google.com'
            subjects=('synthetic-user-a1','synthetic-user-a2','synthetic-user-b1')
            for company,subject in [(company_a,subjects[0]),(company_a,subjects[1]),(company_b,subjects[2])]:
                cur.execute('INSERT INTO company_memberships(company_id,issuer,subject) VALUES(%s,%s,%s)',
                            (company,issuer,subject))
            config={'id':'building-a','name':'Demo A','address':'Test Street 1',
                'apartments':[{'id':aid,'code':code} for aid,code in [('a1','A1'),('b1','B1'),('b2','B2')]],
                'tables':[{'id':'general','name':'General','weights':{'a1':'600','b1':'250','b2':'150'},
                           'expected_total':'1000'}],
                'categories':[{'id':'clean','name':'Cleaning','payer':'TENANT',
                               'rule':{'type':'WEIGHTED','table_id':'general'}}],
                'facilities':{},'heating':{}}
            period={'start_date':'2026-09-01','end_date':'2026-09-30','expenses':[
                {'id':'e1','category_id':'clean','amount':'100.01','description':'Synthetic expense'}]}
            for company,bid in [(company_a,'building-a'),(company_b,'building-b')]:
                cfg={**config,'id':bid,'name':'Demo '+bid[-1].upper()}
                cur.execute('INSERT INTO buildings(id,name,address,configuration,company_id) VALUES(%s,%s,%s,%s,%s)',
                            (bid,cfg['name'],cfg['address'],Jsonb(cfg),company))
                for apartment in cfg['apartments']:
                    cur.execute('INSERT INTO apartments(building_id,id,code) VALUES(%s,%s,%s)',
                                (bid,apartment['id'],apartment['code']))
                cur.execute('INSERT INTO monthly_periods(building_id,period_key,data) VALUES(%s,%s,%s)',
                            (bid,'2026-09',Jsonb(period)))

    # The runtime imports only synthetic credentials, never an admin DSN.
    os.environ.update({'POSTGRES_HOST':'127.0.0.1','POSTGRES_PORT':str(port),
        'POSTGRES_DB':'koinoxrista','POSTGRES_APP_USER':'koinoxrista_app',
        'POSTGRES_APP_PASSWORD':app_password,'KOINOXRISTA_SCOPE_KEY':signing_key,
        'KOINOXRISTA_EMAIL_SENDING_ENABLED':'true','KOINOXRISTA_SMTP_HOST':'smtp.example.com',
        'KOINOXRISTA_SMTP_PORT':'465','KOINOXRISTA_SMTP_SECURITY':'ssl',
        'KOINOXRISTA_SMTP_USERNAME':'synthetic','KOINOXRISTA_SMTP_PASSWORD':'synthetic',
        'KOINOXRISTA_SMTP_FROM_EMAIL':'sender@example.com'})
    import database
    import statement_store as store
    import statement_delivery as delivery
    from psycopg import errors

    def denied(callback):
        try:
            callback()
        except (psycopg.Error,ValueError):
            return
        raise AssertionError('Cross-company or forbidden operation was accepted.')

    with database.tenant_scope(issuer,subjects[0],company_a):
        delivery.save_contacts('building-a',[{'id':aid,'tenant_name':name,'email':email,'enabled':True}
            for aid,name,email in [('a1','Tenant A','a@example.com'),('b1','Tenant B','b@example.com'),
                                   ('b2','Tenant C','c@example.com')]])
        issued=store.issue_statement('building-a','2026-09',config,period)
        docs=store.list_property_pdfs('building-a',issued['id'])
        assert len(docs)==3
        master=store.get_statement('building-a',issued['id'])
        original_digest=hashlib.sha256(master['pdf']).hexdigest()
        assert sum((Decimal(d['property']['total']) for d in docs), Decimal('0.00')) == Decimal('100.01')
        assert store.issue_statement('building-a','2026-09',config,period)['reused']
        assert len(store.list_statements('building-a'))==1
        for doc in docs:
            p=store.get_property_pdf('building-a',issued['id'],doc['apartment_id'])
            assert hashlib.sha256(p['pdf']).hexdigest()==doc['sha256']
            assert p['pdf'].startswith(b'%PDF-')
        print('PASS: atomic issuance, exact individual totals, immutable archive, repeated issuance')
        # All same-company users share the same records.
    with database.tenant_scope(issuer,subjects[1],company_a):
        assert len(store.list_property_pdfs('building-a',issued['id']))==3
        assert store.get_statement('building-a',issued['id'])['pdf']==master['pdf']
    print('PASS: shared company access')

    with database.tenant_scope(issuer,subjects[2],company_b):
        denied(lambda:store.get_property_pdf('building-a',issued['id'],'a1'))
        assert delivery.list_contacts('building-a')==[]
        denied(lambda:delivery.save_contacts('building-a',[{'id':'a1','tenant_name':'X','email':'x@example.com','enabled':True}]))
        denied(lambda:delivery.prepare('building-a',issued['id'],['a1'],'invalid'))
        with database.get_connection() as conn,conn.cursor() as cur:
            assert cur.execute('SELECT id FROM issued_property_pdfs WHERE id=%s',(docs[0]['id'],)).fetchone() is None
            assert cur.execute('SELECT id FROM statement_email_deliveries WHERE company_id=%s',(company_a,)).fetchone() is None
        denied(lambda:store.ensure_property_pdfs('building-a',issued['id']))
    print('PASS: foreign PDF, contacts, preparation and SQL reads denied')

    with database.tenant_scope(issuer,subjects[0],company_a):
        with database.get_connection() as conn,conn.cursor() as cur:
            denied(lambda:cur.execute('UPDATE issued_property_pdfs SET pdf_data=%s WHERE id=%s',(b'%PDF-changed',docs[0]['id'])))
        with database.get_connection() as conn,conn.cursor() as cur:
            denied(lambda:cur.execute('DELETE FROM issued_property_pdfs WHERE id=%s',(docs[0]['id'],)))
        with database.get_connection() as conn,conn.cursor() as cur:
            denied(lambda:cur.execute('CREATE TABLE public.unapproved_table(id integer)'))
        # Final approval cannot be reused after a recipient changes.
        data=delivery.preview('building-a',issued['id'])
        fingerprint=delivery.approval_fingerprint(data,['a1'])
        delivery.save_contacts('building-a',[{'id':'a1','tenant_name':'Tenant A','email':'changed@example.com','enabled':True}])
        denied(lambda:delivery.prepare('building-a',issued['id'],['a1'],fingerprint))
        delivery.save_contacts('building-a',[{'id':'a1','tenant_name':'Tenant A','email':'a@example.com','enabled':True}])
        data=delivery.preview('building-a',issued['id'])
        fingerprint=delivery.approval_fingerprint(data,['a1'])
        prepared=delivery.prepare('building-a',issued['id'],['a1'],fingerprint)
        assert len(prepared)==1
        denied(lambda:delivery.prepare('building-a',issued['id'],['a1'],fingerprint))
        sent=[]
        result=delivery.send_prepared('building-a',prepared,submit=lambda msg,cfg:sent.append(msg))
        assert result[0]['status']=='ACCEPTED' and len(sent)==1
        assert sent[0]['To']=='a@example.com'
        assert len(list(sent[0].iter_attachments()))==1
        denied(lambda:delivery.send_prepared('building-a',prepared,submit=lambda msg,cfg:sent.append(msg)))
        assert len(sent)==1
        with database.get_connection() as conn,conn.cursor() as cur:
            denied(lambda:cur.execute('UPDATE statement_email_deliveries SET status=%s WHERE id=%s',('ACCEPTED',prepared[0])))
        print('PASS: immutable PDF, no schema writes, stale approval, recipient freeze, no duplicate SMTP')
        # An uncertain result cannot be retried without offline reconciliation.
        data=delivery.preview('building-a',issued['id'])
        ids=delivery.prepare('building-a',issued['id'],['b1'],delivery.approval_fingerprint(data,['b1']))
        result=delivery.send_prepared('building-a',ids,submit=lambda msg,cfg:(_ for _ in ()).throw(OSError('synthetic disconnect')))
        assert result[0]['status']=='UNKNOWN'
        denied(lambda:delivery.send_prepared('building-a',ids,submit=lambda msg,cfg:sent.append(msg)))
        # A definite recipient refusal permits only an explicit retry.
        data=delivery.preview('building-a',issued['id'])
        ids=delivery.prepare('building-a',issued['id'],['b2'],delivery.approval_fingerprint(data,['b2']))
        def refused(msg,cfg):
            raise delivery.smtplib.SMTPRecipientsRefused({'c@example.com':(550,b'Rejected')})
        assert delivery.send_prepared('building-a',ids,submit=refused)[0]['status']=='FAILED'
        assert delivery.send_prepared('building-a',ids,submit=lambda msg,cfg:sent.append(msg))[0]['status']=='ACCEPTED'
        with database.get_connection() as conn,conn.cursor() as cur:
            cur.execute('SELECT attempt_count FROM statement_email_deliveries WHERE id=%s',(ids[0],))
            assert cur.fetchone()[0]==2
        print('PASS: uncertain submission blocked, explicit retry after definite refusal')
        # Old issued records receive individual PDFs from the stored snapshot only.
        old_period={**period,'start_date':'2026-08-01','end_date':'2026-08-31'}
        result=calculate_period(load_building(config),load_period(old_period))
        old_report=store._summary(result,config,{})
        old_report['schema_version']=1
        for item in old_report['apartments']:
            item.pop('tenant_name',None)
        old_pdf=store.create_pdf(result,old_period,config)
        legacy_id=str(uuid4())
    with psycopg.connect(**admin) as conn,conn.cursor() as cur:
        cur.execute('''INSERT INTO issued_statements(id,building_id,period_key,revision,configuration,
            period_data,owner_names,report_data,pdf_data,pdf_sha256)
            VALUES(%s,%s,%s,1,%s,%s,%s,%s,%s,%s)''',
            (legacy_id,'building-a','2026-08',Jsonb(config),Jsonb(old_period),Jsonb({}),
             Jsonb(old_report),old_pdf,hashlib.sha256(old_pdf).hexdigest()))
    with database.tenant_scope(issuer,subjects[0],company_a):
        with patch.object(store,'calculate_period',side_effect=AssertionError('Historical recalculation')):
            assert len(store.ensure_property_pdfs('building-a',legacy_id))==3
        assert store.get_statement('building-a',legacy_id)['pdf']==old_pdf
        assert hashlib.sha256(store.get_statement('building-a',issued['id'])['pdf']).hexdigest()==original_digest
        print('PASS: legacy backfill without calculation or master PDF mutation')


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--worker':
        worker()
    else:
        main()
