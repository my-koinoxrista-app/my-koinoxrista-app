"""OIDC identity and membership discovery. No automatic enrollment."""
from database import get_identity_connection, identity_scope

def identity(user):
    issuer, subject = user.get('iss'), user.get('sub')
    if not isinstance(issuer, str) or not issuer or not isinstance(subject, str) or not subject:
        raise ValueError('Ο πάροχος δεν επέστρεψε έγκυρη ταυτότητα OIDC.')
    return issuer, subject

def companies_for_user(issuer, subject):
    with identity_scope(issuer, subject):
        with get_identity_connection() as conn, conn.cursor() as cur:
            cur.execute("""SELECT c.id, c.name FROM company_memberships m
                JOIN companies c ON c.id=m.company_id
                WHERE m.issuer=%s AND m.subject=%s ORDER BY c.name,c.id""",
                (issuer, subject))
            return cur.fetchall()

def company_summary(company_id, issuer, subject):
    from database import tenant_scope, get_connection
    with tenant_scope(issuer, subject, company_id):
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("""SELECT c.name, count(b.id) FROM companies c
                LEFT JOIN buildings b ON b.company_id=c.id
                WHERE c.id=%s GROUP BY c.id,c.name""", (company_id,))
            return cur.fetchone()
