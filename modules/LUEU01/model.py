from database import get_db, get_cursor
from datetime import datetime, date, timedelta


def get_all_lines(page=1, size=20, equipment_name=None, filters=None):
    conn = get_db()
    cur = get_cursor(conn)
    offset = (page - 1) * size

    allowed = {'entry_date', 'shift', 'source_display', 'barge_name', 'cargo_name',
               'delay_name', 'berth_name', 'operator_name', 'route_name'}
    where_clauses, params = [], []

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

    # Coerce blank strings to None for numeric columns — Postgres rejects '' on real/integer.
    def _num(key):
        v = data.get(key)
        if v is None or (isinstance(v, str) and v.strip() == ''):
            return None
        return v
    data['quantity']  = _num('quantity')
    data['source_id'] = _num('source_id')

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
    """Soft-delete lueu lines. Returns empty list (billing no longer tracked via lueu_lines)."""
    conn = get_db()
    cur = get_cursor(conn)
    today = datetime.now().strftime('%Y-%m-%d')
    for line_id in ids:
        cur.execute('''
            UPDATE lueu_lines
            SET is_deleted = TRUE, deleted_by = %s, deleted_date = %s
            WHERE id = %s AND (is_deleted IS NOT TRUE)
        ''', [username, today, line_id])
    conn.commit()
    conn.close()
    return []   # caller checks for invoiced_lines to trigger auto-CN; none here


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
    """Get VCN entries with vessel name and anchored time for dropdown."""
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
    """Get MBC entries for dropdown — excludes MBCs where LUEU handled qty >= BL qty."""
    conn = get_db()
    cur = get_cursor(conn)

    # BL quantity per MBC (customer_details sum if rows exist, else header bl_quantity)
    cur.execute('''
        SELECT m.id, m.doc_num, m.mbc_name, m.doc_date, m.cargo_name,
               CASE WHEN COUNT(cd.id) > 0 THEN COALESCE(SUM(cd.quantity), 0)
                    ELSE COALESCE(m.bl_quantity, 0) END AS bl_qty
        FROM mbc_header m
        LEFT JOIN mbc_customer_details cd ON cd.mbc_id = m.id
        GROUP BY m.id, m.doc_num, m.mbc_name, m.doc_date, m.cargo_name, m.bl_quantity
        ORDER BY m.doc_num DESC
    ''')
    mbcs = cur.fetchall()

    # LUEU handled quantity per MBC
    cur.execute('''
        SELECT source_id, COALESCE(SUM(quantity), 0) AS handled_qty
        FROM lueu_lines
        WHERE source_type = 'MBC' AND (is_deleted IS NOT TRUE)
        GROUP BY source_id
    ''')
    handled_map = {r['source_id']: float(r['handled_qty'] or 0) for r in cur.fetchall()}

    conn.close()

    result = []
    for m in mbcs:
        bl = float(m['bl_qty'] or 0)
        handled = handled_map.get(m['id'], 0)
        if bl > 0 and handled >= bl:
            continue
        result.append(dict(m))
    return result


def get_vcn_barges(vcn_id):
    """Get barge trips for a VCN — excludes trips where handled qty >= discharge_quantity."""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT id FROM ldud_header WHERE vcn_id = %s', [vcn_id])
    ldud = cur.fetchone()
    if ldud:
        ldud_id = ldud['id']
        cur.execute('''
            SELECT barge_name, trip_number, COALESCE(discharge_quantity, 0) AS expected_qty
            FROM ldud_barge_lines
            WHERE ldud_id = %s AND barge_name IS NOT NULL AND barge_name != ''
            ORDER BY trip_number, barge_name
        ''', [ldud_id])
        trip_rows = cur.fetchall()

        # Handled quantity per barge/trip display label in LUEU
        cur.execute('''
            SELECT barge_name, COALESCE(SUM(quantity), 0) AS handled_qty
            FROM lueu_lines
            WHERE source_type = 'VCN' AND source_id = %s AND (is_deleted IS NOT TRUE)
              AND barge_name IS NOT NULL
            GROUP BY barge_name
        ''', [vcn_id])
        handled_map = {r['barge_name']: float(r['handled_qty'] or 0) for r in cur.fetchall()}

        conn.close()
        seen = set()
        result = []
        for r in trip_rows:
            trip = r['trip_number'] or ''
            display = f"{r['barge_name']} / {trip}" if trip else r['barge_name']
            expected = float(r['expected_qty'] or 0)
            handled = handled_map.get(display, 0)
            if expected > 0 and handled >= expected:
                continue
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


