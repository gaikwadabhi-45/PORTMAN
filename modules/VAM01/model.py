from database import get_db, get_cursor

TABLE = 'vessel_agents'

FIELDS = [
    'name', 'sap_customer_code', 'company_code', 'gl_code',
    'gstin', 'gst_state_code', 'gst_state_name', 'pan', 'cin',
    'billing_address', 'city', 'pincode',
    'contact_person', 'contact_email', 'contact_phone',
    'default_currency', 'is_active', 'virtual_account_number'
]

def get_data(page=1, size=20):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute(f'SELECT COUNT(*) FROM {TABLE}')
    total = cur.fetchone()['count']
    cur.execute(f'SELECT * FROM {TABLE} ORDER BY id DESC LIMIT %s OFFSET %s', (size, (page-1)*size))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows], total

def get_all():
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute(f"SELECT name FROM {TABLE} WHERE name IS NOT NULL AND name != '' ORDER BY name ASC")
    rows = cur.fetchall()
    conn.close()
    return [r['name'] for r in rows]

def save_data(data):
    conn = get_db()
    cur = get_cursor(conn)
    row_id = data.get('id')
    vals = [data.get(f) for f in FIELDS]

    if row_id:
        sets = ', '.join(f'{f}=%s' for f in FIELDS)
        cur.execute(f'UPDATE {TABLE} SET {sets} WHERE id=%s', vals + [row_id])
    else:
        cols = ', '.join(FIELDS)
        phs = ', '.join('%s' for _ in FIELDS)
        cur.execute(f'INSERT INTO {TABLE} ({cols}) VALUES ({phs}) RETURNING id', vals)
        row_id = cur.fetchone()['id']

    conn.commit()
    conn.close()
    return row_id

def delete_data(row_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute(f'DELETE FROM {TABLE} WHERE id=%s', (row_id,))
    conn.commit()
    conn.close()
