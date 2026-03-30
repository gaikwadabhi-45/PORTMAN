from database import get_db, get_cursor
from datetime import datetime


def get_all_lines(page=1, size=20, equipment_name=None, filters=None):
    conn = get_db()
    cur = get_cursor(conn)
    offset = (page - 1) * size

    allowed = {'entry_date', 'shift', 'source_display', 'barge_name', 'cargo_name',
               'delay_name', 'berth_name', 'operator_name', 'route_name'}
    where_clauses, params = [], []
    where_clauses.append('(is_deleted IS NOT TRUE)')

    if equipment_name:
        where_clauses.append('equipment_name = %s')
        params.append(equipment_name)

    for f in (filters or []):
        field = f.get('field', '')
        if field not in allowed:
            continue
        ftype = f.get('type')
        if ftype == 'contains' and f.get('value'):
            where_clauses.append(f"{field} ILIKE %s")
            params.append(f"%{f['value']}%")
        elif ftype == 'multi' and f.get('values'):
            ph = ','.join(['%s'] * len(f['values']))
            where_clauses.append(f"{field} IN ({ph})")
            params.extend(f['values'])
        elif ftype == 'range':
            if f.get('from'):
                where_clauses.append(f"{field} >= %s")
                params.append(f['from'])
            if f.get('to'):
                where_clauses.append(f"{field} <= %s")
                params.append(f['to'])

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    cur.execute(f'SELECT COUNT(*) as cnt FROM lueu_lines {where_sql}', params)
    total = cur.fetchone()['cnt']
    cur.execute(f'SELECT * FROM lueu_lines {where_sql} ORDER BY id DESC LIMIT %s OFFSET %s',
                params + [size, offset])
    rows = [dict(r) for r in cur.fetchall()]

    # Look up customer names from cargo declarations for VCN/MBC sources
    # and flag multi-customer sources
    source_keys = set()
    for r in rows:
        if r.get('source_type') and r.get('source_id'):
            source_keys.add((r['source_type'], r['source_id']))

    # Build map of (source_type, source_id) -> {cargo_name: customer_name}
    source_customer_map = {}  # key -> {cargo_name: customer_name}
    source_multi_customer = {}  # key -> bool (has multiple customers)

    for src_type, src_id in source_keys:
        cargo_customers = {}
        if src_type == 'VCN':
            cur.execute("""
                SELECT cargo_name, customer_name FROM vcn_cargo_declaration WHERE vcn_id = %s AND customer_name IS NOT NULL
                UNION ALL
                SELECT cargo_name, customer_name FROM vcn_export_cargo_declaration WHERE vcn_id = %s AND customer_name IS NOT NULL
            """, [src_id, src_id])
            for cr in cur.fetchall():
                if cr['cargo_name'] and cr['customer_name']:
                    cargo_customers[cr['cargo_name']] = cr['customer_name']
        elif src_type == 'MBC':
            cur.execute("""
                SELECT cargo_name, customer_name FROM mbc_customer_details WHERE mbc_id = %s AND customer_name IS NOT NULL
            """, [src_id])
            for cr in cur.fetchall():
                if cr['customer_name']:
                    cargo_customers[cr.get('cargo_name') or '_all'] = cr['customer_name']

        source_customer_map[(src_type, src_id)] = cargo_customers
        unique_customers = set(cargo_customers.values())
        source_multi_customer[(src_type, src_id)] = len(unique_customers) > 1

    # Enrich rows with customer_name (only when multi-customer)
    for r in rows:
        key = (r.get('source_type'), r.get('source_id'))
        is_multi = source_multi_customer.get(key, False)
        r['_multi_customer'] = is_multi
        if is_multi:
            cargo_customers = source_customer_map.get(key, {})
            r['customer_name'] = cargo_customers.get(r.get('cargo_name'), '')
        else:
            r['customer_name'] = ''

    conn.close()

    return {
        'data': rows,
        'last_page': (total + size - 1) // size,
        'total': total
    }