def get_bl_progress(source_type, source_id):
    """Return BL declared qty vs handled qty per cargo for a VCN or MBC source."""
    conn = get_db()
    cur = get_cursor(conn)

    declared = []
    if source_type == 'VCN':
        cur.execute('''
            SELECT COALESCE(cargo_name, '') as cargo_name, COALESCE(bl_quantity, 0) as bl_qty, 'Import' as decl_type
            FROM vcn_cargo_declaration WHERE vcn_id = %s
        ''', [source_id])
        for r in cur.fetchall():
            declared.append({'cargo_name': r['cargo_name'], 'bl_qty': float(r['bl_qty'] or 0), 'decl_type': 'Import'})
        cur.execute('''
            SELECT COALESCE(cargo_name, '') as cargo_name, COALESCE(bl_quantity, 0) as bl_qty, 'Export' as decl_type
            FROM vcn_export_cargo_declaration WHERE vcn_id = %s
        ''', [source_id])
        for r in cur.fetchall():
            declared.append({'cargo_name': r['cargo_name'], 'bl_qty': float(r['bl_qty'] or 0), 'decl_type': 'Export'})
    elif source_type == 'MBC':
        cur.execute('''
            SELECT COALESCE(cargo_name, '') as cargo_name, COALESCE(quantity, 0) as bl_qty
            FROM mbc_customer_details WHERE mbc_id = %s
        ''', [source_id])
        for r in cur.fetchall():
            declared.append({'cargo_name': r['cargo_name'], 'bl_qty': float(r['bl_qty'] or 0), 'decl_type': 'MBC'})

    # Sum handled quantities from lueu_lines per cargo (exclude deleted)
    cur.execute('''
        SELECT COALESCE(cargo_name, '') as cargo_name,
               COALESCE(SUM(quantity), 0) as handled_qty,
               MAX(quantity_uom) as uom
        FROM lueu_lines
        WHERE source_type = %s AND source_id = %s AND (is_deleted IS NOT TRUE)
        GROUP BY cargo_name
    ''', [source_type, source_id])
    handled_map = {}
    uom_map = {}
    for r in cur.fetchall():
        handled_map[r['cargo_name']] = float(r['handled_qty'] or 0)
        uom_map[r['cargo_name']] = r['uom'] or ''

    conn.close()

    result = []
    seen = set()
    for d in declared:
        cargo = d['cargo_name']
        bl_qty = d['bl_qty']
        handled = handled_map.get(cargo, 0)
        seen.add(cargo)
        result.append({
            'cargo_name': cargo,
            'bl_qty': round(bl_qty, 3),
            'handled_qty': round(handled, 3),
            'uom': uom_map.get(cargo, ''),
            'remaining': round(bl_qty - handled, 3),
            'exceeded': handled > bl_qty and bl_qty > 0,
            'exceeded_by': round(max(0.0, handled - bl_qty), 3),
            'decl_type': d.get('decl_type', ''),
        })

    # Handled cargos with no declaration at all
    for cargo, handled in handled_map.items():
        if cargo not in seen:
            result.append({
                'cargo_name': cargo,
                'bl_qty': 0,
                'handled_qty': round(handled, 3),
                'uom': uom_map.get(cargo, ''),
                'remaining': round(-handled, 3),
                'exceeded': True,
                'exceeded_by': round(handled, 3),
                'decl_type': 'No Declaration',
            })

    return result


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


