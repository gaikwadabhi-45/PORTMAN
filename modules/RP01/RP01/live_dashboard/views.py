from flask import render_template, session, redirect, url_for, jsonify
from functools import wraps
from datetime import datetime, date, timedelta

from .. import bp
from database import get_db, get_cursor


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


@bp.route('/module/RP01/live-dashboard/')
@login_required
def live_dashboard_index():
    return render_template('live_dashboard/live_dashboard.html',
                           username=session.get('username'))


@bp.route('/api/module/RP01/live-dashboard/data')
@login_required
def live_dashboard_data():
    conn = get_db()
    cur = get_cursor(conn)

    today = date.today()
    yesterday = today - timedelta(days=1)
    today_s = today.strftime('%Y-%m-%d')
    yesterday_s = yesterday.strftime('%Y-%m-%d')
    month_start_s = today.replace(day=1).strftime('%Y-%m-%d')
    now = datetime.now()

    h = now.hour
    if 6 <= h < 14:
        current_shift = 'A'
    elif 14 <= h < 22:
        current_shift = 'B'
    else:
        current_shift = 'C'

    # ── Berths (alphabetical) ───────────────────────────────────────────────
    cur.execute('SELECT berth_name FROM port_berth_master ORDER BY berth_name')
    berths_raw = [r['berth_name'] for r in cur.fetchall()]

    # ── Active sources per berth today (distinct vessel/barge per berth) ────
    cur.execute('''
        SELECT
            berth_name,
            source_type,
            source_id,
            MAX(source_display) AS source_display,
            COALESCE(MAX(barge_name), '')  AS barge_name,
            COALESCE(MAX(cargo_name), '')  AS cargo_name
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
          AND berth_name IS NOT NULL AND berth_name != ''
        GROUP BY berth_name, source_type, source_id
        ORDER BY berth_name, MAX(source_display)
    ''', [today_s])
    berth_sources_raw = cur.fetchall()

    berth_sources = {}
    for r in berth_sources_raw:
        bn = r['berth_name']
        if bn not in berth_sources:
            berth_sources[bn] = []
        berth_sources[bn].append({
            'source_type':    r['source_type'],
            'source_id':      r['source_id'],
            'source_display': r['source_display'],
            'barge_name':     r['barge_name'],
            'cargo_name':     r['cargo_name'],
        })

    # ── Latest equipment per (source, berth) today ─────────────────────────
    cur.execute('''
        SELECT DISTINCT ON (source_type, source_id, berth_name)
            source_type, source_id, berth_name,
            equipment_name, shift_incharge, shift
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
          AND berth_name IS NOT NULL AND berth_name != ''
          AND equipment_name IS NOT NULL AND equipment_name != ''
        ORDER BY source_type, source_id, berth_name, id DESC
    ''', [today_s])
    latest_equipment = {
        (r['source_type'], r['source_id'], r['berth_name']): {
            'equipment_name':  r['equipment_name'],
            'shift_incharge':  r['shift_incharge'],
            'shift':           r['shift'],
        }
        for r in cur.fetchall()
    }

    # ── Routes per source+berth today ──────────────────────────────────────
    cur.execute('''
        SELECT
            source_type, source_id, berth_name,
            COALESCE(route_name, 'Unknown') AS route_name,
            COALESCE(SUM(quantity), 0)      AS qty
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
          AND berth_name IS NOT NULL AND berth_name != ''
        GROUP BY source_type, source_id, berth_name, route_name
        ORDER BY qty DESC
    ''', [today_s])
    source_routes = {}
    for r in cur.fetchall():
        key = (r['source_type'], r['source_id'], r['berth_name'])
        source_routes.setdefault(key, []).append({
            'route': r['route_name'],
            'qty':   round(float(r['qty'] or 0), 2),
        })

    # ── Total actual handled per source (all time, not just today) ─────────
    cur.execute('''
        SELECT source_type, source_id,
               COALESCE(SUM(quantity), 0) AS actual
        FROM lueu_lines
        WHERE is_deleted IS NOT TRUE
        GROUP BY source_type, source_id
    ''')
    source_actual = {(r['source_type'], r['source_id']): float(r['actual'] or 0)
                     for r in cur.fetchall()}

    # ── BL quantities: VCN ─────────────────────────────────────────────────
    cur.execute('''
        SELECT v.id, COALESCE(SUM(cd.bl_quantity), 0) AS bl_qty
        FROM vcn_header v
        JOIN vcn_cargo_declaration cd ON cd.vcn_id = v.id
        WHERE v.doc_status != 'Closed'
        GROUP BY v.id
        UNION ALL
        SELECT v.id, COALESCE(SUM(cd.bl_quantity), 0) AS bl_qty
        FROM vcn_header v
        JOIN vcn_export_cargo_declaration cd ON cd.vcn_id = v.id
        WHERE v.doc_status != 'Closed'
        GROUP BY v.id
    ''')
    vcn_bl = {}
    for r in cur.fetchall():
        key = ('VCN', r['id'])
        vcn_bl[key] = vcn_bl.get(key, 0) + float(r['bl_qty'] or 0)

    # ── BL quantities: MBC ─────────────────────────────────────────────────
    cur.execute('''
        SELECT m.id,
            CASE WHEN COUNT(cd.id) > 0
                 THEN COALESCE(SUM(cd.quantity), 0)
                 ELSE COALESCE(m.bl_quantity, 0)
            END AS bl_qty
        FROM mbc_header m
        LEFT JOIN mbc_customer_details cd ON cd.mbc_id = m.id
        WHERE m.doc_status != 'Closed'
        GROUP BY m.id, m.bl_quantity
    ''')
    mbc_bl = {('MBC', r['id']): float(r['bl_qty'] or 0) for r in cur.fetchall()}
    all_bl = {**vcn_bl, **mbc_bl}

    # ── Build berth data ────────────────────────────────────────────────────
    berths = []
    for berth_name in berths_raw:
        sources = berth_sources.get(berth_name, [])
        vessels = []
        for s in sources:
            st, sid = s['source_type'], s['source_id']
            key = (st, sid, berth_name)
            eq_info = latest_equipment.get(key, {})
            routes = source_routes.get(key, [])
            actual = source_actual.get((st, sid), 0)
            bl = all_bl.get((st, sid), 0)
            pct = round((actual / bl * 100) if bl > 0 else 0, 1)
            vessels.append({
                'source_display': s['source_display'] or '',
                'barge_name':     s['barge_name'],
                'cargo_name':     s['cargo_name'],
                'equipment':      eq_info.get('equipment_name', ''),
                'shift':          eq_info.get('shift', ''),
                'shift_incharge': eq_info.get('shift_incharge', ''),
                'bl_quantity':    round(bl, 2),
                'actual':         round(actual, 2),
                'pct':            min(pct, 100),
                'routes':         routes,
            })
        berths.append({
            'berth_name': berth_name,
            'vessels':    vessels,
            'active':     len(vessels) > 0,
        })

    # ── Current shift incharge (most frequent in current shift today) ───────
    cur.execute('''
        SELECT shift_incharge, COUNT(*) AS cnt
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
          AND shift = %s
          AND shift_incharge IS NOT NULL AND shift_incharge != ''
        GROUP BY shift_incharge
        ORDER BY cnt DESC LIMIT 1
    ''', [today_s, current_shift])
    si_row = cur.fetchone()
    shift_incharge = si_row['shift_incharge'] if si_row else '—'

    # ── Today's shift breakdown ─────────────────────────────────────────────
    cur.execute('''
        SELECT shift, COALESCE(SUM(quantity), 0) AS tonnes
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
          AND shift IS NOT NULL AND shift != ''
        GROUP BY shift ORDER BY shift
    ''', [today_s])
    today_shifts = {r['shift']: round(float(r['tonnes'] or 0), 2)
                    for r in cur.fetchall()}

    # ── Yesterday's shift breakdown ─────────────────────────────────────────
    cur.execute('''
        SELECT shift,
               COALESCE(SUM(quantity), 0) AS tonnes,
               COUNT(*)                   AS ops
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
          AND shift IS NOT NULL AND shift != ''
        GROUP BY shift ORDER BY shift
    ''', [yesterday_s])
    yesterday_shifts = {
        r['shift']: {'tonnes': round(float(r['tonnes'] or 0), 2), 'ops': int(r['ops'] or 0)}
        for r in cur.fetchall()
    }

    # ── Route performance today ─────────────────────────────────────────────
    cur.execute('''
        SELECT COALESCE(route_name, 'Unknown') AS route_name,
               COALESCE(SUM(quantity), 0)      AS qty
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
        GROUP BY route_name
        ORDER BY qty DESC
    ''', [today_s])
    route_perf = [{'route': r['route_name'], 'qty': round(float(r['qty'] or 0), 2)}
                  for r in cur.fetchall()]

    # ── Tide data (last 32 hours) ───────────────────────────────────────────
    tide_from = (now - timedelta(hours=32)).strftime('%Y-%m-%dT%H:%M:%S')
    cur.execute('''
        SELECT tide_datetime, tide_meters
        FROM tide_master
        WHERE tide_datetime >= %s
        ORDER BY tide_datetime
    ''', [tide_from])
    tides = [{'dt': str(r['tide_datetime']), 'h': float(r['tide_meters'] or 0)}
             for r in cur.fetchall()]

    # ── KPIs ───────────────────────────────────────────────────────────────
    cur.execute('''
        SELECT COALESCE(SUM(quantity), 0) AS mtd
        FROM lueu_lines
        WHERE entry_date >= %s AND entry_date <= %s
          AND (is_deleted IS NOT TRUE)
    ''', [month_start_s, today_s])
    mtd = round(float(cur.fetchone()['mtd'] or 0), 2)

    cur.execute('''
        SELECT COALESCE(SUM(quantity), 0) AS today_total
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
    ''', [today_s])
    today_total = round(float(cur.fetchone()['today_total'] or 0), 2)

    # ── Equipment performance today ─────────────────────────────────────────
    cur.execute('''
        SELECT equipment_name,
               COALESCE(SUM(quantity), 0) AS qty,
               COUNT(*)                   AS ops
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
          AND equipment_name IS NOT NULL AND equipment_name != ''
        GROUP BY equipment_name
        ORDER BY qty DESC
    ''', [today_s])
    equipment_perf = [
        {'equipment': r['equipment_name'], 'qty': round(float(r['qty'] or 0), 2), 'ops': int(r['ops'] or 0)}
        for r in cur.fetchall()
    ]

    # Count active sources (vessels/barges active today across all berths)
    active_count = sum(len(b['vessels']) for b in berths)

    conn.close()

    return jsonify({
        'berths':           berths,
        'current_shift':    current_shift,
        'shift_incharge':   shift_incharge,
        'today_shifts':     today_shifts,
        'yesterday_shifts': yesterday_shifts,
        'equipment_perf':   equipment_perf,
        'route_perf':       route_perf,
        'tides':            tides,
        'mtd':              mtd,
        'today_total':      today_total,
        'active_sources':   active_count,
        'as_of':            now.strftime('%Y-%m-%d %H:%M:%S'),
    })
