"""Conservative, transactional cleanup of buildings without historical data.

A building may have properties, shares, rules and facilities and still be
empty for this purpose. Monthly periods and receipts are historical data.
Unknown dependencies are never deleted. No schema changes are performed.
"""
from psycopg import sql

from database import get_connection

CONFIG_TABLES = (
    'allocation_rule_participants', 'allocation_shares',
    'expense_categories', 'allocation_rules', 'allocation_tables',
    'apartments', 'building_facilities', 'heating_systems',
)
HISTORY_TABLES = {'monthly_periods', 'receipt_documents', 'issued_statements'}

# These tables have building_id but are linked through composite FKs.
INDIRECT_TABLES = {'allocation_shares', 'allocation_rule_participants'}


# Recognized relationships from the supplied migrations. Legacy single-ID
# references are supported as well as the building-scoped composite keys.
CONFIG_REFERENCES = {
    ('allocation_shares', 'allocation_tables'): ('allocation_table_id', 'id'),
    ('allocation_shares', 'apartments'): ('apartment_id', 'id'),
    ('allocation_rule_participants', 'allocation_rules'): ('rule_id', 'id'),
    ('allocation_rule_participants', 'apartments'): ('apartment_id', 'id'),
    ('allocation_rules', 'allocation_tables'): ('allocation_table_id', 'id'),
    ('expense_categories', 'allocation_rules'): ('default_rule_id', 'id'),
}


def list_archive_candidates():
    """Include archived records without requiring the deletion schema checks."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT id,name,address,
            coalesce(configuration->>'archived'='true',false)
            FROM buildings ORDER BY name,id""")
        return [dict(id=r[0], name=r[1], address=r[2], archived=r[3])
                for r in cur.fetchall()]


