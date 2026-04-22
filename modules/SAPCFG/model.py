from database import get_db, get_cursor
from datetime import datetime


def get_all_configs():
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT * FROM sap_api_config ORDER BY id')
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_config_by_env(environment):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT * FROM sap_api_config WHERE environment=%s', [environment])
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def save_config(data, updated_by=None):
    conn = get_db()
    cur = get_cursor(conn)
    row_id = data.get('id')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if row_id:
        cur.execute('''UPDATE sap_api_config SET
            base_url=%s, token_url=%s, client_id=%s, client_secret=%s,
            company_code=%s, payment_term=%s,
            business_place=%s, section_code=%s, plant_code=%s,
            tax_code=%s, profit_center=%s,
            tds_gl=%s, tcs_gl=%s, round_off_gl=%s,
            credit_control_area=%s,
            is_active=%s, updated_by=%s, updated_date=%s
            WHERE id=%s''', [
            data.get('base_url'), data.get('token_url'),
            data.get('client_id'), data.get('client_secret'),
            data.get('company_code'), data.get('payment_term'),
            data.get('business_place') or None, data.get('section_code') or None,
            data.get('plant_code') or None,
            data.get('tax_code') or None, data.get('profit_center') or None,
            data.get('tds_gl') or None, data.get('tcs_gl') or None,
            data.get('round_off_gl') or None,
            data.get('credit_control_area') or None,
            data.get('is_active', 0), updated_by, now, row_id
        ])
    else:
        cur.execute('''INSERT INTO sap_api_config
            (environment, base_url, token_url, client_id, client_secret,
             company_code, payment_term,
             business_place, section_code, plant_code,
             tax_code, profit_center,
             tds_gl, tcs_gl, round_off_gl,
             credit_control_area,
             is_active, created_by, created_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id''', [
            data.get('environment'), data.get('base_url'), data.get('token_url'),
            data.get('client_id'), data.get('client_secret'),
            data.get('company_code'), data.get('payment_term'),
            data.get('business_place') or None, data.get('section_code') or None,
            data.get('plant_code') or None,
            data.get('tax_code') or None, data.get('profit_center') or None,
            data.get('tds_gl') or None, data.get('tcs_gl') or None,
            data.get('round_off_gl') or None,
            data.get('credit_control_area') or None,
            data.get('is_active', 0), updated_by, now
        ])
        row_id = cur.fetchone()['id']
    conn.commit()
    conn.close()
    return row_id


def set_active_env(environment):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('UPDATE sap_api_config SET is_active=0')
    cur.execute('UPDATE sap_api_config SET is_active=1 WHERE environment=%s', [environment])
    conn.commit()
    conn.close()


def get_active_config():
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('''
        SELECT * FROM sap_api_config
        WHERE COALESCE(is_active, 0) = 1
        ORDER BY id
        LIMIT 1
    ''')
    row = cur.fetchone()
    if not row:
        cur.execute('''
            SELECT * FROM sap_api_config
            WHERE COALESCE(base_url, '') <> ''
              AND COALESCE(client_id, '') <> ''
              AND COALESCE(client_secret, '') <> ''
            ORDER BY updated_date DESC NULLS LAST,
                     created_date DESC NULLS LAST,
                     id DESC
            LIMIT 1
        ''')
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None
