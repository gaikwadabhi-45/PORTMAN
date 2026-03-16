from database import get_db, get_cursor

def _clean_empty(data):
    """Convert empty strings to None so timestamp/date columns get NULL."""
    for k in data:
        if data[k] == '':
            data[k] = None
    return data

def get_next_doc_num():
    import datetime
    conn = get_db()
    cur = get_cursor(conn)
    now = datetime.datetime.now()
    fy_start = now.year if now.month >= 4 else now.year - 1
    fy_suffix = f"{str(fy_start)[2:]}{str(fy_start + 1)[2:]}"  # e.g. "2526"
    prefix = f"VCN-{fy_suffix}-"
    cur.execute(
        "SELECT MAX(CAST(SPLIT_PART(vcn_doc_num, '-', 3) AS INTEGER)) FROM vcn_header WHERE vcn_doc_num LIKE %s",
        (prefix + '%',)
    )
    result = cur.fetchone()['max']
    conn.close()
    next_num = (result or 0) + 1
    return f"{prefix}{next_num:03d}"

def get_vessels():
    """Get vessels from VC01 for dropdown"""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT doc_num, vessel_name FROM vessels ORDER BY doc_num')
    rows = cur.fetchall()
    conn.close()
    return [{'value': f"{r['doc_num']}/{r['vessel_name']}", 'doc_num': r['doc_num'], 'vessel_name': r['vessel_name']} for r in rows]

def get_data(page=1, size=20, filters=None):
    conn = get_db()
    cur = get_cursor(conn)

    allowed = {'operation_type','vcn_doc_num','vessel_name','vessel_agent_name',
               'cargo_type','doc_status','doc_date','importer_exporter_name',
               'customer_name','load_port','discharge_port'}
    where_clauses, params = [], []
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
    try:
        cur.execute(f'SELECT COUNT(*) FROM vcn_header {where_sql}', params)
        total = cur.fetchone()['count']
        cur.execute(f'SELECT * FROM vcn_header {where_sql} ORDER BY id DESC LIMIT %s OFFSET %s',
                    params + [size, (page - 1) * size])
        rows = cur.fetchall()
        return [dict(r) for r in rows], total
    finally:
        conn.close()

def save_header(data):
    _clean_empty(data)
    conn = get_db()
    cur = get_cursor(conn)
    row_id = data.get('id')

    if row_id:
        cols = [k for k in data if k not in ['id', 'vcn_doc_num']]
        cur.execute(f"UPDATE vcn_header SET {', '.join([f'{c}=%s' for c in cols])} WHERE id=%s",
                   [data[c] for c in cols] + [row_id])
    else:
        data['vcn_doc_num'] = get_next_doc_num()
        cols = [k for k in data if k != 'id']
        cur.execute(f"INSERT INTO vcn_header ({', '.join(cols)}) VALUES ({', '.join(['%s']*len(cols))}) RETURNING id",
                   [data[c] for c in cols])
        row_id = cur.fetchone()['id']

    conn.commit()
    conn.close()
    return row_id, data.get('vcn_doc_num')

