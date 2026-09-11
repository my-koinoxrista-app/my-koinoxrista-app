"""Test migrations 011/012 on a disposable local PostgreSQL cluster, never Neon."""
import os
from pathlib import Path
import subprocess
import tempfile
import time
from uuid import uuid4
import psycopg
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='koinoxrista-payment-test-') as temp:
        root = Path(temp); data = root/'pg'; sock = root/'sock'; sock.mkdir()
        env = {k:v for k,v in os.environ.items() if not k.startswith('PG')}
        def run(*args):
            return subprocess.run(args, check=True, env=env, stdout=subprocess.DEVNULL)
        name = 'koinoxrista-payments-test-' + uuid4().hex[:12]
        started = False
        try:
            run('docker','run','--rm','-d','--name',name,'-e','POSTGRES_USER=fixture_admin',
                '-e','POSTGRES_HOST_AUTH_METHOD=trust','-p','127.0.0.1::5432','postgres:17')
            started = True
            port = int(subprocess.check_output(['docker','port',name,'5432/tcp'],text=True).strip().rsplit(':',1)[1])
            connection = dict(host='127.0.0.1',port=port,dbname='postgres')
            deadline = time.monotonic()+60
            while True:
                try:
                    with psycopg.connect(**connection,user='fixture_admin',connect_timeout=2): break
                except psycopg.OperationalError:
                    if time.monotonic()>deadline: raise
                    time.sleep(1)
            with psycopg.connect(**connection,user='fixture_admin',autocommit=True) as c:
                c.execute('CREATE ROLE koinoxrista_app LOGIN NOINHERIT NOBYPASSRLS')
                # Minimal parent tables; the real payment schema is loaded from 011.
                c.execute('''CREATE TABLE companies(id uuid PRIMARY KEY);
                CREATE TABLE issued_property_pdfs(company_id uuid,building_id text,statement_id uuid,id uuid,
                    UNIQUE(company_id,building_id,statement_id,id));
                CREATE TABLE statement_email_deliveries(id uuid);
                CREATE FUNCTION koinoxrista_can_access(uuid) RETURNS boolean LANGUAGE sql AS 'SELECT true';''')
                for f in ['011_payments.sql','012_payment_confirmation.sql']:
                    c.execute((ROOT/'migrations'/f).read_text())
                company,statement,pdf,request,token=[uuid4() for _ in range(5)]
                c.execute('INSERT INTO companies VALUES(%s)',(company,))
                c.execute("INSERT INTO issued_property_pdfs VALUES(%s,'b',%s,%s)",(company,statement,pdf))
                c.execute('''INSERT INTO payment_requests(id,company_id,building_id,statement_id,statement_revision,
                apartment_id,property_pdf_id,amount_cents,period_key,payment_token,provider,provider_account_id,provider_session_id)
                VALUES(%s,%s,'b',%s,1,'a',%s,1234,'2026-09',%s,'stripe','acct_fixture','cs_fixture')''',
                (request,company,statement,pdf,token))
                with psycopg.connect(**connection,user='koinoxrista_webhook',autocommit=True) as w:
                    def send(kind='checkout.session.completed', **overrides):
                        obj={'id':'cs_fixture','mode':'payment','currency':'eur','amount_total':1234,
                             'payment_status':'paid','payment_intent':'pi_fixture','livemode':False,
                             'metadata':{'payment_token':str(token)}}
                        obj.update(overrides)
                        event={'id':'evt_'+uuid4().hex,'type':kind,'account':'acct_fixture','livemode':False,'data':{'object':obj}}
                        return w.execute('SELECT koinoxrista_confirm_stripe_event(%s)',(Jsonb(event),)).fetchone()[0],event
                    def status(): return c.execute('SELECT status FROM payment_requests').fetchone()[0]
                    send(payment_status='unpaid'); assert status()=='UNPAID'
                    for fields in [{'amount_total':1},{'currency':'usd'},{'id':'cs_wrong'},{'livemode':True}]:
                        try: send(**fields)
                        except psycopg.Error: pass
                        else: raise AssertionError('Mismatched checkout accepted')
                    result,event=send('checkout.session.async_payment_succeeded'); assert status()=='PAID'
                    assert w.execute('SELECT koinoxrista_confirm_stripe_event(%s)',(Jsonb(event),)).fetchone()[0]=='DUPLICATE'
                    send('checkout.session.async_payment_failed'); assert status()=='PAID'
                    send('charge.refunded',amount=1234,amount_refunded=300); assert status()=='PARTIALLY_REFUNDED'
                    send(); assert status()=='PARTIALLY_REFUNDED'
                    send('charge.refunded',amount=1234,amount_refunded=1234); assert status()=='REFUNDED'
                    send(); assert status()=='REFUNDED'
                    try: w.execute('SELECT * FROM companies')
                    except psycopg.errors.InsufficientPrivilege: pass
                    else: raise AssertionError('Webhook can read company data')
                with psycopg.connect(**connection,user='koinoxrista_app',autocommit=True) as app:
                    for query in ["UPDATE payment_requests SET status='PAID'", "SELECT koinoxrista_confirm_stripe_event('{}'::jsonb)"]:
                        try: app.execute(query)
                        except psycopg.errors.InsufficientPrivilege: pass
                        else: raise AssertionError('App can forge confirmation')
                print('PASS: payment migrations, paid/unpaid, amount/session/mode checks, duplicates, late failures, partial/full refunds and role isolation.')
        finally:
            if started: run('docker','rm','-f','-v',name)


if __name__=='__main__': main()