def save_line(data):
    conn = get_db()
    cur = get_cursor(conn)

    line_id = data.get('id')

    if line_id:
        cur.execute('''
            UPDATE lueu_lines SET
                source_type = %s, source_id = %s, source_display = %s, barge_name = %s,
                equipment_name = %s, operator_name = %s, delay_name = %s, cargo_name = %s,
                operation_type = %s, quantity = %s, quantity_uom = %s, route_name = %s,
                start_time = %s, end_time = %s, entry_date = %s,
                shift = %s, from_time = %s, to_time = %s, system_name = %s,
                berth_name = %s, shift_incharge = %s, remarks = %s
            WHERE id = %s
        ''', [
            data.get('source_type'), data.get('source_id'), data.get('source_display'),
            data.get('barge_name'), data.get('equipment_name'), data.get('operator_name'),
            data.get('delay_name'), data.get('cargo_name'), data.get('operation_type'),
            data.get('quantity'), data.get('quantity_uom'), data.get('route_name'),
            data.get('start_time'), data.get('end_time'), data.get('entry_date'),
            data.get('shift'), data.get('from_time'), data.get('to_time'), data.get('system_name'),
            data.get('berth_name'), data.get('shift_incharge'), data.get('remarks'), line_id
        ])
    else:
        cur.execute('''
            INSERT INTO lueu_lines
            (source_type, source_id, source_display, barge_name, equipment_name, operator_name,
             delay_name, cargo_name, operation_type, quantity, quantity_uom, route_name,
             start_time, end_time, entry_date, created_by, created_date,
             shift, from_time, to_time, system_name, berth_name, shift_incharge, remarks)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        ''', [
            data.get('source_type'), data.get('source_id'), data.get('source_display'),
            data.get('barge_name'), data.get('equipment_name'), data.get('operator_name'),
            data.get('delay_name'), data.get('cargo_name'), data.get('operation_type'),
            data.get('quantity'), data.get('quantity_uom'), data.get('route_name'),
            data.get('start_time'), data.get('end_time'), data.get('entry_date'),
            data.get('created_by'), datetime.now().strftime('%Y-%m-%d'),
            data.get('shift'), data.get('from_time'), data.get('to_time'), data.get('system_name'),
            data.get('berth_name'), data.get('shift_incharge'), data.get('remarks')
        ])
        line_id = cur.fetchone()['id']

    conn.commit()
    conn.close()
    return line_id


def soft_delete_lines(ids, username=None):
    """Soft-delete EU lines. Returns list of dicts for lines that were billed+invoiced,
    so the caller can trigger auto-CN creation.
    Each dict: {eu_line_id, eu_line (full row), bill_line_id, bill_id, invoice_id, invoice_number}
    """
    conn = get_db()
    cur = get_cursor(conn)
    today = datetime.now().strftime('%Y-%m-%d')

    invoiced_lines = []

    for line_id in ids:
        # Fetch the line first to check billing status
        cur.execute('SELECT * FROM lueu_lines WHERE id = %s AND (is_deleted IS NOT TRUE)', [line_id])
        line = cur.fetchone()
        if not line:
            continue

        # Check if this line is referenced by any bill_lines that are in an invoice
        cur.execute('''
            SELECT
                bl.id AS bill_line_id,
                bl.bill_id,
                ibm.invoice_id,
                ih.invoice_number
            FROM bill_lines bl
            JOIN invoice_bill_mapping ibm ON ibm.bill_id = bl.bill_id
            JOIN invoice_header ih ON ih.id = ibm.invoice_id
            WHERE bl.eu_line_id = %s
              AND ih.invoice_status NOT IN ('Cancelled')
        ''', [line_id])
        invoice_refs = [dict(r) for r in cur.fetchall()]

        for ref in invoice_refs:
            ref['eu_line_id'] = line_id
            ref['eu_line'] = dict(line)
            invoiced_lines.append(ref)

        # Soft-delete regardless
        cur.execute('''
            UPDATE lueu_lines
            SET is_deleted = TRUE, deleted_by = %s, deleted_date = %s
            WHERE id = %s
        ''', [username, today, line_id])

    conn.commit()
    conn.close()
    return invoiced_lines