def get_dashboard_data():
    """Return all data for the LUEU01 operations dashboard."""
    conn = get_db()
    cur = get_cursor(conn)

    today = date.today()
    yesterday = today - timedelta(days=1)
    month_start = today.replace(day=1)
    # Financial year starts April 1
    fy_start = date(today.year if today.month >= 4 else today.year - 1, 4, 1)

    today_s     = today.strftime('%Y-%m-%d')
    yesterday_s = yesterday.strftime('%Y-%m-%d')
    month_start_s = month_start.strftime('%Y-%m-%d')
    fy_start_s  = fy_start.strftime('%Y-%m-%d')

    # ── KPI stats ────────────────────────────────────────────────────────────
    def _sum_qty(from_date, to_date=None):
        if to_date:
            cur.execute(
                "SELECT COALESCE(SUM(quantity),0) AS t FROM lueu_lines "
                "WHERE entry_date >= %s AND entry_date <= %s AND (is_deleted IS NOT TRUE)",
                [from_date, to_date]
            )
        else:
            cur.execute(
                "SELECT COALESCE(SUM(quantity),0) AS t FROM lueu_lines "
                "WHERE entry_date = %s AND (is_deleted IS NOT TRUE)",
                [from_date]
            )
        return float(cur.fetchone()['t'] or 0)

    kpis = {
        'ytd':       round(_sum_qty(fy_start_s, today_s), 2),
        'mtd':       round(_sum_qty(month_start_s, today_s), 2),
        'yesterday': round(_sum_qty(yesterday_s), 2),
        'today':     round(_sum_qty(today_s), 2),
    }

    # ── Active VCNs ──────────────────────────────────────────────────────────
    cur.execute('''
        SELECT
            v.id, v.vcn_doc_num, v.vessel_name, v.doc_status,
            cd.cargo_name,
            COALESCE(cd.bl_quantity, 0) AS bl_quantity,
            COALESCE(cd.quantity_uom, '') AS uom,
            'Import' AS decl_type
        FROM vcn_header v
        JOIN vcn_cargo_declaration cd ON cd.vcn_id = v.id
        WHERE v.doc_status != 'Closed'
        UNION ALL
        SELECT
            v.id, v.vcn_doc_num, v.vessel_name, v.doc_status,
            cd.cargo_name,
            COALESCE(cd.bl_quantity, 0) AS bl_quantity,
            COALESCE(cd.quantity_uom, '') AS uom,
            'Export' AS decl_type
        FROM vcn_header v
        JOIN vcn_export_cargo_declaration cd ON cd.vcn_id = v.id
        WHERE v.doc_status != 'Closed'
        ORDER BY id DESC
    ''')
    vcn_declarations = cur.fetchall()

    # Actual handled per VCN + cargo
    cur.execute('''
        SELECT source_id, COALESCE(cargo_name,'') AS cargo_name,
               COALESCE(SUM(quantity),0) AS actual
        FROM lueu_lines
        WHERE source_type = 'VCN' AND (is_deleted IS NOT TRUE)
        GROUP BY source_id, cargo_name
    ''')
    vcn_actual = {}
    for r in cur.fetchall():
        vcn_actual[(r['source_id'], r['cargo_name'])] = float(r['actual'] or 0)

    vcn_rows = []
    seen_vcn = {}
    for r in vcn_declarations:
        key = (r['id'], r['cargo_name'], r['decl_type'])
        if key in seen_vcn:
            continue
        seen_vcn[key] = True
        bl = float(r['bl_quantity'] or 0)
        actual = vcn_actual.get((r['id'], r['cargo_name'] or ''), 0)
        pct = round((actual / bl * 100) if bl > 0 else 0, 1)
        vcn_rows.append({
            'id': r['id'],
            'doc_num': r['vcn_doc_num'],
            'vessel_name': r['vessel_name'],
            'status': r['doc_status'],
            'cargo_name': r['cargo_name'],
            'bl_quantity': round(bl, 2),
            'actual': round(actual, 2),
            'remaining': round(bl - actual, 2),
            'pct': pct,
            'uom': r['uom'],
            'decl_type': r['decl_type'],
            'exceeded': actual > bl and bl > 0,
        })

    # ── Active MBCs ──────────────────────────────────────────────────────────
    # Aggregate customer_details quantities per MBC; fall back to mbc_header.bl_quantity
    # if no customer rows exist yet.
    cur.execute('''
        SELECT
            m.id, m.doc_num, m.mbc_name, m.doc_status,
            COALESCE(m.cargo_name, '') AS cargo_name,
            COALESCE(m.quantity_uom, '') AS uom,
            CASE
                WHEN COUNT(cd.id) > 0 THEN COALESCE(SUM(cd.quantity), 0)
                ELSE COALESCE(m.bl_quantity, 0)
            END AS bl_quantity
        FROM mbc_header m
        LEFT JOIN mbc_customer_details cd ON cd.mbc_id = m.id
        WHERE m.doc_status != 'Closed'
        GROUP BY m.id, m.doc_num, m.mbc_name, m.doc_status,
                 m.cargo_name, m.quantity_uom, m.bl_quantity
        ORDER BY m.id DESC
    ''')
    mbc_declarations = cur.fetchall()

    cur.execute('''
        SELECT source_id, COALESCE(SUM(quantity), 0) AS actual
        FROM lueu_lines
        WHERE source_type = 'MBC' AND (is_deleted IS NOT TRUE)
        GROUP BY source_id
    ''')
    mbc_actual_map = {r['source_id']: float(r['actual'] or 0) for r in cur.fetchall()}

    mbc_rows = []
    for r in mbc_declarations:
        bl = float(r['bl_quantity'] or 0)
        actual = mbc_actual_map.get(r['id'], 0)
        if bl > 0 and actual >= bl:
            continue
        pct = round((actual / bl * 100) if bl > 0 else 0, 1)
        mbc_rows.append({
            'id': r['id'],
            'doc_num': r['doc_num'],
            'mbc_name': r['mbc_name'],
            'status': r['doc_status'],
            'cargo_name': r['cargo_name'],
            'bl_quantity': round(bl, 2),
            'actual': round(actual, 2),
            'remaining': round(bl - actual, 2),
            'pct': pct,
            'uom': r['uom'],
            'exceeded': actual > bl and bl > 0,
        })

    # ── Shift breakdown: Today + Yesterday ───────────────────────────────────
    cur.execute('''
        SELECT
            entry_date,
            shift,
            COALESCE(SUM(quantity), 0) AS total_tonnes,
            ROUND(COALESCE(SUM(
                CASE
                    WHEN from_time IS NOT NULL AND to_time IS NOT NULL
                         AND from_time != '' AND to_time != ''
                    THEN
                        CASE
                            WHEN to_time > from_time
                            THEN (
                                (CAST(SPLIT_PART(to_time,':',1) AS INT)*60 + CAST(SPLIT_PART(to_time,':',2) AS INT))
                              - (CAST(SPLIT_PART(from_time,':',1) AS INT)*60 + CAST(SPLIT_PART(from_time,':',2) AS INT))
                            ) / 60.0
                            ELSE
                            (1440
                              - (CAST(SPLIT_PART(from_time,':',1) AS INT)*60 + CAST(SPLIT_PART(from_time,':',2) AS INT))
                              + (CAST(SPLIT_PART(to_time,':',1) AS INT)*60 + CAST(SPLIT_PART(to_time,':',2) AS INT))
                            ) / 60.0
                        END
                    ELSE 0
                END
            ), 0)::numeric, 2) AS total_hrs
        FROM lueu_lines
        WHERE entry_date IN (%s, %s) AND (is_deleted IS NOT TRUE)
        GROUP BY entry_date, shift
        ORDER BY entry_date, shift
    ''', [today_s, yesterday_s])

    shifts_raw = cur.fetchall()

    shifts = {'today': {}, 'yesterday': {}}
    for r in shifts_raw:
        day = 'today' if r['entry_date'] == today_s else 'yesterday'
        shift = r['shift'] or '?'
        shifts[day][shift] = {
            'tonnes': round(float(r['total_tonnes'] or 0), 2),
            'hrs':    float(r['total_hrs'] or 0),
        }

    # ── Current shift (A=06-14, B=14-22, C=22-06) ───────────────────────────
    now = datetime.now()
    h = now.hour
    if 6 <= h < 14:
        current_shift = 'A'
    elif 14 <= h < 22:
        current_shift = 'B'
    else:
        current_shift = 'C'

    # ── All equipment master list ─────────────────────────────────────────────
    cur.execute('SELECT name FROM equipment ORDER BY name')
    all_equipment = [r['name'] for r in cur.fetchall()]

    # ── Today aggregates per equipment ───────────────────────────────────────
    cur.execute('''
        SELECT
            equipment_name,
            COUNT(*)                         AS entry_count,
            COALESCE(SUM(quantity), 0)       AS today_qty,
            MAX(quantity_uom)                AS uom,
            COUNT(CASE WHEN shift = %s THEN 1 END) AS current_shift_count
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
          AND equipment_name IS NOT NULL AND equipment_name != ''
        GROUP BY equipment_name
    ''', [current_shift, today_s])
    eq_agg = {r['equipment_name']: dict(r) for r in cur.fetchall()}

    # ── Most recent assignment per equipment today (by highest id) ───────────
    cur.execute('''
        SELECT DISTINCT ON (equipment_name)
            equipment_name,
            source_display,
            barge_name,
            cargo_name,
            shift,
            from_time,
            to_time,
            shift_incharge,
            operator_name,
            delay_name,
            id
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
          AND equipment_name IS NOT NULL AND equipment_name != ''
        ORDER BY equipment_name, id DESC
    ''', [today_s])
    eq_latest = {r['equipment_name']: dict(r) for r in cur.fetchall()}

    # ── Last entry across all equipment today ────────────────────────────────
    cur.execute('''
        SELECT equipment_name, to_time, created_by, source_display, barge_name, id
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
          AND to_time IS NOT NULL AND to_time != ''
        ORDER BY id DESC
        LIMIT 1
    ''', [today_s])
    last_row = cur.fetchone()
    last_entry = dict(last_row) if last_row else None

    conn.close()

    # ── Build equipment board ─────────────────────────────────────────────────
    equipment_board = []
    for eq in all_equipment:
        agg = eq_agg.get(eq, {})
        lat = eq_latest.get(eq, {})
        entry_count = int(agg.get('entry_count', 0))
        today_qty   = round(float(agg.get('today_qty', 0)), 2)
        cur_shift_count = int(agg.get('current_shift_count', 0))

        if entry_count == 0:
            status = 'no_data'
        elif cur_shift_count == 0:
            status = 'idle'   # has entries today but none in current shift
        else:
            status = 'active'

        equipment_board.append({
            'name':            eq,
            'status':          status,
            'entry_count':     entry_count,
            'today_qty':       today_qty,
            'uom':             agg.get('uom') or '',
            'source_display':  lat.get('source_display') or '',
            'barge_name':      lat.get('barge_name') or '',
            'cargo_name':      lat.get('cargo_name') or '',
            'last_shift':      lat.get('shift') or '',
            'last_to_time':    lat.get('to_time') or '',
            'shift_incharge':  lat.get('shift_incharge') or '',
            'operator_name':   lat.get('operator_name') or '',
            'delay_name':      lat.get('delay_name') or '',
        })

    return {
        'kpis':            kpis,
        'vcn':             vcn_rows,
        'mbc':             mbc_rows,
        'shifts':          shifts,
        'equipment_board': equipment_board,
        'current_shift':   current_shift,
        'last_entry':      last_entry,
        'as_of':           now.strftime('%d-%b-%Y %H:%M:%S'),
        'today':           today_s,
        'yesterday':       yesterday_s,
    }
