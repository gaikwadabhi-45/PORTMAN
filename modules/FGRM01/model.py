from database import get_db, get_cursor
from datetime import datetime


def get_all_gst_rates():
    """Get all active GST rates for dropdown"""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('''
        SELECT id, rate_name, cgst_rate, sgst_rate, igst_rate, is_default
        FROM gst_rates
        WHERE is_active = 1
        ORDER BY is_default DESC, igst_rate
    ''')
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_gst_rate_data(page=1, size=20):
    """Get paginated GST rate data"""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT COUNT(*) FROM gst_rates')
    total = cur.fetchone()['count']
    cur.execute('''
        SELECT * FROM gst_rates
        ORDER BY igst_rate, id DESC
        LIMIT %s OFFSET %s
    ''', (size, (page-1)*size))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows], total


def save_gst_rate(data):
    """Save GST rate record"""
    conn = get_db()
    cur = get_cursor(conn)

    is_default = data.get('is_default', False)
    if is_default and str(is_default) in ('1', 'true', 'True', True):
        cur.execute("UPDATE gst_rates SET is_default = FALSE WHERE is_default = TRUE")
        is_default = True
    else:
        is_default = False

    if data.get('id'):
        cur.execute('''
            UPDATE gst_rates
            SET rate_name=%s, cgst_rate=%s, sgst_rate=%s, igst_rate=%s,
                effective_from=%s, effective_to=%s, is_active=%s, is_default=%s
            WHERE id=%s
        ''', [
            data.get('rate_name'),
            data.get('cgst_rate'),
            data.get('sgst_rate'),
            data.get('igst_rate'),
            data.get('effective_from'),
            data.get('effective_to'),
            data.get('is_active', 1),
            is_default,
            data['id']
        ])
        row_id = data['id']
    else:
        cur.execute('''
            INSERT INTO gst_rates
            (rate_name, cgst_rate, sgst_rate, igst_rate, effective_from, effective_to, is_active, is_default, created_by, created_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        ''', [
            data.get('rate_name'),
            data.get('cgst_rate'),
            data.get('sgst_rate'),
            data.get('igst_rate'),
            data.get('effective_from'),
            data.get('effective_to'),
            data.get('is_active', 1),
            is_default,
            data.get('created_by'),
            datetime.now().strftime('%Y-%m-%d')
        ])
        row_id = cur.fetchone()['id']

    conn.commit()
    conn.close()
    return row_id


def delete_gst_rate(row_id):
    """Delete GST rate record"""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('DELETE FROM gst_rates WHERE id=%s', (row_id,))
    conn.commit()
    conn.close()


def get_gst_rate_by_id(rate_id):
    """Get GST rate details by ID"""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT * FROM gst_rates WHERE id = %s', [rate_id])
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None
