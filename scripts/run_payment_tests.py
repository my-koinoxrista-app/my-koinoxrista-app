"""Disposable PostgreSQL 17 integration tests for Stripe payments and migration 012.

Run from the project with: python3 scripts/run_payment_tests.py
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
         'statement_store.py','statement_delivery.py','smtp_profiles.py','database.py',
         'payment_service.py','stripe_checkout.py','payment_webhook.py',
         'payment_webhook_database.py','public_payment.py')
MIGRATIONS=('schema.sql','002_apartments.sql','003_allocation_tables.sql',
            '004_expense_categories.sql','005_building_facilities.sql',
            '006_composite_keys.sql','007_issued_statements.sql','008_companies.sql',
            '009_tenant_security.sql','010_statement_delivery.sql','011_payments.sql','012_payment_deployment.sql')

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
    name='koinoxrista-payment-test-'+uuid4().hex[:12]
    password=secrets.token_urlsafe(32)
    app_password=secrets.token_urlsafe(32)
    signing_key=secrets.token_hex(32)
    with tempfile.TemporaryDirectory(prefix='koinoxrista-payment-') as tmp:
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
            _run(['docker','run','--rm','-d','--name',name,'--label','koinoxrista.disposable=payment-test',
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
            print('ALL PAYMENT INTEGRATION TESTS PASSED',flush=True)
        finally:
            if started:
                subprocess.run(['docker','rm','-f','-v',name],stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,check=False)
                print('Disposable PostgreSQL container removed.',flush=True)


def worker():
    # Never accept a caller-supplied database URL or credentials for this test.
    name=os.environ.get('KOINOXRISTA_TEST_CONTAINER','')
    if not name.startswith('koinoxrista-payment-test-') or not os.environ.get('KOINOXRISTA_TEST_PORT'):
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
            for filename in ('011_payments.sql','012_payment_deployment.sql'):
                cur.execute((ROOT/'migrations'/filename).read_text(encoding='utf-8'))
            cur.execute(sql.SQL('ALTER ROLE koinoxrista_webhook PASSWORD {}').format(sql.Literal(app_password)))
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

    import json
    import hmac
    import copy
    import payment_service as payments
    import public_payment
    import payment_webhook
    from psycopg.conninfo import make_conninfo
    from concurrent.futures import ThreadPoolExecutor
    os.environ.update({
        'KOINOXRISTA_WEBHOOK_DATABASE_URL': make_conninfo(**{**admin,
            'user':'koinoxrista_webhook','password':app_password}),
        'KOINOXRISTA_STRIPE_COMPANY_ACCOUNTS':json.dumps({company_a:'acct_syntheticA',company_b:'acct_syntheticB'}),
        'KOINOXRISTA_STRIPE_ACCOUNT_ID':'acct_syntheticA',
        'KOINOXRISTA_STRIPE_MODE':'direct',
        'KOINOXRISTA_STRIPE_WEBHOOK_SECRET':'whsec_synthetic',
        'KOINOXRISTA_PAYMENT_PUBLIC_BASE_URL':'https://payments.example.com'})

    def denied(callback):
        try:
            callback()
        except (psycopg.Error,ValueError):
            return
        raise AssertionError('Forbidden operation was accepted.')

    def app_sql(query, args=()):
        with database.get_connection() as conn,conn.cursor() as cur:
            cur.execute(query,args)

    with database.tenant_scope(issuer,subjects[0],company_a):
        issued=store.issue_statement('building-a','2026-09',config,period)
        original_pdf=store.get_statement('building-a',issued['id'])['pdf']
        payments.save_provider_settings('acct_syntheticA')
        denied(lambda:payments.save_provider_settings('acct_other'))
        assert payments.ensure_payment_requests('building-a',issued['id'])['created']==3
        assert payments.ensure_payment_requests('building-a',issued['id'])['created']==0
        requests=payments.list_payment_requests('building-a',issued['id'])
        assert sum(p['amount_cents'] for p in requests)==10001
        denied(lambda:app_sql("UPDATE payment_requests SET status='PAID'"))
        denied(lambda:app_sql("UPDATE payment_requests SET amount_cents=1"))
        denied(lambda:app_sql("SELECT koinoxrista_payment_webhook('{}'::jsonb,'acct_syntheticA')"))
        denied(lambda:app_sql('SELECT * FROM payment_webhook_events'))
    with database.tenant_scope(issuer,subjects[2],company_b):
        payments.save_provider_settings('acct_syntheticB')
        assert payments.list_payment_requests('building-a',issued['id'])==[]
        denied(lambda:payments.ensure_payment_requests('building-a',issued['id']))
    print('PASS: migration, request reuse, exact totals, company isolation, portal cannot mark paid',flush=True)

    request=requests[0]
    token=request['payment_token']
    session={'id':'cs_test_synthetic','object':'checkout.session','mode':'payment',
        'status':'open','payment_status':'unpaid','payment_intent':'pi_synthetic',
        'amount_total':request['amount_cents'],'currency':'eur','livemode':False,
        'expires_at':int(time.time())+86400,'url':'https://checkout.stripe.com/c/pay/synthetic',
        'metadata':{'payment_token':token}}
    created=[]
    current_session=copy.deepcopy(session)

    def fake_stripe(account,path,form=None,idempotency_key=None):
        assert account=='acct_syntheticA'
        if path=='account':
            return {'id':account}
        if form is not None:
            assert form['line_items[0][price_data][unit_amount]']==str(request['amount_cents'])
            assert form['success_url']=='https://payments.example.com/payment/success'
            assert idempotency_key.startswith('koinoxrista:'+token+':')
            created.append(idempotency_key)
            return copy.deepcopy(session)
        return copy.deepcopy(current_session)

    with patch.object(public_payment,'stripe_request',side_effect=fake_stripe):
        with ThreadPoolExecutor(max_workers=4) as executor:
            results=list(executor.map(public_payment.open_checkout,[token]*4))
        assert all(r==('REDIRECT',session['url']) for r in results)
        assert len(created)==1
        current_session['status']='expired'
        session['id']='cs_test_renewed'
        assert public_payment.open_checkout(token)[0]=='REDIRECT'
        assert len(created)==2 and created[0]!=created[1]
        current_session=copy.deepcopy(session)
        current_session['status']='complete'
        assert public_payment.open_checkout(token)==('PENDING',None)
    print('PASS: permanent links, concurrent checkout creation, expiration renewal, pending confirmation',flush=True)

    counter=0
    def event_for(kind='checkout.session.completed', **updates):
        nonlocal counter
        counter+=1
        obj={**session,'status':'complete',**updates}
        return {'id':f'evt_synthetic_{counter}','type':kind,'livemode':False,
                'data':{'object':obj}}

    def deliver(event):
        raw=json.dumps(event).encode()
        timestamp=int(time.time())
        signature=hmac.new(b'whsec_synthetic',str(timestamp).encode()+b'.'+raw,hashlib.sha256).hexdigest()
        return payment_webhook.handle_stripe_webhook(raw,f't={timestamp},v1={signature}')

    def status():
        with database.tenant_scope(issuer,subjects[0],company_a):
            return payments.list_payment_requests('building-a',issued['id'])[0]['status']

    # No tenant context exists here: real restricted webhook connections must work.
    assert deliver(event_for())=='APPLIED'
    assert status()=='UNPAID'
    for changes in ({'amount_total':1},{'currency':'usd'},{'id':'cs_wrong'},
                    {'livemode':True},{'amount_total':None},{'metadata':{'payment_token':token},'mode':'subscription'}):
        denied(lambda:deliver(event_for(payment_status='paid',**changes)))
        assert status()=='UNPAID'
    wrong=event_for(payment_status='paid');wrong['account']='acct_wrong'
    denied(lambda:deliver(wrong))
    early_refund=event_for('charge.refunded',object='charge',id='ch_synthetic',
        amount=request['amount_cents'],amount_refunded=1)
    denied(lambda:deliver(early_refund))
    assert status()=='UNPAID'
    success=event_for('checkout.session.async_payment_succeeded',payment_status='paid')
    with ThreadPoolExecutor(max_workers=4) as executor:
        results=list(executor.map(deliver,[success]*4))
    assert results.count('APPLIED')==1 and results.count('DUPLICATE')==3
    assert status()=='PAID'
    assert deliver(early_refund)=='APPLIED'
    assert public_payment.open_checkout(token)==('PAID',None)
    assert deliver(event_for('checkout.session.async_payment_failed'))=='APPLIED'
    assert status()=='PAID'
    print('PASS: signed webhook without login, unpaid completion, amount/currency/session/mode checks, duplicates and ordering',flush=True)

    refund=event_for('charge.refunded',object='charge',id='ch_synthetic',
                     amount=request['amount_cents'],amount_refunded=1)
    assert deliver(refund)=='APPLIED' and status()=='PAID'
    refund=event_for('charge.refunded',object='charge',id='ch_synthetic',
                     amount=request['amount_cents'],amount_refunded=request['amount_cents'])
    assert deliver(refund)=='APPLIED' and status()=='REFUNDED'
    assert deliver(event_for(payment_status='paid'))=='APPLIED' and status()=='REFUNDED'
    assert public_payment.open_checkout(token)==('REFUNDED',None)
    assert deliver({'id':'evt_irrelevant','type':'customer.created'})=='IGNORED'
    with database.tenant_scope(issuer,subjects[0],company_a):
        assert store.get_statement('building-a',issued['id'])['pdf']==original_pdf
    with public_payment.get_payment_connection() as conn:
        try:
            conn.execute('SELECT * FROM buildings')
        except psycopg.errors.InsufficientPrivilege:
            conn.rollback()
        else:
            raise AssertionError('Webhook role can read building data')
    legacy=requests[1]
    legacy_session={**session,'id':'cs_legacy','status':'complete','url':None,
        'amount_total':legacy['amount_cents'], 'metadata':{'payment_token':legacy['payment_token']}}
    with psycopg.connect(**admin) as conn:
        conn.execute('UPDATE payment_requests SET provider_session_id=%s WHERE payment_token=%s',
                     ('cs_legacy',legacy['payment_token']))
    def legacy_stripe(account,path,form=None,idempotency_key=None):
        return {'id':account} if path=='account' else legacy_session
    with patch.object(public_payment,'stripe_request',side_effect=legacy_stripe):
        assert public_payment.open_checkout(legacy['payment_token'])==('PENDING',None)
    legacy_event={'id':'evt_legacy','type':'checkout.session.completed','livemode':False,
        'data':{'object':{**legacy_session,'payment_status':'paid'}}}
    assert deliver(legacy_event)=='APPLIED'
    assert public_payment.open_checkout(legacy['payment_token'])==('PAID',None)
    print('PASS: legacy checkout adoption')
    print('PASS: refunds, immutable PDFs, ignored unrelated events, restricted webhook credentials',flush=True)


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--worker':
        worker()
    else:
        main()
