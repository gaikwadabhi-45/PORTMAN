"""Go-live cutover logic: seed numbers, mark items billed, lock. Pure helpers
have no DB dependency so they are unit-testable; DB functions open their own
connection like the rest of the codebase."""
from database import get_db, get_cursor, get_module_config, save_module_config
import json

# cargo_source_type -> (declaration table, total-quantity column)
CARGO_SOURCES = {
    'VCN_IMPORT': ('vcn_cargo_declaration', 'bl_quantity'),
    'VCN_EXPORT': ('vcn_export_cargo_declaration', 'bl_quantity'),
    'MBC':        ('mbc_customer_details', 'quantity'),
}


def cargo_source(source_type):
    """Map a cargo_source_type to its (table, qty_column), or None if unknown."""
    return CARGO_SOURCES.get(source_type)


def validate_start_seq(start_seq, current_max):
    """A cutover start number must be a positive integer strictly greater than
    the highest number already issued (else it would be silently ignored)."""
    if not isinstance(start_seq, int) or start_seq <= 0:
        return False, 'Start number must be a positive integer.'
    if start_seq <= (current_max or 0):
        return False, (f'Start number must be greater than the highest number '
                       f'already issued ({current_max or 0}).')
    return True, ''


# ===== Lock state + audit =====

def is_locked():
    cfg = get_module_config('ADMIN') or {}
    return str(cfg.get('cutover_locked', '0')) == '1'


def set_lock(locked, username):
    cfg = get_module_config('ADMIN') or {}
    cfg['cutover_locked'] = '1' if locked else '0'
    save_module_config('ADMIN', cfg)
    write_audit('lock' if locked else 'unlock', {}, username)


def write_audit(action, details, username):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute(
        'INSERT INTO cutover_audit (action, details, performed_by) VALUES (%s, %s, %s)',
        [action, json.dumps(details), username])
    conn.commit()
    conn.close()


# ===== Seed read/write =====

def get_seeds():
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT * FROM cutover_seed ORDER BY seed_type, doc_series, financial_year')
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _current_invoice_max(cur, doc_series, financial_year):
    cur.execute(
        'SELECT MAX(doc_series_seq) AS m FROM invoice_header WHERE doc_series=%s AND financial_year=%s',
        [doc_series, financial_year])
    return (cur.fetchone()['m'] or 0)


def _current_bill_max(cur):
    cur.execute(
        "SELECT MAX(CAST(SUBSTR(bill_number, 5) AS INTEGER)) AS m FROM bill_header WHERE bill_number LIKE 'BILL%%'")
    return (cur.fetchone()['m'] or 0)


def set_invoice_seed(doc_series, financial_year, start_seq, username):
    """Upsert an invoice seed after validating against the current max.
    Returns (ok, message)."""
    if is_locked():
        return False, 'Cutover is locked.'
    conn = get_db()
    cur = get_cursor(conn)
    current_max = _current_invoice_max(cur, doc_series, financial_year)
    ok, msg = validate_start_seq(start_seq, current_max)
    if not ok:
        conn.close()
        return False, msg
    cur.execute('''
        INSERT INTO cutover_seed (seed_type, doc_series, financial_year, start_seq, created_by, updated_by, updated_at)
        VALUES ('invoice', %s, %s, %s, %s, %s, now())
        ON CONFLICT (seed_type, doc_series, financial_year)
        DO UPDATE SET start_seq=EXCLUDED.start_seq, updated_by=EXCLUDED.updated_by, updated_at=now()
    ''', [doc_series, financial_year, start_seq, username, username])
    conn.commit()
    conn.close()
    write_audit('set_invoice_seed',
                {'doc_series': doc_series, 'financial_year': financial_year, 'start_seq': start_seq},
                username)
    return True, ''


def set_bill_seed(start_seq, username):
    if is_locked():
        return False, 'Cutover is locked.'
    conn = get_db()
    cur = get_cursor(conn)
    current_max = _current_bill_max(cur)
    ok, msg = validate_start_seq(start_seq, current_max)
    if not ok:
        conn.close()
        return False, msg
    cur.execute('''
        INSERT INTO cutover_seed (seed_type, doc_series, financial_year, start_seq, created_by, updated_by, updated_at)
        VALUES ('bill', '', '', %s, %s, %s, now())
        ON CONFLICT (seed_type, doc_series, financial_year)
        DO UPDATE SET start_seq=EXCLUDED.start_seq, updated_by=EXCLUDED.updated_by, updated_at=now()
    ''', [start_seq, username, username])
    conn.commit()
    conn.close()
    write_audit('set_bill_seed', {'start_seq': start_seq}, username)
    return True, ''