def split_line(line_id, split_qty, split_remark, created_by=None):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT * FROM lueu_lines WHERE id = %s', [line_id])
    parent = cur.fetchone()
    if not parent:
        conn.close()
        return None
    parent = dict(parent)
    original_qty = float(parent.get('quantity') or 0)
    split_qty = float(split_qty)
    remaining_qty = original_qty - split_qty

    # Mark parent as split, update its quantity to the remaining
    cur.execute('''UPDATE lueu_lines SET is_split = TRUE, quantity = %s WHERE id = %s''',
                [remaining_qty, line_id])

    # Create child line with split quantity
    cur.execute('''
        INSERT INTO lueu_lines
        (source_type, source_id, source_display, barge_name, equipment_name, operator_name,
         delay_name, cargo_name, operation_type, quantity, quantity_uom, route_name,
         start_time, end_time, entry_date, created_by, created_date,
         shift, from_time, to_time, system_name, berth_name, shift_incharge, remarks,
         is_split, parent_line_id, split_quantity, split_remark)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s)
        RETURNING id
    ''', [
        parent.get('source_type'), parent.get('source_id'), parent.get('source_display'),
        parent.get('barge_name'), parent.get('equipment_name'), parent.get('operator_name'),
        parent.get('delay_name'), parent.get('cargo_name'), parent.get('operation_type'),
        split_qty, parent.get('quantity_uom'), parent.get('route_name'),
        parent.get('start_time'), parent.get('end_time'), parent.get('entry_date'),
        created_by, datetime.now().strftime('%Y-%m-%d'),
        parent.get('shift'), parent.get('from_time'), parent.get('to_time'),
        parent.get('system_name'), parent.get('berth_name'), parent.get('shift_incharge'),
        split_remark or parent.get('remarks'),
        line_id, split_qty, split_remark
    ])
    child_id = cur.fetchone()['id']

    # Also update parent's split fields
    cur.execute('''UPDATE lueu_lines SET split_quantity = %s, split_remark = %s WHERE id = %s''',
                [remaining_qty, 'Parent line (split)', line_id])

    conn.commit()
    conn.close()
    return {'child_id': child_id, 'parent_qty': remaining_qty, 'child_qty': split_qty}


def get_vcn_options():
    """Get VCN entries with vessel name and anchored time for dropdown"""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('''
        SELECT h.id, h.vcn_doc_num, h.vessel_name, a.anchorage_arrival
        FROM vcn_header h
        LEFT JOIN vcn_anchorage a ON h.id = a.vcn_id
        ORDER BY h.vcn_doc_num DESC
    ''')
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_mbc_options():
    """Get MBC entries for dropdown with doc_date and cargo_name"""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT id, doc_num, mbc_name, doc_date, cargo_name FROM mbc_header ORDER BY doc_num DESC')
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_vcn_barges(vcn_id):
    """Get barges from a specific VCN's LDUD barge lines as 'barge_name / trip_number'"""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT id FROM ldud_header WHERE vcn_id = %s', [vcn_id])
    ldud = cur.fetchone()
    if ldud:
        cur.execute('''
            SELECT barge_name, trip_number FROM ldud_barge_lines
            WHERE ldud_id = %s AND barge_name IS NOT NULL AND barge_name != ''
            ORDER BY trip_number, barge_name
        ''', [ldud['id']])
        rows = cur.fetchall()
        conn.close()
        seen = set()
        result = []
        for r in rows:
            trip = r['trip_number'] or ''
            display = f"{r['barge_name']} / {trip}" if trip else r['barge_name']
            if display not in seen:
                seen.add(display)
                result.append(display)
        return result
    conn.close()
    return []


def get_mbc_names():
    """Get all MBC names from master"""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT mbc_name FROM mbc_master ORDER BY mbc_name')
    rows = cur.fetchall()
    conn.close()
    return [r['mbc_name'] for r in rows]


def get_barge_cargos(vcn_id, barge_name):
    """Get cargo names for a specific barge from a VCN's LDUD"""
    # Strip trip number if barge_name is in "barge / trip" format
    if ' / ' in barge_name:
        barge_name = barge_name.split(' / ')[0].strip()
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT id FROM ldud_header WHERE vcn_id = %s', [vcn_id])
    ldud = cur.fetchone()
    cargos = []
    if ldud:
        cur.execute('''
            SELECT DISTINCT cargo_name FROM ldud_barge_lines
            WHERE ldud_id = %s AND barge_name = %s AND cargo_name IS NOT NULL AND cargo_name != ''
        ''', [ldud['id'], barge_name])
        cargos = [r['cargo_name'] for r in cur.fetchall()]
    conn.close()
    return cargos
