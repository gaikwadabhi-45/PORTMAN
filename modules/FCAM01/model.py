from database import get_db, get_cursor
from datetime import datetime


def get_next_agreement_code():
    """Generate next agreement code"""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute(
        "SELECT MAX(CAST(SUBSTR(agreement_code, 4) AS INTEGER)) FROM customer_agreements WHERE agreement_code LIKE 'AGR%'"
    )
    result = cur.fetchone()['max']
    conn.close()
    next_num = (result or 0) + 1
    return f"AGR{next_num:04d}"


def get_agreement_data(page=1, size=20):
    """Get paginated agreement headers"""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT COUNT(*) FROM customer_agreements')
    total = cur.fetchone()['count']
    cur.execute('''
        SELECT * FROM customer_agreements
        ORDER BY id DESC
        LIMIT %s OFFSET %s
    ''', (size, (page-1)*size))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows], total


def save_agreement_header(data):
    """Save agreement header"""
    conn = get_db()
    cur = get_cursor(conn)
    row_id = data.get('id')

    if row_id:
        cols = [k for k in data if k not in ['id', 'agreement_code']]
        cur.execute(f'''UPDATE customer_agreements
            SET {', '.join([f'{c}=%s' for c in cols])}
            WHERE id=%s''',
            [data[c] for c in cols] + [row_id])
    else:
        data['agreement_code'] = get_next_agreement_code()
        cols = [k for k in data if k != 'id']
        cur.execute(f'''INSERT INTO customer_agreements
            ({', '.join(cols)})
            VALUES ({', '.join(['%s']*len(cols))})
            RETURNING id''',
            [data[c] for c in cols])
        row_id = cur.fetchone()['id']

    conn.commit()
    conn.close()
    return row_id, data.get('agreement_code')


def delete_agreement_header(row_id):
    """Delete agreement header and all lines (CASCADE)"""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('DELETE FROM customer_agreements WHERE id=%s', (row_id,))
    conn.commit()
    conn.close()


def get_agreement_lines(agreement_id):
    """Get all lines for an agreement"""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('''
        SELECT al.*, st.service_name as service_type_name
        FROM customer_agreement_lines al
        LEFT JOIN finance_service_types st ON al.service_type_id = st.id
        WHERE al.agreement_id = %s
        ORDER BY al.id
    ''', (agreement_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_agreement_line(data):
    """Save agreement line"""
    conn = get_db()
    cur = get_cursor(conn)
    to_none = lambda v: None if isinstance(v, str) and v.strip() == '' else v

    service_type_id = to_none(data.get('service_type_id'))
    service_name = to_none(data.get('service_name'))
    rate = to_none(data.get('rate'))
    uom = to_none(data.get('uom'))
    currency_code = to_none(data.get('currency_code'))
    min_charge = to_none(data.get('min_charge'))
    max_charge = to_none(data.get('max_charge'))
    remarks = to_none(data.get('remarks'))
    cargo_id = to_none(data.get('cargo_id'))
    cargo_name = to_none(data.get('cargo_name'))

    if data.get('id'):
        cur.execute('''UPDATE customer_agreement_lines
            SET service_type_id=%s, service_name=%s, rate=%s, uom=%s,
                currency_code=%s, min_charge=%s, max_charge=%s, remarks=%s,
                cargo_id=%s, cargo_name=%s
            WHERE id=%s''',
            [service_type_id, service_name, rate, uom, currency_code,
             min_charge, max_charge, remarks, cargo_id, cargo_name, data['id']])
        row_id = data['id']
    else:
        cur.execute('''INSERT INTO customer_agreement_lines
            (agreement_id, service_type_id, service_name, rate, uom,
             currency_code, min_charge, max_charge, remarks, cargo_id, cargo_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id''',
            [data['agreement_id'], service_type_id, service_name, rate, uom,
             currency_code, min_charge, max_charge, remarks, cargo_id, cargo_name])
        row_id = cur.fetchone()['id']

    conn.commit()
    conn.close()
    return row_id


def delete_agreement_line(row_id):
    """Delete agreement line"""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('DELETE FROM customer_agreement_lines WHERE id=%s', (row_id,))
    conn.commit()
    conn.close()


def get_customer_rate(customer_type, customer_id, service_type_id, as_of_date=None, cargo_name=None):
    """Get rate for a customer-service combination from active agreements.
    For cargo handling services, optionally match by cargo_name first."""
    if as_of_date is None:
        as_of_date = datetime.now().strftime('%Y-%m-%d')

    conn = get_db()
    cur = get_cursor(conn)

    # Try cargo-specific rate first if cargo_name provided
    if cargo_name:
        cur.execute('''
            SELECT al.rate, al.uom, al.currency_code, al.min_charge, al.max_charge
            FROM customer_agreement_lines al
            JOIN customer_agreements ah ON al.agreement_id = ah.id
            WHERE ah.customer_type = %s
              AND ah.customer_id = %s
              AND al.service_type_id = %s
              AND al.cargo_name = %s
              AND ah.agreement_status = 'Approved'
              AND ah.is_active = 1
              AND ah.valid_from <= %s
              AND (ah.valid_to IS NULL OR ah.valid_to >= %s)
            ORDER BY ah.valid_from DESC
            LIMIT 1
        ''', [customer_type, customer_id, service_type_id, cargo_name, as_of_date, as_of_date])
        row = cur.fetchone()
        if row:
            conn.close()
            return dict(row)

    # Fallback to generic rate (no cargo_name)
    cur.execute('''
        SELECT al.rate, al.uom, al.currency_code, al.min_charge, al.max_charge
        FROM customer_agreement_lines al
        JOIN customer_agreements ah ON al.agreement_id = ah.id
        WHERE ah.customer_type = %s
          AND ah.customer_id = %s
          AND al.service_type_id = %s
          AND ah.agreement_status = 'Approved'
          AND ah.is_active = 1
          AND ah.valid_from <= %s
          AND (ah.valid_to IS NULL OR ah.valid_to >= %s)
        ORDER BY ah.valid_from DESC
        LIMIT 1
    ''', [customer_type, customer_id, service_type_id, as_of_date, as_of_date])
    row = cur.fetchone()
    conn.close()

    return dict(row) if row else None