def set_building_archived(building_id, archived):
    """Update only archive metadata through the tenant-scoped connection."""
    if not isinstance(archived, bool):
        raise ValueError('Μη έγκυρη κατάσταση αρχειοθέτησης.')
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE buildings SET configuration=jsonb_set(
            configuration, '{archived}', to_jsonb(%s::boolean)),updated_at=now()
            WHERE id=%s RETURNING id""", (archived, building_id))
        if cur.fetchone() is None:
            raise ValueError('Η πολυκατοικία δεν βρέθηκε.')


def _references(cur, schema):
    """Return complete FK column pairs, including composite building-scoped keys."""
    cur.execute('''
        SELECT parent_ns.nspname, parent.relname,
               child_ns.nspname, child.relname, f.conname,
               ARRAY(SELECT a.attname FROM unnest(f.conkey) WITH ORDINALITY k(attnum, ord)
                     JOIN pg_attribute a ON a.attrelid = f.conrelid AND a.attnum = k.attnum
                     ORDER BY k.ord),
               ARRAY(SELECT a.attname FROM unnest(f.confkey) WITH ORDINALITY k(attnum, ord)
                     JOIN pg_attribute a ON a.attrelid = f.confrelid AND a.attnum = k.attnum
                     ORDER BY k.ord),
               f.confdeltype
        FROM pg_constraint f
        JOIN pg_class parent ON parent.oid = f.confrelid
        JOIN pg_namespace parent_ns ON parent_ns.oid = parent.relnamespace
        JOIN pg_class child ON child.oid = f.conrelid
        JOIN pg_namespace child_ns ON child_ns.oid = child.relnamespace
        WHERE f.contype = 'f'
          AND ((parent_ns.nspname = %s AND parent.relname = ANY(%s))
            OR (child_ns.nspname = %s AND child.relname = ANY(%s)))
    ''', (schema, ['buildings', *CONFIG_TABLES], schema, list(CONFIG_TABLES)))
    return cur.fetchall()


def _schema(cur):
    cur.execute('''SELECT n.nspname FROM pg_class c
                   JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE c.oid = 'buildings'::regclass''')
    return cur.fetchone()[0]


def _dependencies(cur, schema):
    """Recognize known composite FKs and reject unknown relationships."""
    references = _references(cur, schema)
    direct = {}
    unsafe = []
    required = set(CONFIG_TABLES) | HISTORY_TABLES
    known = set(CONFIG_TABLES)
    recognized = set()

    for parent_schema, parent, child_schema, child, constraint, child_cols, parent_cols, action in references:
        label = f'{child_schema}.{child}: {constraint}'
        pairs = frozenset(zip(child_cols, parent_cols))
        if len(child_cols) != len(parent_cols) or not child_cols:
            unsafe.append(label)
            continue
        # No cascading or SET NULL operations may affect uninspected data.
        if action not in ('a', 'r'):  # NO ACTION / RESTRICT
            unsafe.append(label)
            continue
        if parent_schema == schema and parent == 'buildings':
            if pairs not in (frozenset({('building_id', 'id')}),
                             frozenset({('company_id', 'company_id'), ('building_id', 'id')})):
                unsafe.append(label)
            else:
                direct[(child_schema, child)] = 'building_id'
        elif (parent_schema == schema and parent == 'apartments'
              and child_schema == schema and child == 'property_delivery_contacts'
              and pairs == frozenset({('building_id', 'building_id'), ('apartment_id', 'id')})):
            # Contact rows remain blockers; they are never silently deleted.
            direct[(schema, child)] = 'building_id'
        elif child_schema == schema and child in known:
            pair = CONFIG_REFERENCES.get((child, parent))
            allowed = set()
            if pair:
                allowed.add(frozenset({pair}))
                allowed.add(frozenset({('building_id', 'building_id'), pair}))
            if parent_schema != schema or pairs not in allowed:
                unsafe.append(label)
            else:
                recognized.add((child, parent))
        elif parent_schema == schema and parent in known:
            unsafe.append(label)

    if unsafe:
        raise ValueError('Μη αναγνωρισμένες εξαρτήσεις: ' + ', '.join(sorted(set(unsafe))))

    # Both composite relationships must exist before an indirect table can
    # be safely scoped by its building_id. A matching column alone is not enough.
    missing_config_refs = set(CONFIG_REFERENCES) - recognized
    if missing_config_refs:
        details = ', '.join(f'{child} → {parent}'
                            for child, parent in sorted(missing_config_refs))
        raise ValueError('Λείπουν αναμενόμενες σχέσεις παραμετροποίησης: '
                         + details + '. Η διαγραφή ακυρώθηκε.')
    for table in INDIRECT_TABLES:
        direct[(schema, table)] = 'building_id'

    # Missing tables or missing building FKs must not be mistaken for emptiness.
    cur.execute('''
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = ANY(%s)
          AND c.relkind IN ('r', 'p')
    ''', (schema, list(required)))
    present = {row[0] for row in cur.fetchall()}
    missing = required - present
    # Only the six directly linked configuration tables and the two history
    # tables need a direct FK to buildings. The indirect tables were verified
    # through their complete, building-scoped relationships above.
    direct_required = required - INDIRECT_TABLES
    missing_refs = {(schema, name) for name in direct_required} - set(direct)
    if missing or missing_refs:
        details = sorted(missing | {name for _, name in missing_refs})
        raise ValueError('Λείπουν αναμενόμενοι πίνακες ή άμεσες σχέσεις με buildings: '
                         + ', '.join(details) + '. Η διαγραφή ακυρώθηκε.')
    return direct


def _counts(cur, building_id, direct):
    counts = {}
    for (schema, table), column in sorted(direct.items()):
        cur.execute(sql.SQL('SELECT count(*) FROM {} WHERE {} = %s').format(
            sql.Identifier(schema, table), sql.Identifier(column)), (building_id,))
        counts[f'{schema}.{table}'] = cur.fetchone()[0]
    return counts


def _status(building_id, name, address, counts, schema):
    configuration = {f'{schema}.{table}' for table in CONFIG_TABLES}
    blockers = {table: count for table, count in counts.items()
                if count and table not in configuration}
    history = sum(count for table, count in counts.items()
                  if table.split('.')[-1] in HISTORY_TABLES)
    return {'id': building_id, 'name': name, 'address': address,
            'counts': counts, 'history_count': history,
            'eligible': not blockers, 'blockers': blockers}


def list_cleanup_candidates():
    """Read-only inspection. This does not reserve a building for deletion."""
    with get_connection() as conn, conn.cursor() as cur:
        schema = _schema(cur)
        direct = _dependencies(cur, schema)
        cur.execute('SELECT id, name, address FROM buildings ORDER BY name, id')
        buildings = cur.fetchall()
        return [_status(bid, name, address, _counts(cur, bid, direct), schema)
                for bid, name, address in buildings]


def delete_empty_buildings(building_ids):
    """Atomically delete selected empty buildings, rechecking under DB locks.

    The caller must explicitly supply IDs. No historical or unknown rows are
    deleted. An error rolls back the entire batch, including configuration rows.
    """
    if isinstance(building_ids, (str, bytes)):
        raise ValueError('Δώσε λίστα από IDs, όχι ένα κείμενο.')
    ids = list(building_ids)
    if not ids or any(not isinstance(bid, str) or not bid.strip() for bid in ids):
        raise ValueError('Επίλεξε τουλάχιστον μία πολυκατοικία.')
    if len(ids) != len(set(ids)):
        raise ValueError('Η επιλογή περιέχει διπλά IDs.')
    ids = sorted(ids)
    with get_connection() as conn, conn.cursor() as cur:
        schema = _schema(cur)
        # Lock known tables before inspecting FKs, so concurrent DDL cannot
        # add an unobserved dependency between discovery and deletion.
        tables = [sql.Identifier(schema, 'buildings')]
        tables += [sql.Identifier(schema, table) for table in CONFIG_TABLES]
        cur.execute(sql.SQL('LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE').format(
            sql.SQL(', ').join(tables)))
        direct = _dependencies(cur, schema)
        # Lock every other direct dependent table as well. This prevents
        # concurrent writes from invalidating the empty check.
        extras = [sql.Identifier(*key) for key in sorted(direct)
                  if key not in {(schema, name) for name in CONFIG_TABLES}
                  and key != (schema, 'buildings')]
        if extras:
            cur.execute(sql.SQL('LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE').format(
                sql.SQL(', ').join(extras)))
        cur.execute('SELECT id, name, address FROM buildings WHERE id = ANY(%s) ORDER BY id FOR UPDATE', (ids,))
        buildings = cur.fetchall()
        if {row[0] for row in buildings} != set(ids):
            raise ValueError('Κάποια επιλεγμένη πολυκατοικία δεν υπάρχει πλέον. Η διαγραφή ακυρώθηκε.')
        for bid, name, address in buildings:
            status = _status(bid, name, address, _counts(cur, bid, direct), schema)
            if not status['eligible']:
                details = ', '.join(f'{table}: {count}' for table, count in status['blockers'].items())
                raise ValueError(f'Η {name} ({bid}) έχει εξαρτημένα δεδομένα ({details}). Δεν διαγράφηκε τίποτα.')
        # Only the eight known configuration tables may be cleared.
        for table in CONFIG_TABLES:
            cur.execute(sql.SQL('DELETE FROM {} WHERE building_id = ANY(%s)').format(
                sql.Identifier(schema, table)), (ids,))
        cur.execute('DELETE FROM buildings WHERE id = ANY(%s)', (ids,))
        if cur.rowcount != len(ids):
            raise ValueError('Η διαγραφή δεν ολοκληρώθηκε. Η συναλλαγή ακυρώθηκε.')
    return len(ids)
