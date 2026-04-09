from database import get_db, get_cursor

TABLE = 'vessel_customers'

def get_all():
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute(f'SELECT name FROM {TABLE} ORDER BY name')
    rows = cur.fetchall()
    conn.close()
    return [r['name'] for r in rows]

def get_data(page=1, size=20):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute(f'SELECT COUNT(*) FROM {TABLE}')
    total = cur.fetchone()['count']
    cur.execute(f'SELECT * FROM {TABLE} ORDER BY id DESC LIMIT %s OFFSET %s', (size, (page-1)*size))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows], total

def save_data(data):
    conn = get_db()
    cur = get_cursor(conn)
    if data.get('id'):
        cur.execute(f'''UPDATE {TABLE} SET
            name=%s, sap_customer_code=%s, company_code=%s, gl_code=%s,
            gstin=%s, gst_state_code=%s, gst_state_name=%s,
            pan=%s, cin=%s, billing_address=%s, city=%s, pincode=%s,
            contact_person=%s, contact_email=%s, contact_phone=%s, default_currency=%s,
            virtual_account_number=%s
            WHERE id=%s''',
            [data.get('name', ''), data.get('sap_customer_code'), data.get('company_code'),
             data.get('gl_code'), data.get('gstin'),
             data.get('gst_state_code'), data.get('gst_state_name'), data.get('pan'), data.get('cin'),
             data.get('billing_address'), data.get('city'), data.get('pincode'),
             data.get('contact_person'), data.get('contact_email'), data.get('contact_phone'),
             data.get('default_currency', 'INR'), data.get('virtual_account_number'), data['id']])
        row_id = data['id']
    else:
        cur.execute(f'''INSERT INTO {TABLE}
            (name, sap_customer_code, company_code, gl_code, gstin, gst_state_code, gst_state_name,
             pan, cin, billing_address, city, pincode, contact_person, contact_email, contact_phone,
             default_currency, virtual_account_number)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id''',
            [data.get('name', ''), data.get('sap_customer_code'), data.get('company_code'),
             data.get('gl_code'), data.get('gstin'),
             data.get('gst_state_code'), data.get('gst_state_name'), data.get('pan'), data.get('cin'),
             data.get('billing_address'), data.get('city'), data.get('pincode'),
             data.get('contact_person'), data.get('contact_email'), data.get('contact_phone'),
             data.get('default_currency', 'INR'), data.get('virtual_account_number')])
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
