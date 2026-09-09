"""Offline operator-only enrollment. Never import this module from Streamlit."""
import os
import psycopg

def main():
    dsn = os.getenv('KOINOXRISTA_ADMIN_DSN')
    if not dsn:
        raise SystemExit('Set KOINOXRISTA_ADMIN_DSN only in the offline administration environment.')
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute('SELECT id,name FROM companies ORDER BY name')
        rows = cur.fetchall()
        for company_id, name in rows:
            print(company_id, name)
        company_id = input('Company UUID: ').strip()
        issuer = input('Verified OIDC issuer (iss): ').strip()
        subject = input('Verified OIDC subject (sub): ').strip()
        if not issuer or not subject:
            raise SystemExit('Issuer and subject are required.')
        if input('Type YES to grant access: ').strip() != 'YES':
            raise SystemExit('Cancelled.')
        cur.execute("""INSERT INTO company_memberships(company_id,issuer,subject)
            SELECT id,%s,%s FROM companies WHERE id=%s
            ON CONFLICT DO NOTHING RETURNING company_id""",
            (issuer,subject,company_id))
        if cur.fetchone() is None:
            cur.execute('SELECT 1 FROM companies WHERE id=%s', (company_id,))
            if cur.fetchone() is None:
                raise SystemExit('Company not found.')
        print('Membership saved or already present.')
if __name__ == '__main__':
    main()
