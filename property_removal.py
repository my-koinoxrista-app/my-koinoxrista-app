"""Conservative, tenant-scoped removal checks for unused properties.

No financial records are deleted, rewritten or recalculated. The repository
repeats these checks under its building row lock before deleting configuration.
"""
from decimal import Decimal, InvalidOperation

from psycopg import sql


class PropertyRemovalError(ValueError):
    pass


def _money_number(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError
        return result
    except (InvalidOperation, TypeError, ValueError):
        raise PropertyRemovalError('Μη έγκυρο ποσό ή χιλιοστά. Η διαγραφή ακυρώθηκε.') from None


def _block(message):
    raise PropertyRemovalError(message)


def _apartments(config):
    return {a['id']: a for a in config['apartments']}


def _rule(rule, config, aid, label):
    if not isinstance(rule, dict):
        _block(f'{label}: μη αναγνωρισμένος κανόνας κατανομής.')
    kind = rule.get('type')
    if kind == 'DIRECT':
        return
    if kind == 'EQUAL':
        participants = rule.get('participants')
        if not isinstance(participants, list):
            _block(f'{label}: μη αναγνωρισμένη λίστα συμμετοχών.')
        if aid in participants:
            _block(f'{label}: η ιδιοκτησία συμμετέχει σε ισόποση κατανομή.')
        return
    if kind == 'WEIGHTED':
        tables = {t['id']: t for t in config['tables']}
        table = tables.get(rule.get('table_id'))
        if table is None or aid not in table.get('weights', {}):
            _block(f'{label}: λείπει ο πίνακας ή η συμμετοχή της ιδιοκτησίας.')
        if _money_number(table['weights'][aid]) != 0:
            _block(f'{label}: μηδένισε πρώτα τα χιλιοστά της ιδιοκτησίας.')
        return
    _block(f'{label}: μη αναγνωρισμένος κανόνας κατανομής.')


def _explicit_reference(value, aid):
    """Detect known property-reference fields, not arbitrary matching prose."""
    if isinstance(value, list):
        return any(_explicit_reference(item, aid) for item in value)
    if not isinstance(value, dict):
        return False
    for key, item in value.items():
        if key in ('apartment_id', 'property_id') and item == aid:
            return True
        if key in ('direct_charges', 'allocations', 'weights') and isinstance(item, dict) and aid in item:
            return True
        if key == 'participants' and isinstance(item, list) and aid in item:
            return True
        if _explicit_reference(item, aid):
            return True
    return False


def check_configuration(config, aid):
    """Reject changes to explicitly configured allocation participation."""
    if aid not in _apartments(config):
        _block('Η ιδιοκτησία δεν βρέθηκε. Φόρτωσε ξανά την πολυκατοικία.')
    if len(config['apartments']) <= 1:
        _block('Δεν μπορεί να διαγραφεί η τελευταία ιδιοκτησία.')
    for table in config.get('tables', []):
        if _money_number(table.get('weights', {}).get(aid, '0')) != 0:
            _block('Μηδένισε πρώτα τα χιλιοστά στον πίνακα ' + table.get('name', table['id']) + '.')
    for category in config.get('categories', []):
        _rule(category['rule'], config, aid, 'Κατηγορία ' + category['name'])


def check_period(config, aid, period, label='Τρέχουσα περίοδος'):
    """Check effective rules and explicit references without changing allocations."""
    if not isinstance(period, dict) or not isinstance(period.get('expenses', []), list):
        _block(f'{label}: μη αναγνωρισμένα δεδομένα περιόδου.')
    categories = {c['id']: c for c in config['categories']}
    for expense in period.get('expenses', []):
        if not isinstance(expense, dict):
            _block(f'{label}: μη αναγνωρισμένη δαπάνη.')
        if _explicit_reference(expense, aid):
            _block(f'{label}: υπάρχει άμεση αναφορά στην ιδιοκτησία.')
        category = categories.get(expense.get('category_id'))
        if category is None:
            _block(f'{label}: υπάρχει δαπάνη με άγνωστη κατηγορία.')
        rule = expense.get('rule') or category['rule']
        _rule(rule, config, aid, label)


def _exists(cur, query, params):
    cur.execute(query, params)
    return bool(cur.fetchone()[0])


def _required_table(cur, table):
    cur.execute('SELECT to_regclass(%s)', ('public.' + table,))
    if cur.fetchone()[0] is None:
        _block('Λείπει απαραίτητος πίνακας ιστορικού: ' + table + '.')


def _direct_dependencies(cur, building_id, aid):
    """Reject unknown references; known configuration rows are handled by save."""
    cur.execute("""
        SELECT n.nspname, c.relname,
               array_agg(ca.attname::text ORDER BY ck.ord),
               array_agg(pa.attname::text ORDER BY ck.ord)
        FROM pg_constraint f
        JOIN pg_class c ON c.oid=f.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN unnest(f.conkey) WITH ORDINALITY ck(attnum,ord) ON true
        JOIN unnest(f.confkey) WITH ORDINALITY pk(attnum,ord) ON pk.ord=ck.ord
        JOIN pg_attribute ca ON ca.attrelid=f.conrelid AND ca.attnum=ck.attnum
        JOIN pg_attribute pa ON pa.attrelid=f.confrelid AND pa.attnum=pk.attnum
        WHERE f.contype='f' AND f.confrelid='public.apartments'::regclass
        GROUP BY f.oid,n.nspname,c.relname
    """)
    for schema, table, child_cols, parent_cols in cur.fetchall():
        pairs = set(zip(child_cols, parent_cols))
        known = schema == 'public' and table in ('allocation_shares', 'allocation_rule_participants') and pairs in (
            {('apartment_id', 'id')},
            {('building_id', 'building_id'), ('apartment_id', 'id')},
        )
        values = {'id': aid, 'building_id': building_id}
        if not child_cols or len(child_cols) != len(parent_cols) or any(p not in values for p in parent_cols):
            _block('Μη αναγνωρισμένη σχέση βάσης. Η διαγραφή ακυρώθηκε.')
        if known and table == 'allocation_shares':
            if _exists(cur, 'SELECT EXISTS (SELECT 1 FROM allocation_shares WHERE building_id=%s AND apartment_id=%s AND weight<>0)', (building_id, aid)):
                _block('Υπάρχουν μη μηδενικά αποθηκευμένα χιλιοστά.')
            continue
        if known and table == 'allocation_rule_participants':
            if _exists(cur, 'SELECT EXISTS (SELECT 1 FROM allocation_rule_participants WHERE building_id=%s AND apartment_id=%s)', (building_id, aid)):
                _block('Η ιδιοκτησία συμμετέχει σε αποθηκευμένο κανόνα κατανομής.')
            continue
        cur.execute("""
            SELECT attname FROM pg_attribute
            WHERE attrelid=to_regclass(%s) AND attnum>0 AND NOT attisdropped
        """, (schema + '.' + table,))
        columns = {r[0] for r in cur.fetchall()}
        if 'building_id' not in columns:
            _block('Μη αναγνωρισμένη σχέση χωρίς απομόνωση πολυκατοικίας: ' + table + '.')
        conditions = [sql.SQL('{}=%s').format(sql.Identifier(c)) for c in child_cols]
        params = [values[p] for p in parent_cols]
        if 'building_id' not in child_cols:
            conditions.append(sql.SQL('building_id=%s'))
            params.append(building_id)
        query = sql.SQL('SELECT EXISTS (SELECT 1 FROM {} WHERE {})').format(
            sql.Identifier(schema, table), sql.SQL(' AND ').join(conditions))
        if _exists(cur, query, tuple(params)):
            _block('Υπάρχουν συνδεδεμένα δεδομένα στον πίνακα ' + table + '.')

    # Also inspect public property-reference columns not protected by a FK.
    cur.execute("""
        SELECT DISTINCT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid
        WHERE n.nspname='public' AND c.relkind IN ('r','p')
          AND a.attname IN ('apartment_id','property_id') AND NOT a.attisdropped
        ORDER BY c.relname
    """)
    known = {'allocation_shares', 'allocation_rule_participants'}
    for (table,) in cur.fetchall():
        if table in known:
            continue
        cur.execute("""
            SELECT a.attname FROM pg_attribute a
            WHERE a.attrelid=to_regclass(%s) AND a.attnum>0 AND NOT a.attisdropped
        """, ('public.' + table,))
        columns = {r[0] for r in cur.fetchall()}
        if 'building_id' not in columns:
            _block('Μη αναγνωρισμένος πίνακας με αναφορές ιδιοκτησιών: ' + table + '.')
        for column in ('apartment_id', 'property_id'):
            if column in columns:
                query = sql.SQL('SELECT EXISTS (SELECT 1 FROM {} WHERE building_id=%s AND {}=%s)').format(
                    sql.Identifier('public', table), sql.Identifier(column))
                if _exists(cur, query, (building_id, aid)):
                    _block('Υπάρχουν συνδεδεμένα δεδομένα στον πίνακα ' + table + '.')


def check_database(cur, config, aid):
    """Run with the building row locked. Never use an administrator connection."""
    check_configuration(config, aid)
    bid = config['id']
    for table in ('monthly_periods', 'receipt_documents', 'issued_statements'):
        _required_table(cur, table)

    cur.execute('SELECT period_key,data FROM monthly_periods WHERE building_id=%s', (bid,))
    for key, period in cur.fetchall():
        check_period(config, aid, period, 'Αποθηκευμένη περίοδος ' + str(key))

    # A historical snapshot retains its original roster even for zero charges.
    cur.execute('SELECT configuration,report_data,period_data FROM issued_statements WHERE building_id=%s', (bid,))
    for snapshot, report, period in cur.fetchall():
        if not isinstance(snapshot, dict) or not isinstance(report, dict):
            _block('Δεν είναι δυνατός ο έλεγχος ιστορικής εκκαθάρισης.')
        for data in (snapshot, report):
            apartments = data.get('apartments')
            if not isinstance(apartments, list):
                _block('Μη αναγνωρισμένη ιστορική εκκαθάριση.')
            if any(isinstance(a, dict) and a.get('id') == aid for a in apartments):
                _block('Η ιδιοκτησία υπάρχει σε εκδοθείσα εκκαθάριση. Χρησιμοποίησε αρχειοθέτηση.')
        if _explicit_reference(report, aid) or _explicit_reference(period, aid):
            _block('Υπάρχει ιστορική οικονομική αναφορά στην ιδιοκτησία.')

    cur.execute('SELECT extracted FROM receipt_documents WHERE building_id=%s', (bid,))
    for (extracted,) in cur.fetchall():
        if _explicit_reference(extracted, aid):
            _block('Υπάρχει παραστατικό συνδεδεμένο με την ιδιοκτησία.')

    _direct_dependencies(cur, bid, aid)


def check_removal(cur, saved, candidate, aid, period_data=None):
    """Preflight using the saved configuration and the proposed configuration."""
    if saved is not None:
        if aid not in _apartments(saved):
            _block('Η ιδιοκτησία άλλαξε στη βάση. Φόρτωσε ξανά την πολυκατοικία.')
        check_database(cur, saved, aid)
    check_configuration(candidate, aid)
    if period_data is not None:
        check_period(candidate, aid, period_data)


def check_saved_removals(cur, saved, proposed):
    """Repository guard for every removed ID, before any configuration write."""
    old_ids = set(_apartments(saved))
    new_ids = set(_apartments(proposed))
    for aid in sorted(old_ids - new_ids):
        check_database(cur, saved, aid)
    if old_ids - new_ids and not new_ids:
        _block('Δεν μπορεί να διαγραφεί η τελευταία ιδιοκτησία.')
