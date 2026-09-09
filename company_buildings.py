"""Company-scoped read helpers; all queries also enforce PostgreSQL RLS."""
from database import tenant_scope, get_connection

class CompanyAccessError(ValueError):
    pass

def list_company_buildings(company_id, issuer, subject):
    with tenant_scope(issuer, subject, company_id):
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("""SELECT id,name,address FROM buildings
                WHERE company_id=%s AND configuration->>'archived' IS DISTINCT FROM 'true'
                ORDER BY name,id""", (company_id,))
            return [{'id':r[0], 'name':r[1], 'address':r[2]} for r in cur.fetchall()]

def list_company_statements(company_id, building_id, issuer, subject):
    with tenant_scope(issuer, subject, company_id):
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("""SELECT s.id,s.period_key,s.revision,s.issued_at,
                s.report_data->>'grand_total' FROM issued_statements s
                JOIN buildings b ON b.id=s.building_id
                WHERE b.company_id=%s AND s.building_id=%s
                ORDER BY s.period_key DESC,s.revision DESC""", (company_id,building_id))
            return [{'id':str(r[0]),'period_key':r[1],'revision':r[2],
                     'issued_at':r[3],'total':r[4]} for r in cur.fetchall()]