def delete_header(row_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('DELETE FROM vcn_header WHERE id=%s', (row_id,))
    conn.commit()
    conn.close()

# Nomination sub-table operations
def get_nominations(vcn_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT * FROM vcn_nominations WHERE vcn_id=%s ORDER BY id DESC', (vcn_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def save_nomination(data):
    _clean_empty(data)
    conn = get_db()
    cur = get_cursor(conn)
    if data.get('id'):
        cur.execute('''UPDATE vcn_nominations SET eta=%s, etd=%s, vessel_run_type=%s,
                       arrival_fore_draft=%s, arrival_after_draft=%s WHERE id=%s''',
                   [data.get('eta'), data.get('etd'), data.get('vessel_run_type'),
                    data.get('arrival_fore_draft'), data.get('arrival_after_draft'), data['id']])
        row_id = data['id']
    else:
        cur.execute('''INSERT INTO vcn_nominations (vcn_id, eta, etd, vessel_run_type, arrival_fore_draft, arrival_after_draft)
                       VALUES (%s, %s, %s, %s, %s, %s) RETURNING id''',
                   [data['vcn_id'], data.get('eta'), data.get('etd'), data.get('vessel_run_type'),
                    data.get('arrival_fore_draft'), data.get('arrival_after_draft')])
        row_id = cur.fetchone()['id']
    conn.commit()
    conn.close()
    return row_id

def delete_nomination(row_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('DELETE FROM vcn_nominations WHERE id=%s', (row_id,))
    conn.commit()
    conn.close()

# Delays sub-table operations
def get_delays(vcn_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT * FROM vcn_delays WHERE vcn_id=%s ORDER BY id DESC', (vcn_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def save_delay(data):
    _clean_empty(data)
    conn = get_db()
    cur = get_cursor(conn)
    if data.get('id'):
        cur.execute('UPDATE vcn_delays SET delay_name=%s, delay_start=%s, delay_end=%s WHERE id=%s',
                   [data.get('delay_name'), data.get('delay_start'), data.get('delay_end'), data['id']])
        row_id = data['id']
    else:
        cur.execute('INSERT INTO vcn_delays (vcn_id, delay_name, delay_start, delay_end) VALUES (%s, %s, %s, %s) RETURNING id',
                   [data['vcn_id'], data.get('delay_name'), data.get('delay_start'), data.get('delay_end')])
        row_id = cur.fetchone()['id']
    conn.commit()
    conn.close()
    return row_id

def delete_delay(row_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('DELETE FROM vcn_delays WHERE id=%s', (row_id,))
    conn.commit()
    conn.close()

# Cargo Declaration sub-table operations (updated)
def get_cargo_declarations(vcn_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT * FROM vcn_cargo_declaration WHERE vcn_id=%s ORDER BY id DESC', (vcn_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_cargo_names_for_vcn(vcn_id):
    """Get cargo names from cargo declaration for a specific VCN (for stowage plan dropdown)"""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT DISTINCT cargo_name FROM vcn_cargo_declaration WHERE vcn_id=%s AND cargo_name IS NOT NULL', (vcn_id,))
    rows = cur.fetchall()
    conn.close()
    return [r['cargo_name'] for r in rows if r['cargo_name']]

def get_all_cargo_names_for_vcn(vcn_id):
    """Get all cargo names for a VCN from both import and export declaration tables"""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('''
        SELECT DISTINCT cargo_name FROM (
            SELECT cargo_name FROM vcn_cargo_declaration WHERE vcn_id=%s AND cargo_name IS NOT NULL
            UNION
            SELECT cargo_name FROM vcn_export_cargo_declaration WHERE vcn_id=%s AND cargo_name IS NOT NULL
        ) combined ORDER BY cargo_name
    ''', (vcn_id, vcn_id))
    rows = cur.fetchall()
    conn.close()
    return [r['cargo_name'] for r in rows if r['cargo_name']]

def save_cargo_declaration(data):
    _clean_empty(data)
    conn = get_db()
    cur = get_cursor(conn)
    if data.get('id'):
        cur.execute('''UPDATE vcn_cargo_declaration SET cargo_name=%s, bl_no=%s, bl_date=%s, bl_quantity=%s,
                       quantity_uom=%s, customer_name=%s, igm_number=%s, igm_manual_number=%s, igm_date=%s WHERE id=%s''',
                   [data.get('cargo_name'), data.get('bl_no'), data.get('bl_date'), data.get('bl_quantity'),
                    data.get('quantity_uom'), data.get('customer_name'),
                    data.get('igm_number'), data.get('igm_manual_number'), data.get('igm_date'), data['id']])
        row_id = data['id']
    else:
        cur.execute('''INSERT INTO vcn_cargo_declaration (vcn_id, cargo_name, bl_no, bl_date, bl_quantity,
                       quantity_uom, customer_name, igm_number, igm_manual_number, igm_date)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id''',
                   [data['vcn_id'], data.get('cargo_name'), data.get('bl_no'), data.get('bl_date'), data.get('bl_quantity'),
                    data.get('quantity_uom'), data.get('customer_name'),
                    data.get('igm_number'), data.get('igm_manual_number'), data.get('igm_date')])
        row_id = cur.fetchone()['id']
    conn.commit()
    conn.close()
    return row_id

# Export Cargo Declaration sub-table operations
def get_export_cargo_declarations(vcn_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT * FROM vcn_export_cargo_declaration WHERE vcn_id=%s ORDER BY id DESC', (vcn_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def save_export_cargo_declaration(data):
    _clean_empty(data)
    conn = get_db()
    cur = get_cursor(conn)
    if data.get('id'):
        cur.execute('''UPDATE vcn_export_cargo_declaration SET egm_shipping_bill_number=%s, egm_shipping_bill_date=%s,
                       cargo_name=%s, customer_name=%s, bl_no=%s, bl_date=%s, bl_quantity=%s, quantity_uom=%s WHERE id=%s''',
                   [data.get('egm_shipping_bill_number'), data.get('egm_shipping_bill_date'),
                    data.get('cargo_name'), data.get('customer_name'),
                    data.get('bl_no'), data.get('bl_date'), data.get('bl_quantity'),
                    data.get('quantity_uom'), data['id']])
        row_id = data['id']
    else:
        cur.execute('''INSERT INTO vcn_export_cargo_declaration (vcn_id, egm_shipping_bill_number, egm_shipping_bill_date,
                       cargo_name, customer_name, bl_no, bl_date, bl_quantity, quantity_uom)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id''',
                   [data['vcn_id'], data.get('egm_shipping_bill_number'), data.get('egm_shipping_bill_date'),
                    data.get('cargo_name'), data.get('customer_name'),
                    data.get('bl_no'), data.get('bl_date'), data.get('bl_quantity'),
                    data.get('quantity_uom')])
        row_id = cur.fetchone()['id']
    conn.commit()
    conn.close()
    return row_id

def delete_export_cargo_declaration(row_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('DELETE FROM vcn_export_cargo_declaration WHERE id=%s', (row_id,))
    conn.commit()
    conn.close()

def get_export_cargo_names_for_vcn(vcn_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT DISTINCT cargo_name FROM vcn_export_cargo_declaration WHERE vcn_id=%s AND cargo_name IS NOT NULL', (vcn_id,))
    rows = cur.fetchall()
    conn.close()
    return [r['cargo_name'] for r in rows if r['cargo_name']]

def get_export_cargo_total_quantity(vcn_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT SUM(bl_quantity) FROM vcn_export_cargo_declaration WHERE vcn_id=%s', (vcn_id,))
    result = cur.fetchone()['sum']
    conn.close()
    return result or 0

def delete_cargo_declaration(row_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('DELETE FROM vcn_cargo_declaration WHERE id=%s', (row_id,))
    conn.commit()
    conn.close()

def get_cargo_total_quantity(vcn_id):
    """Get total BL quantity from cargo declarations for a VCN (replaces IGM total)"""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT SUM(bl_quantity) FROM vcn_cargo_declaration WHERE vcn_id=%s', (vcn_id,))
    result = cur.fetchone()['sum']
    conn.close()
    return result or 0

# Stowage Plan sub-table operations
def get_stowage_plan(vcn_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT * FROM vcn_stowage_plan WHERE vcn_id=%s ORDER BY id ASC', (vcn_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_stowage_total_quantity(vcn_id):
    """Get total stowage quantity for a VCN"""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT SUM(hatchwise_quantity) FROM vcn_stowage_plan WHERE vcn_id=%s', (vcn_id,))
    result = cur.fetchone()['sum']
    conn.close()
    return result or 0

def save_stowage_plan(data):
    _clean_empty(data)
    conn = get_db()
    cur = get_cursor(conn)

    # Validate that hatchwise quantity doesn't exceed cargo BL total
    vcn_id = data.get('vcn_id')
    if vcn_id:
        # Check operation_type to use correct cargo total
        cur.execute('SELECT operation_type FROM vcn_header WHERE id=%s', (vcn_id,))
        header_row = cur.fetchone()
        op_type = header_row['operation_type'] if header_row else None
        if op_type == 'Export':
            igm_total = get_export_cargo_total_quantity(vcn_id)
        else:
            igm_total = get_cargo_total_quantity(vcn_id)
        current_stowage_total = get_stowage_total_quantity(vcn_id)
        new_quantity = data.get('hatchwise_quantity') or 0

        # If updating, subtract the old quantity
        if data.get('id'):
            cur.execute('SELECT hatchwise_quantity FROM vcn_stowage_plan WHERE id=%s', (data['id'],))
            old_row = cur.fetchone()
            if old_row:
                current_stowage_total -= (old_row['hatchwise_quantity'] or 0)

        # Check if new total would exceed IGM quantity
        if current_stowage_total + new_quantity > igm_total:
            conn.close()
            return None, f"Total stowage quantity ({current_stowage_total + new_quantity}) cannot exceed cargo BL quantity ({igm_total})"

    if data.get('id'):
        cur.execute('UPDATE vcn_stowage_plan SET cargo_name=%s, hold_name=%s, hatchwise_quantity=%s WHERE id=%s',
                   [data.get('cargo_name'), data.get('hold_name'), data.get('hatchwise_quantity'), data['id']])
        row_id = data['id']
    else:
        cur.execute('INSERT INTO vcn_stowage_plan (vcn_id, cargo_name, hold_name, hatchwise_quantity) VALUES (%s, %s, %s, %s) RETURNING id',
                   [data['vcn_id'], data.get('cargo_name'), data.get('hold_name'), data.get('hatchwise_quantity')])
        row_id = cur.fetchone()['id']
    conn.commit()
    conn.close()
    return row_id, None

def delete_stowage_plan(row_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('DELETE FROM vcn_stowage_plan WHERE id=%s', (row_id,))
    conn.commit()
    conn.close()

def get_export_loading_totals(vcn_id):
    """Get loading totals from LDUD MV Anchorage Loading for a VCN, grouped by cargo_name for BL quantity"""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('''SELECT vo.cargo_name, SUM(vo.quantity) as total_qty
                   FROM ldud_vessel_operations vo
                   JOIN ldud_header h ON vo.ldud_id = h.id
                   WHERE h.vcn_id=%s AND vo.cargo_name IS NOT NULL
                   GROUP BY vo.cargo_name''', (vcn_id,))
    rows = cur.fetchall()
    conn.close()
    return {r['cargo_name']: float(r['total_qty'] or 0) for r in rows}


def get_hold_completion_by_vcn(vcn_id):
    """Get hold completion data from all LDUDs linked to this VCN"""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('''SELECT hc.*, h.doc_num as ldud_doc_num, h.operation_type
                   FROM ldud_hold_completion hc
                   JOIN ldud_header h ON hc.ldud_id = h.id
                   WHERE h.vcn_id=%s
                   ORDER BY h.id ASC, hc.id ASC''', (vcn_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_vessel_holds(vcn_id):
    """Return no_of_holds for the vessel linked to this VCN."""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('''
        SELECT COALESCE(v.no_of_holds, 0) AS no_of_holds
        FROM vcn_header h
        LEFT JOIN vessels v ON v.vessel_name = h.vessel_name
        WHERE h.id = %s
    ''', (vcn_id,))
    row = cur.fetchone()
    conn.close()
    return row['no_of_holds'] if row else 0


# Approval functions
def get_doc_status(record_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT doc_status FROM vcn_header WHERE id=%s', (record_id,))
    row = cur.fetchone()
    conn.close()
    return row['doc_status'] if row else None


def get_approval_eligibility(vcn_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('''SELECT operation_type, vessel_name, vessel_agent_name,
                          importer_exporter_name, cargo_type, discharge_port
                   FROM vcn_header WHERE id=%s''', (vcn_id,))
    header = cur.fetchone()
    if not header:
        conn.close()
        return {'eligible': False, 'missing': ['Record not found']}

    missing = []
    if not header['operation_type']:
        missing.append('Operation Type')
    if not header['vessel_name']:
        missing.append('Vessel Name')
    if not header['vessel_agent_name']:
        missing.append('Agent Name')
    if not header['importer_exporter_name']:
        missing.append('Stevedore Name')
    if not header['cargo_type']:
        missing.append('Cargo Type')
    if not header['discharge_port']:
        missing.append('Discharge Port')

    op_type = header['operation_type']
    if op_type == 'Export':
        cur.execute('''SELECT COUNT(*) as cnt FROM vcn_export_cargo_declaration
                       WHERE vcn_id=%s AND cargo_name IS NOT NULL AND cargo_name != \'\'
                       AND bl_quantity IS NOT NULL AND bl_quantity > 0
                       AND quantity_uom IS NOT NULL AND quantity_uom != \'\'
                       AND bl_no IS NOT NULL AND bl_no != \'\'
                       AND bl_date IS NOT NULL''', (vcn_id,))
    else:
        cur.execute('''SELECT COUNT(*) as cnt FROM vcn_cargo_declaration
                       WHERE vcn_id=%s AND cargo_name IS NOT NULL AND cargo_name != \'\'
                       AND bl_quantity IS NOT NULL AND bl_quantity > 0
                       AND quantity_uom IS NOT NULL AND quantity_uom != \'\'
                       AND bl_no IS NOT NULL AND bl_no != \'\'
                       AND bl_date IS NOT NULL''', (vcn_id,))
    if cur.fetchone()['cnt'] < 1:
        missing.append('Cargo Declaration (min 1 complete entry: cargo name, BL no, date, quantity, UOM)')

    cur.execute('''SELECT COALESCE(v.no_of_holds, 0) AS no_of_holds
                   FROM vcn_header h LEFT JOIN vessels v ON v.vessel_name = h.vessel_name
                   WHERE h.id = %s''', (vcn_id,))
    holds_row = cur.fetchone()
    no_of_holds = holds_row['no_of_holds'] if holds_row else 0

    cur.execute('''SELECT COUNT(DISTINCT hold_name) as distinct_holds
                   FROM vcn_stowage_plan WHERE vcn_id=%s
                   AND hold_name IS NOT NULL AND hold_name != \'\'
                   AND cargo_name IS NOT NULL AND cargo_name != \'\'
                   AND hatchwise_quantity IS NOT NULL AND hatchwise_quantity > 0''', (vcn_id,))
    distinct_holds = cur.fetchone()['distinct_holds'] or 0

    if no_of_holds > 0 and distinct_holds < no_of_holds:
        missing.append(f'Stowage Plan ({distinct_holds}/{no_of_holds} holds covered — all holds need cargo & quantity)')
    elif no_of_holds == 0:
        cur.execute('SELECT COUNT(*) as cnt FROM vcn_stowage_plan WHERE vcn_id=%s', (vcn_id,))
        if cur.fetchone()['cnt'] < 1:
            missing.append('Stowage Plan (minimum 1 entry required)')

    conn.close()
    return {'eligible': len(missing) == 0, 'missing': missing}


def approve_record(record_id, username):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute("UPDATE vcn_header SET doc_status='Approved' WHERE id=%s", (record_id,))
    cur.execute("""INSERT INTO approval_log (module_code, record_id, action, comment, actioned_by)
                   VALUES ('VCN01', %s, 'Approved', NULL, %s)""", (record_id, username))
    conn.commit()
    conn.close()


def send_back_to_draft(record_id, comment, username):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute("UPDATE vcn_header SET doc_status='Draft' WHERE id=%s", (record_id,))
    cur.execute("""INSERT INTO approval_log (module_code, record_id, action, comment, actioned_by)
                   VALUES ('VCN01', %s, 'Back to Draft', %s, %s)""", (record_id, comment, username))
    conn.commit()
    conn.close()


def get_approval_log(record_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute("""SELECT action, comment, actioned_by,
                          to_char(actioned_at, 'DD-MM-YYYY HH24:MI') AS actioned_at
                   FROM approval_log WHERE module_code='VCN01' AND record_id=%s
                   ORDER BY actioned_at DESC""", (record_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]
